import pytest
from django.utils import timezone

from datetime import timedelta

from apps.organizations.models import Organization, Plan, Subscription, SubscriptionInvoice
from apps.organizations.services import activate_subscription_invoice, refresh_subscription_lifecycle


@pytest.mark.django_db
def test_paid_subscription_invoice_activates_tenant():
    organization = Organization.objects.create(name="Pending", slug="pending-activation")
    plan = Plan.objects.create(name="Starter", code="activation-starter", monthly_price=1000)
    subscription = Subscription.objects.create(organization=organization, plan=plan)
    invoice = SubscriptionInvoice.objects.create(organization=organization, subscription=subscription, number="INV-1", amount=1000, due_on=timezone.localdate())

    activate_subscription_invoice(invoice=invoice, payment_reference="MPESA123")

    organization.refresh_from_db()
    subscription.refresh_from_db()
    assert organization.status == "active"
    assert subscription.status == "active"


@pytest.mark.django_db
def test_subscription_lifecycle_creates_invoice_then_suspends_after_grace():
    today = timezone.localdate()
    organization = Organization.objects.create(name="Lifecycle", slug="lifecycle", status="active")
    plan = Plan.objects.create(name="Lifecycle Plan", code="lifecycle-plan", monthly_price=2000)
    subscription = Subscription.objects.create(
        organization=organization, plan=plan, status="active",
        renews_on=today, grace_ends_on=today + timedelta(days=7),
    )

    result = refresh_subscription_lifecycle(today=today)
    subscription.refresh_from_db()
    assert result["invoices"] == 1
    assert subscription.status == "grace"
    assert SubscriptionInvoice.objects.filter(subscription=subscription, due_on=today).exists()

    refresh_subscription_lifecycle(today=today + timedelta(days=8))
    subscription.refresh_from_db()
    organization.refresh_from_db()
    assert subscription.status == "suspended"
    assert organization.status == "suspended"
