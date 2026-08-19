from .navigation import build_navigation


def organization_context(request):
    context = {
        "active_organization": getattr(request, "organization", None),
        "active_membership": getattr(request, "membership", None),
        "navigation_groups": build_navigation(request) if request.user.is_authenticated else [],
        "unread_notification_count": 0,
        "pending_approval_count": 0,
    }
    organization = getattr(request, "organization", None)
    if not request.user.is_authenticated or not organization:
        return context

    from apps.notifications.models import Notification
    from apps.operations.models import ApprovalStatus
    from apps.operations.services import approvals_user_can_decide

    context["unread_notification_count"] = Notification.objects.filter(
        organization=organization,
        recipient=request.user,
        read_at__isnull=True,
    ).count()
    context["pending_approval_count"] = approvals_user_can_decide(
        user=request.user,
        organization=organization,
    ).filter(status=ApprovalStatus.PENDING).count()
    return context
