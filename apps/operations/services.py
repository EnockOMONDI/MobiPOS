from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from decimal import Decimal

from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone

from apps.organizations.forms import TENANT_ROLE_PERMISSION_CODES
from apps.organizations.models import Membership, Role

from .models import ApprovalPolicy, ApprovalRequest, ApprovalStatus, InstallmentStatus, ReceivableInstallment


def matching_approval_policy(*, organization, request_type, amount=Decimal("0"), branch=None):
    policies = ApprovalPolicy.objects.filter(
        organization=organization,
        request_type=request_type,
        is_active=True,
        minimum_amount__lte=amount,
    ).filter(branch__isnull=True) if branch is None else ApprovalPolicy.objects.filter(
        organization=organization,
        request_type=request_type,
        is_active=True,
        minimum_amount__lte=amount,
    ).filter(models.Q(branch__isnull=True) | models.Q(branch=branch))
    return policies.order_by("-minimum_amount", "-branch_id").first()


def user_can_decide_approval(*, user, approval):
    membership = Membership.objects.filter(
        organization=approval.organization,
        user=user,
        status="active",
    ).prefetch_related("roles").first()
    if user.is_superuser or user.is_platform_admin or (membership and membership.is_owner):
        allowed = True
    elif approval.policy_id and membership:
        allowed = approval.policy.approver_roles.filter(id__in=membership.roles.values("id")).exists()
    else:
        allowed = False
    if allowed and approval.policy_id and approval.policy.require_separate_approver:
        return approval.requested_by_id != user.id
    return allowed


def approvals_user_can_decide(*, user, organization):
    queryset = ApprovalRequest.objects.filter(organization=organization)
    membership = Membership.objects.filter(
        organization=organization,
        user=user,
        status="active",
    ).prefetch_related("roles").first()
    if user.is_superuser or user.is_platform_admin or (membership and membership.is_owner):
        return queryset
    if not membership:
        return queryset.none()
    role_ids = membership.roles.values_list("id", flat=True)
    return queryset.filter(policy__approver_roles__id__in=role_ids).exclude(
        policy__require_separate_approver=True,
        requested_by=user,
    ).distinct()


def _policy_snapshot(policy):
    if not policy:
        return {}
    return {
        "id": str(policy.id),
        "name": policy.name,
        "request_type": policy.request_type,
        "branch_id": str(policy.branch_id) if policy.branch_id else "",
        "minimum_amount": str(policy.minimum_amount),
        "require_separate_approver": policy.require_separate_approver,
        "approver_role_ids": [str(role_id) for role_id in policy.approver_roles.values_list("id", flat=True)],
    }


def approval_business_summary(approval):
    labels = {
        "access_request": "Access request",
        "credit_sale": "Credit sale approval",
        "discount": "Discount approval",
        "expense": "Expense approval",
        "stock_adjustment": "Stock adjustment approval",
        "aged_stock_action": "Aged-stock action approval",
    }
    title = labels.get(approval.request_type, approval.display_type)
    requested_by = approval.requested_by.get_full_name() or approval.requested_by.get_username()
    branch = f" for {approval.branch.name}" if approval.branch_id else ""
    age_days = max((timezone.now() - approval.created_at).days, 0)
    if approval.status != ApprovalStatus.PENDING:
        urgency = "Decision recorded"
    elif age_days == 0:
        urgency = "New today"
    elif age_days == 1:
        urgency = "Waiting 1 day"
    else:
        urgency = f"Waiting {age_days} days"
    return {
        "title": title,
        "requester": requested_by,
        "branch": approval.branch.name if approval.branch_id else "",
        "message": f"{requested_by} requested {title.lower()}{branch}.",
        "reason": approval.reason,
        "age_days": age_days,
        "urgency": urgency,
    }


