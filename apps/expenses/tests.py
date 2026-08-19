from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.notifications.models import Notification
from apps.operations.models import ApprovalRequest, ApprovalStatus
from apps.organizations.models import Branch, Company, Membership, MembershipStatus, Organization, Role

from .models import Expense, ExpensePayment, ExpenseStatus


def _organization(name, slug):
    organization = Organization.objects.create(name=name, slug=slug, status="active")
    company = Company.objects.create(organization=organization, name=f"{name} Ltd", code="MAIN")
    branch = Branch.objects.create(organization=organization, company=company, name="Main Branch", code="MAIN")
    return organization, branch


def _member(organization, branch, username, permissions=(), owner=False):
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="Strong-pass-123")
    membership = Membership.objects.create(
        organization=organization,
        user=user,
        status=MembershipStatus.ACTIVE,
        is_owner=owner,
    )
    membership.branches.add(branch)
    if permissions:
        role = Role.objects.create(organization=organization, name=f"{username} role", code=f"{username}-role")
        role.permissions.set([
            Permission.objects.get(content_type__app_label="expenses", codename=codename)
            for codename in permissions
        ])
        membership.roles.add(role)
    return user


def _expense(organization, branch, requester, number, status=ExpenseStatus.APPROVED):
    return Expense.objects.create(
        organization=organization,
        number=number,
        branch=branch,
        category="Transport",
        description="Stock delivery",
        amount=Decimal("1200.00"),
        incurred_on="2026-08-14",
        requested_by=requester,
        status=status,
    )


@pytest.mark.django_db
def test_submit_creates_central_approval_and_notifies_owner(client):
    organization, branch = _organization("Expense Org", "expense-org")
    owner = _member(organization, branch, "expense-owner", owner=True)
    requester = _member(organization, branch, "expense-requester", permissions=("add_expense", "view_expense"))
    client.force_login(requester)

    response = client.post(reverse("expense-create"), {
        "branch": branch.id,
        "category": "Transport",
        "description": "Stock delivery",
        "amount": "1200.00",
        "incurred_on": "2026-08-14",
    })

    expense = Expense.objects.get(organization=organization)
    assert response.status_code == 302
    assert response.url == reverse("expense-detail", args=[expense.id])
    assert expense.status == ExpenseStatus.SUBMITTED
    assert expense.approval.request_type == "expense"
    assert expense.approval.amount == Decimal("1200.00")
    assert expense.approval.branch == branch
    notification = Notification.objects.get(organization=organization, recipient=owner)
    assert notification.link == reverse("approval-detail", args=[expense.approval_id])
    assert AuditEvent.objects.filter(action="expense.submitted", target_id=str(expense.id)).exists()


@pytest.mark.django_db
def test_owner_approval_synchronizes_expense_and_preserves_decision(client):
    organization, branch = _organization("Approve Org", "approve-org")
    owner = _member(organization, branch, "approve-owner", owner=True)
    requester = _member(organization, branch, "approve-requester", permissions=("add_expense", "view_expense"))
    client.force_login(requester)
    client.post(reverse("expense-create"), {
        "branch": branch.id, "category": "Fuel", "description": "Delivery fuel",
        "amount": "800.00", "incurred_on": "2026-08-14",
    })
    expense = Expense.objects.get(organization=organization)
    client.force_login(owner)

    response = client.post(reverse("approval-decide", args=[expense.approval_id]), {
        "decision": ApprovalStatus.APPROVED,
        "decision_notes": "Receipt and delivery confirmed.",
    })

    expense.refresh_from_db()
    expense.approval.refresh_from_db()
    assert response.status_code == 302
    assert expense.status == ExpenseStatus.APPROVED
    assert expense.approved_by == owner
    assert expense.approval.decision_notes == "Receipt and delivery confirmed."
    assert AuditEvent.objects.filter(action="expense.approved", target_id=str(expense.id)).exists()


