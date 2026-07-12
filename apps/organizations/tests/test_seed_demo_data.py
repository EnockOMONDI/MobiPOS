import pytest
from django.core.management import call_command

from apps.accounts.models import User
from apps.organizations.models import AgentProfile, Location, Membership, Organization


@pytest.mark.django_db
def test_seed_demo_data_is_repeatable():
    call_command("seed_demo_data")
    call_command("seed_demo_data")

    assert Organization.objects.count() == 2
    assert User.objects.count() == 11
    assert Membership.objects.count() == 10
    assert Location.objects.count() == 6
    assert AgentProfile.objects.count() == 4
    assert User.objects.get(username="alice").check_password("DemoPass123!")
    assert User.objects.get(username="nairobi-mobile-hub-agent").check_password("DemoPass123!")
    assert User.objects.get(username="platformadmin").is_superuser
