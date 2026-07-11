from django import forms

from apps.catalog.models import Product
from apps.inventory.models import StockUnit
from apps.organizations.models import Location
from apps.organizations.permissions import accessible_locations_for


class TransferForm(forms.Form):
    MAX_LINES = 5
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
            self.fields["stock_unit"].queryset = StockUnit.objects.filter(
                organization=organization, status="available", location__in=locations
            )
            for index in range(2, self.MAX_LINES + 1):
                self.fields[f"product_{index}"] = forms.ModelChoiceField(
                    queryset=self.fields["product"].queryset, required=False, label=f"Additional item {index}"
                )
                self.fields[f"stock_unit_{index}"] = forms.ModelChoiceField(
                    queryset=self.fields["stock_unit"].queryset, required=False, label=f"Item {index} serial / IMEI"
                )
                self.fields[f"quantity_{index}"] = forms.DecimalField(
                    min_value=1, decimal_places=3, required=False, label=f"Item {index} quantity"
                )
        self.additional_line_groups = [
            (self[f"product_{index}"], self[f"stock_unit_{index}"], self[f"quantity_{index}"])
            for index in range(2, self.MAX_LINES + 1)
        ] if organization else []

    def clean(self):
        cleaned = super().clean()
        source, destination = cleaned.get("source"), cleaned.get("destination")
        if source and destination and source == destination:
            raise forms.ValidationError("Source and destination must be different.")
        lines = []
        seen_serials = set()
        for index in range(1, self.MAX_LINES + 1):
            suffix = "" if index == 1 else f"_{index}"
            product = cleaned.get(f"product{suffix}")
            stock_unit = cleaned.get(f"stock_unit{suffix}")
            quantity = cleaned.get(f"quantity{suffix}")
            if not product:
                if stock_unit or quantity is not None:
                    self.add_error(f"product{suffix}", "Choose a product for this line.")
                continue
            if quantity is None:
                self.add_error(f"quantity{suffix}", "Enter a quantity.")
                continue
            if product.is_serialized:
                if not stock_unit or stock_unit.product_id != product.id or not source or stock_unit.location_id != source.id:
                    self.add_error(f"stock_unit{suffix}", "Select an available serial at the source location.")
                elif stock_unit.id in seen_serials:
                    self.add_error(f"stock_unit{suffix}", "This serial is already included.")
                if quantity != 1:
                    self.add_error(f"quantity{suffix}", "Serialized transfers move one unit per line.")
            elif stock_unit:
                self.add_error(f"stock_unit{suffix}", "Do not select a serial for a quantity-based product.")
            seen_serials.add(stock_unit.id if stock_unit else None)
            lines.append({"product": product, "stock_unit": stock_unit, "quantity": quantity})
        cleaned["lines"] = lines
        return cleaned