@pytest.mark.django_db
def test_rejection_requires_reason_and_requester_can_resubmit(client):
    organization, branch = _organization("Reject Org", "reject-org")
    owner = _member(organization, branch, "reject-owner", owner=True)
    requester = _member(organization, branch, "reject-requester", permissions=("add_expense", "view_expense"))
    client.force_login(requester)
    client.post(reverse("expense-create"), {
        "branch": branch.id, "category": "Meals", "description": "Team lunch",
        "amount": "3000.00", "incurred_on": "2026-08-14",
    })
    expense = Expense.objects.get(organization=organization)
    first_approval_id = expense.approval_id
    client.force_login(owner)

    client.post(reverse("approval-decide", args=[first_approval_id]), {"decision": ApprovalStatus.REJECTED})
    expense.refresh_from_db()
    assert expense.status == ExpenseStatus.SUBMITTED
    assert ApprovalRequest.objects.get(id=first_approval_id).status == ApprovalStatus.PENDING

    client.post(reverse("approval-decide", args=[first_approval_id]), {
        "decision": ApprovalStatus.REJECTED,
        "decision_notes": "Attach the supplier receipt.",
    })
    expense.refresh_from_db()
    assert expense.status == ExpenseStatus.REJECTED

    client.force_login(requester)
    response = client.post(reverse("expense-resubmit", args=[expense.id]))
    expense.refresh_from_db()
    assert response.status_code == 302
    assert expense.status == ExpenseStatus.SUBMITTED
    assert expense.approval_id != first_approval_id
    assert expense.approval.previous_request_id == first_approval_id


@pytest.mark.django_db
def test_finance_records_payment_once_with_immutable_evidence(client):
    organization, branch = _organization("Pay Org", "pay-org")
    requester = _member(organization, branch, "pay-requester", permissions=("view_expense",))
    finance = _member(organization, branch, "pay-finance", permissions=("view_expense", "change_expense"))
    expense = _expense(organization, branch, requester, "EXP-PAY-001")
    client.force_login(finance)

    response = client.post(reverse("expense-pay", args=[expense.id]), {
        "method": "mpesa",
        "reference": "QHX123ABC",
        "notes": "Paid from business till.",
    })

    expense.refresh_from_db()
    payment = ExpensePayment.objects.get(expense=expense)
    assert response.status_code == 302
    assert expense.status == ExpenseStatus.PAID
    assert payment.amount == expense.amount
    assert payment.reference == "QHX123ABC"
    assert payment.paid_by == finance
    assert AuditEvent.objects.filter(action="expense.paid", target_id=str(expense.id)).exists()
    assert Notification.objects.filter(recipient=requester, title="Expense paid").exists()

    client.post(reverse("expense-pay", args=[expense.id]), {"method": "cash", "reference": ""})
    assert ExpensePayment.objects.filter(expense=expense).count() == 1


@pytest.mark.django_db
def test_duplicate_payment_reference_is_rejected_without_changing_second_expense(client):
    organization, branch = _organization("Reference Org", "reference-org")
    finance = _member(organization, branch, "reference-finance", permissions=("view_expense", "change_expense"))
    first = _expense(organization, branch, finance, "EXP-REF-001")
    second = _expense(organization, branch, finance, "EXP-REF-002")
    client.force_login(finance)
    client.post(reverse("expense-pay", args=[first.id]), {"method": "bank", "reference": "BANK-777"})

    response = client.post(reverse("expense-pay", args=[second.id]), {"method": "bank", "reference": "BANK-777"})

    second.refresh_from_db()
    assert response.status_code == 302
    assert second.status == ExpenseStatus.APPROVED
    assert not ExpensePayment.objects.filter(expense=second).exists()


@pytest.mark.django_db
def test_expense_detail_is_tenant_and_branch_scoped(client):
    first_org, first_branch = _organization("First Org", "first-expense-org")
    second_org, second_branch = _organization("Second Org", "second-expense-org")
    first_user = _member(first_org, first_branch, "first-expense-user", permissions=("view_expense",))
    second_user = _member(second_org, second_branch, "second-expense-user", permissions=("view_expense",))
    expense = _expense(second_org, second_branch, second_user, "EXP-OTHER-001")
    client.force_login(first_user)

    response = client.get(reverse("expense-detail", args=[expense.id]))

    assert response.status_code == 404
