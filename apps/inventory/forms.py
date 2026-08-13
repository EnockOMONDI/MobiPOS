from django import forms

from apps.catalog.models import Product
from apps.organizations.permissions import accessible_locations_for

from .models import AgedStockActionType


class AgedStockActionForm(forms.Form):
    action_type = forms.ChoiceField(
        choices=AgedStockActionType.choices,
        label="Recommended action",
        help_text="Choose what the manager wants to do with this old stock.",
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Explain why this stock needs action. Example: This model has not moved in Westlands but sells faster at TRM.",
    )
    next_step = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Suggested next step",
        help_text="Optional. Example: Transfer to TRM, approve a 5% discount, or return to supplier if still eligible.",
    )


class StockAdjustmentForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    location = forms.ModelChoiceField(queryset=accessible_locations_for(None, None))
    quantity = forms.DecimalField(decimal_places=3, help_text="Use a positive number to add stock or a negative number to deduct.")
    reason = forms.CharField(widget=forms.Textarea)

    def __init__(self, *args, organization=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            self.fields["product"].queryset = Product.objects.filter(
                organization=organization, is_stocked=True, is_serialized=False, is_active=True
            )
            self.fields["location"].queryset = accessible_locations_for(user, organization)

    def clean_quantity(self):
        quantity = self.cleaned_data["quantity"]
        if quantity == 0:
            raise forms.ValidationError("Adjustment quantity cannot be zero.")
        return quantity


class StockReversalForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea)


class BatchSerializedIntakeForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    location = forms.ModelChoiceField(queryset=accessible_locations_for(None, None))
    unit_cost = forms.DecimalField(decimal_places=2, min_value=0, required=False)
    serial_numbers = forms.CharField(
        widget=forms.Textarea,
        required=False,
        label="IMEI / serial numbers",
        help_text="Paste or scan one device per line. Commas are also accepted.",
    )
    csv_file = forms.FileField(
        required=False,
        label="CSV or Excel upload",
        help_text="Optional .csv, .xlsx, or .xlsm file with serial_number and secondary_serial columns, or first two columns used as primary and secondary serials.",
    )
    reason = forms.CharField(
        widget=forms.Textarea,
        initial="Batch serialized stock intake",
    )

    def __init__(self, *args, organization=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        if organization:
            self.fields["product"].queryset = Product.objects.filter(
                organization=organization,
                is_stocked=True,
                is_serialized=True,
                is_active=True,
            )
            self.fields["location"].queryset = accessible_locations_for(user, organization)

    def clean_csv_file(self):
        upload = self.cleaned_data.get("csv_file")
        if upload and not upload.name.lower().endswith((".csv", ".xlsx", ".xlsm")):
            raise forms.ValidationError("Upload a CSV or Excel file.")
        return upload

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("serial_numbers") and not cleaned.get("csv_file"):
            raise forms.ValidationError("Paste serial numbers or upload a CSV file.")
        return cleaned
