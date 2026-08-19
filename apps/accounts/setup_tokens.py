from datetime import timedelta
import secrets

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import AccountSetupToken


def issue_account_setup_token(*, user, organization, created_by=None):
    raw_token = secrets.token_urlsafe(32)
    with transaction.atomic():
        AccountSetupToken.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
        token = AccountSetupToken.objects.create(
            user=user,
            organization=organization,
            created_by=created_by,
            token_hash=AccountSetupToken.hash_token(raw_token),
            expires_at=timezone.now() + timedelta(days=settings.ACCOUNT_SETUP_TOKEN_DAYS),
        )
    return token, raw_token


def account_setup_url(raw_token):
    return f"{settings.APP_BASE_URL}{reverse('account-setup', args=[raw_token])}"
