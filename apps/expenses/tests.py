import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.expenses.models import Expense
from apps.organizations.models import Branch, Organization


@pytest.mark.django_db
def test_expense_submit_and_owner_approve(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="kipekee-electronics")
    branch = Branch.objects.get(organization=organization)
    client.force_login(user)

    response = client.post(reverse("expense-create"), {
        "branch": branch.id, "category": "Transport", "description": "Stock delivery",
        "amount": "1200", "incurred_on": "2026-06-12",
    })
    expense = Expense.objects.filter(organization=organization, category="Transport").get()
    assert response.status_code == 302
    client.post(reverse("expense-approve", args=[expense.id]))
    expense.refresh_from_db()
    assert expense.status == "approved"
