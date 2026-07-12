from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.text import slugify
from django.utils import timezone

from apps.audit.services import record_audit_event
from .models import (
    AgentProfile,
    AgentProfileStatus,
    AgentProfileType,
    Location,
    LocationType,
    MembershipStatus,
    OrganizationStatus,
    OrganizationSetting,
    SubscriptionInvoice,
    SubscriptionInvoiceStatus,
    Subscription,
    SubscriptionStatus,
)


ALLOW_AGENT_DSA_REGISTRATION_SETTING = "allow_agent_dsa_registration"


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
