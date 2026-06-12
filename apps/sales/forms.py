from django import forms


class ReturnRequestForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea)
    refund_amount = forms.DecimalField(min_value=0, decimal_places=2)
