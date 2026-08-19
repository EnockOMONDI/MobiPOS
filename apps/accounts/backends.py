from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrUsernameBackend(ModelBackend):
    """Authenticate with email while retaining compatibility for legacy usernames."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = (username or kwargs.get("email") or "").strip()
        if not identifier or password is None:
            return None

        UserModel = get_user_model()
        lookup = {"email__iexact": identifier} if "@" in identifier else {"username__iexact": identifier}
        try:
            user = UserModel._default_manager.get(**lookup)
        except UserModel.DoesNotExist:
            UserModel().set_password(password)
            return None
        if user.requires_password_setup:
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
