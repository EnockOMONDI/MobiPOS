from django.db import transaction

from apps.organizations.models import Membership, MembershipStatus

from .emailing import build_absolute_app_url, send_branded_email
from .models import Notification


def notify_business_event(*, organization, title, message, link, users=(), include_owners=False):
    recipients = {user.id: user for user in users if user and user.is_active}
    if include_owners:
        owners = Membership.objects.filter(
            organization=organization,
            status=MembershipStatus.ACTIVE,
            is_owner=True,
            user__is_active=True,
        ).select_related("user")
        recipients.update({membership.user_id: membership.user for membership in owners})

    for user in recipients.values():
        Notification.objects.create(
            organization=organization,
            recipient=user,
            title=title,
            message=message,
            link=link,
        )
    emails = [user.email for user in recipients.values() if user.email]
    transaction.on_commit(lambda: send_branded_email(
        subject=f"MobiPOS: {title}",
        template_name="emails/business_event.html",
        context={
            "organization": organization,
            "title": title,
            "message": message,
            "action_url": build_absolute_app_url(link),
        },
        recipient_list=emails,
        organization=organization,
    ))
    return len(recipients)
