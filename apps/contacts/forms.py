from django import forms

from .models import Contact


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        exclude = ("organization",)
        widgets = {"address": forms.Textarea}

    def __init__(self, *args, organization=None, initial_type="", **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        if initial_type in {"customer", "supplier", "both"} and not self.is_bound:
            self.fields["contact_type"].initial = initial_type

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
