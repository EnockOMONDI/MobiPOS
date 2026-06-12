from django import forms

from apps.catalog.models import Product
from apps.contacts.models import Contact
from apps.inventory.models import StockUnit
from apps.payments.models import PaymentMethod


class CheckoutForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    stock_unit = forms.ModelChoiceField(queryset=StockUnit.objects.none(), required=False, label="Serial / IMEI")
    customer = forms.ModelChoiceField(queryset=Contact.objects.none(), required=False)
    quantity = forms.DecimalField(min_value=1, decimal_places=3, initial=1)
    payment_method = forms.ChoiceField(choices=PaymentMethod.choices)
    amount_received = forms.DecimalField(min_value=0, decimal_places=2)

    def __init__(self, *args, organization=None, location=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            self.fields["product"].queryset = Product.objects.filter(
                organization=organization, is_sellable=True, is_active=True
            )
            self.fields["customer"].queryset = Contact.objects.filter(
                organization=organization, contact_type__in=("customer", "both"), is_active=True
            )
            self.fields["stock_unit"].queryset = StockUnit.objects.filter(
                organization=organization, location=location, status="available"
            )

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        stock_unit = cleaned.get("stock_unit")
        quantity = cleaned.get("quantity")
        if product and product.is_serialized:
            if not stock_unit or stock_unit.product_id != product.id:
                raise forms.ValidationError("Select an available serial / IMEI for this product.")
            if quantity != 1:
                raise forms.ValidationError("Serialized products must be sold one unit at a time.")
        elif stock_unit:
            raise forms.ValidationError("Do not select a serial for a quantity-based product.")
        return cleaned


class CloseSessionForm(forms.Form):
    actual_cash = forms.DecimalField(min_value=0, decimal_places=2)
    closing_note = forms.CharField(widget=forms.Textarea, required=False)


class CartItemForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    stock_unit = forms.ModelChoiceField(queryset=StockUnit.objects.none(), required=False, label="Serial / IMEI")
    quantity = forms.DecimalField(min_value=1, decimal_places=3, initial=1)
    discount = forms.DecimalField(min_value=0, decimal_places=2, initial=0, required=False)

    def __init__(self, *args, organization=None, location=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            self.fields["product"].queryset = Product.objects.filter(
                organization=organization, is_sellable=True, is_active=True
            )
            self.fields["stock_unit"].queryset = StockUnit.objects.filter(
                organization=organization, location=location, status="available"
            )

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        stock_unit = cleaned.get("stock_unit")
        quantity = cleaned.get("quantity")
        discount = cleaned.get("discount") or 0
        cleaned["discount"] = discount
        if product and product.is_serialized:
            if not stock_unit or stock_unit.product_id != product.id:
                raise forms.ValidationError("Select an available serial / IMEI for this product.")
            if quantity != 1:
                raise forms.ValidationError("Serialized products must be sold one unit at a time.")
        elif stock_unit:
            raise forms.ValidationError("Do not select a serial for a quantity-based product.")
        if product and quantity and discount > product.selling_price * quantity:
            self.add_error("discount", "Discount cannot exceed the gross line value.")
        return cleaned


class CartCompleteForm(forms.Form):
    customer = forms.ModelChoiceField(queryset=Contact.objects.none(), required=False)
    payment_method = forms.ChoiceField(choices=PaymentMethod.choices)
    amount_received = forms.DecimalField(min_value=0, decimal_places=2)

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            self.fields["customer"].queryset = Contact.objects.filter(
                organization=organization, contact_type__in=("customer", "both"), is_active=True
            )
