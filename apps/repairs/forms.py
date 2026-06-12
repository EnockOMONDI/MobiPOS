from django import forms

from apps.contacts.models import Contact
from apps.inventory.models import StockUnit
from apps.catalog.models import Product
from apps.organizations.models import Branch, Location
from apps.organizations.permissions import accessible_branches_for, accessible_locations_for
from .models import RepairStatus, WarrantyType


class RepairTicketForm(forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none())
    customer = forms.ModelChoiceField(queryset=Contact.objects.none())
    stock_unit = forms.ModelChoiceField(queryset=StockUnit.objects.none(), required=False)
    issue = forms.CharField(widget=forms.Textarea)
    warranty = forms.BooleanField(required=False)
    quoted_amount = forms.DecimalField(min_value=0, decimal_places=2)

    def __init__(self, *args, organization=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            branches = accessible_branches_for(user, organization)
            self.fields["branch"].queryset = branches
            self.fields["customer"].queryset = Contact.objects.filter(organization=organization, contact_type__in=("customer", "both"))
            self.fields["stock_unit"].queryset = StockUnit.objects.filter(
                organization=organization, location__branch__in=branches
            )


class RepairStatusForm(forms.Form):
    status = forms.ChoiceField(choices=RepairStatus.choices)
    diagnosis = forms.CharField(widget=forms.Textarea, required=False)
    warranty_type = forms.ChoiceField(choices=WarrantyType.choices)
    warranty_decision_notes = forms.CharField(widget=forms.Textarea, required=False)


class RepairPartForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    stock_unit = forms.ModelChoiceField(queryset=StockUnit.objects.none(), required=False, label="Serial / IMEI")
    location = forms.ModelChoiceField(queryset=Location.objects.none())
    quantity = forms.DecimalField(min_value=1, decimal_places=3)

    def __init__(self, *args, organization=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            locations = accessible_locations_for(user, organization)
            self.fields["product"].queryset = Product.objects.filter(organization=organization, is_stocked=True, is_active=True)
            self.fields["location"].queryset = locations
            self.fields["stock_unit"].queryset = StockUnit.objects.filter(
                organization=organization, location__in=locations, status="available"
            )

    def clean(self):
        cleaned = super().clean()
        product, stock_unit, quantity = cleaned.get("product"), cleaned.get("stock_unit"), cleaned.get("quantity")
        if product and product.is_serialized:
            if not stock_unit or stock_unit.product_id != product.id:
                raise forms.ValidationError("Select the serialized part being used.")
            if quantity != 1:
                raise forms.ValidationError("Serialized parts must be used one unit at a time.")
        elif stock_unit:
            raise forms.ValidationError("Do not select a serial for a quantity-based part.")
        return cleaned