def approval_target_context(approval):
    """Return business-facing immutable proposal and current execution outcome."""
    if approval.request_type == "aged_stock_action":
        from apps.inventory.models import AgedStockAction

        action = AgedStockAction.objects.select_related(
            "stock_unit__product", "stock_unit__location"
        ).filter(
            approval=approval,
            organization=approval.organization,
        ).first()
        if action:
            return {
                "kind": "aged_stock_action",
                "object": action,
                "title": action.get_action_type_display(),
                "record": f"{action.stock_unit.product.name} · {action.stock_unit.serial_number}",
                "proposal": action.proposal or {},
                "outcome": action.execution_result or {},
            }
    if approval.request_type == "access_request":
        return {
            "kind": "access_request",
            "title": "Requested permission",
            "record": approval.requested_permission_code,
            "proposal": {},
            "outcome": {},
        }
    if approval.request_type == "expense":
        from apps.expenses.models import Expense

        expense = Expense.objects.select_related("branch", "requested_by", "approved_by").filter(
            organization=approval.organization,
            id=approval.target_id,
        ).first()
        if expense:
            payment = getattr(expense, "payment", None)
            return {
                "kind": "expense",
                "object": expense,
                "title": expense.category,
                "record": f"{expense.number} · KES {expense.amount}",
                "proposal": {
                    "category": expense.category,
                    "description": expense.description,
                    "incurred_on": expense.incurred_on.isoformat(),
                    "branch_name": expense.branch.name,
                },
                "outcome": {
                    "status": expense.get_status_display(),
                    "payment_method": payment.get_method_display() if payment else "",
                    "payment_reference": payment.reference if payment else "",
                },
            }
    if approval.request_type in {"credit_sale", "discount"}:
        from apps.sales.models import Sale

        sale = Sale.objects.select_related("customer", "location__branch", "created_by").filter(
            organization=approval.organization,
            id=approval.target_id,
        ).first()
        if sale:
            return {
                "kind": "sale",
                "object": sale,
                "title": "Credit sale" if approval.request_type == "credit_sale" else "Sale discount",
                "record": f"{sale.number} · KES {sale.total}",
                "proposal": {
                    "customer": sale.customer.name if sale.customer_id else "Walk-in customer",
                    "branch_name": sale.location.branch.name,
                    "amount": str(approval.amount),
                },
                "outcome": {"status": sale.get_status_display()},
            }
    if approval.request_type == "session_variance":
        from apps.pos.models import POSSession

        session = POSSession.objects.select_related("cashier", "location__branch").filter(
            organization=approval.organization,
            id=approval.target_id,
        ).first()
        if session:
            cashier = session.cashier.get_full_name() or session.cashier.get_username()
            return {
                "kind": "session_variance",
                "object": session,
                "title": "Cashier variance",
                "record": f"{session.number} · KES {session.variance}",
                "proposal": {
                    "cashier": cashier,
                    "branch_name": session.location.branch.name,
                    "expected_cash": str(session.expected_cash),
                    "actual_cash": str(session.actual_cash),
                },
                "outcome": {"status": session.get_status_display()},
            }
    return {
        "kind": "generic",
        "title": approval.display_type,
        "record": f"{approval.target_type} · {approval.target_id}",
        "proposal": {},
        "outcome": {},
    }


def eligible_approver_memberships(approval):
    memberships = (
        Membership.objects.filter(
            organization=approval.organization,
            status="active",
            user__is_active=True,
        )
        .select_related("user")
        .prefetch_related("roles")
    )
    eligible = []
    seen = set()
    for membership in memberships:
        if approval.policy_id and approval.policy.require_separate_approver and membership.user_id == approval.requested_by_id:
            continue
        if membership.is_owner:
            allowed = True
        elif approval.policy_id:
            allowed = approval.policy.approver_roles.filter(id__in=membership.roles.values("id")).exists()
        else:
            allowed = False
        if allowed and membership.user_id not in seen:
            eligible.append(membership)
            seen.add(membership.user_id)
    return eligible


