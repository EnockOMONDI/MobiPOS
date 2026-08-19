from django import forms
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm


class EmailAuthenticationForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Please enter the correct email address and password. Both fields may be case-sensitive.",
    }
    username = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}),
    )


class AccountSetupPasswordForm(SetPasswordForm):
    accept_terms = forms.BooleanField(
        label="I understand this password is personal and must not be shared.",
    )


class MFAConfirmForm(forms.Form):
    token = forms.CharField(max_length=8, min_length=6, label="Authentication code")


class RecoveryCodeForm(forms.Form):
    code = forms.CharField(max_length=32, label="Recovery code")
