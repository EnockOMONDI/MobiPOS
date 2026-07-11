from django import forms

from apps.catalog.models import Product
from apps.contacts.models import Contact
from apps.inventory.models import StockUnit
from apps.payments.models import PaymentMethod
from apps.sales.models import CreditAgency, SaleChannel
from .models import CashMovementType


class PaymentAllocationForm(forms.Form):
    PAYMENT_FIELDS = {
        PaymentMethod.CASH: ("cash_amount", None),
        PaymentMethod.MPESA: ("mpesa_amount", "mpesa_reference"),
        PaymentMethod.CARD: ("card_amount", "card_reference"),
        PaymentMethod.BANK: ("bank_amount", "bank_reference"),
    }

    payment_method = forms.ChoiceField(
        choices=PaymentMethod.choices,
        required=False,
        widget=forms.HiddenInput,
    )
    amount_received = forms.DecimalField(
        min_value=0,
        decimal_places=2,
        required=False,
        widget=forms.HiddenInput,
    )
    cash_amount = forms.DecimalField(min_value=0, decimal_places=2, required=False, label="Cash")
    mpesa_amount = forms.DecimalField(min_value=0, decimal_places=2, required=False, label="M-Pesa")
    mpesa_reference = forms.CharField(max_length=120, required=False, label="M-Pesa reference")
    card_amount = forms.DecimalField(min_value=0, decimal_places=2, required=False, label="Card")
    card_reference = forms.CharField(max_length=120, required=False, label="Card reference")
    bank_amount = forms.DecimalField(min_value=0, decimal_places=2, required=False, label="Bank transfer")
    bank_reference = forms.CharField(max_length=120, required=False, label="Bank reference")
    credit_amount = forms.DecimalField(
        min_value=0,
        decimal_places=2,
        required=False,
        label="Customer credit",
    )

    def clean(self):
        cleaned = super().clean()
        allocations = []
        split_amount = 0
        for method, (amount_field, reference_field) in self.PAYMENT_FIELDS.items():
            amount = cleaned.get(amount_field) or 0
            reference = (cleaned.get(reference_field) or "").strip() if reference_field else ""
            if amount and reference_field and not reference:
                self.add_error(reference_field, f"A {method.label} reference is required.")
            if amount:
                allocations.append({
                    "method": method,
                    "amount": amount,
                    "provider_reference": reference,
                })
                split_amount += amount

        credit_amount = cleaned.get("credit_amount") or 0
        if not allocations and not credit_amount:
            legacy_amount = cleaned.get("amount_received") or 0
            legacy_method = cleaned.get("payment_method")
            if legacy_method == PaymentMethod.CREDIT:
                credit_amount = legacy_amount
            elif legacy_amount and legacy_method:
                allocations.append({
                    "method": legacy_method,
                    "amount": legacy_amount,
                    "provider_reference": "",
                })
                split_amount = legacy_amount

        cleaned["payment_allocations"] = allocations
        cleaned["paid_amount"] = split_amount
        cleaned["credit_amount"] = credit_amount
        return cleaned


class CheckoutForm(PaymentAllocationForm):
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    stock_unit = forms.ModelChoiceField(queryset=StockUnit.objects.none(), required=False, label="Serial / IMEI")
    customer = forms.ModelChoiceField(queryset=Contact.objects.none(), required=False)
    quantity = forms.DecimalField(min_value=1, decimal_places=3, initial=1)
    sale_channel = forms.ChoiceField(choices=SaleChannel.choices, initial=SaleChannel.CASH, required=False, label="Sale type")
    credit_agency = forms.ChoiceField(choices=CreditAgency.choices, required=False, label="Credit agency")
    customer_national_id = forms.CharField(max_length=80, required=False, label="Customer national ID")
    next_of_kin_name = forms.CharField(max_length=200, required=False, label="Next of kin name")
    next_of_kin_phone = forms.CharField(max_length=32, required=False, label="Next of kin phone")

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
        if cleaned.get("sale_channel") == SaleChannel.CREDIT:
            if not cleaned.get("customer"):
                self.add_error("customer", "A customer is required for credit sales.")
            if not cleaned.get("credit_agency"):
                self.add_error("credit_agency", "Select the credit agency for credit sales.")
        return cleaned


class CloseSessionForm(forms.Form):
    actual_cash = forms.DecimalField(min_value=0, decimal_places=2)
    closing_note = forms.CharField(widget=forms.Textarea, required=False)


