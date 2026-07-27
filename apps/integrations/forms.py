from django import forms

from apps.organizations.models import Company, Location
from .models import FiscalDevice, FiscalDeviceStatus


class FiscalDeviceForm(forms.ModelForm):
    class Meta:
        model = FiscalDevice
        fields = (
            "environment",
            "mode",
            "status",
            "receipt_policy",
            "company",
            "location",
            "taxpayer_pin",
            "branch_office_id",
            "device_serial",
            "device_name",
        )
        widgets = {
            "taxpayer_pin": forms.TextInput(attrs={"placeholder": "Example: P051234568B"}),
            "branch_office_id": forms.TextInput(attrs={"placeholder": "KRA branch office ID / bhfId"}),
            "device_serial": forms.TextInput(attrs={"placeholder": "OSCU/VSCU serial number"}),
            "device_name": forms.TextInput(attrs={"placeholder": "Main eTIMS device"}),
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        if organization:
            self.fields["company"].queryset = Company.objects.filter(organization=organization, is_active=True)
            self.fields["location"].queryset = Location.objects.filter(organization=organization, is_active=True)
        else:
            self.fields["company"].queryset = Company.objects.none()
            self.fields["location"].queryset = Location.objects.none()
        self.fields["company"].required = False
        self.fields["location"].required = False

    def clean_taxpayer_pin(self):
        return self.cleaned_data["taxpayer_pin"].strip().upper()

    def clean_branch_office_id(self):
        return self.cleaned_data["branch_office_id"].strip()

    def clean(self):
        cleaned = super().clean()
        company = cleaned.get("company")
        location = cleaned.get("location")
        if location and company and location.branch.company_id != company.id:
            self.add_error("location", "The selected location must belong to the selected company.")
        if cleaned.get("status") == FiscalDeviceStatus.ACTIVE and cleaned.get("environment") != "production":
            self.add_error("status", "Only production eTIMS devices should be marked active. Use sandbox testing for test devices.")
        return cleaned
