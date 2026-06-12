from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.services import record_audit_event
from apps.organizations.permissions import organization_owner_required

from .forms import CommissionPayoutForm
from .models import CommissionPayout
from .services import approve_commission_payout, pay_commission_payout, populate_commission_payout


@login_required
@organization_owner_required
@transaction.atomic
def commission_payout_create(request):
    form = CommissionPayoutForm(request.POST or None, organization=request.organization)
    if request.method == "POST" and form.is_valid():
        payout = CommissionPayout.objects.create(
            organization=request.organization,
            number=f"COM-{timezone.now():%Y%m%d%H%M%S%f}",
            requested_by=request.user,
            **form.cleaned_data,
        )
        try:
            populate_commission_payout(payout=payout)
        except ValidationError as error:
            transaction.set_rollback(True)
            form.add_error(None, error.message)
        else:
            record_audit_event(action="commission_payout.requested", actor=request.user, organization=request.organization, target=payout, request=request)
            return redirect("commission-payout-detail", payout_id=payout.id)
    return render(request, "commissions/payout_create.html", {"form": form})


@login_required
@organization_owner_required
def commission_payout_detail(request, payout_id):
    payout = get_object_or_404(CommissionPayout, id=payout_id, organization=request.organization)
    return render(request, "commissions/payout_detail.html", {"payout": payout})


@login_required
@organization_owner_required
@require_POST
def commission_payout_approve(request, payout_id):
    payout = get_object_or_404(CommissionPayout, id=payout_id, organization=request.organization)
    try:
        approve_commission_payout(payout=payout, actor=request.user)
        record_audit_event(action="commission_payout.approved", actor=request.user, organization=request.organization, target=payout, request=request)
    except ValidationError as error:
        messages.error(request, error.message)
    return redirect("commission-payout-detail", payout_id=payout.id)


@login_required
@organization_owner_required
@require_POST
def commission_payout_pay(request, payout_id):
    payout = get_object_or_404(CommissionPayout, id=payout_id, organization=request.organization)
    try:
        pay_commission_payout(
            payout=payout, actor=request.user,
            payment_reference=request.POST.get("payment_reference", "MANUAL"),
        )
        record_audit_event(action="commission_payout.paid", actor=request.user, organization=request.organization, target=payout, request=request)
    except ValidationError as error:
        messages.error(request, error.message)
    return redirect("commission-payout-detail", payout_id=payout.id)
