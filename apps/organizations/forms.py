from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models import Q

from .models import AgentDocument, AgentDocumentType, AgentProfile, AgentProfileStatus, AgentProfileType, Branch, Company, LocationType, Membership, Organization, Plan, Role

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
    "organizations.add_agentprofile",
    "inventory.add_stockadjustment",
    "inventory.view_stockbalance",
    "inventory.view_stockmovement",
    "inventory.view_stockunit",
    "organizations.view_activity_report",
    "organizations.view_operational_report",
    "organizations.view_retail_analytics",
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
    enable_stock_custody = forms.BooleanField(
        required=False,
        label="Create agent stock custody location",
        help_text="Use this when the user will hold phones or accessories assigned from a warehouse.",
    )
    custody_branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        required=False,
        label="Custody branch",
        help_text="The branch that owns this user's agent stock location.",
    )
    create_agent_profile = forms.BooleanField(
        required=False,
        label="Create agent profile",
        help_text="Use this for field agents, DSAs, or sales representatives who need compliance and hierarchy tracking.",
    )
    agent_profile_type = forms.ChoiceField(choices=AgentProfileType.choices, required=False, label="Agent type")
    agent_branch = forms.ModelChoiceField(queryset=Branch.objects.none(), required=False, label="Agent branch")
    agent_supervisor = forms.ModelChoiceField(
        queryset=AgentProfile.objects.none(),
        required=False,
        label="Supervising agent",
        help_text="Required for DSA profiles where a supervising agent is known.",
    )
    legal_name = forms.CharField(max_length=255, required=False, label="Legal name")
    national_id_number = forms.CharField(max_length=80, required=False, label="National ID number")
    registration_notes = forms.CharField(widget=forms.Textarea, required=False)

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        if organization:
            branch_queryset = Branch.objects.filter(organization=organization, is_active=True)
            self.fields["branches"].queryset = branch_queryset
            self.fields["custody_branch"].queryset = branch_queryset
            self.fields["agent_branch"].queryset = branch_queryset
            self.fields["agent_supervisor"].queryset = AgentProfile.objects.filter(
                organization=organization,
                profile_type=AgentProfileType.AGENT,
                status__in=(AgentProfileStatus.PENDING, AgentProfileStatus.ACTIVE),
            ).select_related("membership__user", "branch")
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

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("enable_stock_custody"):
            custody_branch = cleaned.get("custody_branch")
            branches = cleaned.get("branches")
            if not custody_branch:
                self.add_error("custody_branch", "Choose the branch for this user's stock custody location.")
            elif branches is not None and custody_branch not in branches:
                self.add_error("custody_branch", "The custody branch must be included in the user's assigned branches.")
        if cleaned.get("create_agent_profile"):
            branches = cleaned.get("branches")
            agent_branch = cleaned.get("agent_branch")
            profile_type = cleaned.get("agent_profile_type") or AgentProfileType.AGENT
            if not agent_branch:
                self.add_error("agent_branch", "Choose the agent branch.")
            elif branches is not None and agent_branch not in branches:
                self.add_error("agent_branch", "The agent branch must be included in the user's assigned branches.")
            if profile_type == AgentProfileType.DSA and not cleaned.get("agent_supervisor"):
                self.add_error("agent_supervisor", "Choose the supervising agent for this DSA.")
            if not cleaned.get("legal_name"):
                first_name = cleaned.get("first_name", "")
                last_name = cleaned.get("last_name", "")
                cleaned["legal_name"] = f"{first_name} {last_name}".strip()
            if self.organization and cleaned.get("national_id_number") and AgentProfile.objects.filter(
                organization=self.organization,
                national_id_number=cleaned["national_id_number"],
            ).exists():
                self.add_error("national_id_number", "This national ID is already linked to an agent profile.")
        return cleaned


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
    enable_stock_custody = forms.BooleanField(
        required=False,
        label="Create or keep agent stock custody location",
        help_text="Provision a searchable stock location for devices assigned to this user.",
    )
    custody_branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        required=False,
        label="Custody branch",
    )
    create_agent_profile = forms.BooleanField(
        required=False,
        label="Create or update agent profile",
        help_text="Tracks agent status, branch, legal identity, and supervisor hierarchy.",
    )
    agent_profile_type = forms.ChoiceField(choices=AgentProfileType.choices, required=False, label="Agent type")
    agent_branch = forms.ModelChoiceField(queryset=Branch.objects.none(), required=False, label="Agent branch")
    agent_status = forms.ChoiceField(choices=AgentProfileStatus.choices, required=False, label="Agent status")
    agent_supervisor = forms.ModelChoiceField(
        queryset=AgentProfile.objects.none(),
        required=False,
        label="Supervising agent",
    )
    legal_name = forms.CharField(max_length=255, required=False, label="Legal name")
    national_id_number = forms.CharField(max_length=80, required=False, label="National ID number")
    registration_notes = forms.CharField(widget=forms.Textarea, required=False)

    class Meta:
        model = Membership
        fields = ("roles", "branches", "status")
        widgets = {
            "roles": forms.CheckboxSelectMultiple,
            "branches": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        branch_queryset = Branch.objects.filter(
            organization=organization, is_active=True
        ).order_by("name")
        self.fields["roles"].queryset = Role.objects.filter(
            organization=organization, is_active=True
        ).order_by("name")
        self.fields["branches"].queryset = branch_queryset
        self.fields["custody_branch"].queryset = branch_queryset
        self.fields["agent_branch"].queryset = branch_queryset
        self.fields["agent_supervisor"].queryset = AgentProfile.objects.filter(
            organization=organization,
            profile_type=AgentProfileType.AGENT,
            status__in=(AgentProfileStatus.PENDING, AgentProfileStatus.ACTIVE),
        ).exclude(membership=self.instance).select_related("membership__user", "branch")
        if self.instance and self.instance.pk:
            custody_location = self.instance.custody_locations.filter(
                organization=organization,
                location_type=LocationType.AGENT,
                is_active=True,
            ).select_related("branch").first()
            if custody_location:
                self.fields["enable_stock_custody"].initial = True
                self.fields["custody_branch"].initial = custody_location.branch_id
            try:
                agent_profile = self.instance.agent_profile
            except AgentProfile.DoesNotExist:
                agent_profile = None
            if agent_profile:
                self.fields["create_agent_profile"].initial = True
                self.fields["agent_profile_type"].initial = agent_profile.profile_type
                self.fields["agent_branch"].initial = agent_profile.branch_id
                self.fields["agent_status"].initial = agent_profile.status
                self.fields["agent_supervisor"].initial = agent_profile.supervisor_id
                self.fields["legal_name"].initial = agent_profile.legal_name
                self.fields["national_id_number"].initial = agent_profile.national_id_number
                self.fields["registration_notes"].initial = agent_profile.registration_notes
            else:
                user = self.instance.user
                self.fields["legal_name"].initial = user.get_full_name() or user.get_username()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("enable_stock_custody"):
            custody_branch = cleaned.get("custody_branch")
            branches = cleaned.get("branches")
            if not custody_branch:
                self.add_error("custody_branch", "Choose the branch for this user's stock custody location.")
            elif branches is not None and custody_branch not in branches:
                self.add_error("custody_branch", "The custody branch must be included in the user's assigned branches.")
        if cleaned.get("create_agent_profile"):
            branches = cleaned.get("branches")
            agent_branch = cleaned.get("agent_branch")
            profile_type = cleaned.get("agent_profile_type") or AgentProfileType.AGENT
            if not cleaned.get("agent_status"):
                cleaned["agent_status"] = AgentProfileStatus.PENDING
            if not agent_branch:
                self.add_error("agent_branch", "Choose the agent branch.")
            elif branches is not None and agent_branch not in branches:
                self.add_error("agent_branch", "The agent branch must be included in the user's assigned branches.")
            if profile_type == AgentProfileType.DSA and not cleaned.get("agent_supervisor"):
                self.add_error("agent_supervisor", "Choose the supervising agent for this DSA.")
            if not cleaned.get("legal_name"):
                user = self.instance.user
                cleaned["legal_name"] = user.get_full_name() or user.get_username()
            if self.organization and cleaned.get("national_id_number"):
                conflicts = AgentProfile.objects.filter(
                    organization=self.organization,
                    national_id_number=cleaned["national_id_number"],
                )
                if self.instance and self.instance.pk:
                    conflicts = conflicts.exclude(membership=self.instance)
                if conflicts.exists():
                    self.add_error("national_id_number", "This national ID is already linked to an agent profile.")
        return cleaned


class AgentDocumentUploadForm(forms.Form):
    document_type = forms.ChoiceField(choices=AgentDocumentType.choices)
    file = forms.FileField(
        label="Document file",
        help_text="Upload a PDF, JPG, JPEG, or PNG file up to 10 MB. On mobile, use the camera option to capture an ID photo directly.",
        widget=forms.ClearableFileInput(attrs={
            "accept": "application/pdf,image/jpeg,image/png",
            "capture": "environment",
        }),
    )
    notes = forms.CharField(widget=forms.Textarea, required=False)

    allowed_extensions = {".pdf", ".jpg", ".jpeg", ".png"}
    allowed_content_types = {"application/pdf", "image/jpeg", "image/png"}
    max_size = 10 * 1024 * 1024

    def clean_file(self):
        upload = self.cleaned_data["file"]
        name = upload.name.lower()
        if not any(name.endswith(extension) for extension in self.allowed_extensions):
            raise forms.ValidationError("Upload a PDF, JPG, JPEG, or PNG file.")
        content_type = getattr(upload, "content_type", "")
        if content_type and content_type not in self.allowed_content_types:
            raise forms.ValidationError("Unsupported document type.")
        if upload.size > self.max_size:
            raise forms.ValidationError("Document must be 10 MB or smaller.")
        return upload


class AgentProfileDecisionForm(forms.Form):
    notes = forms.CharField(
        widget=forms.Textarea,
        required=False,
        label="Decision notes",
        help_text="Record why this decision was made for future audit review.",
    )


class AgentDSARegistrationForm(forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    phone_number = forms.CharField(max_length=32, required=False)
    branch = forms.ModelChoiceField(queryset=Branch.objects.none(), label="DSA branch")
    legal_name = forms.CharField(max_length=255, required=False, label="Legal name")
    national_id_number = forms.CharField(max_length=80, required=False, label="National ID number")
    registration_notes = forms.CharField(widget=forms.Textarea, required=False)

    def __init__(self, *args, organization=None, supervising_membership=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.supervising_membership = supervising_membership
        if organization and supervising_membership:
            self.fields["branch"].queryset = supervising_membership.branches.filter(
                organization=organization,
                is_active=True,
            ).order_by("name")

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

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("legal_name"):
            cleaned["legal_name"] = f"{cleaned.get('first_name', '')} {cleaned.get('last_name', '')}".strip()
        if self.organization and cleaned.get("national_id_number") and AgentProfile.objects.filter(
            organization=self.organization,
            national_id_number=cleaned["national_id_number"],
        ).exists():
            self.add_error("national_id_number", "This national ID is already linked to an agent profile.")
        return cleaned


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
