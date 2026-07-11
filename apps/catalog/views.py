from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.audit.models import AuditEvent
from apps.audit.services import record_audit_event
from apps.organizations.permissions import organization_permission_required

from .models import Product
from .forms import BrandForm, CategoryForm, ProductForm


def _create(request, form_class, action, title, template="catalog/create.html"):
    form = form_class(request.POST or None, organization=request.organization)
    if request.method == "POST" and form.is_valid():
        instance = form.save(commit=False)
        instance.organization = request.organization
        instance.save()
        record_audit_event(action=action, actor=request.user, organization=request.organization, target=instance, request=request)
        messages.success(request, f"{instance} created.")
        if isinstance(instance, Product):
            return redirect("product-detail", product_id=instance.id)
        return redirect("module-overview", module="products")
    return render(request, template, {"form": form, "title": title, "submit_label": "Create record"})


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
    stock_total = product.balances.aggregate(total=Sum("quantity"))["total"] or 0
    sold_total = product.saleline_set.aggregate(total=Sum("quantity"))["total"] or 0
    purchased_total = product.purchaseorderline_set.aggregate(total=Sum("quantity"))["total"] or 0
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
        "balances": product.balances.select_related("location", "location__branch"),
        "activity": activity,
    })


@login_required
@organization_permission_required("catalog.change_product")
@transaction.atomic
def product_update(request, product_id):
    product = get_object_or_404(Product, id=product_id, organization=request.organization)
    form = ProductForm(request.POST or None, instance=product, organization=request.organization)
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
