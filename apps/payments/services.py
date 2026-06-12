from django.core.exceptions import ValidationError
from django.db import transaction

from apps.sales.models import SaleStatus
from .models import PaymentStatus


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
        from apps.commissions.services import refresh_sale_commissions
        refresh_sale_commissions(sale=sale)
    return payment


@transaction.atomic
def confirm_refund(*, refund):
    if refund.status == PaymentStatus.REFUNDED:
        return refund
    payment = refund.payment.__class__.objects.select_for_update().get(pk=refund.payment_id)
    if refund.amount <= 0 or refund.amount > payment.amount:
        raise ValidationError("Invalid refund amount.")
    refund.status = PaymentStatus.REFUNDED
    refund.save(update_fields=["status", "updated_at"])
    if payment.sale:
        sale = payment.sale.__class__.objects.select_for_update().get(pk=payment.sale_id)
        sale.paid_total = max(sale.paid_total - refund.amount, 0)
        sale.save(update_fields=["paid_total", "updated_at"])
    if refund.amount == payment.amount:
        payment.status = PaymentStatus.REFUNDED
        payment.save(update_fields=["status", "updated_at"])
    return refund
