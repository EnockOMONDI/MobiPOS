from .models import AuditEvent


def record_audit_event(
    *,
    action,
    actor=None,
    organization=None,
    target=None,
    message="",
    metadata=None,
    request=None,
):
    target_type = ""
    target_id = ""
    if target is not None:
        target_type = target._meta.label
        target_id = str(target.pk)

    return AuditEvent.objects.create(
        organization=organization,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        message=message,
        metadata={
            **(metadata or {}),
            **({"request_id": request.request_id} if request and hasattr(request, "request_id") else {}),
        },
        ip_address=_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "") if request else "",
    )


def _client_ip(request):
    if not request:
        return None
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded_for.split(",", maxsplit=1)[0].strip() or request.META.get("REMOTE_ADDR")
