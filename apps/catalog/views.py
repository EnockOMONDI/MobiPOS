from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render

from apps.audit.services import record_audit_event
from apps.organizations.permissions import organization_permission_required

from .forms import BrandForm, CategoryForm, ProductForm
def _create(request, form_class, action, title, template="catalog/create.html"):
    form = form_class(request.POST or None, organization=request.organization)
    if request.method == "POST" and form.is_valid():
        instance = form.save(commit=False)
        instance.organization = request.organization
        instance.save()
        record_audit_event(action=action, actor=request.user, organization=request.organization, target=instance, request=request)
        return redirect("module-overview", module="products")
    return render(request, template, {"form": form, "title": title})


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
