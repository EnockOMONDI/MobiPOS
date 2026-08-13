from datetime import timedelta

from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db import transaction
from django.utils.text import slugify
from django.utils import timezone

from apps.audit.services import record_audit_event
from apps.catalog.models import Brand, Category
from apps.contacts.models import Contact, ContactType
from .models import (
    AgentProfile,
    AgentProfileStatus,
    AgentProfileType,
    Location,
    LocationType,
    MembershipStatus,
    OrganizationStatus,
    OrganizationSetting,
    Role,
    SubscriptionInvoice,
    SubscriptionInvoiceStatus,
    Subscription,
    SubscriptionStatus,
)


ALLOW_AGENT_DSA_REGISTRATION_SETTING = "allow_agent_dsa_registration"


DEFAULT_ROLE_PERMISSION_CODES = {
    "manager": (
        "catalog.add_brand", "catalog.add_category", "catalog.add_product", "catalog.change_product", "catalog.view_product",
        "contacts.add_contact", "contacts.change_contact", "contacts.view_contact",
        "expenses.add_expense", "expenses.view_expense",
        "inventory.add_stockadjustment", "inventory.view_stockbalance", "inventory.view_stockmovement", "inventory.view_stockunit",
        "organizations.add_agentprofile", "organizations.view_activity_report", "organizations.view_operational_report", "organizations.view_retail_analytics",
        "payments.add_payment", "payments.view_payment",
        "purchasing.add_purchaseorder", "purchasing.add_supplierreturn", "purchasing.change_purchaseorder", "purchasing.view_purchasediscrepancy", "purchasing.view_purchaseorder", "purchasing.view_supplierreturn",
        "repairs.add_repairticket", "repairs.change_repairticket", "repairs.view_repairticket",
        "sales.add_sale", "sales.add_salereturn", "sales.view_sale", "sales.view_salereturn",
        "transfers.add_stocktransfer", "transfers.change_stocktransfer", "transfers.view_stocktransfer",
        "commissions.view_commissionaccrual",
    ),
    "cashier": (
        "catalog.view_product", "contacts.add_contact", "contacts.view_contact",
        "payments.add_payment", "payments.view_payment", "sales.add_sale", "sales.view_sale",
    ),
    "inventory-officer": (
        "catalog.add_brand", "catalog.add_category", "catalog.add_product", "catalog.change_product", "catalog.view_product",
        "inventory.add_stockadjustment", "inventory.view_stockbalance", "inventory.view_stockmovement", "inventory.view_stockunit",
        "purchasing.view_purchaseorder", "transfers.add_stocktransfer", "transfers.change_stocktransfer", "transfers.view_stocktransfer",
    ),
    "purchasing-officer": (
        "catalog.view_product", "contacts.add_contact", "contacts.change_contact", "contacts.view_contact",
        "purchasing.add_purchaseorder", "purchasing.add_supplierreturn", "purchasing.change_purchaseorder", "purchasing.view_purchasediscrepancy", "purchasing.view_purchaseorder", "purchasing.view_supplierreturn",
    ),
    "agent": (
        "catalog.view_product", "contacts.add_contact", "contacts.view_contact",
        "inventory.view_stockbalance", "inventory.view_stockunit",
        "organizations.add_agentprofile", "sales.add_sale", "sales.view_sale", "transfers.view_stocktransfer",
    ),
    "direct-sales-agent": (
        "catalog.view_product", "contacts.add_contact", "contacts.view_contact",
        "inventory.view_stockunit", "sales.add_sale", "sales.view_sale",
    ),
    "technician": (
        "catalog.view_product", "inventory.view_stockunit",
        "repairs.add_repairticket", "repairs.change_repairticket", "repairs.view_repairticket",
    ),
    "finance": (
        "contacts.view_contact", "expenses.add_expense", "expenses.view_expense",
        "organizations.view_activity_report", "organizations.view_operational_report", "organizations.view_retail_analytics",
        "payments.add_payment", "payments.view_payment", "sales.view_sale", "sales.view_salereturn",
        "commissions.view_commissionaccrual",
    ),
    "viewer": (
        "catalog.view_product", "contacts.view_contact", "inventory.view_stockbalance", "inventory.view_stockmovement", "inventory.view_stockunit",
        "organizations.view_operational_report", "payments.view_payment", "purchasing.view_purchaseorder", "sales.view_sale", "transfers.view_stocktransfer",
    ),
}

