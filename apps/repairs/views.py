from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.services import record_audit_event
from apps.organizations.permissions import accessible_branches_for, organization_permission_required
from .forms import RepairPartForm, RepairStatusForm, RepairTicketForm
from .models import RepairStatus, RepairTicket
from .services import use_repair_part


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
    return render(request, "repairs/detail.html", {
        "ticket": ticket,
        "form": RepairStatusForm(initial={
            "status": ticket.status, "diagnosis": ticket.diagnosis,
            "warranty_type": ticket.warranty_type,
            "warranty_decision_notes": ticket.warranty_decision_notes,
        }),
        "part_form": RepairPartForm(organization=request.organization, user=request.user),
    })


@login_required
@organization_permission_required("repairs.change_repairticket")
@require_POST
def repair_update(request, ticket_id):
    ticket = get_object_or_404(
        RepairTicket, id=ticket_id, organization=request.organization,
        branch__in=accessible_branches_for(request.user, request.organization),
    )
    form = RepairStatusForm(request.POST)
    if form.is_valid():
        ticket.status = form.cleaned_data["status"]
        ticket.diagnosis = form.cleaned_data["diagnosis"]
        ticket.warranty_type = form.cleaned_data["warranty_type"]
        ticket.warranty = ticket.warranty_type != "none"
        ticket.warranty_decision_notes = form.cleaned_data["warranty_decision_notes"]
        if ticket.status == RepairStatus.CLOSED:
            ticket.collected_at = timezone.now()
        ticket.save(update_fields=["status", "diagnosis", "warranty_type", "warranty", "warranty_decision_notes", "collected_at", "updated_at"])
        record_audit_event(action="repair.status_changed", actor=request.user, organization=request.organization, target=ticket, request=request)
        messages.success(request, "Repair ticket updated.")
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
        except Exception as error:
            messages.error(request, str(error))
    else:
        messages.error(request, "Correct the repair part details.")
    return redirect("repair-detail", ticket_id=ticket.id)
