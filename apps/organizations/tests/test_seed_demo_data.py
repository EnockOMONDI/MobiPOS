import pytest
from django.core.management import call_command

from apps.accounts.models import User
from apps.organizations.models import Location, Membership, Organization


@pytest.mark.django_db
def test_seed_demo_data_is_repeatable():
    call_command("seed_demo_data")
    call_command("seed_demo_data")

    assert Organization.objects.count() == 2
    assert User.objects.count() == 3
    assert Membership.objects.count() == 2
    assert Location.objects.count() == 4
    assert User.objects.get(username="alice").check_password("DemoPass123!")
    assert User.objects.get(username="platformadmin").is_superuser
