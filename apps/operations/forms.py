from decimal import Decimal

from django import forms
from django.utils.dateparse import parse_date

from apps.organizations.models import Branch, Role

from .models import ApprovalPolicy, PayablePaymentMethod


class InstallmentScheduleForm(forms.Form):
    schedule = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="Enter one installment per line as YYYY-MM-DD, amount.",
    )

    def __init__(self, *args, receivable=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.receivable = receivable

    def clean_schedule(self):
        entries = []
        for line_number, raw_line in enumerate(self.cleaned_data["schedule"].splitlines(), start=1):
            parts = [part.strip() for part in raw_line.split(",", maxsplit=1)]
            if len(parts) != 2 or not parse_date(parts[0]):
                raise forms.ValidationError(f"Line {line_number} must use YYYY-MM-DD, amount.")
            try:
                amount = Decimal(parts[1])
            except Exception as error:
                raise forms.ValidationError(f"Line {line_number} has an invalid amount.") from error
            if amount <= 0:
                raise forms.ValidationError(f"Line {line_number} amount must be greater than zero.")
            entries.append((parse_date(parts[0]), amount))
        if not entries:
            raise forms.ValidationError("Add at least one installment.")
        if self.receivable and sum((amount for _, amount in entries), Decimal("0")) != self.receivable.outstanding_amount:
            raise forms.ValidationError("Installment amounts must equal the current outstanding balance.")
        return entries


class PayablePaymentForm(forms.Form):
    request_id = forms.UUIDField(widget=forms.HiddenInput)
    amount = forms.DecimalField(min_value=Decimal("0.01"), decimal_places=2)
    method = forms.ChoiceField(choices=PayablePaymentMethod.choices)
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
        if method and method != PayablePaymentMethod.CASH and not reference:
            self.add_error("reference", "Enter the payment reference for this payment method.")
        cleaned_data["reference"] = reference
        return cleaned_data


class PayablePaymentReversalForm(forms.Form):
    reason = forms.CharField(
        min_length=5,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Explain why this payment record must be reversed.",
    )


class ApprovalPolicyForm(forms.ModelForm):
    class Meta:
        model = ApprovalPolicy
        fields = (
            "name",
            "request_type",
            "branch",
            "minimum_amount",
            "approver_roles",
            "require_separate_approver",
            "is_active",
        )
        widgets = {"approver_roles": forms.CheckboxSelectMultiple}

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.fields["branch"].queryset = Branch.objects.filter(organization=organization, is_active=True)
        self.fields["approver_roles"].queryset = Role.objects.filter(organization=organization, is_active=True)

    def save(self, commit=True):
        self.instance.organization = self.organization
        return super().save(commit=commit)
