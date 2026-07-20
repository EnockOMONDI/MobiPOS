import pytest

from apps.accounts.models import User


@pytest.mark.django_db
def test_user_uses_uuid_and_unique_email():
    user = User.objects.create_user(username="owner", email="owner@example.com")

    assert user.pk is not None
    assert str(user) == "owner@example.com"
    assert not user.is_demo_account
