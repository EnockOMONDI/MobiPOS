from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.services import record_audit_event
from apps.notifications.services import notify_business_event
from apps.organizations.permissions import accessible_locations_for, organization_owner_required, organization_permission_required
from .document_extraction import extract_purchase_document_text
from .forms import PurchaseOrderForm, ReceivePurchaseLineForm, SupplierReturnForm
from .models import PurchaseDiscrepancy, PurchaseDocumentExtractionStatus, PurchaseOrder, PurchaseOrderLine, SupplierReturn
from .services import (
    approve_purchase_order,
    complete_supplier_return,
    receive_purchase_line,
    resolve_purchase_discrepancy,
)


@login_required
@organization_permission_required("purchasing.add_purchaseorder")
@transaction.atomic
def purchase_create(request):
    if not request.organization:
        return redirect("dashboard")
    form = PurchaseOrderForm(request.POST or None, request.FILES or None, organization=request.organization, user=request.user)
    if request.method == "POST" and form.is_valid():
        order = PurchaseOrder.objects.create(
            organization=request.organization,
            number=f"PO-{timezone.now():%Y%m%d%H%M%S%f}",
            supplier=form.cleaned_data["supplier"],
            destination=form.cleaned_data["destination"],
            ordered_on=timezone.localdate(),
            supplier_reference=form.cleaned_data["supplier_reference"],
            attachment=form.cleaned_data["attachment"],
            notes=form.cleaned_data["notes"],
            created_by=request.user,
        )
        PurchaseOrderLine.objects.bulk_create([
            PurchaseOrderLine(organization=request.organization, order=order, **line)
            for line in form.cleaned_data["lines"]
        ])
        record_audit_event(action="purchase.created", actor=request.user, organization=request.organization, target=order, request=request)
        messages.success(request, f"Purchase order {order.number} created.")
        return redirect("purchase-detail", order_id=order.id)
    return render(request, "purchasing/create.html", {
        "form": form,
        "has_suppliers": form.fields["supplier"].queryset.exists(),
        "has_real_suppliers": form.fields["supplier"].queryset.exclude(name__iexact="Opening Stock Supplier").exists(),
        "has_destinations": form.fields["destination"].queryset.exists(),
        "has_products": form.fields["product"].queryset.exists(),
    })


@login_required
@organization_permission_required("purchasing.view_purchaseorder")
def purchase_detail(request, order_id):
    order = get_object_or_404(
        PurchaseOrder, id=order_id, organization=request.organization,
        destination__in=accessible_locations_for(request.user, request.organization),
    )
    return render(request, "purchasing/detail.html", {"order": order, "receive_form": ReceivePurchaseLineForm()})


@login_required
@organization_permission_required("purchasing.change_purchaseorder")
@require_POST
def purchase_extract_document(request, order_id):
    order = get_object_or_404(
        PurchaseOrder,
        id=order_id,
        organization=request.organization,
        destination__in=accessible_locations_for(request.user, request.organization),
    )
    try:
        status, text = extract_purchase_document_text(order.attachment)
        order.extraction_status = status
        order.extracted_text = text
        order.save(update_fields=["extraction_status", "extracted_text", "updated_at"])
        record_audit_event(action="purchase.document_extracted", actor=request.user, organization=request.organization, target=order, request=request)
        if status == "extracted":
            messages.success(request, "Supplier document text extracted for review.")
        elif status == "manual_review":
            messages.error(request, "Supplier document needs manual review; scanned files require a configured OCR provider.")
        else:
            messages.error(request, text)
    except ValidationError as error:
        message = error.message if hasattr(error, "message") else "; ".join(error.messages)
        order.extraction_status = PurchaseDocumentExtractionStatus.FAILED
        order.extracted_text = message
        order.save(update_fields=["extraction_status", "extracted_text", "updated_at"])
        messages.error(request, message)
    return redirect("purchase-detail", order_id=order.id)


@login_required
@organization_owner_required
@require_POST
def purchase_approve(request, order_id):
    order = get_object_or_404(PurchaseOrder, id=order_id, organization=request.organization)
    try:
        approve_purchase_order(order=order, actor=request.user)
        record_audit_event(action="purchase.approved", actor=request.user, organization=request.organization, target=order, request=request)
        messages.success(request, "Purchase order approved.")
    except ValidationError as error:
        messages.error(request, error.message)
    return redirect("purchase-detail", order_id=order.id)


