from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.audit.services import record_audit_event
from apps.organizations.permissions import (
    accessible_locations_for,
    accessible_branches_for,
    organization_owner_required,
    organization_permission_required,
)

from .forms import (
    ApprovalPolicyForm,
    InstallmentScheduleForm,
    PayablePaymentForm,
    PayablePaymentReversalForm,
)
from .models import ApprovalPolicy, ApprovalRequest, ApprovalStatus, Payable, PayablePayment, Receivable
from .payables import record_payable_payment, reverse_payable_payment
from .services import approval_business_summary, approval_target_context, approvals_user_can_decide, decide_approval, replace_installment_schedule, request_permission_access, user_can_decide_approval


@login_required
def approval_inbox(request):
    from django.core.paginator import Paginator

    candidates = approvals_user_can_decide(user=request.user, organization=request.organization).select_related(
        "requested_by", "decided_by", "policy", "branch"
    ).prefetch_related("policy__approver_roles")
    status_filter = request.GET.get("status", ApprovalStatus.PENDING)
    search = request.GET.get("q", "").strip()
    request_type = request.GET.get("type", "").strip()
    branch_id = request.GET.get("branch", "").strip()
    if status_filter in ApprovalStatus.values:
        filtered = candidates.filter(status=status_filter)
    else:
        status_filter = "all"
        filtered = candidates
    if search:
        filtered = filtered.filter(
            Q(reason__icontains=search)
            | Q(requested_by__first_name__icontains=search)
            | Q(requested_by__last_name__icontains=search)
            | Q(requested_by__email__icontains=search)
            | Q(target_type__icontains=search)
        )
    if request_type:
        filtered = filtered.filter(request_type=request_type)
    if branch_id:
        filtered = filtered.filter(branch_id=branch_id)
    branches = accessible_branches_for(request.user, request.organization).order_by("name")
    page = Paginator(filtered, 20).get_page(request.GET.get("page"))
    approval_cards = [
        {"approval": approval, "target": approval_target_context(approval), "summary": approval_business_summary(approval)}
        for approval in page.object_list
    ]
    request_types = filtered.values_list("request_type", flat=True).distinct().order_by("request_type")
    return render(request, "operations/approval_inbox.html", {
        "approval_cards": approval_cards,
        "page_obj": page,
        "active_status": status_filter,
        "search": search,
        "request_type": request_type,
        "branch_id": branch_id,
        "branches": branches,
        "request_types": request_types,
        "pending_count": candidates.filter(status=ApprovalStatus.PENDING).count(),
        "approved_count": candidates.filter(status=ApprovalStatus.APPROVED).count(),
        "rejected_count": candidates.filter(status=ApprovalStatus.REJECTED).count(),
    })


