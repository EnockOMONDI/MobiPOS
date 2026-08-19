from django import forms

from apps.catalog.models import Product
from apps.organizations.models import Location
from apps.organizations.permissions import accessible_locations_for
from config.uploads import validate_uploaded_content

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
    destination = forms.ModelChoiceField(
        queryset=Location.objects.none(),
        required=False,
        help_text="Required for a transfer. Choose where the device should move.",
    )
    promotional_price = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        help_text="Required for a discount. This applies to this device only.",
    )
    campaign_name = forms.CharField(
        required=False,
        max_length=160,
        help_text="Required for a campaign. Example: August clearance campaign.",
    )
    valid_until = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Required for discounts and campaigns.",
    )

    def __init__(self, *args, organization=None, user=None, stock_unit=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.stock_unit = stock_unit
        if organization:
            locations = accessible_locations_for(user, organization).filter(is_active=True)
            if stock_unit and stock_unit.location_id:
                locations = locations.exclude(id=stock_unit.location_id)
            self.fields["destination"].queryset = locations

    def clean(self):
        cleaned = super().clean()
        action_type = cleaned.get("action_type")
        if action_type == AgedStockActionType.TRANSFER and not cleaned.get("destination"):
            self.add_error("destination", "Choose the destination for this transfer.")
        if action_type == AgedStockActionType.DISCOUNT:
            price = cleaned.get("promotional_price")
            if price is None:
                self.add_error("promotional_price", "Enter the approved promotional price.")
            elif self.stock_unit and price >= self.stock_unit.product.selling_price:
                self.add_error("promotional_price", "Promotional price must be below the normal selling price.")
        if action_type == AgedStockActionType.CAMPAIGN and not cleaned.get("campaign_name"):
            self.add_error("campaign_name", "Enter the campaign name.")
        if action_type in {AgedStockActionType.DISCOUNT, AgedStockActionType.CAMPAIGN} and not cleaned.get("valid_until"):
            self.add_error("valid_until", "Choose when this offer ends.")
        return cleaned


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
    MAX_UPLOAD_SIZE = 5 * 1024 * 1024
    ALLOWED_UPLOAD_EXTENSIONS = {".csv", ".xlsx", ".xlsm"}
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
        if not upload:
            return upload
        return validate_uploaded_content(
            upload,
            allowed_extensions=self.ALLOWED_UPLOAD_EXTENSIONS,
            max_size=self.MAX_UPLOAD_SIZE,
            label="Stock intake file",
        )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("serial_numbers") and not cleaned.get("csv_file"):
            raise forms.ValidationError("Paste serial numbers or upload a CSV file.")
        return cleaned
