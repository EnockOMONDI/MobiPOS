from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models import Q

from .models import Branch, Company, LocationType, Membership, Organization, Plan, Role

TENANT_ROLE_PERMISSION_CODES = (
    "catalog.add_brand",
    "catalog.add_category",
    "catalog.add_product",
    "catalog.change_product",
    "catalog.view_product",
    "contacts.add_contact",
    "contacts.change_contact",
    "contacts.view_contact",
    "expenses.add_expense",
    "expenses.view_expense",
    "inventory.add_stockadjustment",
    "inventory.view_stockbalance",
    "inventory.view_stockmovement",
    "inventory.view_stockunit",
    "payments.add_payment",
    "payments.view_payment",
    "pos.view_possession",
    "purchasing.add_purchaseorder",
    "purchasing.add_supplierreturn",
    "purchasing.change_purchaseorder",
    "purchasing.view_purchasediscrepancy",
    "purchasing.view_purchaseorder",
    "purchasing.view_supplierreturn",
    "repairs.add_repairticket",
    "repairs.change_repairticket",
    "repairs.view_repairticket",
    "sales.add_sale",
    "sales.add_salereturn",
    "sales.view_sale",
    "sales.view_salereturn",
    "transfers.add_stocktransfer",
    "transfers.change_stocktransfer",
    "transfers.view_stocktransfer",
    "commissions.view_commissionaccrual",
)


def tenant_role_permission_queryset():
    permission_filter = Q()
    for permission_code in TENANT_ROLE_PERMISSION_CODES:
        app_label, codename = permission_code.split(".", maxsplit=1)
        permission_filter |= Q(content_type__app_label=app_label, codename=codename)
    return Permission.objects.filter(permission_filter).select_related("content_type").order_by(
        "content_type__app_label", "codename"
    )


class OrganizationRegistrationForm(forms.Form):
    organization_name = forms.CharField(max_length=200)
    organization_slug = forms.SlugField(max_length=200)
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput, min_length=10)
    plan = forms.ModelChoiceField(queryset=Plan.objects.filter(is_active=True))

    def clean_organization_slug(self):
        slug = self.cleaned_data["organization_slug"]
        if Organization.objects.filter(slug=slug).exists():
            raise forms.ValidationError("This organization URL is already in use.")
        return slug

    def clean_username(self):
        username = self.cleaned_data["username"]
        if get_user_model().objects.filter(username=username).exists():
            raise forms.ValidationError("This username is already in use.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"]
        if get_user_model().objects.filter(email=email).exists():
            raise forms.ValidationError("This email is already in use.")
        return email


class TenantUserForm(forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    phone_number = forms.CharField(max_length=32, required=False)
    branches = forms.ModelMultipleChoiceField(queryset=Branch.objects.none())
    roles = forms.ModelMultipleChoiceField(queryset=Role.objects.none(), required=False)

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            self.fields["branches"].queryset = Branch.objects.filter(organization=organization, is_active=True)
            self.fields["roles"].queryset = Role.objects.filter(organization=organization, is_active=True)

    def clean_username(self):
        username = self.cleaned_data["username"]
        if get_user_model().objects.filter(username=username).exists():
            raise forms.ValidationError("This username is already in use.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"]
        if get_user_model().objects.filter(email=email).exists():
            raise forms.ValidationError("This email is already in use.")
        return email


class InvitationAcceptForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput, min_length=10)
    password_confirm = forms.CharField(widget=forms.PasswordInput, min_length=10)
    accept_terms = forms.BooleanField(label="I accept the organization policies and terms")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") and cleaned.get("password") != cleaned.get("password_confirm"):
            self.add_error("password_confirm", "Passwords do not match.")
        return cleaned


class MembershipAccessForm(forms.ModelForm):
    class Meta:
        model = Membership
        fields = ("roles", "branches", "status")
        widgets = {
            "roles": forms.CheckboxSelectMultiple,
            "branches": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["roles"].queryset = Role.objects.filter(
            organization=organization, is_active=True
        ).order_by("name")
        self.fields["branches"].queryset = Branch.objects.filter(
            organization=organization, is_active=True
        ).order_by("name")


class BranchCreateForm(forms.Form):
    company = forms.ModelChoiceField(queryset=Company.objects.none())
    name = forms.CharField(max_length=200)
    code = forms.CharField(max_length=32)
    email = forms.EmailField(required=False)
    phone_number = forms.CharField(max_length=32, required=False)

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            self.fields["company"].queryset = Company.objects.filter(organization=organization, is_active=True)

    def clean_code(self):
        code = self.cleaned_data["code"].upper()
        if self.fields["company"].queryset.filter(branches__code=code).exists():
            raise forms.ValidationError("This branch code is already in use.")
        return code


class LocationCreateForm(forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none())
    name = forms.CharField(max_length=200)
    code = forms.CharField(max_length=32)
    location_type = forms.ChoiceField(choices=LocationType.choices)

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            self.fields["branch"].queryset = Branch.objects.filter(organization=organization, is_active=True)

    def clean_code(self):
        from .models import Location
        code = self.cleaned_data["code"].upper()
        organizations = self.fields["branch"].queryset.values_list("organization_id", flat=True)
        if Location.objects.filter(organization_id__in=organizations, code=code).exists():
            raise forms.ValidationError("This location code is already in use.")
        return code


class RoleCreateForm(forms.Form):
    name = forms.CharField(max_length=100)
    code = forms.SlugField(max_length=100)
    description = forms.CharField(widget=forms.Textarea, required=False)
    permissions = forms.ModelMultipleChoiceField(
        queryset=tenant_role_permission_queryset()
    )

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization

    def clean_code(self):
        code = self.cleaned_data["code"]
        if Role.objects.filter(organization=self.organization, code=code).exists():
            raise forms.ValidationError("This role code is already in use.")
        return code
