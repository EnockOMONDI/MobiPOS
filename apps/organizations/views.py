from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.audit.services import record_audit_event
from .forms import BranchCreateForm, InvitationAcceptForm, LocationCreateForm, MembershipAccessForm, OrganizationRegistrationForm, RoleCreateForm, TenantUserForm
from .permissions import organization_owner_required, platform_admin_required
from .models import (
    Membership,
    MembershipStatus,
    Invitation,
    InvitationStatus,
    Organization,
    OrganizationStatus,
    Subscription,
    SubscriptionInvoice,
    Company,
    Branch,
    Location,
    Role,
)


def _send_invitation(request, invitation):
    invitation.last_sent_at = timezone.now()
    invitation.save(update_fields=["last_sent_at", "updated_at"])
    url = request.build_absolute_uri(reverse("invitation-accept", args=[invitation.token]))
    send_mail(
        subject=f"You are invited to {invitation.organization.name} on MobiPOS",
        message=f"Accept your invitation and set your password: {url}",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[invitation.membership.user.email],
    )


@transaction.atomic
def register_organization(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = OrganizationRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        organization = Organization.objects.create(
            name=form.cleaned_data["organization_name"],
            slug=form.cleaned_data["organization_slug"],
            email=form.cleaned_data["email"],
            status=OrganizationStatus.PENDING,
        )
        user = get_user_model().objects.create_user(
            username=form.cleaned_data["username"],
            email=form.cleaned_data["email"],
            password=form.cleaned_data["password"],
            first_name=form.cleaned_data["first_name"],
            last_name=form.cleaned_data["last_name"],
        )
        membership = Membership.objects.create(
            organization=organization,
            user=user,
            status=MembershipStatus.ACTIVE,
            is_owner=True,
        )
        company = Company.objects.create(
            organization=organization, name=f"{organization.name} Company", code="MAIN"
        )
        branch = Branch.objects.create(
            organization=organization, company=company, name="Main Branch", code="MAIN"
        )
        Location.objects.create(
            organization=organization, branch=branch, name="Main POS", code="MAIN-POS", location_type="pos"
        )
        membership.branches.add(branch)
        subscription = Subscription.objects.create(
            organization=organization,
            plan=form.cleaned_data["plan"],
        )
        SubscriptionInvoice.objects.create(
            organization=organization,
            subscription=subscription,
            number=f"SUB-{str(organization.id)[:8].upper()}",
            amount=subscription.plan.monthly_price,
            due_on=timezone.localdate(),
        )
        record_audit_event(
            action="organization.registered",
            actor=user,
            organization=organization,
            target=organization,
            request=request,
        )
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return redirect("dashboard")
    return render(request, "registration/register.html", {"form": form})


@login_required
@platform_admin_required
@require_POST
def subscription_invoice_activate(request, invoice_id):
    from .services import activate_subscription_invoice
    invoice = get_object_or_404(SubscriptionInvoice, id=invoice_id)
    reference = request.POST.get("payment_reference", "MANUAL-APPROVAL")
    activate_subscription_invoice(invoice=invoice, payment_reference=reference)
    record_audit_event(action="subscription.activated", actor=request.user, organization=invoice.organization, target=invoice, request=request)
    return redirect("module-overview", module="subscriptions")


@login_required
@organization_owner_required
@transaction.atomic
def tenant_user_create(request):
    from django.core.exceptions import ValidationError
    from .services import enforce_plan_limit

    form = TenantUserForm(request.POST or None, organization=request.organization)
    if request.method == "POST" and form.is_valid():
        try:
            enforce_plan_limit(
                organization=request.organization,
                key="users",
                current_count=Membership.objects.filter(
                    organization=request.organization,
                    status__in=(MembershipStatus.ACTIVE, MembershipStatus.INVITED),
                ).count(),
            )
        except ValidationError as error:
            form.add_error(None, error.message)
        else:
            user = get_user_model().objects.create_user(
                username=form.cleaned_data["username"],
                email=form.cleaned_data["email"],
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
                phone_number=form.cleaned_data["phone_number"],
                is_active=False,
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
            membership = Membership.objects.create(
                organization=request.organization, user=user, status=MembershipStatus.INVITED,
            )
            membership.branches.set(form.cleaned_data["branches"])
            membership.roles.set(form.cleaned_data["roles"])
            invitation = Invitation.objects.create(
                organization=request.organization,
                membership=membership,
                invited_by=request.user,
                expires_at=timezone.now() + timedelta(days=7),
            )
            _send_invitation(request, invitation)
            record_audit_event(action="user.invited", actor=request.user, organization=request.organization, target=user, request=request)
            messages.success(request, f"Invitation sent to {user.email}.")
            return redirect("module-overview", module="users")
    return render(request, "organizations/user_create.html", {"form": form})


def invitation_accept(request, token):
    invitation = get_object_or_404(
        Invitation.objects.select_related("membership__user", "organization"),
        token=token,
    )
    if invitation.status != InvitationStatus.PENDING:
        raise Http404("This invitation is no longer active.")
    if invitation.expires_at <= timezone.now():
        invitation.status = InvitationStatus.EXPIRED
        invitation.save(update_fields=["status", "updated_at"])
        raise Http404("This invitation has expired.")
    form = InvitationAcceptForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            invitation = Invitation.objects.select_for_update().select_related("membership__user").get(pk=invitation.pk)
            if invitation.status != InvitationStatus.PENDING or invitation.expires_at <= timezone.now():
                raise Http404("This invitation is no longer active.")
            user = invitation.membership.user
            user.set_password(form.cleaned_data["password"])
            user.is_active = True
            user.save(update_fields=["password", "is_active"])
            invitation.membership.status = MembershipStatus.ACTIVE
            invitation.membership.save(update_fields=["status", "updated_at"])
            invitation.status = InvitationStatus.ACCEPTED
            invitation.accepted_at = timezone.now()
            invitation.save(update_fields=["status", "accepted_at", "updated_at"])
            record_audit_event(action="user.invitation_accepted", actor=user, organization=invitation.organization, target=invitation, request=request)
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Invitation accepted. Set up two-factor authentication to secure your account.")
        return redirect("mfa-setup")
    return render(request, "registration/invitation_accept.html", {"form": form, "invitation": invitation})


@login_required
@organization_owner_required
@require_POST
def invitation_resend(request, invitation_id):
    invitation = get_object_or_404(
        Invitation, id=invitation_id, organization=request.organization, status=InvitationStatus.PENDING
    )
    invitation.expires_at = timezone.now() + timedelta(days=7)
    invitation.save(update_fields=["expires_at", "updated_at"])
    _send_invitation(request, invitation)
    record_audit_event(action="user.invitation_resent", actor=request.user, organization=request.organization, target=invitation, request=request)
    messages.success(request, "Invitation resent.")
    return redirect("membership-access-list")


@login_required
@organization_owner_required
@require_POST
def invitation_revoke(request, invitation_id):
    invitation = get_object_or_404(
        Invitation.objects.select_related("membership__user"),
        id=invitation_id,
        organization=request.organization,
        status=InvitationStatus.PENDING,
    )
    invitation.status = InvitationStatus.REVOKED
    invitation.save(update_fields=["status", "updated_at"])
    invitation.membership.status = MembershipStatus.SUSPENDED
    invitation.membership.save(update_fields=["status", "updated_at"])
    record_audit_event(action="user.invitation_revoked", actor=request.user, organization=request.organization, target=invitation, request=request)
    messages.success(request, "Invitation revoked.")
    return redirect("membership-access-list")


@login_required
@organization_owner_required
def membership_access_list(request):
    memberships = Membership.objects.filter(
        organization=request.organization,
    ).select_related("user", "invitation").prefetch_related("roles__permissions__content_type", "branches")
    rows = []
    for membership in memberships:
        permissions = sorted({
            f"{permission.content_type.app_label}.{permission.codename}"
            for role in membership.roles.all()
            for permission in role.permissions.all()
        })
        rows.append({"membership": membership, "permissions": permissions})
    return render(request, "organizations/access_list.html", {"rows": rows})


@login_required
@organization_owner_required
@transaction.atomic
def membership_access_update(request, membership_id):
    membership = get_object_or_404(
        Membership.objects.select_for_update().select_related("user"),
        id=membership_id,
        organization=request.organization,
    )
    form = MembershipAccessForm(
        request.POST or None,
        instance=membership,
        organization=request.organization,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        record_audit_event(
            action="membership.access_updated",
            actor=request.user,
            organization=request.organization,
            target=membership,
            metadata={
                "roles": list(membership.roles.values_list("code", flat=True)),
                "branches": list(membership.branches.values_list("code", flat=True)),
            },
            request=request,
        )
        return redirect("membership-access-list")
    return render(
        request,
        "organizations/access_update.html",
        {"form": form, "membership": membership},
    )


@login_required
@organization_owner_required
@transaction.atomic
def branch_create(request):
    from django.core.exceptions import ValidationError
    from .services import enforce_plan_limit

    form = BranchCreateForm(request.POST or None, organization=request.organization)
    if request.method == "POST" and form.is_valid():
        try:
            enforce_plan_limit(
                organization=request.organization,
                key="branches",
                current_count=Branch.objects.filter(organization=request.organization, is_active=True).count(),
            )
        except ValidationError as error:
            form.add_error(None, error.message)
        else:
            branch = Branch.objects.create(organization=request.organization, is_active=True, **form.cleaned_data)
            request.membership.branches.add(branch)
            record_audit_event(action="branch.created", actor=request.user, organization=request.organization, target=branch, request=request)
            return redirect("module-overview", module="branches")
    return render(request, "organizations/branch_create.html", {"form": form})


@login_required
@organization_owner_required
@transaction.atomic
def location_create(request):
    from django.core.exceptions import ValidationError
    from .models import LocationType
    from .services import enforce_plan_limit

    form = LocationCreateForm(request.POST or None, organization=request.organization)
    if request.method == "POST" and form.is_valid():
        if form.cleaned_data["location_type"] == LocationType.POS:
            try:
                enforce_plan_limit(
                    organization=request.organization,
                    key="pos_locations",
                    current_count=Location.objects.filter(
                        organization=request.organization, is_active=True, location_type=LocationType.POS
                    ).count(),
                )
            except ValidationError as error:
                form.add_error(None, error.message)
                return render(request, "organizations/location_create.html", {"form": form})
        location = Location.objects.create(organization=request.organization, is_active=True, **form.cleaned_data)
        record_audit_event(action="location.created", actor=request.user, organization=request.organization, target=location, request=request)
        return redirect("module-overview", module="locations")
    return render(request, "organizations/location_create.html", {"form": form})


@login_required
@organization_owner_required
@transaction.atomic
def role_create(request):
    form = RoleCreateForm(request.POST or None, organization=request.organization)
    if request.method == "POST" and form.is_valid():
        role = Role.objects.create(
            organization=request.organization,
            name=form.cleaned_data["name"],
            code=form.cleaned_data["code"],
            description=form.cleaned_data["description"],
        )
        role.permissions.set(form.cleaned_data["permissions"])
        record_audit_event(action="role.created", actor=request.user, organization=request.organization, target=role, request=request)
        return redirect("module-overview", module="roles")
    return render(request, "organizations/role_create.html", {"form": form})

# Create your views here.
