from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.integrations.services import queue_integration_event
from apps.commissions.services import refresh_sale_commissions
from apps.inventory.models import SerialStatus, StockMovementType, StockUnit
from apps.inventory.services import post_stock_movement
from apps.operations.models import Receivable
from .models import ReturnDisposition, ReturnStatus, SaleStatus


def calculate_sale_line_amounts(*, product, quantity, discount=Decimal("0")):
    gross = product.selling_price * quantity
    net = gross - discount
    if net < 0:
        raise ValidationError("Discount cannot exceed the gross line value.")
    tax = (net * product.tax_rate / Decimal("100")).quantize(Decimal("0.01"))
    return {"gross": gross, "discount": discount, "tax": tax, "total": net + tax}


@transaction.atomic
def complete_sale(*, sale, actor):
    if sale.status != SaleStatus.DRAFT:
        raise ValidationError("Only draft sales can be completed.")
    subtotal = tax = discount = Decimal("0")
    for line in sale.lines.select_related("product", "stock_unit"):
        if line.product.is_serialized:
            if not line.stock_unit_id:
                raise ValidationError("Serialized products require an available stock unit.")
            stock_unit = StockUnit.objects.select_for_update().get(pk=line.stock_unit_id)
            if stock_unit.status != SerialStatus.AVAILABLE:
                raise ValidationError("Serialized products require an available stock unit.")
            if stock_unit.location_id != sale.location_id:
                raise ValidationError("Serialized item is not at this sale location.")
            line.stock_unit = stock_unit
        post_stock_movement(
            organization=sale.organization, product=line.product, location=sale.location,
            quantity=-line.quantity, movement_type=StockMovementType.SALE, actor=actor,
            stock_unit=line.stock_unit, unit_cost=line.unit_cost, reference_type="sale", reference_id=sale.id,
        )
        if line.stock_unit:
            line.stock_unit.status = SerialStatus.SOLD
            line.stock_unit.location = None
            line.stock_unit.save(update_fields=["status", "location", "updated_at"])
        subtotal += line.unit_price * line.quantity
        tax += line.tax
        discount += line.discount
    sale.subtotal = subtotal
    sale.tax_total = tax
    sale.discount_total = discount
    sale.total = subtotal + tax - discount
    sale.status = SaleStatus.PAID if sale.paid_total >= sale.total else SaleStatus.COMPLETED
    sale.completed_at = timezone.now()
    sale.save(update_fields=["subtotal", "tax_total", "discount_total", "total", "status", "completed_at", "updated_at"])
    refresh_sale_commissions(sale=sale)
    queue_integration_event(
        organization=sale.organization,
        provider="etims",
        event_type="invoice.submit",
        idempotency_key=f"sale-{sale.id}",
        payload={"sale_number": sale.number, "total": str(sale.total)},
    )
    return sale


@transaction.atomic
def create_credit_receivable(*, sale):
    if not sale.customer:
        raise ValidationError("Credit sales require a customer.")
    outstanding = sale.total - sale.paid_total
    exposure = Receivable.objects.filter(
        organization=sale.organization,
        customer=sale.customer,
        outstanding_amount__gt=0,
    ).exclude(sale=sale).aggregate(total=models.Sum("outstanding_amount"))["total"] or Decimal("0")
    if outstanding <= 0:
        return None
    if exposure + outstanding > sale.customer.credit_limit:
        raise ValidationError("Customer credit limit would be exceeded.")
    if not sale.due_on:
        sale.due_on = timezone.localdate() + timedelta(days=sale.customer.payment_terms_days or 30)
        sale.save(update_fields=["due_on", "updated_at"])
    receivable, _ = Receivable.objects.update_or_create(
        organization=sale.organization,
        sale=sale,
        defaults={
            "customer": sale.customer,
            "original_amount": outstanding,
            "outstanding_amount": outstanding,
            "due_on": sale.due_on,
        },
    )
    if not receivable.installments.exists():
        from apps.operations.models import ReceivableInstallment
        ReceivableInstallment.objects.create(
            organization=sale.organization,
            receivable=receivable,
            sequence=1,
            due_on=receivable.due_on,
            amount=receivable.outstanding_amount,
        )
    return receivable


@transaction.atomic
def complete_return(*, sale_return, actor):
    if sale_return.status != ReturnStatus.APPROVED:
        raise ValidationError("Only approved returns can be completed.")
    for return_line in sale_return.lines.select_related("sale_line__product", "sale_line__stock_unit"):
        line = return_line.sale_line.__class__.objects.select_for_update().get(pk=return_line.sale_line_id)
        if return_line.quantity <= 0 or line.returned_quantity + return_line.quantity > line.quantity:
            raise ValidationError("Return quantity exceeds the remaining sold quantity.")
        post_stock_movement(
            organization=sale_return.organization, product=line.product, location=sale_return.sale.location,
            quantity=return_line.quantity, movement_type=StockMovementType.CUSTOMER_RETURN,
            actor=actor, stock_unit=line.stock_unit, unit_cost=line.unit_cost,
            reference_type="sale_return", reference_id=sale_return.id,
        )
        line.returned_quantity += return_line.quantity
        line.save(update_fields=["returned_quantity", "updated_at"])
        if line.stock_unit:
            line.stock_unit.location = sale_return.sale.location
            if return_line.disposition == ReturnDisposition.RESTOCK:
                line.stock_unit.status = SerialStatus.AVAILABLE
            elif return_line.disposition == ReturnDisposition.DAMAGED:
                line.stock_unit.status = SerialStatus.DAMAGED
            else:
                line.stock_unit.status = SerialStatus.WARRANTY_REPAIR
            line.stock_unit.save(update_fields=["status", "location", "updated_at"])
        if return_line.disposition != ReturnDisposition.RESTOCK:
            post_stock_movement(
                organization=sale_return.organization, product=line.product, location=sale_return.sale.location,
                quantity=-return_line.quantity, movement_type=StockMovementType.ADJUSTMENT,
                actor=actor, stock_unit=line.stock_unit, unit_cost=line.unit_cost,
                reference_type="sale_return_disposition", reference_id=sale_return.id,
                reason=return_line.get_disposition_display(),
            )
    sale_return.status = ReturnStatus.COMPLETED
    sale_return.save(update_fields=["status", "updated_at"])
    all_returned = all(
        line.returned_quantity >= line.quantity for line in sale_return.sale.lines.all()
    )
    if all_returned:
        sale_return.sale.status = SaleStatus.RETURNED
        sale_return.sale.save(update_fields=["status", "updated_at"])
    sale_return.sale.commissions.update(is_payable=False)
    queue_integration_event(
        organization=sale_return.organization,
        provider="etims",
        event_type="credit_note.submit",
        idempotency_key=f"return-{sale_return.id}",
        payload={"sale_number": sale_return.sale.number, "refund_amount": str(sale_return.refund_amount)},
    )
    return sale_return
