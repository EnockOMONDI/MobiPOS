import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.expenses.models import Expense, ExpenseStatus
from apps.organizations.models import Branch, Membership, MembershipStatus, Organization


@pytest.mark.django_db
def test_expense_submit_and_owner_approve(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    branch = Branch.objects.get(organization=organization)
    client.force_login(user)

    response = client.post(reverse("expense-create"), {
        "branch": branch.id, "category": "Transport", "description": "Stock delivery",
        "amount": "1200", "incurred_on": "2026-06-12",
    })
    expense = Expense.objects.filter(
        organization=organization,
        category="Transport",
        description="Stock delivery",
    ).get()
    assert response.status_code == 302
    client.post(reverse("expense-approve", args=[expense.id]))
    expense.refresh_from_db()
    assert expense.status == "approved"


@pytest.mark.django_db
def test_non_owner_cannot_approve_expense(client):
    call_command("seed_demo_data")
    organization = Organization.objects.get(slug="mobipos-electronics")
    branch = Branch.objects.get(organization=organization)
    owner = User.objects.get(username="alice")
    staff = User.objects.create_user(username="expense-staff", email="expense-staff@example.com")
    Membership.objects.create(
        organization=organization,
        user=staff,
        status=MembershipStatus.ACTIVE,
    ).branches.add(branch)
    expense = Expense.objects.create(
        organization=organization,
        number="EXP-DENY",
        branch=branch,
        category="Transport",
        description="Pending owner approval",
        amount="900.00",
        incurred_on="2026-06-12",
        requested_by=owner,
        status=ExpenseStatus.SUBMITTED,
    )
    client.force_login(staff)

    response = client.post(reverse("expense-approve", args=[expense.id]))

    expense.refresh_from_db()
    assert response.status_code == 403
    assert expense.status == ExpenseStatus.SUBMITTED
    assert expense.approved_by is None
