import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import models
from django_ckeditor_5.fields import CKEditor5Field


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class OrganizationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    CLOSED = "closed", "Closed"


class Organization(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    status = models.CharField(
        max_length=20,
        choices=OrganizationStatus.choices,
        default=OrganizationStatus.PENDING,
    )
    currency = models.CharField(max_length=3, default="KES")
    timezone = models.CharField(max_length=64, default="Africa/Nairobi")
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ("name",)
        permissions = [
            ("view_operational_report", "Can view operational report"),
            ("view_retail_analytics", "Can view retail analytics report"),
            ("view_activity_report", "Can view activity report"),
        ]

    def __str__(self):
        return self.name


class OrganizationOwnedModel(TimestampedModel):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)

    class Meta:
        abstract = True

    def clean(self):
        """Reject cross-tenant foreign keys in admin and explicit validation."""
        super().clean()
        if not self.organization_id:
            return
        for field in self._meta.fields:
            if not field.is_relation or not field.many_to_one:
                continue
            if not getattr(self, field.attname, None):
                continue
            related = getattr(self, field.name, None)
            related_organization_id = getattr(related, "organization_id", None)
            if related_organization_id and related_organization_id != self.organization_id:
                raise ValidationError(
                    {field.name: "Related record must belong to the same organization."}
                )


class Company(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=32)
    legal_name = models.CharField(max_length=255, blank=True)
    tax_number = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "code"), name="unique_company_code_per_org"
            )
        ]

    def __str__(self):
        return self.name


class Branch(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="branches")
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=32)
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "code"), name="unique_branch_code_per_org"
            )
        ]

    def clean(self):
        super().clean()
        if self.company_id and self.company.organization_id != self.organization_id:
            raise ValidationError("Branch and company must belong to the same organization.")

    def __str__(self):
        return self.name


class LocationType(models.TextChoices):
    WAREHOUSE = "warehouse", "Warehouse"
    POS = "pos", "POS location"
    AGENT = "agent", "Agent custody"
    IN_TRANSIT = "in_transit", "In transit"
    REPAIR = "repair", "Repair"


class Location(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="locations")
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=32)
    location_type = models.CharField(max_length=20, choices=LocationType.choices)
    custodian_membership = models.ForeignKey(
        "Membership",
        on_delete=models.PROTECT,
        related_name="custody_locations",
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "code"), name="unique_location_code_per_org"
            ),
            models.UniqueConstraint(
                fields=("organization", "custodian_membership"),
                condition=models.Q(location_type=LocationType.AGENT, is_active=True, custodian_membership__isnull=False),
                name="unique_active_agent_location_per_membership",
            ),
        ]

    def clean(self):
        super().clean()
        if self.branch_id and self.branch.organization_id != self.organization_id:
            raise ValidationError("Location and branch must belong to the same organization.")
        if self.custodian_membership_id:
            if self.custodian_membership.organization_id != self.organization_id:
                raise ValidationError("Custodian membership must belong to the same organization.")
            if self.branch_id and not self.custodian_membership.branches.filter(id=self.branch_id).exists():
                raise ValidationError("Custodian must be assigned to the location branch.")
            if self.location_type != LocationType.AGENT:
                raise ValidationError("Custodian membership is only valid for agent custody locations.")

    def __str__(self):
        return self.name


class Role(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=100)
    description = models.TextField(blank=True)
    permissions = models.ManyToManyField(Permission, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "code"), name="unique_role_code_per_org"
            )
        ]

    def __str__(self):
        return self.name


class MembershipStatus(models.TextChoices):
    INVITED = "invited", "Invited"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"


class Membership(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    roles = models.ManyToManyField(Role, blank=True, related_name="memberships")
    branches = models.ManyToManyField(Branch, blank=True, related_name="memberships")
    status = models.CharField(
        max_length=20, choices=MembershipStatus.choices, default=MembershipStatus.INVITED
    )
    is_owner = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "user"), name="unique_user_membership_per_org"
            )
        ]

    def __str__(self):
        return f"{self.user} at {self.organization}"


class AgentProfileType(models.TextChoices):
    AGENT = "agent", "Agent"
    DSA = "dsa", "Direct Sales Agent"


class AgentProfileStatus(models.TextChoices):
    PENDING = "pending", "Pending verification"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    REJECTED = "rejected", "Rejected"
    ARCHIVED = "archived", "Archived"