DEFAULT_ROLE_LABELS = {
    "manager": "Manager",
    "cashier": "Cashier",
    "inventory-officer": "Inventory Officer",
    "purchasing-officer": "Purchasing Officer",
    "agent": "Agent",
    "direct-sales-agent": "Direct Sales Agent",
    "technician": "Technician",
    "finance": "Finance",
    "viewer": "Viewer",
}

DEFAULT_CATEGORIES = (
    ("phones", "Mobile Phones"),
    ("accessories", "Accessories"),
    ("spare-parts", "Spare Parts"),
    ("services", "Services"),
)

DEFAULT_BRANDS = ("Samsung", "Tecno", "Infinix", "Itel", "Oppo", "Xiaomi", "Apple", "Generic")


def _permissions_for_codes(codes):
    permission_filter = Q()
    for permission_code in codes:
        app_label, codename = permission_code.split(".", maxsplit=1)
        permission_filter |= Q(content_type__app_label=app_label, codename=codename)
    if not permission_filter:
        return Permission.objects.none()
    return Permission.objects.filter(permission_filter)


@transaction.atomic
def ensure_organization_onboarding_defaults(organization):
    from apps.inventory.aging import ensure_aged_stock_policy

    ensure_aged_stock_policy(organization)
    for code, name in DEFAULT_CATEGORIES:
        Category.objects.get_or_create(
            organization=organization,
            code=code,
            defaults={"name": name, "is_active": True},
        )
    for name in DEFAULT_BRANDS:
        Brand.objects.get_or_create(
            organization=organization,
            name=name,
            defaults={"is_active": True},
        )
    Contact.objects.get_or_create(
        organization=organization,
        name="Opening Stock Supplier",
        contact_type=ContactType.SUPPLIER,
        defaults={
            "phone_number": "",
            "email": "",
            "address": "Use this supplier for opening stock, or replace it with your real supplier.",
            "is_active": True,
        },
    )
    roles = {}
    for code, permission_codes in DEFAULT_ROLE_PERMISSION_CODES.items():
        role, created = Role.objects.get_or_create(
            organization=organization,
            code=code,
            defaults={
                "name": DEFAULT_ROLE_LABELS[code],
                "description": f"Default {DEFAULT_ROLE_LABELS[code].lower()} access profile.",
                "is_active": True,
            },
        )
        if created or not role.permissions.exists():
            role.permissions.set(_permissions_for_codes(permission_codes))
        roles[code] = role
    return roles


def next_available_username(*, email, first_name="", last_name=""):
    base = slugify((email or "").split("@")[0] or f"{first_name}-{last_name}") or "user"
    candidate = base[:140]
    suffix = 1
    from django.contrib.auth import get_user_model

    User = get_user_model()
    while User.objects.filter(username=candidate).exists():
        suffix_text = f"-{suffix}"
        candidate = f"{base[:150 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    return candidate


def organization_setting_enabled(organization, key, default=False):
    setting = OrganizationSetting.objects.filter(organization=organization, key=key).first()
    if not setting:
        return default
    value = setting.value
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return bool(value.get("enabled", default))
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


@transaction.atomic
def activate_subscription_invoice(*, invoice, payment_reference):
    if invoice.status == SubscriptionInvoiceStatus.PAID:
        return invoice
    if invoice.status == SubscriptionInvoiceStatus.VOID:
        raise ValidationError("A void subscription invoice cannot be paid.")
    invoice.status = SubscriptionInvoiceStatus.PAID
    invoice.paid_at = timezone.now()
    invoice.payment_reference = payment_reference
    invoice.save(update_fields=["status", "paid_at", "payment_reference", "updated_at"])
    subscription = invoice.subscription
    subscription.status = SubscriptionStatus.ACTIVE
    today = timezone.localdate()
    subscription.starts_on = subscription.starts_on or today
    subscription.renews_on = max(today, subscription.renews_on or today) + timedelta(days=30)
    subscription.grace_ends_on = subscription.renews_on + timedelta(days=7)
    subscription.save(update_fields=["status", "starts_on", "renews_on", "grace_ends_on", "updated_at"])
    organization = invoice.organization
    organization.status = OrganizationStatus.ACTIVE
    organization.save(update_fields=["status", "updated_at"])
    return invoice


