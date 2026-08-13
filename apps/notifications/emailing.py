from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
import smtplib

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from apps.audit.models import AuditEvent
from apps.organizations.models import Membership, MembershipStatus, Organization

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailResult:
    recipients: tuple[str, ...]
    sent_count: int


def build_absolute_app_url(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{settings.APP_BASE_URL}{path}"


def send_branded_email(*, subject: str, template_name: str, context: dict, recipient_list: list[str]) -> EmailResult:
    recipients = tuple(email for email in recipient_list if email)
    if not recipients:
        return EmailResult(recipients=(), sent_count=0)

    html_body = render_to_string(template_name, context)
    text_body = render_to_string(
        template_name.replace(".html", ".txt"),
        context,
    ) if template_name.endswith(".html") else strip_tags(html_body)
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=list(recipients),
    )
    message.attach_alternative(html_body, "text/html")
    try:
        sent_count = message.send(fail_silently=False)
    except (OSError, TimeoutError, smtplib.SMTPException):
        logger.exception("Email delivery failed for template %s to %s", template_name, ", ".join(recipients))
        if getattr(settings, "EMAIL_RAISE_DELIVERY_ERRORS", False):
            raise
        sent_count = 0
    return EmailResult(recipients=recipients, sent_count=sent_count)


def send_owner_welcome_email(*, user, organization: Organization) -> EmailResult:
    return send_branded_email(
        subject=f"Welcome to MobiPOS, {user.first_name or user.get_username()}",
        template_name="emails/owner_welcome.html",
        context={
            "user": user,
            "organization": organization,
            "dashboard_url": build_absolute_app_url(reverse("dashboard")),
            "help_url": build_absolute_app_url(reverse("help-center")),
        },
        recipient_list=[user.email],
    )


def send_staff_account_created_email(*, user, organization: Organization, created_by) -> EmailResult:
    return send_branded_email(
        subject=f"Your MobiPOS account for {organization.name} is ready",
        template_name="emails/staff_account_created.html",
        context={
            "user": user,
            "organization": organization,
            "created_by": created_by,
            "login_url": build_absolute_app_url(reverse("login")),
            "password_reset_url": build_absolute_app_url(reverse("password-reset")),
        },
        recipient_list=[user.email],
    )


def send_daily_owner_activity_summary(*, organization: Organization, start=None, end=None) -> EmailResult:
    end = end or timezone.now()
    start = start or (end - timedelta(days=1))
    owner_memberships = (
        Membership.objects.filter(
            organization=organization,
            status=MembershipStatus.ACTIVE,
            is_owner=True,
            user__is_active=True,
        )
        .select_related("user")
        .order_by("user__email")
    )
    recipients = [membership.user.email for membership in owner_memberships if membership.user.email]
    events = list(
        AuditEvent.objects.filter(
            organization=organization,
            created_at__gte=start,
            created_at__lt=end,
        )
        .select_related("actor")
        .order_by("-created_at")[:12]
    )
    event_count = AuditEvent.objects.filter(
        organization=organization,
        created_at__gte=start,
        created_at__lt=end,
    ).count()
    return send_branded_email(
        subject=f"Daily MobiPOS activity summary for {organization.name}",
        template_name="emails/daily_owner_activity_summary.html",
        context={
            "organization": organization,
            "events": events,
            "event_count": event_count,
            "start": start,
            "end": end,
            "dashboard_url": build_absolute_app_url(reverse("dashboard")),
            "activity_url": build_absolute_app_url(reverse("activity-report")),
        },
        recipient_list=recipients,
    )


def send_approval_request_email(*, approval, recipients: list[str]) -> EmailResult:
    return send_branded_email(
        subject=f"MobiPOS approval needed: {approval.display_type}",
        template_name="emails/approval_request.html",
        context={
            "approval": approval,
            "organization": approval.organization,
            "approval_url": build_absolute_app_url(reverse("approval-detail", args=[approval.id])),
        },
        recipient_list=recipients,
    )


def send_approval_decision_email(*, approval, recipients: list[str]) -> EmailResult:
    return send_branded_email(
        subject=f"MobiPOS approval {approval.get_status_display().lower()}: {approval.display_type}",
        template_name="emails/approval_decision.html",
        context={
            "approval": approval,
            "organization": approval.organization,
            "approval_url": build_absolute_app_url(reverse("approval-detail", args=[approval.id])),
        },
        recipient_list=recipients,
    )
