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
    return {
        "title": title,
        "requester": requested_by,
        "branch": approval.branch.name if approval.branch_id else "",
        "message": f"{requested_by} requested {title.lower()}{branch}.",
        "reason": approval.reason,
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
        title=title,
        message=f"Your {approval.display_type.lower()} request was {status}.",
        link=link,
    )
    recipients = [approval.requested_by.email] if approval.requested_by.email else []
    transaction.on_commit(lambda: send_approval_decision_email(approval=approval, recipients=recipients))


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
