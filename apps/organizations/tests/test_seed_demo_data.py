import pytest
from django.core.management import call_command

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.catalog.models import Product
from apps.contacts.models import Contact, ContactType
from apps.integrations.models import FiscalDevice, FiscalDocument, FiscalDocumentStatus, IntegrationEvent, IntegrationStatus
from apps.inventory.models import StockUnit
from apps.organizations.models import AgentProfile, Branch, Location, Membership, Organization
from apps.purchasing.models import PurchaseDiscrepancy, PurchaseOrder, SupplierReturn
from apps.repairs.models import RepairTicket
from apps.sales.models import Sale, SaleReturn
from apps.transfers.models import StockTransfer, TransferDiscrepancy


@pytest.mark.django_db
def test_seed_demo_data_is_repeatable_and_rich():
    call_command("seed_demo_data")
    call_command("seed_demo_data")

    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    assert Organization.objects.count() == 1
    assert not User.objects.filter(username="alice").exists()
    assert User.objects.get(username="brian").check_password("DemoPass123!")
    assert User.objects.get(username="brian").is_demo_account
    assert User.objects.get(username="nairobi-mobile-hub-agent").check_password("DemoPass123!")
    assert User.objects.get(username="platformadmin").is_superuser
    assert Membership.objects.filter(organization=organization).count() >= 7
    assert Branch.objects.filter(organization=organization).count() >= 3
    assert Location.objects.filter(organization=organization).count() >= 7
    assert AgentProfile.objects.filter(organization=organization).count() == 2
    assert Product.objects.filter(organization=organization).count() >= 12
    assert Contact.objects.filter(organization=organization, contact_type=ContactType.CUSTOMER).count() >= 8
    assert StockUnit.objects.filter(organization=organization).count() >= 60
    assert PurchaseOrder.objects.filter(organization=organization).count() >= 6
    assert PurchaseDiscrepancy.objects.filter(organization=organization).exists()
    assert SupplierReturn.objects.filter(organization=organization).exists()
    assert StockTransfer.objects.filter(organization=organization).count() >= 7
    assert TransferDiscrepancy.objects.filter(organization=organization).exists()
    assert Sale.objects.filter(organization=organization).count() >= 25
    etims_device = FiscalDevice.objects.get(organization=organization, branch_office_id="51404677J")
    assert etims_device.taxpayer_pin == "P051234568B"
    assert etims_device.environment == "sandbox"
    assert etims_device.device_serial == "SBX-NMH-OSCU-001"
    etims_document = FiscalDocument.objects.get(
        organization=organization,
        sale__number="DEMO-SALE-001",
    )
    assert etims_document.status == FiscalDocumentStatus.ACCEPTED
    assert etims_document.etims_invoice_number
    assert IntegrationEvent.objects.get(idempotency_key=f"etims-sale-{etims_document.sale_id}").status == IntegrationStatus.SUCCEEDED
    assert SaleReturn.objects.filter(organization=organization).exists()
    assert RepairTicket.objects.filter(organization=organization).count() >= 6
    assert AuditEvent.objects.filter(organization=organization).count() >= 20
