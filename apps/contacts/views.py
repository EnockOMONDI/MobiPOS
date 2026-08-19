import csv
from decimal import Decimal
from io import BytesIO
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.dateparse import parse_date
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


def _contact_statement_context(contact, *, date_from=None, date_to=None, paginate=False, request=None):
    from apps.operations.models import PayablePayment
    from apps.purchasing.models import SupplierReturn, SupplierReturnStatus

    sales = contact.sales.order_by("-created_at")
    payments = contact.payments.order_by("-received_at")
    receivables = contact.receivables.order_by("due_on")
    payables = contact.payables.order_by("due_on")
    supplier_payments = PayablePayment.objects.filter(
        organization=contact.organization,
        payable__supplier=contact,
    ).select_related("payable__purchase_order", "paid_by", "reversed_by").order_by("-paid_at")
    supplier_returns = SupplierReturn.objects.filter(
        organization=contact.organization,
        line__order__supplier=contact,
        status=SupplierReturnStatus.COMPLETED,
    ).select_related("line__order", "line__product").order_by("-updated_at")
    if date_from:
        sales = sales.filter(created_at__date__gte=date_from)
        payments = payments.filter(received_at__date__gte=date_from)
        receivables = receivables.filter(due_on__gte=date_from)
        payables = payables.filter(due_on__gte=date_from)
        supplier_payments = supplier_payments.filter(paid_at__date__gte=date_from)
        supplier_returns = supplier_returns.filter(updated_at__date__gte=date_from)
    if date_to:
        sales = sales.filter(created_at__date__lte=date_to)
        payments = payments.filter(received_at__date__lte=date_to)
        receivables = receivables.filter(due_on__lte=date_to)
        payables = payables.filter(due_on__lte=date_to)
        supplier_payments = supplier_payments.filter(paid_at__date__lte=date_to)
        supplier_returns = supplier_returns.filter(updated_at__date__lte=date_to)
    sales_total = sales.aggregate(total=Sum("total"))["total"] or 0
    payments_total = payments.aggregate(total=Sum("amount"))["total"] or 0
    receivable_total = receivables.aggregate(total=Sum("outstanding_amount"))["total"] or 0
    payable_total = payables.aggregate(total=Sum("outstanding_amount"))["total"] or 0
    supplier_payments_total = supplier_payments.filter(reversed_at__isnull=True).aggregate(total=Sum("amount"))["total"] or 0
    supplier_returns_total = sum(
        (item.credit_amount for item in supplier_returns),
        Decimal("0"),
    )
    pagination_queries = {}
    if paginate:
        sales = Paginator(sales, 25).get_page(request.GET.get("sales_page"))
        payments = Paginator(payments, 25).get_page(request.GET.get("payments_page"))
        receivables = Paginator(receivables, 25).get_page(request.GET.get("receivables_page"))
        payables = Paginator(payables, 25).get_page(request.GET.get("payables_page"))
        supplier_payments = Paginator(supplier_payments, 25).get_page(request.GET.get("supplier_payments_page"))
        supplier_returns = Paginator(supplier_returns, 25).get_page(request.GET.get("supplier_returns_page"))
        page_parameters = {
            "sales_page": sales.number,
            "payments_page": payments.number,
            "receivables_page": receivables.number,
            "payables_page": payables.number,
            "supplier_payments_page": supplier_payments.number,
            "supplier_returns_page": supplier_returns.number,
        }
        if date_from:
            page_parameters["date_from"] = date_from.isoformat()
        if date_to:
            page_parameters["date_to"] = date_to.isoformat()
        for section in ("sales", "payments", "receivables", "payables", "supplier_payments", "supplier_returns"):
            parameter_name = f"{section}_page"
            section_page = locals()[section]
            pagination_queries[section] = {
                "previous": urlencode({
                    **page_parameters,
                    parameter_name: section_page.previous_page_number(),
                }) if section_page.has_previous() else "",
                "next": urlencode({
                    **page_parameters,
                    parameter_name: section_page.next_page_number(),
                }) if section_page.has_next() else "",
            }
    period_query = urlencode({
        key: value for key, value in {
            "date_from": date_from.isoformat() if date_from else "",
            "date_to": date_to.isoformat() if date_to else "",
        }.items() if value
    })
    return {
        "contact": contact,
        "sales": sales,
        "payments": payments,
        "receivables": receivables,
        "payables": payables,
        "supplier_payments": supplier_payments,
        "supplier_returns": supplier_returns,
        "sales_total": sales_total,
        "payments_total": payments_total,
        "receivable_total": receivable_total,
        "payable_total": payable_total,
        "supplier_payments_total": supplier_payments_total,
        "supplier_returns_total": supplier_returns_total,
        "date_from": date_from.isoformat() if date_from else "",
        "date_to": date_to.isoformat() if date_to else "",
        "period_query": period_query,
        "pagination_queries": pagination_queries,
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
        ["Summary", "", "Supplier payments", "", context["supplier_payments_total"], ""],
        ["Summary", "", "Supplier returns", "", context["supplier_returns_total"], ""],
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
    for payment in context["supplier_payments"]:
        rows.append([
            "Supplier payment" if not payment.reversed_at else "Reversed supplier payment",
            payment.paid_at.strftime("%Y-%m-%d %H:%M"),
            payment.reference or payment.number,
            f"{payment.get_method_display()} · {'Reversed' if payment.reversed_at else 'Active'}",
            payment.amount,
            "",
        ])
    for supplier_return in context["supplier_returns"]:
        rows.append([
            "Supplier return",
            supplier_return.updated_at.strftime("%Y-%m-%d %H:%M"),
            supplier_return.number,
            supplier_return.line.product.name,
            supplier_return.credit_amount,
            "",
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
    date_from = parse_date(request.GET.get("date_from", ""))
    date_to = parse_date(request.GET.get("date_to", ""))
    requested_format = request.GET.get("format")
    context = _contact_statement_context(
        contact,
        date_from=date_from,
        date_to=date_to,
        paginate=not requested_format,
        request=request,
    )
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
