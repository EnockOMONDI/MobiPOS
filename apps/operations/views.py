from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.db import transaction
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.utils.http import url_has_allowed_host_and_scheme

from apps.audit.services import record_audit_event
from apps.organizations.permissions import organization_owner_required

from .models import ApprovalRequest, ApprovalStatus
from .services import grant_requested_access, request_permission_access


@login_required
@organization_owner_required
def approval_inbox(request):
    approvals = ApprovalRequest.objects.filter(organization=request.organization).select_related(
        "requested_by", "decided_by"
    )[:100]
    return render(request, "operations/approval_inbox.html", {"approvals": approvals})


@login_required
@organization_owner_required
def approval_detail(request, approval_id):
    approval = get_object_or_404(ApprovalRequest, id=approval_id, organization=request.organization)
    return render(request, "operations/approval_detail.html", {"approval": approval})


@login_required
@organization_owner_required
@require_POST
@transaction.atomic
def approval_decide(request, approval_id):
    approval = get_object_or_404(
        ApprovalRequest.objects.select_for_update(),
        id=approval_id, organization=request.organization, status=ApprovalStatus.PENDING,
    )
    decision = request.POST.get("decision")
    if decision not in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
        messages.error(request, "Choose approve or reject.")
        return redirect("approval-detail", approval_id=approval.id)
    approval.status = decision
    approval.decided_by = request.user
    approval.decided_at = timezone.now()
    approval.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
    if decision == ApprovalStatus.APPROVED:
        grant_requested_access(approval=approval)
    record_audit_event(action=f"approval.{decision}", actor=request.user, organization=request.organization, target=approval, request=request)
    messages.success(request, f"Approval request {decision}.")
    return redirect("approval-detail", approval_id=approval.id)


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
