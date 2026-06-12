from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.services import record_audit_event
from apps.organizations.permissions import accessible_locations_for, organization_owner_required, organization_permission_required
from .forms import TransferForm
from .models import StockTransfer, StockTransferLine, TransferDiscrepancy
from .services import approve_transfer, dispatch_transfer, receive_transfer, resolve_transfer_discrepancy


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
        StockTransferLine.objects.create(
            organization=request.organization,
            transfer=transfer,
            product=form.cleaned_data["product"],
            stock_unit=form.cleaned_data["stock_unit"],
            quantity=form.cleaned_data["quantity"],
        )
        record_audit_event(action="transfer.requested", actor=request.user, organization=request.organization, target=transfer, request=request)
        return redirect("transfer-detail", transfer_id=transfer.id)
    return render(request, "transfers/create.html", {"form": form})


@login_required
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
