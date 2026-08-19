from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from apps.purchasing.models import SupplierReturnStatus

from .models import Payable, PayablePayment


def _money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def _payable_totals(purchase_order):
    received_total = purchase_order.lines.aggregate(
        total=models.Sum(models.F("received_quantity") * models.F("unit_cost"))
    )["total"] or Decimal("0")
    supplier_credit_total = purchase_order.lines.filter(
        supplier_returns__status=SupplierReturnStatus.COMPLETED
    ).aggregate(
        total=models.Sum(models.F("supplier_returns__quantity") * models.F("unit_cost"))
    )["total"] or Decimal("0")
    return _money(received_total), _money(supplier_credit_total)


def recalculate_payable(*, payable):
    """Rebuild the balance from durable stock, return, and payment evidence."""

    payable = Payable.objects.select_for_update().select_related("purchase_order").get(pk=payable.pk)
    received_total, supplier_credit_total = _payable_totals(payable.purchase_order)
    payment_total = payable.payments.filter(reversed_at__isnull=True).aggregate(
        total=models.Sum("amount")
    )["total"] or Decimal("0")
    net_balance = received_total - supplier_credit_total - _money(payment_total)
    payable.original_amount = received_total
    payable.outstanding_amount = _money(max(net_balance, Decimal("0")))
    payable.credit_balance = _money(max(-net_balance, Decimal("0")))
    payable.is_settled = payable.outstanding_amount == 0
    payable.save(
        update_fields=("original_amount", "outstanding_amount", "credit_balance", "is_settled", "updated_at")
    )
    return payable


def ensure_payable_for_order(*, purchase_order):
    payable, _ = Payable.objects.select_for_update().get_or_create(
        organization=purchase_order.organization,
        purchase_order=purchase_order,
        defaults={
            "supplier": purchase_order.supplier,
            "original_amount": Decimal("0"),
            "outstanding_amount": Decimal("0"),
            "due_on": timezone.localdate() + timedelta(days=30),
        },
    )
    return recalculate_payable(payable=payable)


@transaction.atomic
def record_payable_payment(*, payable, actor, request_id, amount, method, reference="", notes=""):
    payable = Payable.objects.select_for_update().get(
        pk=payable.pk,
        organization=payable.organization,
    )
    existing = PayablePayment.objects.filter(
        organization=payable.organization,
        request_id=request_id,
    ).first()
    amount = _money(amount)
    reference = reference.strip()
    if existing:
        if (
            existing.payable_id != payable.id
            or existing.amount != amount
            or existing.method != method
            or existing.reference != reference
        ):
            raise ValidationError("This payment request was already used with different details.")
        return existing, False
    payable = recalculate_payable(payable=payable)
    if payable.outstanding_amount <= 0:
        raise ValidationError("This supplier payable has no outstanding balance.")
    if amount > payable.outstanding_amount:
        raise ValidationError(
            f"Payment exceeds the outstanding balance of KES {payable.outstanding_amount}."
        )
    try:
        payment = PayablePayment.objects.create(
            organization=payable.organization,
            request_id=request_id,
            number=f"SP-{timezone.now():%Y%m%d%H%M%S%f}",
            payable=payable,
            amount=amount,
            method=method,
            reference=reference,
            notes=notes,
            paid_by=actor,
            paid_at=timezone.now(),
        )
    except IntegrityError as error:
        raise ValidationError("This supplier payment reference has already been used.") from error
    recalculate_payable(payable=payable)
    return payment, True


@transaction.atomic
def reverse_payable_payment(*, payment, actor, reason):
    payment = PayablePayment.objects.select_for_update().select_related("payable").get(
        pk=payment.pk,
        organization=payment.organization,
    )
    if payment.reversed_at:
        raise ValidationError("This supplier payment has already been reversed.")
    reason = reason.strip()
    if len(reason) < 5:
        raise ValidationError("Explain why the supplier payment is being reversed.")
    reversed_at = timezone.now()
    PayablePayment.objects.filter(pk=payment.pk, reversed_at__isnull=True).update(
        reversed_at=reversed_at,
        reversed_by=actor,
        reversal_reason=reason,
        updated_at=reversed_at,
    )
    payment.refresh_from_db()
    recalculate_payable(payable=payment.payable)
    return payment
