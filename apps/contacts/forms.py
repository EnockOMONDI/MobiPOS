from django import forms

from .models import Contact


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        exclude = ("organization",)
        labels = {
            "name": "Business or customer name",
            "phone_number": "Phone number",
            "email": "Email address",
            "tax_number": "Tax number / KRA PIN",
            "payment_terms_days": "Payment terms in days",
        }
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Demo Device Supplier"}),
            "phone_number": forms.TextInput(attrs={"placeholder": "e.g. 0725 435 536"}),
            "email": forms.EmailInput(attrs={"placeholder": "e.g. supplier@example.com"}),
            "tax_number": forms.TextInput(attrs={"placeholder": "e.g. P051234567A"}),
            "address": forms.Textarea(attrs={"placeholder": "e.g. Nairobi CBD, Kimathi Street"}),
            "payment_terms_days": forms.NumberInput(attrs={"placeholder": "e.g. 14"}),
            "credit_limit": forms.NumberInput(attrs={"placeholder": "e.g. 50000"}),
        }

    def __init__(self, *args, organization=None, initial_type="", **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        if initial_type in {"customer", "supplier", "both"} and not self.is_bound:
            self.fields["contact_type"].initial = initial_type
        self.fields["tax_number"].help_text = "For Kenyan suppliers or customers, enter the KRA PIN where available."
        self.fields["payment_terms_days"].help_text = "For suppliers, this controls payable follow-up. For credit customers, it supports statement aging."
        self.fields["credit_limit"].help_text = "Use 0 when the contact should not buy on credit."

    def _validate_unique_value(self, field, message):
        value = self.cleaned_data.get(field, "")
        if value and Contact.objects.filter(
            organization=self.organization,
            **{f"{field}__iexact": value},
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(message)
        return value

    def clean_phone_number(self):
        return self._validate_unique_value("phone_number", "This phone number is already used by another contact.")

    def clean_email(self):
        return self._validate_unique_value("email", "This email address is already used by another contact.")

    def clean_tax_number(self):
        return self._validate_unique_value("tax_number", "This tax number is already used by another contact.")
