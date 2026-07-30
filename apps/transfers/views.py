from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.services import record_audit_event
from apps.organizations.permissions import accessible_locations_for, organization_owner_required, organization_permission_required, user_has_organization_permission
from .forms import AgentAllocationForm, AgentRecallForm, TransferForm
from .models import StockTransfer, StockTransferLine, TransferDiscrepancy
from .services import approve_transfer, dispatch_transfer, receive_transfer, resolve_transfer_discrepancy


@login_required
@organization_permission_required("transfers.view_stocktransfer")
def transfer_list(request):
    if not request.organization:
        return redirect("dashboard")
    locations = accessible_locations_for(request.user, request.organization)
    transfers = (
        StockTransfer.objects.filter(
            Q(source__in=locations) | Q(destination__in=locations),
            organization=request.organization,
        )
        .select_related("source", "destination", "requested_by", "approved_by")
        .prefetch_related("lines__product", "lines__stock_unit", "discrepancies")
        .distinct()
        .order_by("-created_at")
    )
    status = request.GET.get("status", "").strip()
    query = request.GET.get("q", "").strip()
    if status:
        transfers = transfers.filter(status=status)
    if query:
        transfers = transfers.filter(
            Q(number__icontains=query)
            | Q(source__name__icontains=query)
            | Q(destination__name__icontains=query)
            | Q(requested_by__first_name__icontains=query)
            | Q(requested_by__last_name__icontains=query)
            | Q(lines__product__name__icontains=query)
            | Q(lines__product__sku__icontains=query)
            | Q(lines__stock_unit__serial_number__icontains=query)
            | Q(lines__stock_unit__secondary_serial__icontains=query)
        ).distinct()

    base_scope = StockTransfer.objects.filter(
        Q(source__in=locations) | Q(destination__in=locations),
        organization=request.organization,
    ).distinct()
    status_counts = dict(base_scope.values_list("status").annotate(total=Count("id")))
    membership = getattr(request, "membership", None)
    can_approve = bool(request.user.is_superuser or request.user.is_platform_admin or (membership and membership.is_owner))
    can_change = bool(
        can_approve
        or user_has_organization_permission(request.user, request.organization, "transfers.change_stocktransfer")
    )
    return render(request, "transfers/list.html", {
        "transfers": transfers[:50],
        "status": status,
        "query": query,
        "status_counts": status_counts,
        "total_transfers": base_scope.count(),
        "can_approve": can_approve,
        "can_change": can_change,
    })


@login_required
@organization_permission_required("transfers.add_stocktransfer")
@transaction.atomic
def transfer_create(request):
    if not request.organization:
        return redirect("dashboard")
    form = TransferForm(request.POST or None, organization=request.organization, user=request.user)
    if request.method == "POST" and form.is_valid():
        transfer = StockTransfer.objects.create(
            organization=request.organization,
            number=f"TRF-{timezone.now():%Y%m%d%H%M%S%f}",
            source=form.cleaned_data["source"],
            destination=form.cleaned_data["destination"],
            requested_by=request.user,
            notes=form.cleaned_data["notes"],
        )
        StockTransferLine.objects.bulk_create([
            StockTransferLine(organization=request.organization, transfer=transfer, **line)
            for line in form.cleaned_data["lines"]
        ])
        record_audit_event(action="transfer.requested", actor=request.user, organization=request.organization, target=transfer, request=request)
        return redirect("transfer-detail", transfer_id=transfer.id)
    return render(request, "transfers/create.html", {"form": form})


@login_required
@organization_permission_required("transfers.add_stocktransfer")
@transaction.atomic
def agent_allocation_create(request):
    if not request.organization:
        return redirect("dashboard")
    membership = getattr(request, "membership", None)
    can_complete = bool(
        request.user.is_superuser
        or request.user.is_platform_admin
        or (membership and membership.is_owner)
        or user_has_organization_permission(request.user, request.organization, "transfers.change_stocktransfer")
    )
    form = AgentAllocationForm(
        request.POST or None,
        organization=request.organization,
        user=request.user,
        can_complete=can_complete,
    )
    if request.method == "POST" and form.is_valid():
        transfer = StockTransfer.objects.create(
            organization=request.organization,
            number=f"AGT-{timezone.now():%Y%m%d%H%M%S%f}",
            source=form.cleaned_data["source"],
            destination=form.cleaned_data["agent_location"],
            requested_by=request.user,
            notes=form.cleaned_data["notes"],
        )
        StockTransferLine.objects.bulk_create([
            StockTransferLine(organization=request.organization, transfer=transfer, **line)
            for line in form.cleaned_data["lines"]
        ])
        record_audit_event(
            action="agent_stock.allocation_requested",
            actor=request.user,
            organization=request.organization,
            target=transfer,
            metadata={
                "source": str(transfer.source_id),
                "agent_location": str(transfer.destination_id),
                "devices": [str(line["stock_unit"].id) for line in form.cleaned_data["lines"]],
            },
            request=request,
        )
        if form.cleaned_data.get("complete_now"):
            try:
                approve_transfer(transfer=transfer, actor=request.user)
                dispatch_transfer(transfer=transfer, actor=request.user)
                receive_transfer(transfer=transfer, actor=request.user)
                record_audit_event(
                    action="agent_stock.allocation_completed",
                    actor=request.user,
                    organization=request.organization,
                    target=transfer,
                    request=request,
                )
                messages.success(request, "Agent stock allocation completed.")
            except ValidationError as error:
                messages.error(request, error.message)
        else:
            messages.success(request, "Agent stock allocation request created.")
        return redirect("transfer-detail", transfer_id=transfer.id)
    return render(request, "transfers/agent_allocate.html", {"form": form, "can_complete": can_complete})


