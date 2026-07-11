from django import forms


class MFAConfirmForm(forms.Form):
    token = forms.CharField(max_length=8, min_length=6, label="Authentication code")


class RecoveryCodeForm(forms.Form):
    code = forms.CharField(max_length=32, label="Recovery code")
