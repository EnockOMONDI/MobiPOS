from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import (
    CommissionAccrual,
    CommissionPayoutLine,
    CommissionPayoutStatus,
    CommissionRule,
)


@transaction.atomic
def refresh_sale_commissions(*, sale):
    if not sale.agent_id:
        return []
    lines = list(sale.lines.select_related("product"))
    product_ids = {line.product_id for line in lines}
    rules = CommissionRule.objects.filter(
        organization=sale.organization,
        is_active=True,
    ).filter(product__isnull=True) | CommissionRule.objects.filter(
        organization=sale.organization,
        is_active=True,
        product_id__in=product_ids,
    )
    accruals = []
    for rule in rules.distinct():
        eligible_lines = [line for line in lines if not rule.product_id or line.product_id == rule.product_id]
        basis = sum((line.line_total for line in eligible_lines), Decimal("0"))
        amount = (basis * rule.percentage / Decimal("100")) + rule.fixed_amount
        accrual, _ = CommissionAccrual.objects.update_or_create(
            organization=sale.organization,
            sale=sale,
            agent=sale.agent,
            rule=rule,
            defaults={
                "amount": amount,
                "is_payable": not rule.requires_full_payment or sale.paid_total >= sale.total,
            },
        )
        accruals.append(accrual)
    return accruals


@transaction.atomic
def populate_commission_payout(*, payout):
    if payout.lines.exists():
        raise ValidationError("This payout has already been populated.")
    accruals = CommissionAccrual.objects.select_for_update().filter(
        organization=payout.organization,
        agent=payout.agent,
        is_payable=True,
        paid_at__isnull=True,
        sale__completed_at__date__range=(payout.period_start, payout.period_end),
    ).exclude(payout_line__isnull=False)
    if not accruals.exists():
        raise ValidationError("No payable commission accruals were found for this period.")
    total = Decimal("0")
    for accrual in accruals:
        CommissionPayoutLine.objects.create(
            organization=payout.organization, payout=payout, accrual=accrual, amount=accrual.amount
        )
        total += accrual.amount
    payout.amount = total
    payout.save(update_fields=["amount", "updated_at"])
    return payout


@transaction.atomic
def approve_commission_payout(*, payout, actor):
    if payout.status != CommissionPayoutStatus.REQUESTED or not payout.lines.exists():
        raise ValidationError("Only populated requested payouts can be approved.")
    payout.status = CommissionPayoutStatus.APPROVED
    payout.approved_by = actor
    payout.save(update_fields=["status", "approved_by", "updated_at"])
    return payout


@transaction.atomic
def pay_commission_payout(*, payout, actor, payment_reference):
    if payout.status != CommissionPayoutStatus.APPROVED:
        raise ValidationError("Only approved commission payouts can be paid.")
    paid_at = timezone.now()
    for line in payout.lines.select_related("accrual"):
        line.accrual.paid_at = paid_at
        line.accrual.save(update_fields=["paid_at", "updated_at"])
    payout.status = CommissionPayoutStatus.PAID
    payout.paid_at = paid_at
    payout.payment_reference = payment_reference
    payout.save(update_fields=["status", "paid_at", "payment_reference", "updated_at"])
    return payout
