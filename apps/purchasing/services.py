from decimal import Decimal
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.inventory.models import SerialStatus, StockMovementType, StockUnit
from apps.inventory.services import post_stock_movement
from .models import (
    PurchaseDiscrepancyStatus,
    PurchaseStatus,
    SupplierReturnStatus,
)


@transaction.atomic
def approve_purchase_order(*, order, actor):
    if order.status != PurchaseStatus.DRAFT:
        raise ValidationError("Only draft purchase orders can be approved.")
    if not order.lines.exists():
        raise ValidationError("A purchase order requires at least one line.")
    order.status = PurchaseStatus.APPROVED
    order.save(update_fields=["status", "updated_at"])
    return order


@transaction.atomic
def receive_purchase_line(
    *, line, quantity, actor, serial_numbers=None, damaged_quantity=0,
    close_with_discrepancy=False, discrepancy_reason=""
):
    quantity = Decimal(quantity)
    damaged_quantity = Decimal(damaged_quantity)
    if line.order.status not in {PurchaseStatus.APPROVED, PurchaseStatus.PART_RECEIVED, PurchaseStatus.DISCREPANCY}:
        raise ValidationError("Purchase order is not ready to receive.")
    remaining = line.quantity - line.received_quantity
    if quantity <= 0 or damaged_quantity < 0 or quantity + damaged_quantity > remaining:
        raise ValidationError("Invalid receipt quantity.")
    serial_numbers = serial_numbers or []
    if line.product.is_serialized and len(serial_numbers) != int(quantity):
        raise ValidationError("Each serialized item requires one serial number.")
    for serial in serial_numbers:
        unit = StockUnit.objects.create(
            organization=line.organization, product=line.product, serial_number=serial,
            location=line.order.destination, status=SerialStatus.AVAILABLE, unit_cost=line.unit_cost,
        )
        post_stock_movement(
            organization=line.organization, product=line.product, location=line.order.destination,
            quantity=1, movement_type=StockMovementType.PURCHASE_RECEIPT, actor=actor,
            stock_unit=unit, unit_cost=line.unit_cost, reference_type="purchase_order", reference_id=line.order_id,
        )
    if not line.product.is_serialized:
        post_stock_movement(
            organization=line.organization, product=line.product, location=line.order.destination,
            quantity=quantity, movement_type=StockMovementType.PURCHASE_RECEIPT, actor=actor,
            unit_cost=line.unit_cost, reference_type="purchase_order", reference_id=line.order_id,
        )
    line.received_quantity += quantity
    line.save(update_fields=["received_quantity", "updated_at"])
    has_remaining = line.order.lines.filter(received_quantity__lt=models.F("quantity")).exists()
    if close_with_discrepancy:
        missing = remaining - quantity - damaged_quantity
        if not damaged_quantity and not missing:
            raise ValidationError("No receipt discrepancy was detected.")
        from .models import PurchaseDiscrepancy
        PurchaseDiscrepancy.objects.create(
            organization=line.organization,
            number=f"POD-{timezone.now():%Y%m%d%H%M%S%f}",
            line=line,
            expected_quantity=remaining,
            accepted_quantity=quantity,
            damaged_quantity=damaged_quantity,
            missing_quantity=missing,
            reason=discrepancy_reason,
        )
        line.order.status = PurchaseStatus.DISCREPANCY
    else:
        line.order.status = PurchaseStatus.PART_RECEIVED if has_remaining else PurchaseStatus.RECEIVED
    line.order.save(update_fields=["status", "updated_at"])
    from apps.operations.models import Payable
    received_total = line.order.lines.aggregate(
        total=models.Sum(models.F("received_quantity") * models.F("unit_cost"))
    )["total"] or Decimal("0")
    Payable.objects.update_or_create(
        organization=line.organization,
        purchase_order=line.order,
        defaults={
            "supplier": line.order.supplier,
            "original_amount": received_total,
            "outstanding_amount": received_total,
            "due_on": timezone.localdate() + timedelta(days=30),
        },
    )
    return line


@transaction.atomic
def resolve_purchase_discrepancy(*, discrepancy, actor, resolution):
    if discrepancy.status != PurchaseDiscrepancyStatus.PENDING:
        raise ValidationError("Only pending purchase discrepancies can be resolved.")
    if resolution not in {"accept_short", "replacement_pending"}:
        raise ValidationError("Choose accept short or replacement pending.")
    discrepancy.status = PurchaseDiscrepancyStatus.RESOLVED
    discrepancy.resolution = resolution
    discrepancy.resolved_by = actor
    discrepancy.save(update_fields=["status", "resolution", "resolved_by", "updated_at"])
    order = discrepancy.line.order
    if resolution == "accept_short":
        order.status = PurchaseStatus.CLOSED
    else:
        order.status = PurchaseStatus.PART_RECEIVED
    order.save(update_fields=["status", "updated_at"])
    return discrepancy


@transaction.atomic
def complete_supplier_return(*, supplier_return, actor):
    if supplier_return.status != SupplierReturnStatus.REQUESTED:
        raise ValidationError("Only requested supplier returns can be completed.")
    completed = supplier_return.line.supplier_returns.filter(
        status=SupplierReturnStatus.COMPLETED
    ).exclude(pk=supplier_return.pk).aggregate(total=models.Sum("quantity"))["total"] or Decimal("0")
    if completed + supplier_return.quantity > supplier_return.line.received_quantity:
        raise ValidationError("Return quantity exceeds received stock.")
    post_stock_movement(
        organization=supplier_return.organization,
        product=supplier_return.line.product,
        location=supplier_return.line.order.destination,
        quantity=-supplier_return.quantity,
        movement_type=StockMovementType.SUPPLIER_RETURN,
        actor=actor,
        stock_unit=supplier_return.stock_unit,
        unit_cost=supplier_return.line.unit_cost,
        reference_type="supplier_return",
        reference_id=supplier_return.id,
        reason=supplier_return.reason,
    )
    if supplier_return.stock_unit:
        supplier_return.stock_unit.status = SerialStatus.WRITTEN_OFF
        supplier_return.stock_unit.location = None
        supplier_return.stock_unit.save(update_fields=["status", "location", "updated_at"])
    supplier_return.status = SupplierReturnStatus.COMPLETED
    supplier_return.approved_by = actor
    supplier_return.save(update_fields=["status", "approved_by", "updated_at"])
    if hasattr(supplier_return.line.order, "payable"):
        payable = supplier_return.line.order.payable
        reduction = supplier_return.quantity * supplier_return.line.unit_cost
        payable.outstanding_amount = max(payable.outstanding_amount - reduction, Decimal("0"))
        payable.is_settled = payable.outstanding_amount == 0
        payable.save(update_fields=["outstanding_amount", "is_settled", "updated_at"])
    return supplier_return