@transaction.atomic
def refresh_subscription_lifecycle(*, today=None):
    today = today or timezone.localdate()
    changed = {"grace": 0, "suspended": 0, "invoices": 0}
    for subscription in Subscription.objects.select_for_update().select_related("organization", "plan"):
        if subscription.status == SubscriptionStatus.ACTIVE and subscription.renews_on and subscription.renews_on <= today:
            subscription.status = SubscriptionStatus.GRACE
            subscription.save(update_fields=["status", "updated_at"])
            changed["grace"] += 1
        if subscription.status == SubscriptionStatus.GRACE and subscription.grace_ends_on and subscription.grace_ends_on < today:
            subscription.status = SubscriptionStatus.SUSPENDED
            subscription.save(update_fields=["status", "updated_at"])
            subscription.organization.status = OrganizationStatus.SUSPENDED
            subscription.organization.save(update_fields=["status", "updated_at"])
            changed["suspended"] += 1
        if subscription.status in {SubscriptionStatus.ACTIVE, SubscriptionStatus.GRACE} and subscription.renews_on:
            number = f"SUB-{str(subscription.id)[:8].upper()}-{subscription.renews_on:%Y%m%d}"
            _, created = SubscriptionInvoice.objects.get_or_create(
                number=number,
                defaults={
                    "organization": subscription.organization,
                    "subscription": subscription,
                    "amount": subscription.plan.monthly_price,
                    "due_on": subscription.renews_on,
                },
            )
            changed["invoices"] += int(created)
    return changed


def enforce_plan_limit(*, organization, key, current_count):
    subscription = organization.subscription_set.filter(
        status__in=[SubscriptionStatus.ACTIVE, SubscriptionStatus.GRACE]
    ).select_related("plan").first()
    if not subscription:
        raise ValidationError("An active subscription is required.")
    limit = subscription.plan.limits.get(key)
    if limit is not None and current_count >= int(limit):
        raise ValidationError(f"The plan limit for {key.replace('_', ' ')} has been reached.")


@transaction.atomic
def provision_agent_custody_location(*, membership, branch, actor=None, request=None):
    if membership.organization_id != branch.organization_id:
        raise ValidationError("Custody branch must belong to the same organization as the user.")
    if not membership.branches.filter(id=branch.id).exists():
        raise ValidationError("The user must be assigned to the custody branch.")
    if membership.status == MembershipStatus.SUSPENDED:
        raise ValidationError("Suspended users cannot receive an active stock custody location.")

    location = (
        Location.objects.select_for_update()
        .filter(
            organization=membership.organization,
            custodian_membership=membership,
            location_type=LocationType.AGENT,
            is_active=True,
        )
        .first()
    )
    display_name = membership.user.get_full_name() or membership.user.get_username()
    if location:
        changed_fields = []
        if location.branch_id != branch.id:
            location.branch = branch
            changed_fields.append("branch")
        expected_name = f"{display_name} Stock Custody"
        if location.name != expected_name:
            location.name = expected_name
            changed_fields.append("name")
        if changed_fields:
            location.full_clean()
            location.save(update_fields=[*changed_fields, "updated_at"])
            record_audit_event(
                action="location.agent_custody_updated",
                actor=actor,
                organization=membership.organization,
                target=location,
                metadata={"membership": str(membership.id), "branch": branch.code},
                request=request,
            )
        return location

    base_code = slugify(membership.user.get_username()).upper().replace("-", "")[:16] or "USER"
    code_prefix = f"AG-{base_code}"
    code = code_prefix[:32]
    suffix = 1
    while Location.objects.filter(organization=membership.organization, code=code).exists():
        suffix_text = f"-{suffix}"
        code = f"{code_prefix[:32 - len(suffix_text)]}{suffix_text}"
        suffix += 1

    location = Location(
        organization=membership.organization,
        branch=branch,
        name=f"{display_name} Stock Custody",
        code=code,
        location_type=LocationType.AGENT,
        custodian_membership=membership,
        is_active=True,
    )
    location.full_clean()
    location.save()
    record_audit_event(
        action="location.agent_custody_created",
        actor=actor,
        organization=membership.organization,
        target=location,
        metadata={"membership": str(membership.id), "branch": branch.code},
        request=request,
    )
    return location


