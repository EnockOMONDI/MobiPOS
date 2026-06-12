from django import forms
from django.contrib.auth import get_user_model

from apps.organizations.models import MembershipStatus


class CommissionPayoutForm(forms.Form):
    agent = forms.ModelChoiceField(queryset=get_user_model().objects.none())
    period_start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    period_end = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            self.fields["agent"].queryset = get_user_model().objects.filter(
                memberships__organization=organization,
                memberships__status=MembershipStatus.ACTIVE,
            ).distinct()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("period_start") and cleaned.get("period_end") and cleaned["period_start"] > cleaned["period_end"]:
            raise forms.ValidationError("Period start must be on or before period end.")
        return cleaned
