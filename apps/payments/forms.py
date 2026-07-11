from django import forms

from .models import PaymentMethod


class AdditionalPaymentForm(forms.Form):
    method = forms.ChoiceField(choices=[choice for choice in PaymentMethod.choices if choice[0] != PaymentMethod.CREDIT])
    amount = forms.DecimalField(min_value=0.01, decimal_places=2)
    provider_reference = forms.CharField(max_length=120, required=False)

    def clean(self):
        cleaned = super().clean()
        method = cleaned.get("method")
        reference = (cleaned.get("provider_reference") or "").strip()
        if method in {PaymentMethod.MPESA, PaymentMethod.CARD, PaymentMethod.BANK} and not reference:
            self.add_error("provider_reference", f"A {PaymentMethod(method).label} reference is required.")
        return cleaned
