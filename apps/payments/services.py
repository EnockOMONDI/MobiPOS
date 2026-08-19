from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.sales.models import SaleStatus
from .models import Payment, PaymentMethod, PaymentStatus, PaymentVerificationSource


PROVIDER_CONFIRMED_METHODS = {PaymentMethod.MPESA, PaymentMethod.CARD, PaymentMethod.BANK}


@transaction.atomic
def record_sale_payments(*, sale, allocations, received_by):
    total = sum((allocation["amount"] for allocation in allocations), 0)
    if total > sale.total:
        raise ValidationError("Payment allocations exceed the sale total.")

    payments = []
    for index, allocation in enumerate(allocations, start=1):
        if allocation["method"] == PaymentMethod.CREDIT:
            raise ValidationError("Customer credit must be recorded as a receivable, not a payment.")
        provider_reference = allocation.get("provider_reference", "")
        if provider_reference and Payment.objects.filter(
            organization=sale.organization,
            method=allocation["method"],
            provider_reference=provider_reference,
        ).exists():
            raise ValidationError("This payment reference has already been used.")
        payment = Payment.objects.create(
            organization=sale.organization,
            number=f"PAY-{timezone.now():%Y%m%d%H%M%S%f}-{index}",
            customer=sale.customer,
            sale=sale,
            method=allocation["method"],
            status=PaymentStatus.PENDING,
            amount=allocation["amount"],
            provider_reference=provider_reference,
            received_by=received_by,
        )
        confirm_payment(
            payment=payment,
            confirmed_by=received_by,
            verification_source=PaymentVerificationSource.MANUAL,
        )
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
def confirm_payment(
    *,
    payment,
    confirmed_by=None,
    verification_source=PaymentVerificationSource.MANUAL,
):
    if payment.status == PaymentStatus.CONFIRMED:
        return payment
    if payment.status != PaymentStatus.PENDING:
        raise ValidationError("Only pending payments can be confirmed.")
    valid_sources = {choice for choice, _label in PaymentVerificationSource.choices}
    if verification_source not in valid_sources:
        raise ValidationError("Select a valid payment verification source.")
    if payment.method in PROVIDER_CONFIRMED_METHODS and verification_source == PaymentVerificationSource.MANUAL:
        if not confirmed_by:
            raise ValidationError("A staff member must verify manually recorded electronic payments.")
        if not payment.provider_reference:
            raise ValidationError("A payment reference is required for manually recorded electronic payments.")
    payment.status = PaymentStatus.CONFIRMED
    payment.verification_source = verification_source
    payment.verified_at = timezone.now()
    payment.verified_by = confirmed_by
    payment.save(update_fields=[
        "status", "verification_source", "verified_at", "verified_by", "updated_at",
    ])
    from apps.audit.services import record_audit_event
    record_audit_event(
        action=(
            "payment.manually_verified"
            if verification_source == PaymentVerificationSource.MANUAL
            else "payment.provider_verified"
        ),
        actor=confirmed_by,
        organization=payment.organization,
        target=payment,
        metadata={
            "method": payment.method,
            "reference": payment.provider_reference,
            "verification_source": verification_source,
        },
    )
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
