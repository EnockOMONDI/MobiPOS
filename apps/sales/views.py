from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.services import record_audit_event
from apps.organizations.permissions import accessible_locations_for, organization_owner_required, organization_permission_required
from apps.payments.forms import AdditionalPaymentForm
from apps.payments.models import Payment, PaymentStatus, Refund
from apps.payments.services import confirm_payment, confirm_refund
from .forms import ReturnRequestForm
from .models import ReturnStatus, Sale, SaleReturn
from .services import complete_return


@login_required
def sale_detail(request, sale_id):
    sale = get_object_or_404(
        Sale, id=sale_id, organization=request.organization,
        location__in=accessible_locations_for(request.user, request.organization),
    )
    return render(request, "sales/detail.html", {"sale": sale, "payment_form": AdditionalPaymentForm(), "return_form": ReturnRequestForm()})


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
        payment = Payment.objects.create(
            organization=request.organization,
            number=f"PAY-{timezone.now():%Y%m%d%H%M%S%f}",
            customer=sale.customer,
            sale=sale,
            method=form.cleaned_data["method"],
            amount=form.cleaned_data["amount"],
            provider_reference=form.cleaned_data["provider_reference"],
            received_by=request.user,
        )
        try:
            confirm_payment(payment=payment)
            messages.success(request, "Payment confirmed.")
        except ValidationError as error:
            messages.error(request, error.message)
            transaction.set_rollback(True)
    return redirect("sale-detail", sale_id=sale.id)


@login_required
@organization_permission_required("sales.add_salereturn")
@require_POST
def sale_request_return(request, sale_id):
    sale = get_object_or_404(
        Sale, id=sale_id, organization=request.organization,
        location__in=accessible_locations_for(request.user, request.organization),
    )
    form = ReturnRequestForm(request.POST)
    if form.is_valid():
        sale_return = SaleReturn.objects.create(
            organization=request.organization,
            number=f"RET-{timezone.now():%Y%m%d%H%M%S%f}",
            sale=sale,
            reason=form.cleaned_data["reason"],
            refund_amount=form.cleaned_data["refund_amount"],
            requested_by=request.user,
        )
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
        payment = sale_return.sale.payments.filter(status=PaymentStatus.CONFIRMED).first()
        if payment:
            refund = Refund.objects.create(
                organization=request.organization,
                number=f"RFD-{timezone.now():%Y%m%d%H%M%S%f}",
                payment=payment,
                amount=min(sale_return.refund_amount, payment.amount),
                reason=sale_return.reason,
                approved_by=request.user,
            )
            confirm_refund(refund=refund)
    record_audit_event(action="return.completed", actor=request.user, organization=request.organization, target=sale_return, request=request)
    messages.success(request, "Return and refund completed.")
    return redirect("sale-detail", sale_id=sale_return.sale_id)
