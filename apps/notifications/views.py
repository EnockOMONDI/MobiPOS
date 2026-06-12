from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
def notification_list(request):
    notifications = Notification.objects.filter(
        organization=request.organization, recipient=request.user
    )[:100]
    return render(request, "notifications/list.html", {"notifications": notifications})


@login_required
@require_POST
def notification_read(request, notification_id):
    notification = get_object_or_404(
        Notification, id=notification_id, organization=request.organization, recipient=request.user
    )
    notification.read_at = timezone.now()
    notification.save(update_fields=["read_at", "updated_at"])
    return redirect("notification-list")
