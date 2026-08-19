from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.services import record_audit_event
from apps.organizations.permissions import (
    accessible_branches_for,
    organization_owner_required,
    organization_permission_required,
)
from .forms import (
    RepairPartForm,
    RepairPartReversalForm,
    RepairPaymentForm,
    RepairPaymentReversalForm,
    RepairStatusForm,
    RepairTicketForm,
)
from .models import RepairPartUsage, RepairPayment, RepairTicket
from .services import (
    allowed_repair_transitions,
    record_repair_payment,
    reverse_repair_part,
    reverse_repair_payment,
    transition_repair,
    use_repair_part,
)


def _domain_error_message(error):
    if isinstance(error, ValidationError):
        return " ".join(error.messages)
    return "The repair could not be updated because another operation changed its records. Refresh and try again."


@login_required
@organization_permission_required("repairs.add_repairticket")
def repair_create(request):
    form = RepairTicketForm(request.POST or None, organization=request.organization, user=request.user)
    if request.method == "POST" and form.is_valid():
        ticket = RepairTicket.objects.create(
            organization=request.organization,
            number=f"REP-{timezone.now():%Y%m%d%H%M%S%f}",
            technician=request.user,
            **form.cleaned_data,
        )
        record_audit_event(action="repair.created", actor=request.user, organization=request.organization, target=ticket, request=request)
        return redirect("repair-detail", ticket_id=ticket.id)
    return render(request, "repairs/create.html", {"form": form})


@login_required
@organization_permission_required("repairs.view_repairticket")
def repair_detail(request, ticket_id):
    ticket = get_object_or_404(
        RepairTicket, id=ticket_id, organization=request.organization,
        branch__in=accessible_branches_for(request.user, request.organization),
    )
    allowed_statuses = allowed_repair_transitions(ticket.status)
    return render(request, "repairs/detail.html", {
        "ticket": ticket,
        "allowed_statuses": allowed_statuses,
        "form": RepairStatusForm(initial={
            "status": allowed_statuses[0] if len(allowed_statuses) == 1 else "",
            "diagnosis": ticket.diagnosis,
            "warranty_type": ticket.warranty_type,
            "warranty_decision_notes": ticket.warranty_decision_notes,
            "quoted_amount": ticket.quoted_amount,
        }, current_status=ticket.status, allowed_statuses=allowed_statuses),
        "part_form": RepairPartForm(organization=request.organization, user=request.user),
        "payment_form": RepairPaymentForm(initial={"amount": ticket.outstanding_amount}),
        "part_reversal_form": RepairPartReversalForm(),
        "payment_reversal_form": RepairPaymentReversalForm(),
        "can_reverse": bool(
            request.user.is_superuser
            or request.user.is_platform_admin
            or (request.membership and request.membership.is_owner)
        ),
    })


@login_required
@organization_permission_required("repairs.change_repairticket")
@require_POST
def repair_update(request, ticket_id):
    ticket = get_object_or_404(
        RepairTicket, id=ticket_id, organization=request.organization,
        branch__in=accessible_branches_for(request.user, request.organization),
    )
    form = RepairStatusForm(
        request.POST,
        current_status=ticket.status,
        allowed_statuses=allowed_repair_transitions(ticket.status),
    )
    if form.is_valid():
        try:
            transition_data = form.cleaned_data.copy()
            transition_data["to_status"] = transition_data.pop("status")
            transition_data["notes"] = transition_data.pop("transition_notes")
            transition = transition_repair(ticket=ticket, actor=request.user, **transition_data)
            record_audit_event(
                action="repair.status_changed",
                actor=request.user,
                organization=request.organization,
                target=transition,
                request=request,
                metadata={"from": transition.from_status, "to": transition.to_status},
            )
            messages.success(request, f"Repair moved to {transition.get_to_status_display()}.")
        except (ValidationError, IntegrityError) as error:
            messages.error(request, _domain_error_message(error))
    else:
        messages.error(request, "Select one of the valid next stages and complete its required details.")
    return redirect("repair-detail", ticket_id=ticket.id)


