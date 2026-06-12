from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    OrganizationStatus,
    SubscriptionInvoice,
    SubscriptionInvoiceStatus,
    Subscription,
    SubscriptionStatus,
)


@transaction.atomic
def activate_subscription_invoice(*, invoice, payment_reference):
    if invoice.status == SubscriptionInvoiceStatus.PAID:
        return invoice
    if invoice.status == SubscriptionInvoiceStatus.VOID:
        raise ValidationError("A void subscription invoice cannot be paid.")
    invoice.status = SubscriptionInvoiceStatus.PAID
    invoice.paid_at = timezone.now()
    invoice.payment_reference = payment_reference
    invoice.save(update_fields=["status", "paid_at", "payment_reference", "updated_at"])
    subscription = invoice.subscription
    subscription.status = SubscriptionStatus.ACTIVE
    today = timezone.localdate()
    subscription.starts_on = subscription.starts_on or today
    subscription.renews_on = max(today, subscription.renews_on or today) + timedelta(days=30)
    subscription.grace_ends_on = subscription.renews_on + timedelta(days=7)
    subscription.save(update_fields=["status", "starts_on", "renews_on", "grace_ends_on", "updated_at"])
    organization = invoice.organization
    organization.status = OrganizationStatus.ACTIVE
    organization.save(update_fields=["status", "updated_at"])
    return invoice


@transaction.atomic
def refresh_subscription_lifecycle(*, today=None):
    today = today or timezone.localdate()
    changed = {"grace": 0, "suspended": 0, "invoices": 0}
    for subscription in Subscription.objects.select_for_update().select_related("organization", "plan"):
        if subscription.status == SubscriptionStatus.ACTIVE and subscription.renews_on and subscription.renews_on <= today:
            subscription.status = SubscriptionStatus.GRACE
            subscription.save(update_fields=["status", "updated_at"])
            changed["grace"] += 1
        if subscription.status == SubscriptionStatus.GRACE and subscription.grace_ends_on and subscription.grace_ends_on < today:
            subscription.status = SubscriptionStatus.SUSPENDED
            subscription.save(update_fields=["status", "updated_at"])
            subscription.organization.status = OrganizationStatus.SUSPENDED
            subscription.organization.save(update_fields=["status", "updated_at"])
            changed["suspended"] += 1
        if subscription.status in {SubscriptionStatus.ACTIVE, SubscriptionStatus.GRACE} and subscription.renews_on:
            number = f"SUB-{str(subscription.id)[:8].upper()}-{subscription.renews_on:%Y%m%d}"
            _, created = SubscriptionInvoice.objects.get_or_create(
                number=number,
                defaults={
                    "organization": subscription.organization,
                    "subscription": subscription,
                    "amount": subscription.plan.monthly_price,
                    "due_on": subscription.renews_on,
                },
            )
            changed["invoices"] += int(created)
    return changed


def enforce_plan_limit(*, organization, key, current_count):
    subscription = organization.subscription_set.filter(
        status__in=[SubscriptionStatus.ACTIVE, SubscriptionStatus.GRACE]
    ).select_related("plan").first()
    if not subscription:
        raise ValidationError("An active subscription is required.")
    limit = subscription.plan.limits.get(key)
    if limit is not None and current_count >= int(limit):
        raise ValidationError(f"The plan limit for {key.replace('_', ' ')} has been reached.")
