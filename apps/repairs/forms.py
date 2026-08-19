import uuid

from django import forms

from apps.contacts.models import Contact
from apps.inventory.models import StockUnit
from apps.catalog.models import Product
from apps.organizations.models import Branch, Location
from apps.organizations.permissions import accessible_branches_for, accessible_locations_for
from .models import RepairPaymentMethod, RepairStatus, WarrantyType


class RepairTicketForm(forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none())
    customer = forms.ModelChoiceField(queryset=Contact.objects.none())
    stock_unit = forms.ModelChoiceField(queryset=StockUnit.objects.none(), required=False)
    issue = forms.CharField(widget=forms.Textarea)
    warranty_type = forms.ChoiceField(choices=WarrantyType.choices, required=False, initial=WarrantyType.NONE)
    quoted_amount = forms.DecimalField(min_value=0, decimal_places=2)

    def __init__(self, *args, organization=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            branches = accessible_branches_for(user, organization)
            self.fields["branch"].queryset = branches
            self.fields["customer"].queryset = Contact.objects.filter(organization=organization, contact_type__in=("customer", "both"))
            self.fields["stock_unit"].queryset = StockUnit.objects.filter(
                organization=organization,
                product__is_serialized=True,
                status__in=("sold", "returned", "warranty_repair"),
            ).select_related("product")

    def clean(self):
        cleaned = super().clean()
        customer = cleaned.get("customer")
        stock_unit = cleaned.get("stock_unit")
        if stock_unit and customer:
            from apps.sales.models import SaleLine

            belongs_to_customer = SaleLine.objects.filter(
                stock_unit=stock_unit,
                sale__customer=customer,
                sale__organization=customer.organization,
                sale__status__in=("completed", "part_paid", "paid", "returned"),
            ).exists()
            if not belongs_to_customer:
                self.add_error("stock_unit", "Select a device previously sold to this customer.")
        warranty_type = cleaned.get("warranty_type") or WarrantyType.NONE
        cleaned["warranty_type"] = warranty_type
        cleaned["warranty"] = warranty_type != WarrantyType.NONE
        return cleaned


class RepairStatusForm(forms.Form):
    request_id = forms.UUIDField(widget=forms.HiddenInput)
    status = forms.ChoiceField(choices=())
    diagnosis = forms.CharField(widget=forms.Textarea, required=False)
    warranty_type = forms.ChoiceField(choices=WarrantyType.choices)
    warranty_decision_notes = forms.CharField(widget=forms.Textarea, required=False)
    quoted_amount = forms.DecimalField(min_value=0, decimal_places=2)
    transition_notes = forms.CharField(widget=forms.Textarea, required=False)

    def __init__(self, *args, current_status=None, allowed_statuses=(), **kwargs):
        super().__init__(*args, **kwargs)
        labels = dict(RepairStatus.choices)
        self.fields["status"].choices = [(status, labels[status]) for status in allowed_statuses]
        self.fields["status"].label = "Next stage"
        if not self.is_bound:
            self.initial.setdefault("request_id", uuid.uuid4())
        self.current_status = current_status


class RepairPartForm(forms.Form):
    request_id = forms.UUIDField(widget=forms.HiddenInput)
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    stock_unit = forms.ModelChoiceField(queryset=StockUnit.objects.none(), required=False, label="Serial / IMEI")
    location = forms.ModelChoiceField(queryset=Location.objects.none())
    quantity = forms.DecimalField(min_value=1, decimal_places=3)

    def __init__(self, *args, organization=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial.setdefault("request_id", uuid.uuid4())
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


class RepairPartReversalForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea, min_length=5)


class RepairPaymentForm(forms.Form):
    request_id = forms.UUIDField(widget=forms.HiddenInput)
    amount = forms.DecimalField(min_value=0.01, decimal_places=2)
    method = forms.ChoiceField(choices=RepairPaymentMethod.choices)
    reference = forms.CharField(max_length=120, required=False)
    notes = forms.CharField(widget=forms.Textarea, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial.setdefault("request_id", uuid.uuid4())

    def clean(self):
        cleaned = super().clean()
        method = cleaned.get("method")
        reference = (cleaned.get("reference") or "").strip()
        if method and method != RepairPaymentMethod.CASH and not reference:
            self.add_error("reference", "Enter the provider or bank payment reference.")
        cleaned["reference"] = reference
        return cleaned


class RepairPaymentReversalForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea, min_length=5)
