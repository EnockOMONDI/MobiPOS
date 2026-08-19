from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.audit.services import record_audit_event

from .forms import AccountSetupPasswordForm, MFAConfirmForm, RecoveryCodeForm
from .models import AccountSetupToken, UserSession
from .services import confirmed_totp_device, consume_recovery_code, generate_recovery_codes, revoke_session


def account_setup(request, token):
    token_hash = AccountSetupToken.hash_token(token)
    setup_token = get_object_or_404(
        AccountSetupToken.objects.select_related("user", "organization"),
        token_hash=token_hash,
    )
    if setup_token.used_at or setup_token.expires_at <= timezone.now():
        raise Http404("This account setup link has expired or has already been used.")

    form = AccountSetupPasswordForm(setup_token.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            locked_token = AccountSetupToken.objects.select_for_update().select_related("user").get(pk=setup_token.pk)
            if locked_token.used_at or locked_token.expires_at <= timezone.now():
                raise Http404("This account setup link has expired or has already been used.")
            user = locked_token.user
            form.user = user
            form.save()
            user.requires_password_setup = False
            user.save(update_fields=["requires_password_setup"])
            locked_token.used_at = timezone.now()
            locked_token.save(update_fields=["used_at"])
            record_audit_event(
                action="identity.account_setup_completed",
                actor=user,
                organization=locked_token.organization,
                target=user,
                request=request,
            )
        messages.success(request, "Your password is ready. Sign in with your email address.")
        return redirect("login")
    return render(
        request,
        "accounts/account_setup.html",
        {"form": form, "setup_token": setup_token},
    )


@login_required
def security_settings(request):
    return render(
        request,
        "accounts/security.html",
        {
            "device": confirmed_totp_device(request.user),
            "sessions": request.user.tracked_sessions.all(),
            "unused_recovery_codes": request.user.recovery_codes.filter(used_at__isnull=True).count(),
        },
    )


@login_required
def mfa_setup(request):
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if next_url and not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = ""
    device = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()
    if not device:
        device = TOTPDevice.objects.create(user=request.user, name="MobiPOS", confirmed=False)
    form = MFAConfirmForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if not device.verify_token(form.cleaned_data["token"]):
            form.add_error("token", "The authentication code is invalid.")
        else:
            device.confirmed = True
            device.save(update_fields=["confirmed"])
            otp_login(request, device)
            codes = generate_recovery_codes(user=request.user)
            record_audit_event(action="identity.mfa_enabled", actor=request.user, request=request)
            return render(request, "accounts/recovery_codes.html", {"codes": codes, "continue_url": next_url or None})
    return render(request, "accounts/mfa_setup.html", {"device": device, "form": form, "next_url": next_url})


@login_required
def mfa_verify(request):
    device = confirmed_totp_device(request.user)
    token_form = MFAConfirmForm(request.POST or None, prefix="token")
    recovery_form = RecoveryCodeForm(request.POST or None, prefix="recovery")
    if request.method == "POST":
        verified = False
        if "verify_token" in request.POST and token_form.is_valid() and device:
            verified = device.verify_token(token_form.cleaned_data["token"])
            if not verified:
                token_form.add_error("token", "The authentication code is invalid.")
        elif "verify_recovery" in request.POST and recovery_form.is_valid():
            verified = consume_recovery_code(user=request.user, code=recovery_form.cleaned_data["code"])
            if not verified:
                recovery_form.add_error("code", "The recovery code is invalid or has already been used.")
        if verified:
            if device:
                otp_login(request, device)
            request.session["mfa_recovery_verified"] = True
            record_audit_event(action="identity.mfa_verified", actor=request.user, request=request)
            next_url = request.GET.get("next") or ""
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect("dashboard")
    return render(
        request,
        "accounts/mfa_verify.html",
        {
            "device": device,
            "token_form": token_form,
            "recovery_form": recovery_form,
            "next_url": request.GET.get("next") or "",
        },
    )


@login_required
@require_POST
def recovery_codes_regenerate(request):
    if not getattr(request.user, "is_verified", lambda: False)():
        return redirect("mfa-verify")
    codes = generate_recovery_codes(user=request.user)
    record_audit_event(action="identity.recovery_codes_regenerated", actor=request.user, request=request)
    return render(request, "accounts/recovery_codes.html", {"codes": codes})


@login_required
@require_POST
def session_revoke(request, session_id):
    tracked = get_object_or_404(UserSession, id=session_id, user=request.user)
    current = tracked.session_key == request.session.session_key
    revoke_session(user=request.user, session_key=tracked.session_key)
    record_audit_event(action="identity.session_revoked", actor=request.user, metadata={"session_id": str(session_id)}, request=request)
    messages.success(request, "Session revoked.")
    if current:
        logout(request)
        return redirect("login")
    return redirect("security-settings")


@login_required
@require_POST
def sessions_revoke_others(request):
    current_key = request.session.session_key
    for tracked in request.user.tracked_sessions.exclude(session_key=current_key):
        revoke_session(user=request.user, session_key=tracked.session_key)
    record_audit_event(action="identity.other_sessions_revoked", actor=request.user, request=request)
    messages.success(request, "Other sessions revoked.")
    return redirect("security-settings")