def notify_approval_requested(approval):
    from apps.notifications.emailing import send_approval_request_email
    from apps.notifications.models import Notification

    summary = approval_business_summary(approval)
    link = reverse("approval-detail", args=[approval.id])
    memberships = eligible_approver_memberships(approval)
    for membership in memberships:
        Notification.objects.create(
            organization=approval.organization,
            recipient=membership.user,
            kind=Notification.Kind.APPROVAL_REQUEST,
            approval=approval,
            title=summary["title"],
            message=f"{summary['message']} Reason: {summary['reason']}",
            link=link,
        )
    recipients = [membership.user.email for membership in memberships if membership.user.email]
    transaction.on_commit(lambda: send_approval_request_email(approval=approval, recipients=recipients))


def notify_approval_decided(approval):
    from apps.notifications.emailing import send_approval_decision_email
    from apps.notifications.models import Notification

    status = approval.get_status_display().lower()
    title = f"{approval.display_type} {status}"
    link = reverse("approval-detail", args=[approval.id])
    Notification.objects.create(
        organization=approval.organization,
        recipient=approval.requested_by,
        kind=Notification.Kind.APPROVAL_DECISION,
        approval=approval,
        title=title,
        message=f"Your {approval.display_type.lower()} request was {status}.",
        link=link,
    )
    recipients = [approval.requested_by.email] if approval.requested_by.email else []
    transaction.on_commit(lambda: send_approval_decision_email(approval=approval, recipients=recipients))


@transaction.atomic
def decide_approval(*, approval_id, organization, actor, decision, decision_notes=""):
    from apps.notifications.models import Notification

    if decision not in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
        raise ValidationError("Choose approve or reject.")
    approval = ApprovalRequest.objects.select_for_update().select_related("policy").filter(
        id=approval_id,
        organization=organization,
    ).first()
    if not approval:
        raise ValidationError("This approval request is no longer available.")
    if approval.status != ApprovalStatus.PENDING:
        raise ValidationError(
            f"This request was already {approval.get_status_display().lower()} by another approver."
        )
    if not user_can_decide_approval(user=actor, approval=approval):
        raise ValidationError("You are not eligible to decide this approval request.")
    notes = (decision_notes or "").strip()
    if decision == ApprovalStatus.REJECTED and not notes:
        raise ValidationError("Explain why the request is being rejected so the requester can correct it.")

    approval.status = decision
    approval.decided_by = actor
    approval.decided_at = timezone.now()
    approval.decision_notes = notes
    approval.save(update_fields=["status", "decided_by", "decided_at", "decision_notes", "updated_at"])

    affected_target = None
    if decision == ApprovalStatus.APPROVED:
        affected_target = grant_requested_access(approval=approval)
    if approval.request_type == "aged_stock_action":
        from apps.inventory.aging import sync_aged_stock_action_from_approval

        affected_target = sync_aged_stock_action_from_approval(approval=approval)
    elif approval.request_type == "expense":
        from apps.expenses.services import sync_expense_from_approval

        affected_target = sync_expense_from_approval(approval=approval)

    notify_approval_decided(approval)
    Notification.objects.filter(
        organization=organization,
        approval=approval,
        kind=Notification.Kind.APPROVAL_REQUEST,
        recipient=actor,
        read_at__isnull=True,
    ).update(read_at=timezone.now())
    return approval, affected_target


@transaction.atomic
def request_approval(*, organization, request_type, target, requested_by, reason, amount=Decimal("0"), branch=None):
    policy = matching_approval_policy(
        organization=organization, request_type=request_type, amount=amount, branch=branch
    )
    target_type = target._meta.label
    target_id = str(target.pk)
    pending = ApprovalRequest.objects.filter(
        organization=organization,
        request_type=request_type,
        target_type=target_type,
        target_id=target_id,
        status=ApprovalStatus.PENDING,
    ).first()
    if pending:
        return pending
    previous = ApprovalRequest.objects.filter(
        organization=organization,
        request_type=request_type,
        target_type=target_type,
        target_id=target_id,
    ).first()
    approval = ApprovalRequest.objects.create(
        organization=organization,
        request_type=request_type,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        requested_by=requested_by,
        amount=amount,
        branch=branch,
        policy=policy,
        policy_snapshot=_policy_snapshot(policy),
        previous_request=previous,
    )
    notify_approval_requested(approval)
    return approval


