from .models import MembershipStatus
from .models import Branch, Location
from functools import wraps
from django.core.exceptions import PermissionDenied
from django.conf import settings


def _otp_is_verified(user):
    verifier = getattr(user, "is_verified", None)
    return bool(verifier and verifier())


def active_membership_for(user, organization):
    if not user.is_authenticated:
        return None
    return (
        user.memberships.filter(
            organization=organization,
            organization__status="active",
            status=MembershipStatus.ACTIVE,
        )
        .prefetch_related("roles__permissions", "branches")
        .first()
    )


def user_has_organization_permission(user, organization, permission_codename):
    if user.is_superuser or user.is_platform_admin:
        return True

    membership = active_membership_for(user, organization)
    if not membership:
        return False
    if membership.is_owner:
        return True

    app_label, codename = permission_codename.split(".", maxsplit=1)
    return membership.roles.filter(
        is_active=True,
        permissions__content_type__app_label=app_label,
        permissions__codename=codename,
    ).exists()


def user_can_access_branch(user, organization, branch):
    if branch.organization_id != organization.id:
        return False
    if user.is_superuser or user.is_platform_admin:
        return True

    membership = active_membership_for(user, organization)
    if not membership:
        return False
    if membership.is_owner:
        return True
    return membership.branches.filter(id=branch.id).exists()


def accessible_branches_for(user, organization):
    if not organization:
        return Branch.objects.none()
    if user.is_superuser or user.is_platform_admin:
        return Branch.objects.filter(organization=organization, is_active=True)
    membership = active_membership_for(user, organization)
    if not membership:
        return Branch.objects.none()
    if membership.is_owner:
        return Branch.objects.filter(organization=organization, is_active=True)
    return membership.branches.filter(organization=organization, is_active=True)


def accessible_locations_for(user, organization):
    return Location.objects.filter(
        organization=organization,
        branch__in=accessible_branches_for(user, organization),
        is_active=True,
    )


def organization_owner_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        membership = getattr(request, "membership", None)
        if request.user.is_superuser or request.user.is_platform_admin or (membership and membership.is_owner):
            if settings.PRIVILEGED_OTP_REQUIRED and not _otp_is_verified(request.user):
                raise PermissionDenied("Verified two-factor authentication is required.")
            return view(request, *args, **kwargs)
        raise PermissionDenied("Organization owner access is required.")
    return wrapped


def organization_permission_required(permission_codename):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            organization = getattr(request, "organization", None)
            if organization and user_has_organization_permission(request.user, organization, permission_codename):
                return view(request, *args, **kwargs)
            raise PermissionDenied(f"Permission {permission_codename} is required.")
        return wrapped
    return decorator


def platform_admin_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.user.is_authenticated and (request.user.is_superuser or request.user.is_platform_admin):
            if settings.PRIVILEGED_OTP_REQUIRED and not _otp_is_verified(request.user):
                raise PermissionDenied("Verified two-factor authentication is required.")
            return view(request, *args, **kwargs)
        raise PermissionDenied("Platform administrator access is required.")
    return wrapped