class AgentProfile(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    membership = models.OneToOneField(Membership, on_delete=models.PROTECT, related_name="agent_profile")
    profile_type = models.CharField(max_length=20, choices=AgentProfileType.choices, default=AgentProfileType.AGENT)
    status = models.CharField(max_length=20, choices=AgentProfileStatus.choices, default=AgentProfileStatus.PENDING)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="agent_profiles")
    supervisor = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="sub_agents",
        null=True,
        blank=True,
    )
    legal_name = models.CharField(max_length=255)
    national_id_number = models.CharField(max_length=80, blank=True)
    phone_number = models.CharField(max_length=32, blank=True)
    registration_notes = models.TextField(blank=True)
    verification_notes = models.TextField(blank=True)
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="registered_agent_profiles",
        null=True,
        blank=True,
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="verified_agent_profiles",
        null=True,
        blank=True,
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("legal_name",)
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "national_id_number"),
                condition=~models.Q(national_id_number=""),
                name="unique_agent_national_id_per_org",
            )
        ]

    def clean(self):
        super().clean()
        if self.membership_id and self.membership.organization_id != self.organization_id:
            raise ValidationError("Agent membership must belong to the same organization.")
        if self.branch_id:
            if self.branch.organization_id != self.organization_id:
                raise ValidationError("Agent branch must belong to the same organization.")
            if self.membership_id and not self.membership.branches.filter(id=self.branch_id).exists():
                raise ValidationError("Agent must be assigned to the selected branch.")
        if self.supervisor_id:
            if self.supervisor.organization_id != self.organization_id:
                raise ValidationError("Supervisor must belong to the same organization.")
            if self.supervisor_id == self.id:
                raise ValidationError("An agent cannot supervise their own profile.")
            if self.profile_type == AgentProfileType.AGENT:
                raise ValidationError("Only DSA profiles can have a supervising agent.")

    def __str__(self):
        return self.legal_name


class AgentDocumentType(models.TextChoices):
    NATIONAL_ID = "national_id", "National ID"
    AGENT_PHOTO = "agent_photo", "Agent photo"
    CONTRACT = "contract", "Contract"
    SUPPORTING = "supporting", "Supporting document"


def agent_document_upload_path(instance, filename):
    extension = Path(filename).suffix.lower()
    return f"organizations/{instance.organization_id}/agents/{instance.profile_id}/{uuid.uuid4()}{extension}"


class AgentDocument(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(AgentProfile, on_delete=models.PROTECT, related_name="documents")
    document_type = models.CharField(max_length=30, choices=AgentDocumentType.choices)
    file = models.FileField(upload_to=agent_document_upload_path, max_length=500)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_agent_documents",
    )

    class Meta:
        ordering = ("-created_at",)

    def clean(self):
        super().clean()
        if self.profile_id and self.profile.organization_id != self.organization_id:
            raise ValidationError("Agent document must belong to the same organization as the profile.")

    def __str__(self):
        return f"{self.get_document_type_display()} for {self.profile}"


class InvitationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    REVOKED = "revoked", "Revoked"
    EXPIRED = "expired", "Expired"


class Invitation(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    membership = models.OneToOneField(Membership, on_delete=models.CASCADE, related_name="invitation")
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sent_invitations"
    )
    status = models.CharField(max_length=20, choices=InvitationStatus.choices, default=InvitationStatus.PENDING)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"Invitation for {self.membership.user} to {self.organization}"


class Plan(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=100, unique=True)
    monthly_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    limits = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class SubscriptionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACTIVE = "active", "Active"
    GRACE = "grace", "Grace period"
    SUSPENDED = "suspended", "Suspended"
    CANCELLED = "cancelled", "Cancelled"


class Subscription(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.PENDING,
    )
    starts_on = models.DateField(null=True, blank=True)
    renews_on = models.DateField(null=True, blank=True)
    grace_ends_on = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.organization} - {self.plan}"


class SubscriptionInvoiceStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PAID = "paid", "Paid"
    OVERDUE = "overdue", "Overdue"
    VOID = "void", "Void"


class SubscriptionInvoice(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name="invoices")
    number = models.CharField(max_length=40, unique=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=20, choices=SubscriptionInvoiceStatus.choices, default=SubscriptionInvoiceStatus.PENDING)
    due_on = models.DateField()
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_reference = models.CharField(max_length=120, blank=True)


class OrganizationSetting(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.SlugField(max_length=100)
    value = models.JSONField(default=dict, blank=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("key",)
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "key"), name="unique_setting_key_per_org"
            )
        ]

    def __str__(self):
        return f"{self.organization}: {self.key}"


class Announcement(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, null=True, blank=True
    )
    title = models.CharField(max_length=200)
    body = CKEditor5Field(config_name="default")
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return self.title

# Create your models here.
