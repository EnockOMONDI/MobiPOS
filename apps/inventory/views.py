from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.services import record_audit_event
from apps.organizations.permissions import accessible_locations_for, organization_owner_required, organization_permission_required

from .aging import age_days_for, bucket_for_age, execute_aged_stock_action, get_aged_stock_policy, request_aged_stock_action
from .forms import AgedStockActionForm, BatchSerializedIntakeForm, StockAdjustmentForm, StockReversalForm
from .models import StockAdjustment, StockMovement, StockUnit
from .services import batch_receive_serialized_stock, complete_stock_adjustment, parse_serial_intake_rows, reverse_stock_movement


@login_required
@organization_permission_required("inventory.view_stockunit")
def device_search(request):
    query = request.GET.get("q", "").strip()
    source_id = request.GET.get("source", "").strip()
    status = request.GET.get("status", "available").strip()
    try:
        limit = min(max(int(request.GET.get("limit", "50")), 1), 100)
    except ValueError:
        limit = 50
    locations = accessible_locations_for(request.user, request.organization)
    units = StockUnit.objects.filter(
        organization=request.organization,
        location__in=locations,
    ).select_related("product", "location", "location__branch", "location__custodian_membership__user")
    if source_id:
        units = units.filter(location_id=source_id)
    if status:
        units = units.filter(status=status)
    if query:
        units = units.filter(
            Q(serial_number__icontains=query)
            | Q(secondary_serial__icontains=query)
            | Q(product__name__icontains=query)
            | Q(product__sku__icontains=query)
            | Q(product__barcode__icontains=query)
            | Q(location__name__icontains=query)
            | Q(location__code__icontains=query)
            | Q(location__custodian_membership__user__username__icontains=query)
            | Q(location__custodian_membership__user__first_name__icontains=query)
            | Q(location__custodian_membership__user__last_name__icontains=query)
        ).distinct()
    ordered_units = units.order_by("product__name", "serial_number")
    total_matches = ordered_units.count()
    payload = []
    for unit in ordered_units[:limit]:
        custodian = ""
        membership = getattr(unit.location, "custodian_membership", None) if unit.location_id else None
        if membership:
            custodian = membership.user.get_full_name() or membership.user.get_username()
        payload.append({
            "id": str(unit.id),
            "serial_number": unit.serial_number,
            "secondary_serial": unit.secondary_serial,
            "product": str(unit.product),
            "sku": unit.product.sku,
            "status": unit.get_status_display(),
            "location": str(unit.location) if unit.location_id else "",
            "branch": unit.location.branch.name if unit.location_id else "",
            "custodian": custodian,
            "age_days": (timezone.now().date() - unit.created_at.date()).days,
        })
    return JsonResponse({"results": payload, "count": total_matches, "limit": limit, "has_more": total_matches > limit})


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
@organization_permission_required("inventory.add_stockadjustment")
@transaction.atomic
def batch_serial_intake(request):
    form = BatchSerializedIntakeForm(
        request.POST or None,
        request.FILES or None,
        organization=request.organization,
        user=request.user,
    )
    result = None
    if request.method == "POST" and form.is_valid():
        try:
            rows = parse_serial_intake_rows(
                serial_numbers=form.cleaned_data["serial_numbers"],
                csv_file=form.cleaned_data.get("csv_file"),
            )
            result = batch_receive_serialized_stock(
                organization=request.organization,
                product=form.cleaned_data["product"],
                location=form.cleaned_data["location"],
                rows=rows,
                actor=request.user,
                unit_cost=form.cleaned_data.get("unit_cost") or 0,
                reason=form.cleaned_data["reason"],
            )
            record_audit_event(
                action="inventory.batch_serial_intake",
                actor=request.user,
                organization=request.organization,
                target=form.cleaned_data["location"],
                metadata={
                    "product": str(form.cleaned_data["product"].id),
                    "location": str(form.cleaned_data["location"].id),
                    "created": len(result["created_units"]),
                    "failures": len(result["failures"]),
                },
                request=request,
            )
            if result["failures"]:
                messages.warning(
                    request,
                    f"Created {len(result['created_units'])} devices. {len(result['failures'])} rows need correction.",
                )
            else:
                messages.success(request, f"Created {len(result['created_units'])} devices.")
        except (UnicodeDecodeError, ValidationError) as error:
            form.add_error(None, str(error))
    return render(request, "inventory/batch_serial_intake.html", {"form": form, "result": result})


@login_required
@organization_permission_required("inventory.view_stockunit")
@transaction.atomic
def aged_stock_action_create(request, stock_unit_id):
    stock_unit = get_object_or_404(
        StockUnit.objects.select_related("product", "product__category", "location", "location__branch"),
        id=stock_unit_id,
        organization=request.organization,
        location__in=accessible_locations_for(request.user, request.organization),
    )
    policy = get_aged_stock_policy(request.organization)
    age_days = age_days_for(stock_unit)
    bucket = bucket_for_age(age_days, policy)
    form = AgedStockActionForm(
        request.POST or None,
        organization=request.organization,
        user=request.user,
        stock_unit=stock_unit,
    )
    if request.method == "POST" and form.is_valid():
        try:
            action = request_aged_stock_action(
                stock_unit=stock_unit,
                action_type=form.cleaned_data["action_type"],
                reason=form.cleaned_data["reason"],
                next_step=form.cleaned_data["next_step"],
                proposal={
                    "destination_id": (
                        str(form.cleaned_data["destination"].id)
                        if form.cleaned_data.get("destination")
                        else ""
                    ),
                    "destination_name": (
                        form.cleaned_data["destination"].name
                        if form.cleaned_data.get("destination")
                        else ""
                    ),
                    "promotional_price": (
                        str(form.cleaned_data["promotional_price"])
                        if form.cleaned_data.get("promotional_price") is not None
                        else ""
                    ),
                    "campaign_name": form.cleaned_data.get("campaign_name", ""),
                    "valid_until": (
                        form.cleaned_data["valid_until"].isoformat()
                        if form.cleaned_data.get("valid_until")
                        else ""
                    ),
                },
                requested_by=request.user,
            )
        except ValidationError as error:
            form.add_error(None, error.message)
        else:
            record_audit_event(
                action="aged_stock_action.requested",
                actor=request.user,
                organization=request.organization,
                target=action,
                metadata={
                    "stock_unit": str(stock_unit.id),
                    "serial_number": stock_unit.serial_number,
                    "action_type": action.action_type,
                },
                request=request,
            )
            messages.success(request, "Aged-stock action sent for owner approval.")
            return redirect("module-overview", module="aged-stock")
    return render(request, "inventory/aged_stock_action_form.html", {
        "form": form,
        "stock_unit": stock_unit,
        "age_days": age_days,
        "bucket": bucket,
    })


@login_required
@organization_permission_required("inventory.change_agedstockaction")
@require_POST
def aged_stock_action_execute(request, action_id):
    from .models import AgedStockAction

    action = get_object_or_404(
        AgedStockAction,
        id=action_id,
        organization=request.organization,
    )
    try:
        action = execute_aged_stock_action(action=action, actor=request.user)
    except (KeyError, ValueError, ValidationError) as error:
        messages.error(request, getattr(error, "message", str(error)))
    else:
        record_audit_event(
            action="aged_stock_action.executed",
            actor=request.user,
            organization=request.organization,
            target=action,
            metadata=action.execution_result,
            request=request,
        )
        messages.success(request, action.execution_result.get("outcome", "Aged-stock action executed."))
    return redirect("approval-detail", approval_id=action.approval_id)


@login_required
@organization_permission_required("inventory.view_stockadjustment")
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