@transaction.atomic
def upsert_agent_profile(
    *,
    membership,
    profile_type=AgentProfileType.AGENT,
    branch,
    legal_name,
    status=AgentProfileStatus.PENDING,
    supervisor=None,
    national_id_number="",
    phone_number="",
    registration_notes="",
    actor=None,
    request=None,
):
    if membership.organization_id != branch.organization_id:
        raise ValidationError("Agent branch must belong to the same organization as the user.")
    if not membership.branches.filter(id=branch.id).exists():
        raise ValidationError("The user must be assigned to the agent branch.")
    if supervisor and supervisor.organization_id != membership.organization_id:
        raise ValidationError("Supervisor must belong to the same organization.")
    if profile_type == AgentProfileType.DSA and not supervisor:
        raise ValidationError("A DSA profile requires a supervising agent.")
    if profile_type == AgentProfileType.AGENT and supervisor:
        raise ValidationError("Agent profiles cannot have a supervising agent.")

    try:
        profile = membership.agent_profile
    except AgentProfile.DoesNotExist:
        profile = None
    created = profile is None
    if created:
        profile = AgentProfile(
            organization=membership.organization,
            membership=membership,
            registered_by=actor,
        )
    profile.profile_type = profile_type
    profile.branch = branch
    profile.status = status or AgentProfileStatus.PENDING
    profile.supervisor = supervisor
    profile.legal_name = legal_name or membership.user.get_full_name() or membership.user.get_username()
    profile.national_id_number = (national_id_number or "").strip()
    profile.phone_number = phone_number or membership.user.phone_number or ""
    profile.registration_notes = registration_notes or ""
    profile.full_clean()
    profile.save()
    record_audit_event(
        action="agent_profile.created" if created else "agent_profile.updated",
        actor=actor,
        organization=membership.organization,
        target=profile,
        metadata={
            "membership": str(membership.id),
            "profile_type": profile.profile_type,
            "status": profile.status,
            "branch": branch.code,
            "supervisor": str(supervisor.id) if supervisor else "",
        },
        request=request,
    )
    return profile


@transaction.atomic
def decide_agent_profile(*, profile, decision, actor, notes="", request=None):
    allowed = {
        "approve": AgentProfileStatus.ACTIVE,
        "reject": AgentProfileStatus.REJECTED,
        "suspend": AgentProfileStatus.SUSPENDED,
        "reactivate": AgentProfileStatus.ACTIVE,
    }
    if decision not in allowed:
        raise ValidationError("Unknown agent profile decision.")
    if decision == "approve" and profile.status not in {AgentProfileStatus.PENDING, AgentProfileStatus.REJECTED}:
        raise ValidationError("Only pending or rejected profiles can be approved.")
    if decision == "reject" and profile.status not in {AgentProfileStatus.PENDING, AgentProfileStatus.ACTIVE}:
        raise ValidationError("Only pending or active profiles can be rejected.")
    if decision == "suspend" and profile.status != AgentProfileStatus.ACTIVE:
        raise ValidationError("Only active profiles can be suspended.")
    if decision == "reactivate" and profile.status != AgentProfileStatus.SUSPENDED:
        raise ValidationError("Only suspended profiles can be reactivated.")

    previous_status = profile.status
    profile.status = allowed[decision]
    profile.verified_by = actor
    profile.verified_at = timezone.now()
    profile.verification_notes = notes or ""
    profile.full_clean()
    profile.save(update_fields=["status", "verified_by", "verified_at", "verification_notes", "updated_at"])
    record_audit_event(
        action=f"agent_profile.{decision}",
        actor=actor,
        organization=profile.organization,
        target=profile,
        metadata={
            "previous_status": previous_status,
            "new_status": profile.status,
            "notes": notes or "",
        },
        request=request,
    )
    return profile
