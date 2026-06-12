from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .services import record_audit_event


@receiver(user_logged_in)
def audit_login(sender, request, user, **kwargs):
    record_audit_event(
        action="auth.login",
        actor=user,
        organization=getattr(request, "organization", None),
        request=request,
    )


@receiver(user_logged_out)
def audit_logout(sender, request, user, **kwargs):
    record_audit_event(
        action="auth.logout",
        actor=user,
        organization=getattr(request, "organization", None),
        request=request,
    )


@receiver(user_login_failed)
def audit_login_failure(sender, credentials, request, **kwargs):
    identifier = credentials.get("username") or credentials.get("email") or ""
    record_audit_event(
        action="auth.login_failed",
        message="Authentication failed.",
        metadata={"identifier": identifier},
        request=request,
    )
