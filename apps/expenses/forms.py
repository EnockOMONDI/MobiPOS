from django import forms

from apps.organizations.models import Branch
from apps.organizations.permissions import accessible_branches_for

from .models import ExpensePaymentMethod


class ExpenseForm(forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none())
    category = forms.CharField(max_length=100)
    description = forms.CharField(widget=forms.Textarea)
    amount = forms.DecimalField(min_value=0.01, decimal_places=2)
    incurred_on = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, organization=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            self.fields["branch"].queryset = accessible_branches_for(user, organization)


class ExpensePaymentForm(forms.Form):
    method = forms.ChoiceField(choices=ExpensePaymentMethod.choices)
    reference = forms.CharField(
        max_length=120,
        required=False,
        help_text="Required for M-Pesa, card and bank payments. Use the provider or bank reference.",
    )
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def clean(self):
        cleaned_data = super().clean()
        method = cleaned_data.get("method")
        reference = (cleaned_data.get("reference") or "").strip()
        if method != ExpensePaymentMethod.CASH and not reference:
            self.add_error("reference", "Enter the payment reference for this payment method.")
        cleaned_data["reference"] = reference
        return cleaned_data
