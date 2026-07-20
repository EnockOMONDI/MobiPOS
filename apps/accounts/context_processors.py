from django.conf import settings


def _current_module(request):
    resolver_match = getattr(request, "resolver_match", None)
    if not resolver_match:
        return ""
    namespace = resolver_match.namespace
    if namespace:
        return namespace
    view_name = resolver_match.view_name or ""
    return view_name.split("-", 1)[0] if view_name else ""


def _role_names(membership):
    if not membership:
        return []
    return list(membership.roles.order_by("name").values_list("name", flat=True))


def build_usertour_payload(request):
    """Return only the privacy-safe metadata approved for demo onboarding."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None
    if not settings.USERTOUR_ENABLED or not settings.USERTOUR_TOKEN:
        return None
    if settings.USERTOUR_DEMO_ONLY and not getattr(user, "is_demo_account", False):
        return None

    organization = getattr(request, "organization", None)
    membership = getattr(request, "membership", None)
    branch = None
    if membership:
        branch = membership.branches.order_by("name").first()

    display_name = user.first_name or user.username or "Demo User"
    payload = {
        "user_id": str(user.id),
        "display_name": f"{display_name} Demo" if getattr(user, "is_demo_account", False) else display_name,
        "account_type": "demo" if getattr(user, "is_demo_account", False) else "client",
        "organization_id": str(organization.id) if organization else "",
        "organization_name": organization.name if organization else "Demo Workspace",
        "branch_id": str(branch.id) if branch else "",
        "branch_name": f"{branch.name} Demo" if branch and getattr(user, "is_demo_account", False) else (branch.name if branch else ""),
        "roles": _role_names(membership),
        "is_owner": bool(getattr(membership, "is_owner", False)),
        "module": _current_module(request),
    }
    return payload


def usertour_context(request):
    payload = build_usertour_payload(request)
    return {
        "usertour_enabled": bool(payload),
        "usertour_token": settings.USERTOUR_TOKEN if payload else "",
        "usertour_payload": payload or {},
    }
