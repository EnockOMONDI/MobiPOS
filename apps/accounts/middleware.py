from datetime import timedelta

from django.utils import timezone

from .models import UserSession


class UserSessionTrackingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.user.is_authenticated:
            if not request.session.session_key:
                request.session.save()
            forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
            ip_address = forwarded.split(",", 1)[0].strip() or request.META.get("REMOTE_ADDR")
            tracked, created = UserSession.objects.get_or_create(
                session_key=request.session.session_key,
                defaults={
                    "user": request.user,
                    "ip_address": ip_address,
                    "user_agent": request.META.get("HTTP_USER_AGENT", "")[:500],
                },
            )
            user_agent = request.META.get("HTTP_USER_AGENT", "")[:500]
            if not created and (
                tracked.user_id != request.user.id
                or tracked.last_seen_at < timezone.now() - timedelta(minutes=5)
                or tracked.ip_address != ip_address
                or tracked.user_agent != user_agent
            ):
                tracked.user = request.user
                tracked.ip_address = ip_address
                tracked.user_agent = user_agent
                tracked.save(update_fields=["user", "ip_address", "user_agent", "last_seen_at"])
        return response
