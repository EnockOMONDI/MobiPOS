from .models import MembershipStatus
from .models import Branch, Location
from functools import wraps
from django.core.exceptions import PermissionDenied
from django.conf import settings


def _otp_is_verified(request):
    user = request.user
    verifier = getattr(user, "is_verified", None)
    session = getattr(request, "session", {})
    return bool((verifier and verifier()) or session.get("mfa_recovery_verified"))


def active_membership_for(user, organization):
    if not user.is_authenticated:
        return None
    cache_enabled = getattr(user, "_organization_permission_cache_enabled", False)
    cache = getattr(user, "_organization_membership_cache", None)
    if cache_enabled and cache is None:
        cache = {}
        user._organization_membership_cache = cache
    if cache_enabled and organization.id in cache:
        return cache[organization.id]
    membership = (
        user.memberships.filter(
            organization=organization,
            organization__status="active",
            status=MembershipStatus.ACTIVE,
        )
        .prefetch_related("roles__permissions__content_type", "branches")
        .first()
    )
    if cache_enabled:
        cache[organization.id] = membership
    return membership


def effective_permission_codes(user, organization):
    if user.is_superuser or user.is_platform_admin:
        return None

    membership = active_membership_for(user, organization)
    if not membership:
        return set()
    if membership.is_owner:
        return None

    cache_enabled = getattr(user, "_organization_permission_cache_enabled", False)
    cache = getattr(user, "_organization_permission_cache", None)
    if not cache_enabled:
        return {
            f"{permission.content_type.app_label}.{permission.codename}"
            for role in membership.roles.all()
            if role.is_active
            for permission in role.permissions.all()
        }
    if cache is None:
        cache = {}
        user._organization_permission_cache = cache
    if organization.id not in cache:
        cache[organization.id] = {
            f"{permission.content_type.app_label}.{permission.codename}"
            for role in membership.roles.all()
            if role.is_active
            for permission in role.permissions.all()
        }
    return cache[organization.id]


def user_has_organization_permission(user, organization, permission_codename):
    permission_codes = effective_permission_codes(user, organization)
    return permission_codes is None or permission_codename in permission_codes


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
    return branch.id in {assigned.id for assigned in membership.branches.all()}


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
            if settings.PRIVILEGED_OTP_REQUIRED and not _otp_is_verified(request):
                from django.shortcuts import redirect
                from django.urls import reverse
                return redirect(f"{reverse('mfa-verify')}?next={request.get_full_path()}")
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
            if settings.PRIVILEGED_OTP_REQUIRED and not _otp_is_verified(request):
                from django.shortcuts import redirect
                from django.urls import reverse
                return redirect(f"{reverse('mfa-verify')}?next={request.get_full_path()}")
            return view(request, *args, **kwargs)
        raise PermissionDenied("Platform administrator access is required.")
    return wrapped
