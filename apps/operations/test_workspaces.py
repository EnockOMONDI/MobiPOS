from datetime import timedelta

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.operations.models import Receivable
from apps.organizations.models import Organization


@pytest.mark.django_db
def test_receivables_workspace_is_dedicated_and_paginated(client):
    call_command("seed_demo_data")
    client.force_login(User.objects.get(username="brian"))

    response = client.get(reverse("receivables-workspace"))

    assert response.status_code == 200
    assert b"Customer balances" in response.content
    assert b"Find a customer balance" in response.content
    assert response.context["page_obj"].paginator.count == Receivable.objects.filter(
        outstanding_amount__gt=0,
        is_written_off=False,
    ).count()


@pytest.mark.django_db
def test_receivables_workspace_applies_status_search_date_and_branch_scope(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    client.force_login(owner)
    receivable = Receivable.objects.filter(organization=organization).select_related("customer").first()
    assert receivable is not None

    overdue_date = timezone.localdate() - timedelta(days=1)
    Receivable.objects.filter(id=receivable.id).update(due_on=overdue_date, outstanding_amount=1250)
    response = client.get(
        reverse("receivables-workspace"),
        {"status": "overdue", "q": receivable.customer.name, "date_from": overdue_date.isoformat(), "date_to": overdue_date.isoformat()},
    )

    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 1
    assert response.context["page_obj"].object_list[0].id == receivable.id


@pytest.mark.django_db
def test_receivables_workspace_does_not_expose_another_organization(client):
    call_command("seed_demo_data")
    client.force_login(User.objects.get(username="brian"))

    other = Organization.objects.create(name="Other Organization", slug="other-receivables", status="active")
    response = client.get(reverse("receivables-workspace"), {"q": "Other Organization"})

    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 0
    assert Receivable.objects.filter(organization=other).count() == 0
