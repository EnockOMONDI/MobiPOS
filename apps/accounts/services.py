import secrets

from django.contrib.auth.hashers import check_password
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from .models import RecoveryCode, UserSession


def confirmed_totp_device(user):
    return TOTPDevice.objects.filter(user=user, confirmed=True).first()


@transaction.atomic
def generate_recovery_codes(*, user, count=8):
    user.recovery_codes.filter(used_at__isnull=True).delete()
    codes = [secrets.token_hex(4).upper() for _ in range(count)]
    RecoveryCode.objects.bulk_create([RecoveryCode.from_code(user=user, code=code) for code in codes])
    return codes


@transaction.atomic
def consume_recovery_code(*, user, code):
    for recovery_code in user.recovery_codes.select_for_update().filter(used_at__isnull=True):
        if check_password(code.strip().upper(), recovery_code.code_hash):
            recovery_code.used_at = timezone.now()
            recovery_code.save(update_fields=["used_at"])
            return True
    return False


def revoke_session(*, user, session_key):
    Session.objects.filter(session_key=session_key).delete()
    return UserSession.objects.filter(user=user, session_key=session_key).delete()[0] > 0