@login_required
@organization_permission_required("purchasing.change_purchaseorder")
@require_POST
def purchase_receive(request, line_id):
    line = get_object_or_404(
        PurchaseOrderLine, id=line_id, organization=request.organization,
        order__destination__in=accessible_locations_for(request.user, request.organization),
    )
    form = ReceivePurchaseLineForm(request.POST)
    if form.is_valid():
        try:
            receive_purchase_line(
                line=line, quantity=form.cleaned_data["quantity"], actor=request.user,
                request_id=form.cleaned_data["request_id"],
                serial_numbers=form.serial_list(),
                damaged_quantity=form.cleaned_data["damaged_quantity"],
                close_with_discrepancy=form.cleaned_data["close_with_discrepancy"],
                discrepancy_reason=form.cleaned_data["discrepancy_reason"],
            )
            record_audit_event(action="purchase.received", actor=request.user, organization=request.organization, target=line.order, request=request)
            line.order.refresh_from_db(fields=("status",))
            if line.order.status == "discrepancy":
                notify_business_event(
                    organization=request.organization,
                    title="Supplier delivery discrepancy",
                    message=f"{line.order.number} has missing or damaged stock that needs owner review.",
                    link=reverse("purchase-detail", args=[line.order_id]),
                    include_owners=True,
                )
            messages.success(request, "Stock received successfully.")
        except ValidationError as error:
            messages.error(request, error.message)
        except IntegrityError:
            messages.error(
                request,
                "This receipt conflicts with stock recorded by another user. Refresh the purchase before trying again.",
            )
    else:
        messages.error(request, "Correct the receiving details.")
    return redirect("purchase-detail", order_id=line.order_id)


@login_required
@organization_owner_required
@require_POST
def purchase_discrepancy_resolve(request, discrepancy_id):
    discrepancy = get_object_or_404(PurchaseDiscrepancy, id=discrepancy_id, organization=request.organization)
    try:
        discrepancy = resolve_purchase_discrepancy(
            discrepancy=discrepancy, actor=request.user, resolution=request.POST.get("resolution", "")
        )
        record_audit_event(action="purchase.discrepancy_resolved", actor=request.user, organization=request.organization, target=discrepancy, request=request)
        notify_business_event(
            organization=request.organization,
            title="Supplier discrepancy resolved",
            message=(
                f"The discrepancy on {discrepancy.line.order.number} was resolved as "
                f"{'accepted short delivery' if discrepancy.resolution == 'accept_short' else 'replacement pending'}."
            ),
            link=reverse("purchase-detail", args=[discrepancy.line.order_id]),
            users=(discrepancy.line.order.created_by,),
        )
        messages.success(request, "Purchase discrepancy resolved.")
    except ValidationError as error:
        messages.error(request, error.message)
    return redirect("purchase-detail", order_id=discrepancy.line.order_id)


@login_required
@organization_permission_required("purchasing.add_supplierreturn")
@transaction.atomic
def supplier_return_create(request):
    form = SupplierReturnForm(request.POST or None, organization=request.organization, user=request.user)
    if request.method == "POST" and form.is_valid():
        supplier_return = SupplierReturn.objects.create(
            organization=request.organization,
            number=f"SRT-{timezone.now():%Y%m%d%H%M%S%f}",
            requested_by=request.user,
            **form.cleaned_data,
        )
        record_audit_event(action="supplier_return.requested", actor=request.user, organization=request.organization, target=supplier_return, request=request)
        notify_business_event(
            organization=request.organization,
            title="Supplier return awaiting approval",
            message=f"{supplier_return.number} was requested for {supplier_return.line.product.name}.",
            link=reverse("supplier-return-detail", args=[supplier_return.id]),
            include_owners=True,
        )
        messages.success(request, "Supplier return submitted for approval.")
        return redirect("supplier-return-detail", return_id=supplier_return.id)
    return render(request, "purchasing/supplier_return.html", {"form": form})


@login_required
@organization_permission_required("purchasing.view_supplierreturn")
def supplier_return_detail(request, return_id):
    supplier_return = get_object_or_404(
        SupplierReturn,
        id=return_id,
        organization=request.organization,
        line__order__destination__in=accessible_locations_for(request.user, request.organization),
    )
    return render(request, "purchasing/supplier_return_detail.html", {"supplier_return": supplier_return})


@login_required
@organization_owner_required
@require_POST
def supplier_return_complete(request, return_id):
    supplier_return = get_object_or_404(SupplierReturn, id=return_id, organization=request.organization)
    try:
        complete_supplier_return(supplier_return=supplier_return, actor=request.user)
        record_audit_event(action="supplier_return.completed", actor=request.user, organization=request.organization, target=supplier_return, request=request)
        notify_business_event(
            organization=request.organization,
            title="Supplier return completed",
            message=f"{supplier_return.number} was completed and its stock and payable effects were recorded.",
            link=reverse("supplier-return-detail", args=[supplier_return.id]),
            users=(supplier_return.requested_by,),
        )
        messages.success(request, "Supplier return completed.")
    except ValidationError as error:
        messages.error(request, error.message)
    return redirect("supplier-return-detail", return_id=supplier_return.id)
