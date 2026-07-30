from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.audit.models import AuditEvent
from apps.audit.services import record_audit_event
from apps.inventory.models import SerialStatus, StockMovement, StockUnit
from apps.organizations.permissions import accessible_branches_for, organization_permission_required, user_has_organization_permission
from apps.purchasing.models import PurchaseOrder
from apps.sales.models import Sale
from .permissions import can_view_product_costs

from .models import Product
from .forms import BrandForm, CategoryForm, ProductForm


def _create(request, form_class, action, title, template="catalog/create.html"):
    form = form_class(request.POST or None, request.FILES or None, organization=request.organization)
    if request.method == "POST" and form.is_valid():
        instance = form.save(commit=False)
        instance.organization = request.organization
        instance.save()
        record_audit_event(action=action, actor=request.user, organization=request.organization, target=instance, request=request)
        messages.success(request, f"{instance} created.")
        if isinstance(instance, Product):
            return redirect("product-detail", product_id=instance.id)
        return redirect("module-overview", module="products")
    context = {"form": form, "title": title, "submit_label": "Create record"}
    if form_class is ProductForm:
        context.update({
            "is_product_form": True,
            "has_categories": form.fields["category"].queryset.exists(),
            "has_brands": form.fields["brand"].queryset.exists(),
        })
    return render(request, template, context)


@login_required
@organization_permission_required("catalog.add_category")
@transaction.atomic
def category_create(request):
    return _create(request, CategoryForm, "category.created", "Add category")


@login_required
@organization_permission_required("catalog.add_brand")
@transaction.atomic
def brand_create(request):
    return _create(request, BrandForm, "brand.created", "Add brand")


@login_required
@organization_permission_required("catalog.add_product")
@transaction.atomic
def product_create(request):
    return _create(request, ProductForm, "product.created", "Add product")


@login_required
@organization_permission_required("catalog.view_product")
def product_detail(request, product_id):
    product = get_object_or_404(
        Product.objects.select_related("category", "brand"),
        id=product_id,
        organization=request.organization,
    )
    branches = accessible_branches_for(request.user, request.organization)
    balances = product.balances.filter(location__branch__in=branches).select_related("location", "location__branch")
    stock_total = balances.aggregate(total=Sum("quantity"))["total"] or 0
    sold_total = product.saleline_set.filter(sale__location__branch__in=branches).aggregate(total=Sum("quantity"))["total"] or 0
    purchased_total = product.purchaseorderline_set.filter(order__destination__branch__in=branches).aggregate(total=Sum("quantity"))["total"] or 0
    available_units = StockUnit.objects.filter(
        organization=request.organization,
        product=product,
        location__branch__in=branches,
        status=SerialStatus.AVAILABLE,
    ).select_related("location", "location__branch").order_by("serial_number")
    sold_units = StockUnit.objects.filter(
        organization=request.organization,
        product=product,
        status=SerialStatus.SOLD,
    ).filter(
        Q(location__branch__in=branches)
        | Q(saleline__sale__location__branch__in=branches)
        | Q(movements__location__branch__in=branches)
    ).select_related("location", "location__branch").distinct().order_by("serial_number")
    recent_movements = StockMovement.objects.filter(
        organization=request.organization,
        product=product,
        location__branch__in=branches,
    ).select_related("location", "location__branch", "actor", "stock_unit")[:20]
    recent_sales = Sale.objects.filter(
        organization=request.organization,
        lines__product=product,
        location__branch__in=branches,
    ).select_related("customer", "location").distinct().order_by("-created_at")[:8]
    recent_purchases = PurchaseOrder.objects.filter(
        organization=request.organization,
        lines__product=product,
        destination__branch__in=branches,
    ).select_related("supplier", "destination").distinct().order_by("-created_at")[:8]
    activity = AuditEvent.objects.filter(
        organization=request.organization,
        target_type=product._meta.label,
        target_id=str(product.id),
    ).select_related("actor")[:20]
    return render(request, "catalog/detail.html", {
        "product": product,
        "stock_total": stock_total,
        "sold_total": sold_total,
        "purchased_total": purchased_total,
        "balances": balances,
        "available_units": available_units[:50],
        "available_units_count": available_units.count(),
        "sold_units": sold_units[:50],
        "sold_units_count": sold_units.count(),
        "recent_movements": recent_movements,
        "recent_sales": recent_sales,
        "recent_purchases": recent_purchases,
        "can_view_costs": can_view_product_costs(request.user, request.organization),
        "can_change_product": user_has_organization_permission(request.user, request.organization, "catalog.change_product"),
        "activity": activity,
    })


@login_required
@organization_permission_required("catalog.change_product")
@transaction.atomic
def product_update(request, product_id):
    product = get_object_or_404(Product, id=product_id, organization=request.organization)
    form = ProductForm(request.POST or None, request.FILES or None, instance=product, organization=request.organization)
    if request.method == "POST" and form.is_valid():
        product = form.save()
        record_audit_event(action="product.updated", actor=request.user, organization=request.organization, target=product, request=request)
        messages.success(request, "Product updated.")
        return redirect("product-detail", product_id=product.id)
    return render(request, "catalog/create.html", {
        "form": form,
        "title": f"Edit {product.name}",
        "submit_label": "Save product",
        "cancel_url": f"/catalog/products/{product.id}/",
    })


@login_required
@organization_permission_required("catalog.change_product")
@require_POST
@transaction.atomic
def product_toggle_active(request, product_id):
    product = get_object_or_404(Product, id=product_id, organization=request.organization)
    product.is_active = not product.is_active
    product.save(update_fields=["is_active", "updated_at"])
    action = "product.restored" if product.is_active else "product.archived"
    record_audit_event(action=action, actor=request.user, organization=request.organization, target=product, request=request)
    messages.success(request, "Product restored." if product.is_active else "Product archived.")
    return redirect("product-detail", product_id=product.id)
