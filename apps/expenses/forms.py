from django import forms

from apps.organizations.models import Branch
from apps.organizations.permissions import accessible_branches_for


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
