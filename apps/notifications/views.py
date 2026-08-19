from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
def notification_list(request):
    queryset = Notification.objects.filter(
        organization=request.organization, recipient=request.user
    ).select_related("approval", "approval__requested_by", "approval__branch", "approval__policy")
    filter_name = request.GET.get("filter", "all")
    if filter_name == "unread":
        queryset = queryset.filter(read_at__isnull=True)
    elif filter_name == "approvals":
        queryset = queryset.filter(kind=Notification.Kind.APPROVAL_REQUEST)
    elif filter_name == "decisions":
        queryset = queryset.filter(kind=Notification.Kind.APPROVAL_DECISION)
    else:
        filter_name = "all"
    page = Paginator(queryset, 20).get_page(request.GET.get("page"))
    approval_ids = [item.approval_id for item in page.object_list if item.approval_id]
    if approval_ids:
        from apps.operations.services import approvals_user_can_decide

        approvable_ids = set(
            approvals_user_can_decide(user=request.user, organization=request.organization)
            .filter(id__in=approval_ids, status="pending")
            .values_list("id", flat=True)
        )
    else:
        approvable_ids = set()
    cards = []
    for item in page.object_list:
        target_context = None
        can_approve = False
        if item.approval_id:
            from apps.operations.services import approval_business_summary, approval_target_context

            target_context = approval_target_context(item.approval)
            can_approve = item.approval_id in approvable_ids
            summary = approval_business_summary(item.approval)
        else:
            summary = None
        cards.append({"item": item, "target": target_context, "summary": summary, "can_approve": can_approve})
    return render(request, "notifications/list.html", {
        "notification_cards": cards,
        "page_obj": page,
        "active_filter": filter_name,
        "unread_count": Notification.objects.filter(organization=request.organization, recipient=request.user, read_at__isnull=True).count(),
        "approval_count": Notification.objects.filter(organization=request.organization, recipient=request.user, kind=Notification.Kind.APPROVAL_REQUEST, read_at__isnull=True).count(),
        "decision_count": Notification.objects.filter(organization=request.organization, recipient=request.user, kind=Notification.Kind.APPROVAL_DECISION, read_at__isnull=True).count(),
    })


@login_required
@require_POST
def notification_read(request, notification_id):
    notification = get_object_or_404(
        Notification, id=notification_id, organization=request.organization, recipient=request.user
    )
    notification.read_at = timezone.now()
    notification.save(update_fields=["read_at", "updated_at"])
    return redirect("notification-list")