class OpenSessionForm(forms.Form):
    opening_float = forms.DecimalField(min_value=0, decimal_places=2, initial=0)


class CashMovementForm(forms.Form):
    movement_type = forms.ChoiceField(choices=CashMovementType.choices)
    amount = forms.DecimalField(min_value=0.01, decimal_places=2)
    reason = forms.CharField(max_length=255)


class CartItemForm(forms.Form):
    lookup = forms.CharField(
        max_length=200,
        required=False,
        label="Scan or search",
        help_text="Scan an IMEI/barcode or type a product name/SKU.",
    )
    product = forms.ModelChoiceField(queryset=Product.objects.none(), required=False)
    stock_unit = forms.ModelChoiceField(queryset=StockUnit.objects.none(), required=False, label="Serial / IMEI")
    quantity = forms.DecimalField(min_value=1, decimal_places=3, initial=1)
    discount = forms.DecimalField(min_value=0, decimal_places=2, initial=0, required=False)

    def __init__(self, *args, organization=None, location=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.location = location
        if organization:
            self.fields["product"].queryset = Product.objects.filter(
                organization=organization, is_sellable=True, is_active=True
            )
            self.fields["stock_unit"].queryset = StockUnit.objects.filter(
                organization=organization, location=location, status="available"
            )

    def clean(self):
        cleaned = super().clean()
        lookup = (cleaned.get("lookup") or "").strip()
        if lookup and self.organization:
            from django.db.models import Q
            unit = StockUnit.objects.filter(
                organization=self.organization,
                location=self.location,
                status="available",
            ).filter(Q(serial_number__iexact=lookup) | Q(secondary_serial__iexact=lookup)).select_related("product").first()
            if unit:
                cleaned["stock_unit"] = unit
                cleaned["product"] = unit.product
                cleaned["quantity"] = 1
            else:
                product = Product.objects.filter(
                    organization=self.organization,
                    is_sellable=True,
                    is_active=True,
                ).filter(
                    Q(sku__iexact=lookup) | Q(barcode__iexact=lookup) | Q(name__icontains=lookup)
                ).order_by("name").first()
                if product:
                    cleaned["product"] = product
        stock_unit = cleaned.get("stock_unit")
        product = cleaned.get("product")
        if stock_unit and not product:
            product = stock_unit.product
            cleaned["product"] = product
        quantity = cleaned.get("quantity") or 1
        cleaned["quantity"] = quantity
        discount = cleaned.get("discount") or 0
        cleaned["discount"] = discount
        if not product:
            self.add_error("product", "Select a product or scan an IMEI/barcode.")
            return cleaned
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


class CartCompleteForm(PaymentAllocationForm):
    customer = forms.ModelChoiceField(queryset=Contact.objects.none(), required=False)
    new_customer_name = forms.CharField(max_length=200, required=False, label="New customer name")
    new_customer_phone = forms.CharField(max_length=32, required=False, label="New customer phone")
    new_customer_email = forms.EmailField(required=False, label="New customer email")
    sale_channel = forms.ChoiceField(choices=SaleChannel.choices, initial=SaleChannel.CASH, required=False, label="Sale type")
    credit_agency = forms.ChoiceField(choices=CreditAgency.choices, required=False, label="Credit agency")
    customer_national_id = forms.CharField(max_length=80, required=False, label="Customer national ID")
    next_of_kin_name = forms.CharField(max_length=200, required=False, label="Next of kin name")
    next_of_kin_phone = forms.CharField(max_length=32, required=False, label="Next of kin phone")

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            self.fields["customer"].queryset = Contact.objects.filter(
                organization=organization, contact_type__in=("customer", "both"), is_active=True
            )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("customer") and cleaned.get("new_customer_name"):
            self.add_error("new_customer_name", "Choose an existing customer or create a new one, not both.")
        if (cleaned.get("new_customer_phone") or cleaned.get("new_customer_email")) and not cleaned.get("new_customer_name"):
            self.add_error("new_customer_name", "Enter the new customer's name.")
        if cleaned.get("sale_channel") == SaleChannel.CREDIT:
            if not cleaned.get("customer"):
                self.add_error("customer", "Choose an existing customer for credit sales.")
            if not cleaned.get("credit_agency"):
                self.add_error("credit_agency", "Select the credit agency for credit sales.")
        return cleaned