@login_required
@organization_permission_required("repairs.change_repairticket")
@require_POST
def repair_use_part(request, ticket_id):
    ticket = get_object_or_404(
        RepairTicket, id=ticket_id, organization=request.organization,
        branch__in=accessible_branches_for(request.user, request.organization),
    )
    form = RepairPartForm(request.POST, organization=request.organization, user=request.user)
    if form.is_valid():
        try:
            usage = use_repair_part(ticket=ticket, actor=request.user, **form.cleaned_data)
            record_audit_event(action="repair.part_used", actor=request.user, organization=request.organization, target=usage, request=request)
            messages.success(request, "Repair part issued.")
        except (ValidationError, IntegrityError) as error:
            messages.error(request, _domain_error_message(error))
    else:
        messages.error(request, "Correct the repair part details.")
    return redirect("repair-detail", ticket_id=ticket.id)


@login_required
@organization_owner_required
@require_POST
def repair_reverse_part(request, ticket_id, usage_id):
    ticket = get_object_or_404(
        RepairTicket,
        id=ticket_id,
        organization=request.organization,
        branch__in=accessible_branches_for(request.user, request.organization),
    )
    usage = get_object_or_404(RepairPartUsage, id=usage_id, ticket=ticket, organization=request.organization)
    form = RepairPartReversalForm(request.POST)
    if form.is_valid():
        try:
            usage = reverse_repair_part(usage=usage, actor=request.user, **form.cleaned_data)
            record_audit_event(
                action="repair.part_reversed",
                actor=request.user,
                organization=request.organization,
                target=usage,
                request=request,
                metadata={"reason": usage.reversal_reason},
            )
            messages.success(request, "Repair part issue reversed and stock restored.")
        except (ValidationError, IntegrityError) as error:
            messages.error(request, _domain_error_message(error))
    else:
        messages.error(request, "Enter a clear reversal reason.")
    return redirect("repair-detail", ticket_id=ticket.id)


@login_required
@organization_permission_required("repairs.change_repairticket")
@require_POST
def repair_add_payment(request, ticket_id):
    ticket = get_object_or_404(
        RepairTicket,
        id=ticket_id,
        organization=request.organization,
        branch__in=accessible_branches_for(request.user, request.organization),
    )
    form = RepairPaymentForm(request.POST)
    if form.is_valid():
        try:
            payment = record_repair_payment(ticket=ticket, actor=request.user, **form.cleaned_data)
            record_audit_event(
                action="repair.payment_recorded",
                actor=request.user,
                organization=request.organization,
                target=payment,
                request=request,
                metadata={"amount": str(payment.amount), "method": payment.method},
            )
            messages.success(request, f"Payment {payment.number} recorded.")
        except (ValidationError, IntegrityError) as error:
            messages.error(request, _domain_error_message(error))
    else:
        messages.error(request, "Correct the repair payment details.")
    return redirect("repair-detail", ticket_id=ticket.id)


@login_required
@organization_owner_required
@require_POST
def repair_reverse_payment(request, ticket_id, payment_id):
    ticket = get_object_or_404(
        RepairTicket,
        id=ticket_id,
        organization=request.organization,
        branch__in=accessible_branches_for(request.user, request.organization),
    )
    payment = get_object_or_404(RepairPayment, id=payment_id, ticket=ticket, organization=request.organization)
    form = RepairPaymentReversalForm(request.POST)
    if form.is_valid():
        try:
            payment = reverse_repair_payment(payment=payment, actor=request.user, **form.cleaned_data)
            record_audit_event(
                action="repair.payment_reversed",
                actor=request.user,
                organization=request.organization,
                target=payment,
                request=request,
                metadata={"reason": payment.reversal_reason},
            )
            messages.success(request, f"Payment {payment.number} reversed.")
        except (ValidationError, IntegrityError) as error:
            messages.error(request, _domain_error_message(error))
    else:
        messages.error(request, "Enter a clear payment reversal reason.")
    return redirect("repair-detail", ticket_id=ticket.id)