@login_required
@organization_permission_required("transfers.add_stocktransfer")
@transaction.atomic
def agent_recall_create(request):
    if not request.organization:
        return redirect("dashboard")
    membership = getattr(request, "membership", None)
    can_complete = bool(
        request.user.is_superuser
        or request.user.is_platform_admin
        or (membership and membership.is_owner)
        or user_has_organization_permission(request.user, request.organization, "transfers.change_stocktransfer")
    )
    form = AgentRecallForm(
        request.POST or None,
        organization=request.organization,
        user=request.user,
        can_complete=can_complete,
    )
    if request.method == "POST" and form.is_valid():
        transfer = StockTransfer.objects.create(
            organization=request.organization,
            number=f"RCL-{timezone.now():%Y%m%d%H%M%S%f}",
            source=form.cleaned_data["agent_location"],
            destination=form.cleaned_data["destination"],
            requested_by=request.user,
            notes=form.cleaned_data["notes"],
        )
        StockTransferLine.objects.bulk_create([
            StockTransferLine(organization=request.organization, transfer=transfer, **line)
            for line in form.cleaned_data["lines"]
        ])
        record_audit_event(
            action="agent_stock.recall_requested",
            actor=request.user,
            organization=request.organization,
            target=transfer,
            metadata={
                "agent_location": str(transfer.source_id),
                "destination": str(transfer.destination_id),
                "devices": [str(line["stock_unit"].id) for line in form.cleaned_data["lines"]],
            },
            request=request,
        )
        if form.cleaned_data.get("complete_now"):
            try:
                approve_transfer(transfer=transfer, actor=request.user)
                dispatch_transfer(transfer=transfer, actor=request.user)
                receive_transfer(transfer=transfer, actor=request.user)
                record_audit_event(
                    action="agent_stock.recall_completed",
                    actor=request.user,
                    organization=request.organization,
                    target=transfer,
                    request=request,
                )
                messages.success(request, "Agent stock recall completed.")
            except ValidationError as error:
                messages.error(request, error.message)
        else:
            messages.success(request, "Agent stock recall request created.")
        return redirect("transfer-detail", transfer_id=transfer.id)
    return render(request, "transfers/agent_recall.html", {"form": form, "can_complete": can_complete})


@login_required
@organization_permission_required("transfers.view_stocktransfer")
def transfer_detail(request, transfer_id):
    locations = accessible_locations_for(request.user, request.organization)
    transfers = StockTransfer.objects.filter(
        Q(source__in=locations) | Q(destination__in=locations),
        organization=request.organization,
    )
    transfer = get_object_or_404(transfers, id=transfer_id)
    return render(request, "transfers/detail.html", {"transfer": transfer})


def _transition(request, transfer_id, action, service, success, location_field):
    lookup = {f"{location_field}__in": accessible_locations_for(request.user, request.organization)}
    transfer = get_object_or_404(
        StockTransfer, id=transfer_id, organization=request.organization, **lookup,
    )
    try:
        service(transfer=transfer, actor=request.user)
        record_audit_event(action=action, actor=request.user, organization=request.organization, target=transfer, request=request)
        messages.success(request, success)
    except ValidationError as error:
        messages.error(request, error.message)
    return redirect("transfer-detail", transfer_id=transfer.id)


@login_required
@organization_owner_required
@require_POST
def transfer_approve(request, transfer_id):
    return _transition(request, transfer_id, "transfer.approved", approve_transfer, "Transfer approved.", "source")


@login_required
@organization_permission_required("transfers.change_stocktransfer")
@require_POST
def transfer_dispatch(request, transfer_id):
    return _transition(request, transfer_id, "transfer.dispatched", dispatch_transfer, "Transfer dispatched.", "source")


@login_required
@organization_permission_required("transfers.change_stocktransfer")
@require_POST
def transfer_receive(request, transfer_id):
    transfer = get_object_or_404(
        StockTransfer, id=transfer_id, organization=request.organization,
        destination__in=accessible_locations_for(request.user, request.organization),
    )
    try:
        received = {
            str(line.id): line.quantity.__class__(request.POST.get(f"received_{line.id}", line.quantity))
            for line in transfer.lines.all()
        }
        receive_transfer(
            transfer=transfer, actor=request.user, received_quantities=received,
            discrepancy_reason=request.POST.get("discrepancy_reason", ""),
        )
        record_audit_event(action="transfer.received", actor=request.user, organization=request.organization, target=transfer, request=request)
        messages.success(request, "Transfer receipt recorded.")
    except (ValidationError, ValueError) as error:
        messages.error(request, str(error))
    return redirect("transfer-detail", transfer_id=transfer.id)


@login_required
@organization_owner_required
@require_POST
def transfer_discrepancy_resolve(request, discrepancy_id):
    discrepancy = get_object_or_404(TransferDiscrepancy, id=discrepancy_id, organization=request.organization)
    try:
        resolve_transfer_discrepancy(discrepancy=discrepancy, actor=request.user, resolution=request.POST.get("resolution", ""))
        record_audit_event(action="transfer.discrepancy_resolved", actor=request.user, organization=request.organization, target=discrepancy, request=request)
        messages.success(request, "Transfer discrepancy resolved.")
    except ValidationError as error:
        messages.error(request, error.message)
    return redirect("transfer-detail", transfer_id=discrepancy.transfer_id)
