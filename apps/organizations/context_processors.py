from .navigation import build_navigation


def organization_context(request):
    return {
        "active_organization": getattr(request, "organization", None),
        "active_membership": getattr(request, "membership", None),
        "navigation_groups": build_navigation(request) if request.user.is_authenticated else [],
    }
