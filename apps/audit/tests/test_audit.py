import pytest
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.test import RequestFactory, override_settings

from apps.audit.services import record_audit_event
from apps.audit.models import AuditEvent
from apps.accounts.models import User
from apps.audit.reconciliation import run_reconciliation
from apps.audit.tasks import reconcile_active_organizations
from apps.integrations.models import IntegrationEvent, IntegrationStatus
from apps.inventory.models import StockBalance
from apps.operations.models import Payable
from apps.organizations.models import Organization
from apps.sales.models import Sale


@pytest.mark.django_db
def test_audit_event_is_immutable():
    event = record_audit_event(action="test.event", message="Created once")
    event.message = "Changed"

    with pytest.raises(ValidationError):
        event.save()

    with pytest.raises(ValidationError):
        event.delete()


@pytest.mark.django_db
def test_successful_login_is_audited(client):
    User.objects.create_user(
        username="owner", email="owner@example.com", password="password"
    )

    assert client.login(username="owner", password="password")
    assert AuditEvent.objects.filter(action="auth.login").exists()


@pytest.mark.django_db
@override_settings(TRUST_PROXY_IP_HEADERS=False)
def test_audit_ip_ignores_spoofed_forwarded_header_by_default():
    request = RequestFactory().get(
        "/",
        HTTP_X_FORWARDED_FOR="203.0.113.10",
        REMOTE_ADDR="127.0.0.1",
    )

    event = record_audit_event(action="test.ip", request=request)

    assert event.ip_address == "127.0.0.1"


@pytest.mark.django_db
@override_settings(TRUST_PROXY_IP_HEADERS=True)
def test_audit_ip_can_use_forwarded_header_when_configured():
    request = RequestFactory().get(
        "/",
        HTTP_X_FORWARDED_FOR="203.0.113.10",
        REMOTE_ADDR="127.0.0.1",
    )

    event = record_audit_event(action="test.ip", request=request)

    assert event.ip_address == "203.0.113.10"


@pytest.mark.django_db
def test_operational_reconciliation_reports_corrupted_balances():
    call_command("seed_demo_data")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    balance = StockBalance.objects.filter(organization=organization).first()
    sale = Sale.objects.filter(organization=organization).exclude(status="draft").first()
    payable = Payable.objects.filter(organization=organization).first()
    assert balance and sale and payable

    StockBalance.objects.filter(pk=balance.pk).update(quantity=balance.quantity + 1)
    Sale.objects.filter(pk=sale.pk).update(paid_total=sale.paid_total + 1)
    Payable.objects.filter(pk=payable.pk).update(outstanding_amount=payable.outstanding_amount + 1)
    integration = IntegrationEvent.objects.create(
        organization=organization,
        provider="etims",
        event_type="invoice.submit",
        idempotency_key="reconciliation-dead-event",
        status=IntegrationStatus.DEAD,
    )

    issues = run_reconciliation(organization=organization)
    issue_keys = {(issue.area, issue.record_id) for issue in issues}

    assert ("stock", str(balance.id)) in issue_keys
    assert ("payments", str(sale.id)) in issue_keys
    assert ("payables", str(payable.id)) in issue_keys
    assert ("integrations", str(integration.id)) in issue_keys


@pytest.mark.django_db
def test_daily_reconciliation_alerts_owner_without_changing_balance(monkeypatch):
    call_command("seed_demo_data")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    balance = StockBalance.objects.filter(organization=organization).first()
    original_quantity = balance.quantity
    StockBalance.objects.filter(pk=balance.pk).update(quantity=original_quantity + 1)
    alerts = []
    monkeypatch.setattr("apps.audit.tasks.notify_business_event", lambda **kwargs: alerts.append(kwargs))

    result = reconcile_active_organizations()

    balance.refresh_from_db()
    assert result["organizations_checked"] >= 1
    assert result["issues_found"] >= 1
    assert balance.quantity == original_quantity + 1
    assert any(alert["organization"] == organization and alert["include_owners"] for alert in alerts)
