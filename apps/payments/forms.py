from django import forms

from .models import PaymentMethod


class AdditionalPaymentForm(forms.Form):
    method = forms.ChoiceField(choices=[choice for choice in PaymentMethod.choices if choice[0] != PaymentMethod.CREDIT])
    amount = forms.DecimalField(min_value=0.01, decimal_places=2)
    provider_reference = forms.CharField(max_length=120, required=False)
