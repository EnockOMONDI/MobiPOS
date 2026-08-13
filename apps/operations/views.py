from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.db import transaction
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.utils.http import url_has_allowed_host_and_scheme

from apps.audit.services import record_audit_event
from apps.organizations.permissions import organization_owner_required

from .forms import ApprovalPolicyForm, InstallmentScheduleForm
from .models import ApprovalPolicy, ApprovalRequest, ApprovalStatus, Receivable
from .services import grant_requested_access, notify_approval_decided, replace_installment_schedule, request_permission_access, user_can_decide_approval


@login_required
def approval_inbox(request):
    candidates = ApprovalRequest.objects.filter(organization=request.organization).select_related(
        "requested_by", "decided_by", "policy", "branch"
    ).prefetch_related("policy__approver_roles")[:250]
    approvals = [approval for approval in candidates if user_can_decide_approval(user=request.user, approval=approval)]
    return render(request, "operations/approval_inbox.html", {
        "approvals": approvals,
        "pending_count": sum(1 for approval in approvals if approval.status == ApprovalStatus.PENDING),
        "approved_count": sum(1 for approval in approvals if approval.status == ApprovalStatus.APPROVED),
        "rejected_count": sum(1 for approval in approvals if approval.status == ApprovalStatus.REJECTED),
    })


@login_required
def approval_detail(request, approval_id):
    approval = get_object_or_404(
        ApprovalRequest.objects.select_related("requested_by", "decided_by", "policy"),
        id=approval_id,
        organization=request.organization,
    )
    can_decide = user_can_decide_approval(user=request.user, approval=approval)
    if not can_decide and approval.requested_by_id != request.user.id:
        raise PermissionDenied("You are not an eligible approver for this request.")
    return render(request, "operations/approval_detail.html", {"approval": approval, "can_decide": can_decide})


@login_required
@require_POST
@transaction.atomic
def approval_decide(request, approval_id):
    approval = get_object_or_404(
        ApprovalRequest.objects.select_for_update(),
        id=approval_id, organization=request.organization, status=ApprovalStatus.PENDING,
    )
    if not user_can_decide_approval(user=request.user, approval=approval):
        raise PermissionDenied("You are not an eligible approver for this request.")
    decision = request.POST.get("decision")
    if decision not in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
        messages.error(request, "Choose approve or reject.")
        return redirect("approval-detail", approval_id=approval.id)
    approval.status = decision
    approval.decided_by = request.user
    approval.decided_at = timezone.now()
    approval.decision_notes = request.POST.get("decision_notes", "").strip()
    approval.save(update_fields=["status", "decided_by", "decided_at", "decision_notes", "updated_at"])
    if decision == ApprovalStatus.APPROVED:
        grant_requested_access(approval=approval)
    if approval.request_type == "aged_stock_action":
        from apps.inventory.aging import sync_aged_stock_action_from_approval

        aged_stock_action = sync_aged_stock_action_from_approval(approval=approval)
        if aged_stock_action:
            record_audit_event(
                action=f"aged_stock_action.{decision}",
                actor=request.user,
                organization=request.organization,
                target=aged_stock_action,
                request=request,
            )
    notify_approval_decided(approval)
    record_audit_event(action=f"approval.{decision}", actor=request.user, organization=request.organization, target=approval, request=request)
    messages.success(request, f"Approval request {decision}.")
    return redirect("approval-detail", approval_id=approval.id)


@login_required
@organization_owner_required
def approval_policy_list(request):
    policies = ApprovalPolicy.objects.filter(organization=request.organization).select_related("branch").prefetch_related("approver_roles")
    return render(request, "operations/approval_policy_list.html", {"policies": policies})


@login_required
@organization_owner_required
@transaction.atomic
def approval_policy_create(request):
    form = ApprovalPolicyForm(request.POST or None, organization=request.organization)
    if request.method == "POST" and form.is_valid():
        policy = form.save()
        record_audit_event(action="approval_policy.created", actor=request.user, organization=request.organization, target=policy, request=request)
        messages.success(request, "Approval policy created.")
        return redirect("approval-policy-list")
    return render(request, "operations/approval_policy_create.html", {"form": form})


@login_required
@require_POST
def access_request_create(request):
    from django.core.exceptions import ValidationError

    permission_code = request.POST.get("permission", "")
    reason = request.POST.get("reason", "").strip()
    if not request.organization:
        messages.error(request, "Choose an active organization before requesting access.")
        return redirect("dashboard")
    try:
        approval = request_permission_access(
            organization=request.organization,
            user=request.user,
            permission_code=permission_code,
            reason=reason,
        )
    except ValidationError as error:
        messages.error(request, error.message)
    else:
        record_audit_event(
            action="access.requested",
            actor=request.user,
            organization=request.organization,
            target=approval,
            request=request,
        )
        messages.success(request, "Access request sent to your organization administrator.")
    next_url = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("dashboard")


@login_required
@organization_owner_required
@require_POST
@transaction.atomic
def receivable_installment_schedule(request, receivable_id):
    receivable = get_object_or_404(
        Receivable,
        id=receivable_id,
        organization=request.organization,
    )
    form = InstallmentScheduleForm(request.POST, receivable=receivable)
    if form.is_valid():
        replace_installment_schedule(receivable=receivable, entries=form.cleaned_data["schedule"])
        record_audit_event(
            action="receivable.installments_updated",
            actor=request.user,
            organization=request.organization,
            target=receivable,
            request=request,
        )
        messages.success(request, "Installment schedule updated.")
    else:
        messages.error(request, "Installment schedule is invalid.")
    return redirect("sale-detail", sale_id=receivable.sale_id)
