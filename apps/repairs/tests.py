import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.contacts.models import Contact
from apps.catalog.models import Product
from apps.inventory.models import StockBalance
from apps.organizations.models import Branch, Location, Organization
from apps.repairs.models import RepairPartUsage, RepairTicket


@pytest.mark.django_db
def test_repair_ticket_workflow(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.filter(organization=organization).order_by("code").first()
    customer = Contact.objects.get(organization=organization, name="Demo Credit Customer")
    client.force_login(user)

    response = client.post(reverse("repair-create"), {
        "branch": branch.id, "customer": customer.id, "issue": "Broken screen",
        "quoted_amount": "5000",
    })
    ticket = RepairTicket.objects.filter(organization=organization, issue="Broken screen").get()
    assert response.status_code == 302
    client.post(reverse("repair-update", args=[ticket.id]), {
        "status": "closed", "diagnosis": "Screen replaced",
        "warranty_type": "customer", "warranty_decision_notes": "Covered",
    })
    ticket.refresh_from_db()
    assert ticket.status == "closed"
    assert ticket.collected_at is not None
    assert ticket.warranty

    part = Product.objects.get(organization=organization, sku="CHG-20W")
    location = Location.objects.filter(organization=organization, location_type="pos").order_by("code").first()
    before = StockBalance.objects.get(organization=organization, product=part, location=location).quantity
    client.post(reverse("repair-use-part", args=[ticket.id]), {
        "product": part.id, "location": location.id, "quantity": "1",
    })
    assert RepairPartUsage.objects.filter(ticket=ticket, product=part).exists()
    assert StockBalance.objects.get(organization=organization, product=part, location=location).quantity == before - 1
