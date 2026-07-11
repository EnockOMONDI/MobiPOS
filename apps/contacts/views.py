from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.audit.models import AuditEvent
from apps.audit.services import record_audit_event
from apps.organizations.permissions import organization_permission_required

from .forms import ContactForm
from .models import Contact


@login_required
@organization_permission_required("contacts.add_contact")
@transaction.atomic
def contact_create(request):
    form = ContactForm(request.POST or None, organization=request.organization)
    if request.method == "POST" and form.is_valid():
        contact = form.save(commit=False)
        contact.organization = request.organization
        contact.save()
        record_audit_event(action="contact.created", actor=request.user, organization=request.organization, target=contact, request=request)
        messages.success(request, "Contact created.")
        return redirect("contact-detail", contact_id=contact.id)
    return render(request, "contacts/create.html", {"form": form, "title": "Add customer or supplier", "submit_label": "Create contact"})


@login_required
@organization_permission_required("contacts.view_contact")
def contact_detail(request, contact_id):
    contact = get_object_or_404(Contact, id=contact_id, organization=request.organization)
    sales_total = contact.sales.aggregate(total=Sum("total"))["total"] or 0
    purchases = contact.purchase_orders.aggregate(total=Sum("lines__quantity"))["total"] or 0
    receivable_total = contact.receivables.aggregate(total=Sum("outstanding_amount"))["total"] or 0
    payable_total = contact.payables.aggregate(total=Sum("outstanding_amount"))["total"] or 0
    activity = AuditEvent.objects.filter(
        organization=request.organization,
        target_type=contact._meta.label,
        target_id=str(contact.id),
    ).select_related("actor")[:20]
    return render(request, "contacts/detail.html", {
        "contact": contact,
        "sales_total": sales_total,
        "purchased_quantity": purchases,
        "receivable_total": receivable_total,
        "payable_total": payable_total,
        "recent_sales": contact.sales.order_by("-created_at")[:8],
        "recent_purchases": contact.purchase_orders.order_by("-created_at")[:8],
        "activity": activity,
    })


@login_required
@organization_permission_required("contacts.view_contact")
def contact_statement(request, contact_id):
    contact = get_object_or_404(Contact, id=contact_id, organization=request.organization)
    sales = contact.sales.order_by("-created_at")[:100]
    payments = contact.payments.order_by("-received_at")[:100]
    receivables = contact.receivables.order_by("due_on")
    payables = contact.payables.order_by("due_on")
    return render(request, "contacts/statement.html", {
        "contact": contact,
        "sales": sales,
        "payments": payments,
        "receivables": receivables,
        "payables": payables,
        "sales_total": contact.sales.aggregate(total=Sum("total"))["total"] or 0,
        "payments_total": contact.payments.aggregate(total=Sum("amount"))["total"] or 0,
        "receivable_total": receivables.aggregate(total=Sum("outstanding_amount"))["total"] or 0,
        "payable_total": payables.aggregate(total=Sum("outstanding_amount"))["total"] or 0,
    })


@login_required
@organization_permission_required("contacts.change_contact")
@transaction.atomic
def contact_update(request, contact_id):
    contact = get_object_or_404(Contact, id=contact_id, organization=request.organization)
    form = ContactForm(request.POST or None, instance=contact, organization=request.organization)
    if request.method == "POST" and form.is_valid():
        contact = form.save()
        record_audit_event(action="contact.updated", actor=request.user, organization=request.organization, target=contact, request=request)
        messages.success(request, "Contact updated.")
        return redirect("contact-detail", contact_id=contact.id)
    return render(request, "contacts/create.html", {
        "form": form,
        "title": f"Edit {contact.name}",
        "submit_label": "Save contact",
        "cancel_url": f"/contacts/{contact.id}/",
    })


@login_required
@organization_permission_required("contacts.change_contact")
@require_POST
@transaction.atomic
def contact_toggle_active(request, contact_id):
    contact = get_object_or_404(Contact, id=contact_id, organization=request.organization)
    contact.is_active = not contact.is_active
    contact.save(update_fields=["is_active", "updated_at"])
    action = "contact.restored" if contact.is_active else "contact.archived"
    record_audit_event(action=action, actor=request.user, organization=request.organization, target=contact, request=request)
    messages.success(request, "Contact restored." if contact.is_active else "Contact archived.")
    return redirect("contact-detail", contact_id=contact.id)
