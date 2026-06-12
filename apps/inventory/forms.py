from django import forms

from apps.catalog.models import Product
from apps.organizations.permissions import accessible_locations_for


class StockAdjustmentForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    location = forms.ModelChoiceField(queryset=accessible_locations_for(None, None))
    quantity = forms.DecimalField(decimal_places=3, help_text="Use a positive number to add stock or a negative number to deduct.")
    reason = forms.CharField(widget=forms.Textarea)

    def __init__(self, *args, organization=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            self.fields["product"].queryset = Product.objects.filter(
                organization=organization, is_stocked=True, is_serialized=False, is_active=True
            )
            self.fields["location"].queryset = accessible_locations_for(user, organization)

    def clean_quantity(self):
        quantity = self.cleaned_data["quantity"]
        if quantity == 0:
            raise forms.ValidationError("Adjustment quantity cannot be zero.")
        return quantity


class StockReversalForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea)