@login_required
@organization_permission_required("sales.view_sale")
def receivables_workspace(request):
    """Decision-oriented customer credit register scoped to accessible branches."""
    today = timezone.localdate()
    branches = accessible_branches_for(request.user, request.organization).order_by("name")
    base_queryset = (
        Receivable.objects.filter(
            organization=request.organization,
            sale__location__branch__in=branches,
        )
        .select_related("customer", "sale", "sale__location", "sale__location__branch")
        .prefetch_related("installments")
    )

    search = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "outstanding")
    branch_id = request.GET.get("branch", "").strip()
    date_from_value = request.GET.get("date_from", "").strip()
    date_to_value = request.GET.get("date_to", "").strip()
    date_from = parse_date(date_from_value) if date_from_value else None
    date_to = parse_date(date_to_value) if date_to_value else None
    errors = []
    if date_from_value and not date_from:
        errors.append("Enter a valid start date.")
    if date_to_value and not date_to:
        errors.append("Enter a valid end date.")
    if date_from and date_to and date_from > date_to:
        errors.append("The start date must be on or before the end date.")

    if errors:
        # Do not show unfiltered balances alongside a validation error.
        filtered = Receivable.objects.none()

    else:
        filtered = base_queryset
    if search:
        filtered = filtered.filter(
            Q(customer__name__icontains=search)
            | Q(customer__phone_number__icontains=search)
            | Q(customer__email__icontains=search)
            | Q(sale__number__icontains=search)
        )
    if branch_id:
        if branches.filter(id=branch_id).exists():
            filtered = filtered.filter(sale__location__branch_id=branch_id)
        else:
            errors.append("Choose a branch you can access.")
    if status_filter == "overdue":
        filtered = filtered.filter(outstanding_amount__gt=0, due_on__lt=today, is_written_off=False)
    elif status_filter == "due_soon":
        filtered = filtered.filter(
            outstanding_amount__gt=0,
            due_on__gte=today,
            due_on__lte=today + timedelta(days=7),
            is_written_off=False,
        )
    elif status_filter == "paid":
        filtered = filtered.filter(outstanding_amount__lte=0)
    elif status_filter == "written_off":
        filtered = filtered.filter(is_written_off=True)
    elif status_filter != "all":
        status_filter = "outstanding"
        filtered = filtered.filter(outstanding_amount__gt=0, is_written_off=False)
    if date_from:
        filtered = filtered.filter(due_on__gte=date_from)
    if date_to:
        filtered = filtered.filter(due_on__lte=date_to)

    summary_queryset = base_queryset.filter(outstanding_amount__gt=0, is_written_off=False)
    summary = summary_queryset.aggregate(outstanding=Sum("outstanding_amount"))
    overdue_total = summary_queryset.filter(due_on__lt=today).aggregate(total=Sum("outstanding_amount"))["total"] or 0
    due_soon_total = summary_queryset.filter(
        due_on__gte=today,
        due_on__lte=today + timedelta(days=7),
    ).aggregate(total=Sum("outstanding_amount"))["total"] or 0
    page_obj = Paginator(filtered.order_by("due_on", "created_at"), 25).get_page(request.GET.get("page"))
    return render(request, "operations/receivables_workspace.html", {
        "page_obj": page_obj,
        "branches": branches,
        "active_status": status_filter,
        "search": search,
        "branch_id": branch_id,
        "date_from": date_from_value,
        "date_to": date_to_value,
        "errors": errors,
        "total_outstanding": summary["outstanding"] or 0,
        "overdue_total": overdue_total,
        "due_soon_total": due_soon_total,
        "outstanding_count": summary_queryset.count(),
        "today": today,
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
    return render(request, "operations/approval_detail.html", {
        "approval": approval,
        "can_decide": can_decide,
        "target_context": approval_target_context(approval),
    })


@login_required
@require_POST
@transaction.atomic
def approval_decide(request, approval_id):
    # Resolve the tenant-owned resource before validating form input so an
    # invalid decision cannot disclose or redirect to another tenant's ID.
    get_object_or_404(
        ApprovalRequest.objects.filter(organization=request.organization),
        id=approval_id,
    )
    decision = request.POST.get("decision")
    try:
        approval, affected_target = decide_approval(
            approval_id=approval_id,
            organization=request.organization,
            actor=request.user,
            decision=decision,
            decision_notes=request.POST.get("decision_notes", ""),
        )
    except ValidationError as error:
        messages.error(request, error.message)
        return redirect("approval-detail", approval_id=approval_id)
    if affected_target:
        record_audit_event(
            action=f"{approval.request_type}.{decision}",
            actor=request.user,
            organization=request.organization,
            target=affected_target,
            metadata={"approval_id": str(approval.id)},
            request=request,
        )
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
        detail = "; ".join(str(message) for message in error.messages)
        messages.error(
            request,
            detail or "We could not send that access request. Please try again or contact your administrator.",
        )
    else:
        record_audit_event(
            action="access.requested",
            actor=request.user,
            organization=request.organization,
            target=approval,
            request=request,
        )
        messages.success(
            request,
            "Your access request was sent. An organization administrator will review it and notify you when a decision is made.",
        )
    # Do not send the user back to the locked page. That would immediately
    # reopen the dialog and make a successful request feel like a failure.
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


def _scoped_payable(request, payable_id):
    return get_object_or_404(
        Payable.objects.select_related("supplier", "purchase_order", "purchase_order__destination").prefetch_related(
            "payments", "payments__paid_by", "payments__reversed_by"
        ),
        id=payable_id,
        organization=request.organization,
        purchase_order__destination__in=accessible_locations_for(request.user, request.organization),
    )


@login_required
@organization_permission_required("contacts.view_contact")
def payable_detail(request, payable_id):
    payable = _scoped_payable(request, payable_id)
    payment_form = PayablePaymentForm(
        initial={"request_id": uuid.uuid4(), "amount": payable.outstanding_amount}
    )
    return render(request, "operations/payable_detail.html", {
        "payable": payable,
        "payment_form": payment_form,
        "reversal_form": PayablePaymentReversalForm(),
    })


@login_required
@organization_permission_required("payments.add_payment")
@require_POST
def payable_payment_create(request, payable_id):
    payable = _scoped_payable(request, payable_id)
    form = PayablePaymentForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("payable-detail", payable_id=payable.id)
    try:
        payment, created = record_payable_payment(
            payable=payable,
            actor=request.user,
            **form.cleaned_data,
        )
    except ValidationError as error:
        messages.error(request, error.message)
    else:
        if created:
            record_audit_event(
                action="supplier_payment.recorded",
                actor=request.user,
                organization=request.organization,
                target=payment,
                metadata={
                    "payable_id": str(payable.id),
                    "purchase_order": payable.purchase_order.number,
                    "supplier": payable.supplier.name,
                    "amount": str(payment.amount),
                    "method": payment.method,
                    "reference": payment.reference,
                },
                request=request,
            )
            messages.success(request, f"Supplier payment {payment.number} recorded.")
        else:
            messages.info(request, "This supplier payment was already recorded. No duplicate was created.")
    return redirect("payable-detail", payable_id=payable.id)


@login_required
@organization_owner_required
@require_POST
def payable_payment_reverse(request, payment_id):
    payment = get_object_or_404(
        PayablePayment.objects.select_related("payable", "payable__purchase_order"),
        id=payment_id,
        organization=request.organization,
    )
    form = PayablePaymentReversalForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("payable-detail", payable_id=payment.payable_id)
    try:
        payment = reverse_payable_payment(
            payment=payment,
            actor=request.user,
            reason=form.cleaned_data["reason"],
        )
    except ValidationError as error:
        messages.error(request, error.message)
    else:
        record_audit_event(
            action="supplier_payment.reversed",
            actor=request.user,
            organization=request.organization,
            target=payment,
            metadata={
                "payable_id": str(payment.payable_id),
                "amount": str(payment.amount),
                "reason": payment.reversal_reason,
            },
            request=request,
        )
        messages.success(request, f"Supplier payment {payment.number} reversed with an audit trail.")
    return redirect("payable-detail", payable_id=payment.payable_id)
import uuid
