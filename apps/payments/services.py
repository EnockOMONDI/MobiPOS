from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.sales.models import SaleStatus
from .models import Payment, PaymentMethod, PaymentStatus


@transaction.atomic
def record_sale_payments(*, sale, allocations, received_by):
    total = sum((allocation["amount"] for allocation in allocations), 0)
    if total > sale.total:
        raise ValidationError("Payment allocations exceed the sale total.")

    payments = []
    for index, allocation in enumerate(allocations, start=1):
        if allocation["method"] == PaymentMethod.CREDIT:
            raise ValidationError("Customer credit must be recorded as a receivable, not a payment.")
        payment = Payment.objects.create(
            organization=sale.organization,
            number=f"PAY-{timezone.now():%Y%m%d%H%M%S%f}-{index}",
            customer=sale.customer,
            sale=sale,
            method=allocation["method"],
            status=PaymentStatus.PENDING,
            amount=allocation["amount"],
            provider_reference=allocation.get("provider_reference", ""),
            received_by=received_by,
        )
        confirm_payment(payment=payment)
        payments.append(payment)
    return payments


@transaction.atomic
def allocate_sale_refund(*, sale, amount, reason, approved_by):
    if amount <= 0:
        return []
    if amount > sale.paid_total:
        raise ValidationError("Refund cannot exceed the amount paid for the sale.")

    remaining = amount
    refunds = []
    payments = sale.payments.select_for_update().filter(
        status=PaymentStatus.CONFIRMED,
    ).order_by("received_at")
    for index, payment in enumerate(payments, start=1):
        already_refunded = payment.refunds.filter(
            status=PaymentStatus.REFUNDED,
        ).aggregate(total=Sum("amount"))["total"] or 0
        available = payment.amount - already_refunded
        if available <= 0:
            continue
        refund = payment.refunds.create(
            organization=sale.organization,
            number=f"RFD-{timezone.now():%Y%m%d%H%M%S%f}-{index}",
            amount=min(available, remaining),
            reason=reason,
            approved_by=approved_by,
        )
        confirm_refund(refund=refund)
        refunds.append(refund)
        remaining -= refund.amount
        if remaining <= 0:
            break
    if remaining > 0:
        raise ValidationError("Confirmed payments are insufficient for this refund.")
    return refunds


@transaction.atomic
def confirm_payment(*, payment):
    if payment.status == PaymentStatus.CONFIRMED:
        return payment
    if payment.status != PaymentStatus.PENDING:
        raise ValidationError("Only pending payments can be confirmed.")
    payment.status = PaymentStatus.CONFIRMED
    payment.save(update_fields=["status", "updated_at"])
    if payment.sale:
        sale = payment.sale.__class__.objects.select_for_update().get(pk=payment.sale_id)
        if sale.paid_total + payment.amount > sale.total:
            raise ValidationError("Payment exceeds the outstanding sale balance.")
        sale.paid_total += payment.amount
        sale.status = SaleStatus.PAID if sale.paid_total >= sale.total else SaleStatus.PART_PAID
        sale.save(update_fields=["paid_total", "status", "updated_at"])
        from apps.operations.models import Receivable
        receivable = Receivable.objects.select_for_update().filter(sale=sale).first()
        if receivable:
            receivable.outstanding_amount = max(sale.total - sale.paid_total, 0)
            receivable.save(update_fields=["outstanding_amount", "updated_at"])
            from apps.operations.services import sync_receivable_installments
            sync_receivable_installments(receivable=receivable)
        from apps.commissions.services import refresh_sale_commissions
        refresh_sale_commissions(sale=sale)
    return payment


@transaction.atomic
def confirm_refund(*, refund):
    if refund.status == PaymentStatus.REFUNDED:
        return refund
    if refund.status != PaymentStatus.PENDING:
        raise ValidationError("Only pending refunds can be confirmed.")
    payment = refund.payment.__class__.objects.select_for_update().get(pk=refund.payment_id)
    if payment.status != PaymentStatus.CONFIRMED:
        raise ValidationError("Only confirmed payments can be refunded.")
    refunded_amount = payment.refunds.filter(status=PaymentStatus.REFUNDED).exclude(
        pk=refund.pk
    ).aggregate(total=Sum("amount"))["total"] or 0
    if refund.amount <= 0 or refunded_amount + refund.amount > payment.amount:
        raise ValidationError("Invalid refund amount.")
    refund.status = PaymentStatus.REFUNDED
    refund.save(update_fields=["status", "updated_at"])
    if payment.sale:
        sale = payment.sale.__class__.objects.select_for_update().get(pk=payment.sale_id)
        sale.paid_total = max(sale.paid_total - refund.amount, 0)
        sale.save(update_fields=["paid_total", "updated_at"])
        from apps.operations.models import Receivable
        receivable = Receivable.objects.select_for_update().filter(sale=sale).first()
        if receivable:
            receivable.outstanding_amount = max(sale.total - sale.paid_total, 0)
            receivable.save(update_fields=["outstanding_amount", "updated_at"])
            from apps.operations.services import sync_receivable_installments
            sync_receivable_installments(receivable=receivable)
    if refunded_amount + refund.amount == payment.amount:
        payment.status = PaymentStatus.REFUNDED
        payment.save(update_fields=["status", "updated_at"])
    return refund
