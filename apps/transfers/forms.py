from django import forms

from apps.catalog.models import Product
from apps.inventory.models import StockUnit
from apps.organizations.models import Location
from apps.organizations.permissions import accessible_locations_for


class TransferForm(forms.Form):
    source = forms.ModelChoiceField(queryset=Location.objects.none())
    destination = forms.ModelChoiceField(queryset=Location.objects.none())
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    stock_unit = forms.ModelChoiceField(queryset=StockUnit.objects.none(), required=False, label="Serial / IMEI")
    quantity = forms.DecimalField(min_value=1, decimal_places=3)
    notes = forms.CharField(widget=forms.Textarea, required=False)

    def __init__(self, *args, organization=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            locations = accessible_locations_for(user, organization)
            self.fields["source"].queryset = locations
            self.fields["destination"].queryset = locations
            self.fields["product"].queryset = Product.objects.filter(organization=organization, is_stocked=True, is_active=True)
            self.fields["stock_unit"].queryset = StockUnit.objects.filter(organization=organization, status="available")

    def clean(self):
        cleaned = super().clean()
        source, destination = cleaned.get("source"), cleaned.get("destination")
        product, stock_unit, quantity = cleaned.get("product"), cleaned.get("stock_unit"), cleaned.get("quantity")
        if source and destination and source == destination:
            raise forms.ValidationError("Source and destination must be different.")
        if product and product.is_serialized:
            if not stock_unit or stock_unit.product_id != product.id or stock_unit.location_id != source.id:
                raise forms.ValidationError("Select an available serial at the source location.")
            if quantity != 1:
                raise forms.ValidationError("Serialized transfers move one unit per line.")
        elif stock_unit:
            raise forms.ValidationError("Do not select a serial for a quantity-based product.")
        return cleaned