@transaction.atomic
def replace_installment_schedule(*, receivable, entries):
    current = receivable.installments.select_for_update().filter(is_current=True)
    latest_version = receivable.installments.aggregate(max_version=models.Max("schedule_version"))["max_version"] or 0
    current.update(is_current=False, replaced_at=timezone.now())
    next_version = latest_version + 1
    ReceivableInstallment.objects.bulk_create([
        ReceivableInstallment(
            organization=receivable.organization,
            receivable=receivable,
            sequence=index,
            schedule_version=next_version,
            due_on=due_on,
            amount=amount,
        )
        for index, (due_on, amount) in enumerate(entries, start=1)
    ])
    sync_receivable_installments(receivable=receivable)
    return receivable.installments.filter(is_current=True)


@transaction.atomic
def sync_receivable_installments(*, receivable):
    allocated = max(receivable.original_amount - receivable.outstanding_amount, Decimal("0"))
    today = timezone.localdate()
    for installment in receivable.installments.select_for_update().filter(is_current=True).order_by("sequence"):
        paid_amount = min(allocated, installment.amount)
        allocated -= paid_amount
        if paid_amount >= installment.amount:
            status = InstallmentStatus.PAID
        elif paid_amount > 0:
            status = InstallmentStatus.PART_PAID
        elif installment.due_on < today:
            status = InstallmentStatus.OVERDUE
        else:
            status = InstallmentStatus.PENDING
        installment.paid_amount = paid_amount
        installment.status = status
        installment.save(update_fields=["paid_amount", "status", "updated_at"])


@transaction.atomic
def request_permission_access(*, organization, user, permission_code, reason):
    if permission_code not in TENANT_ROLE_PERMISSION_CODES:
        raise ValidationError("This access cannot be delegated through a tenant role.")
    permission = Permission.objects.get(
        content_type__app_label=permission_code.split(".", 1)[0],
        codename=permission_code.split(".", 1)[1],
    )
    target_id = f"{user.id}:{permission_code}"
    pending = ApprovalRequest.objects.filter(
        organization=organization,
        request_type="access_request",
        target_type="permission",
        target_id=target_id,
        status=ApprovalStatus.PENDING,
    ).first()
    if pending:
        return pending
    previous = ApprovalRequest.objects.filter(
        organization=organization,
        request_type="access_request",
        target_type="permission",
        target_id=target_id,
    ).first()
    approval = ApprovalRequest.objects.create(
        organization=organization,
        request_type="access_request",
        target_type="permission",
        target_id=target_id,
        reason=reason or f"Request access to {permission.name}.",
        requested_by=user,
        previous_request=previous,
    )
    notify_approval_requested(approval)
    return approval


@transaction.atomic
def grant_requested_access(*, approval):
    if approval.request_type != "access_request" or approval.target_type != "permission":
        return None
    _, permission_code = approval.target_id.split(":", maxsplit=1)
    if permission_code not in TENANT_ROLE_PERMISSION_CODES:
        raise ValidationError("This permission cannot be delegated through a tenant role.")
    app_label, codename = permission_code.split(".", maxsplit=1)
    permission = Permission.objects.get(
        content_type__app_label=app_label,
        codename=codename,
    )
    membership = Membership.objects.select_for_update().get(
        organization=approval.organization,
        user=approval.requested_by,
    )
    role, _ = Role.objects.get_or_create(
        organization=approval.organization,
        code=f"approved-access-{approval.requested_by_id}",
        defaults={
            "name": f"Approved access: {approval.requested_by.get_username()}",
            "description": "Managed role containing individually approved access requests.",
        },
    )
    role.permissions.add(permission)
    membership.roles.add(role)
    return role
