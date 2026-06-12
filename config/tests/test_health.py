import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_health_endpoints(client):
    live = client.get(reverse("health-live"))
    assert live.json() == {"status": "ok"}
    assert live["X-Request-ID"]
    assert client.get(reverse("health-ready")).json() == {"status": "ok"}
