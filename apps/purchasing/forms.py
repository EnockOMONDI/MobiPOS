from django import forms

from apps.catalog.models import Product
from apps.contacts.models import Contact
from apps.organizations.models import Location
from apps.organizations.permissions import accessible_locations_for
from apps.inventory.models import StockUnit
from .models import PurchaseOrderLine


class PurchaseOrderForm(forms.Form):
    MAX_LINES = 5
    supplier = forms.ModelChoiceField(queryset=Contact.objects.none())
    destination = forms.ModelChoiceField(queryset=Location.objects.none())
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    quantity = forms.DecimalField(min_value=1, decimal_places=3)
    unit_cost = forms.DecimalField(min_value=0, decimal_places=2)
    notes = forms.CharField(widget=forms.Textarea, required=False)

    def __init__(self, *args, organization=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            self.fields["supplier"].queryset = Contact.objects.filter(
                organization=organization, contact_type__in=("supplier", "both"), is_active=True
            )
            self.fields["destination"].queryset = accessible_locations_for(user, organization)
            self.fields["product"].queryset = Product.objects.filter(
                organization=organization, is_purchasable=True, is_active=True
            )
            for index in range(2, self.MAX_LINES + 1):
                self.fields[f"product_{index}"] = forms.ModelChoiceField(
                    queryset=self.fields["product"].queryset,
                    required=False,
                    label=f"Additional item {index}",
                )
                self.fields[f"quantity_{index}"] = forms.DecimalField(
                    min_value=1, decimal_places=3, required=False, label=f"Item {index} quantity"
                )
                self.fields[f"unit_cost_{index}"] = forms.DecimalField(
                    min_value=0, decimal_places=2, required=False, label=f"Item {index} unit cost"
                )
        self.additional_line_groups = [
            (self[f"product_{index}"], self[f"quantity_{index}"], self[f"unit_cost_{index}"])
            for index in range(2, self.MAX_LINES + 1)
        ] if organization else []

    def clean(self):
        cleaned = super().clean()
        lines = []
        seen = set()
        for index in range(1, self.MAX_LINES + 1):
            suffix = "" if index == 1 else f"_{index}"
            product = cleaned.get(f"product{suffix}")
            quantity = cleaned.get(f"quantity{suffix}")
            unit_cost = cleaned.get(f"unit_cost{suffix}")
            if not product:
                if quantity is not None or unit_cost is not None:
                    self.add_error(f"product{suffix}", "Choose a product for this line.")
                continue
            if quantity is None:
                self.add_error(f"quantity{suffix}", "Enter a quantity.")
            if unit_cost is None:
                self.add_error(f"unit_cost{suffix}", "Enter a unit cost.")
            if product.id in seen:
                self.add_error(f"product{suffix}", "Combine duplicate products into one line.")
            seen.add(product.id)
            if quantity is not None and unit_cost is not None:
                lines.append({"product": product, "quantity": quantity, "unit_cost": unit_cost})
        cleaned["lines"] = lines
        return cleaned


class ReceivePurchaseLineForm(forms.Form):
    quantity = forms.DecimalField(min_value=1, decimal_places=3)
    serial_numbers = forms.CharField(
        required=False,
        widget=forms.Textarea,
        help_text="For serialized products, enter one serial / IMEI per line.",
    )
    damaged_quantity = forms.DecimalField(min_value=0, decimal_places=3, initial=0, required=False)
    close_with_discrepancy = forms.BooleanField(
        required=False,
        help_text="Use when this is the supplier's final delivery and expected stock is missing or damaged.",
    )
    discrepancy_reason = forms.CharField(widget=forms.Textarea, required=False)

    def serial_list(self):
        return [item.strip() for item in self.cleaned_data["serial_numbers"].splitlines() if item.strip()]

    def clean(self):
        cleaned = super().clean()
        cleaned["damaged_quantity"] = cleaned.get("damaged_quantity") or 0
        if cleaned.get("close_with_discrepancy") and not cleaned.get("discrepancy_reason"):
            self.add_error("discrepancy_reason", "Explain the receipt discrepancy.")
        return cleaned


class SupplierReturnForm(forms.Form):
    line = forms.ModelChoiceField(queryset=PurchaseOrderLine.objects.none(), label="Purchase line")
    stock_unit = forms.ModelChoiceField(queryset=StockUnit.objects.none(), required=False, label="Serial / IMEI")
    quantity = forms.DecimalField(min_value=1, decimal_places=3)
    reason = forms.CharField(widget=forms.Textarea)

    def __init__(self, *args, organization=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        locations = accessible_locations_for(user, organization)
        self.fields["line"].queryset = PurchaseOrderLine.objects.filter(
            organization=organization, order__destination__in=locations, received_quantity__gt=0
        )
        self.fields["stock_unit"].queryset = StockUnit.objects.filter(
            organization=organization, location__in=locations, status="available"
        )

    def clean(self):
        cleaned = super().clean()
        line, stock_unit, quantity = cleaned.get("line"), cleaned.get("stock_unit"), cleaned.get("quantity")
        if line and line.product.is_serialized:
            if not stock_unit or stock_unit.product_id != line.product_id or stock_unit.location_id != line.order.destination_id:
                raise forms.ValidationError("Select an available serial from the purchase destination.")
            if quantity != 1:
                raise forms.ValidationError("Serialized supplier returns must be one unit.")
        elif stock_unit:
            raise forms.ValidationError("Do not select a serial for a quantity-based item.")
        return cleaned
