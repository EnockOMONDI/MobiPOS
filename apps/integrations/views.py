from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.audit.services import record_audit_event
from apps.organizations.permissions import organization_owner_required
from .forms import FiscalDeviceForm
from .models import FiscalDevice, FiscalDeviceStatus, FiscalProvider


@login_required
@organization_owner_required
def etims_settings(request):
    device = (
        FiscalDevice.objects.filter(
            organization=request.organization,
            provider=FiscalProvider.ETIMS,
        )
        .select_related("company", "location")
        .order_by("-updated_at")
        .first()
    )
    previous_status = device.status if device else ""
    form = FiscalDeviceForm(
        request.POST or None,
        instance=device,
        organization=request.organization,
    )
    if request.method == "POST" and form.is_valid():
        fiscal_device = form.save(commit=False)
        fiscal_device.organization = request.organization
        fiscal_device.provider = FiscalProvider.ETIMS
        if fiscal_device.status == FiscalDeviceStatus.ACTIVE and previous_status != FiscalDeviceStatus.ACTIVE:
            fiscal_device.activated_at = timezone.now()
            fiscal_device.activated_by = request.user
        fiscal_device.save()
        record_audit_event(
            action="integrations.etims_device_configured",
            actor=request.user,
            organization=request.organization,
            target=fiscal_device,
            request=request,
            metadata={
                "environment": fiscal_device.environment,
                "status": fiscal_device.status,
                "receipt_policy": fiscal_device.receipt_policy,
            },
        )
        messages.success(request, "eTIMS settings saved.")
        return redirect("etims-settings")
    return render(request, "integrations/etims_settings.html", {
        "form": form,
        "device": device,
    })

# Create your views here.
