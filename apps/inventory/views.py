from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.services import record_audit_event
from apps.organizations.permissions import accessible_locations_for, organization_owner_required, organization_permission_required

from .forms import StockAdjustmentForm, StockReversalForm
from .models import StockAdjustment, StockMovement
from .services import complete_stock_adjustment, reverse_stock_movement


@login_required
@organization_permission_required("inventory.add_stockadjustment")
@transaction.atomic
def stock_adjustment_create(request):
    form = StockAdjustmentForm(request.POST or None, organization=request.organization, user=request.user)
    if request.method == "POST" and form.is_valid():
        adjustment = StockAdjustment.objects.create(
            organization=request.organization,
            number=f"ADJ-{timezone.now():%Y%m%d%H%M%S%f}",
            requested_by=request.user,
            **form.cleaned_data,
        )
        record_audit_event(action="stock_adjustment.requested", actor=request.user, organization=request.organization, target=adjustment, request=request)
        return redirect("stock-adjustment-detail", adjustment_id=adjustment.id)
    return render(request, "inventory/adjustment_create.html", {"form": form})


@login_required
def stock_adjustment_detail(request, adjustment_id):
    adjustment = get_object_or_404(
        StockAdjustment, id=adjustment_id, organization=request.organization,
        location__in=accessible_locations_for(request.user, request.organization),
    )
    return render(request, "inventory/adjustment_detail.html", {"adjustment": adjustment})


@login_required
@organization_owner_required
@require_POST
def stock_adjustment_complete(request, adjustment_id):
    adjustment = get_object_or_404(StockAdjustment, id=adjustment_id, organization=request.organization)
    try:
        complete_stock_adjustment(adjustment=adjustment, actor=request.user)
        record_audit_event(action="stock_adjustment.completed", actor=request.user, organization=request.organization, target=adjustment, request=request)
        messages.success(request, "Stock adjustment completed.")
    except ValidationError as error:
        messages.error(request, error.message)
    return redirect("stock-adjustment-detail", adjustment_id=adjustment.id)


@login_required
@organization_owner_required
@require_POST
def stock_movement_reverse(request, movement_id):
    movement = get_object_or_404(StockMovement, id=movement_id, organization=request.organization)
    form = StockReversalForm(request.POST)
    if form.is_valid():
        try:
            reversal = reverse_stock_movement(movement=movement, actor=request.user, reason=form.cleaned_data["reason"])
            record_audit_event(action="stock_movement.reversed", actor=request.user, organization=request.organization, target=reversal, request=request)
            messages.success(request, "Stock movement reversed.")
        except ValidationError as error:
            messages.error(request, error.message)
    return redirect("module-overview", module="movements")
