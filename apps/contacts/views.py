import csv
from io import BytesIO

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from openpyxl import Workbook

from apps.audit.models import AuditEvent
from apps.audit.services import record_audit_event
from apps.organizations.permissions import organization_permission_required
from apps.reports.exporting import build_statement_pdf, safe_csv_row

from .forms import ContactForm
from .models import Contact


def _safe_next_url(request):
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return ""


def _contact_statement_context(contact):
    sales = list(contact.sales.order_by("-created_at")[:100])
    payments = list(contact.payments.order_by("-received_at")[:100])
    receivables = list(contact.receivables.order_by("due_on"))
    payables = list(contact.payables.order_by("due_on"))
    return {
        "contact": contact,
        "sales": sales,
        "payments": payments,
        "receivables": receivables,
        "payables": payables,
        "sales_total": contact.sales.aggregate(total=Sum("total"))["total"] or 0,
        "payments_total": contact.payments.aggregate(total=Sum("amount"))["total"] or 0,
        "receivable_total": contact.receivables.aggregate(total=Sum("outstanding_amount"))["total"] or 0,
        "payable_total": contact.payables.aggregate(total=Sum("outstanding_amount"))["total"] or 0,
    }


def _statement_filename(contact, extension):
    safe_name = "".join(character if character.isalnum() else "-" for character in contact.name.lower()).strip("-")
    return f"{safe_name or 'contact'}-statement.{extension}"


def _contact_statement_export_rows(context):
    rows = [
        ["Summary", "", "Sales", "", context["sales_total"], ""],
        ["Summary", "", "Payments", "", "", context["payments_total"]],
        ["Summary", "", "Receivable balance", "", context["receivable_total"], ""],
        ["Summary", "", "Payable balance", "", "", context["payable_total"]],
    ]
    for sale in context["sales"]:
        rows.append([
            "Sale",
            sale.created_at.strftime("%Y-%m-%d %H:%M"),
            sale.number,
            sale.get_status_display(),
            sale.total,
            "",
        ])
    for payment in context["payments"]:
        rows.append([
            "Payment",
            payment.received_at.strftime("%Y-%m-%d %H:%M"),
            payment.number,
            f"{payment.get_method_display()} · {payment.get_status_display()}",
            "",
            payment.amount,
        ])
    for receivable in context["receivables"]:
        rows.append([
            "Receivable",
            receivable.due_on,
            receivable.sale.number,
            "Written off" if receivable.is_written_off else "Outstanding",
            receivable.outstanding_amount,
            "",
        ])
    for payable in context["payables"]:
        rows.append([
            "Payable",
            payable.due_on,
            payable.purchase_order.number,
            "Settled" if payable.is_settled else "Outstanding",
            "",
            payable.outstanding_amount,
        ])
    return rows


def _contact_statement_csv_response(context):
    contact = context["contact"]
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{_statement_filename(contact, "csv")}"'
    writer = csv.writer(response)
    writer.writerow(safe_csv_row(["Contact", contact.name]))
    writer.writerow(safe_csv_row(["Type", contact.get_contact_type_display()]))
    writer.writerow([])
    headers = ["Section", "Date", "Reference", "Status", "Debit", "Credit"]
    writer.writerow(safe_csv_row(headers))
    for row in _contact_statement_export_rows(context):
        writer.writerow(safe_csv_row(row))
    return response


def _contact_statement_xlsx_response(context):
    contact = context["contact"]
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Statement"
    worksheet.append(["MobiPOS statement"])
    worksheet.append(["Contact", contact.name])
    worksheet.append(["Type", contact.get_contact_type_display()])
    worksheet.append([])
    headers = ["Section", "Date", "Reference", "Status", "Debit", "Credit"]
    worksheet.append(headers)
    for row in _contact_statement_export_rows(context):
        worksheet.append(row)
    for cell in worksheet[1]:
        cell.style = "Title"
    for cell in worksheet[5]:
        cell.style = "Headline 3"
    for column in ("A", "B", "C", "D", "E", "F"):
        worksheet.column_dimensions[column].width = 22
    stream = BytesIO()
    workbook.save(stream)
    response = HttpResponse(
        stream.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{_statement_filename(contact, "xlsx")}"'
    return response


def _contact_statement_pdf_response(context):
    contact = context["contact"]
    headers = ["Section", "Date", "Reference", "Status", "Debit", "Credit"]
    response = HttpResponse(
        build_statement_pdf(
            title="Supplier and Customer Statement",
            organization=contact.organization,
            contact=contact,
            summary={
                "sales_total": context["sales_total"],
                "payments_total": context["payments_total"],
                "receivable_total": context["receivable_total"],
                "payable_total": context["payable_total"],
            },
            headers=headers,
            rows=_contact_statement_export_rows(context),
        ),
        content_type="application/pdf",
    )
    response["Content-Disposition"] = f'attachment; filename="{_statement_filename(contact, "pdf")}"'
    return response


@login_required
@organization_permission_required("contacts.add_contact")
@transaction.atomic
def contact_create(request):
    requested_type = request.GET.get("type", "") or request.POST.get("contact_type", "")
    next_url = _safe_next_url(request)
    form = ContactForm(request.POST or None, organization=request.organization, initial_type=requested_type)
    if request.method == "POST" and form.is_valid():
        contact = form.save(commit=False)
        contact.organization = request.organization
        contact.save()
        record_audit_event(action="contact.created", actor=request.user, organization=request.organization, target=contact, request=request)
        messages.success(request, "Contact created.")
        if next_url:
            return redirect(next_url)
        return redirect("contact-detail", contact_id=contact.id)
    title = "Add supplier" if requested_type == "supplier" else "Add customer" if requested_type == "customer" else "Add customer or supplier"
    return render(request, "contacts/create.html", {
        "form": form,
        "title": title,
        "submit_label": "Create contact",
        "next_url": next_url,
        "cancel_url": next_url or None,
        "requested_type": requested_type,
    })


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
    context = _contact_statement_context(contact)
    requested_format = request.GET.get("format")
    if requested_format == "csv":
        return _contact_statement_csv_response(context)
    if requested_format == "xlsx":
        return _contact_statement_xlsx_response(context)
    if requested_format == "pdf":
        return _contact_statement_pdf_response(context)
    return render(request, "contacts/statement.html", context)


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
