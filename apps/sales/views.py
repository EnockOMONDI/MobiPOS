from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.services import record_audit_event
from apps.operations.forms import InstallmentScheduleForm
from apps.organizations.permissions import accessible_locations_for, organization_owner_required, organization_permission_required
from apps.payments.forms import AdditionalPaymentForm
from apps.payments.models import Payment, PaymentMethod, PaymentStatus
from apps.payments.services import allocate_sale_refund, confirm_payment
from .forms import ReturnRequestForm
from .models import ReturnStatus, Sale, SaleReturn, SaleReturnLine
from .services import complete_return


@login_required
@organization_permission_required("sales.view_sale")
def sale_detail(request, sale_id):
    sale = get_object_or_404(
        Sale, id=sale_id, organization=request.organization,
        location__in=accessible_locations_for(request.user, request.organization),
    )
    receivable = getattr(sale, "receivable", None)
    return render(request, "sales/detail.html", {
        "sale": sale,
        "payment_form": AdditionalPaymentForm(),
        "return_form": ReturnRequestForm(sale=sale),
        "installment_form": InstallmentScheduleForm(receivable=receivable),
        "current_installments": receivable.installments.filter(is_current=True) if receivable else [],
    })


@login_required
@organization_permission_required("payments.add_payment")
@require_POST
@transaction.atomic
def sale_add_payment(request, sale_id):
    sale = get_object_or_404(
        Sale, id=sale_id, organization=request.organization,
        location__in=accessible_locations_for(request.user, request.organization),
    )
    form = AdditionalPaymentForm(request.POST)
    if form.is_valid():
        provider_reference = form.cleaned_data["provider_reference"]
        if provider_reference and Payment.objects.filter(
            organization=request.organization,
            method=form.cleaned_data["method"],
            provider_reference=provider_reference,
        ).exists():
            messages.error(request, "This payment reference has already been used.")
            return redirect("sale-detail", sale_id=sale.id)
        payment = Payment.objects.create(
            organization=request.organization,
            number=f"PAY-{timezone.now():%Y%m%d%H%M%S%f}",
            customer=sale.customer,
            sale=sale,
            method=form.cleaned_data["method"],
            amount=form.cleaned_data["amount"],
            provider_reference=provider_reference,
            received_by=request.user,
        )
        if payment.method == PaymentMethod.CASH:
            try:
                confirm_payment(payment=payment)
                messages.success(request, "Cash payment confirmed.")
            except ValidationError as error:
                messages.error(request, error.message)
                transaction.set_rollback(True)
        elif payment.status == PaymentStatus.PENDING:
            messages.success(request, "Payment recorded as pending confirmation.")
    return redirect("sale-detail", sale_id=sale.id)


@login_required
@organization_permission_required("sales.add_salereturn")
@require_POST
def sale_request_return(request, sale_id):
    sale = get_object_or_404(
        Sale, id=sale_id, organization=request.organization,
        location__in=accessible_locations_for(request.user, request.organization),
    )
    form = ReturnRequestForm(request.POST, sale=sale)
    if form.is_valid():
        sale_return = SaleReturn.objects.create(
            organization=request.organization,
            number=f"RET-{timezone.now():%Y%m%d%H%M%S%f}",
            sale=sale,
            reason=form.cleaned_data["reason"],
            refund_amount=form.cleaned_data["refund_amount"],
            outcome=form.cleaned_data["outcome"],
            requested_by=request.user,
        )
        SaleReturnLine.objects.bulk_create([
            SaleReturnLine(
                organization=request.organization,
                sale_return=sale_return,
                sale_line=line,
                quantity=quantity,
                disposition=form.cleaned_data["disposition"],
                refundable_amount=line_value,
            )
            for line, quantity, line_value in form.cleaned_data["selected_lines"]
        ])
        record_audit_event(action="return.requested", actor=request.user, organization=request.organization, target=sale_return, request=request)
        messages.success(request, "Return request created.")
    return redirect("sale-detail", sale_id=sale.id)


@login_required
@organization_owner_required
@require_POST
@transaction.atomic
def return_approve_complete(request, return_id):
    sale_return = get_object_or_404(SaleReturn, id=return_id, organization=request.organization)
    sale_return.status = ReturnStatus.APPROVED
    sale_return.approved_by = request.user
    sale_return.save(update_fields=["status", "approved_by", "updated_at"])
    complete_return(sale_return=sale_return, actor=request.user)
    if sale_return.refund_amount:
        allocate_sale_refund(
            sale=sale_return.sale,
            amount=sale_return.refund_amount,
            reason=sale_return.reason,
            approved_by=request.user,
        )
    record_audit_event(action="return.completed", actor=request.user, organization=request.organization, target=sale_return, request=request)
    messages.success(request, "Return and refund completed.")
    return redirect("sale-detail", sale_id=sale_return.sale_id)
