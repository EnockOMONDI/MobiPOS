import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.commissions.models import CommissionPayout
from apps.contacts.models import Contact
from apps.expenses.models import Expense
from apps.inventory.models import AgedStockAction, StockAdjustment, StockMovement, StockUnit
from apps.notifications.models import Notification
from apps.operations.models import ApprovalRequest, Payable, PayablePayment, Receivable
from apps.organizations.models import (
    AgentProfile,
    Branch,
    Location,
    Membership,
    MembershipStatus,
    Organization,
)
from apps.purchasing.models import (
    PurchaseDiscrepancy,
    PurchaseOrder,
    PurchaseOrderLine,
    SupplierReturn,
)
from apps.pos.models import OfflineInvoiceQueue, POSSession
from apps.repairs.models import RepairPartUsage, RepairPayment, RepairTicket
from apps.reports.models import ReportExport, ReportExportStatus
from apps.sales.models import Sale, SaleReturn
from apps.transfers.models import StockTransfer, TransferDiscrepancy


def _first(model):
    record = model.objects.first()
    assert record is not None, f"The demo scenario must include {model.__name__}."
    return record


@pytest.mark.django_db
def test_tenant_owned_detail_action_and_export_routes_reject_foreign_ids(client):
    call_command("seed_demo_data", verbosity=0)

    foreign_owner = User.objects.create_user(
        username="foreign-tenant-owner",
        email="foreign-owner@example.com",
        password="password",
    )
    foreign_organization = Organization.objects.create(
        name="Foreign Tenant",
        slug="foreign-tenant",
        status="active",
    )
    Membership.objects.create(
        user=foreign_owner,
        organization=foreign_organization,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    client.force_login(foreign_owner)

    branch = _first(Branch)
    location = _first(Location)
    membership = Membership.objects.exclude(organization=foreign_organization).first()
    agent = _first(AgentProfile)
    product = _first(Product)
    contact = _first(Contact)
    order = _first(PurchaseOrder)
    order_line = _first(PurchaseOrderLine)
    purchase_discrepancy = _first(PurchaseDiscrepancy)
    supplier_return = _first(SupplierReturn)
    transfer = _first(StockTransfer)
    transfer_discrepancy = _first(TransferDiscrepancy)
    sale = _first(Sale)
    sale_return = _first(SaleReturn)
    expense = _first(Expense)
    repair = _first(RepairTicket)
    repair_part = _first(RepairPartUsage)
    movement = _first(StockMovement)
    payout = _first(CommissionPayout)
    receivable = _first(Receivable)
    payable = _first(Payable)
    notification = Notification.objects.exclude(organization=foreign_organization).first()
    assert membership is not None
    assert notification is not None

    source_organization = branch.organization
    source_user = membership.user
    source_unit = _first(StockUnit)
    session = _first(POSSession)
    adjustment = StockAdjustment.objects.create(
        organization=source_organization,
        number="ISO-FOREIGN-001",
        product=product,
        location=location,
        quantity=1,
        reason="Tenant isolation test",
        requested_by=source_user,
    )
    approval = ApprovalRequest.objects.create(
        organization=source_organization,
        request_type="isolation_test",
        target_type="inventory.StockUnit",
        target_id=str(source_unit.id),
        reason="Tenant isolation test",
        requested_by=source_user,
    )
    aged_action = AgedStockAction.objects.create(
        organization=source_organization,
        stock_unit=source_unit,
        action_type="other",
        reason="Tenant isolation test",
        proposed_by=source_user,
    )
    recovery_draft = OfflineInvoiceQueue.objects.create(
        organization=source_organization,
        client_reference="foreign-tenant-recovery",
        session=session,
        location=session.location,
        cashier=session.cashier,
        payload={"schema_version": 1, "lines": []},
    )
    repair_payment = RepairPayment.objects.create(
        organization=source_organization,
        number="RP-FOREIGN-001",
        ticket=repair,
        amount=1,
        method="cash",
        received_by=source_user,
        received_at=timezone.now(),
    )
    payable_payment = PayablePayment.objects.create(
        organization=source_organization,
        number="PP-FOREIGN-001",
        payable=payable,
        amount=1,
        method="cash",
        paid_by=source_user,
        paid_at=timezone.now(),
    )
    report_export = ReportExport.objects.create(
        organization=source_organization,
        requested_by=source_user,
        module="sales",
        export_format="csv",
        status=ReportExportStatus.FAILED,
        expires_at=timezone.now(),
    )

    get_routes = (
        reverse("membership-access-update", args=[membership.id]),
        reverse("agent-profile-detail", args=[agent.id]),
        reverse("branch-update", args=[branch.id]),
        reverse("location-update", args=[location.id]),
        reverse("product-detail", args=[product.id]),
        reverse("product-update", args=[product.id]),
        reverse("contact-detail", args=[contact.id]),
        reverse("contact-statement", args=[contact.id]),
        reverse("contact-update", args=[contact.id]),
        reverse("purchase-detail", args=[order.id]),
        reverse("supplier-return-detail", args=[supplier_return.id]),
        reverse("transfer-detail", args=[transfer.id]),
        reverse("sale-detail", args=[sale.id]),
        reverse("expense-detail", args=[expense.id]),
        reverse("repair-detail", args=[repair.id]),
        reverse("commission-payout-detail", args=[payout.id]),
        reverse("payable-detail", args=[payable.id]),
        reverse("session-detail", args=[session.id]),
        reverse("stock-adjustment-detail", args=[adjustment.id]),
        reverse("approval-detail", args=[approval.id]),
        reverse("pos-recovery-detail", args=[recovery_draft.id]),
        reverse("report-export-detail", args=[report_export.id]),
        reverse("report-export-download", args=[report_export.id]),
    )
    post_routes = (
        reverse("agent-profile-decide", args=[agent.id, "approve"]),
        reverse("product-toggle-active", args=[product.id]),
        reverse("contact-toggle-active", args=[contact.id]),
        reverse("purchase-extract-document", args=[order.id]),
        reverse("purchase-approve", args=[order.id]),
        reverse("purchase-receive", args=[order_line.id]),
        reverse("purchase-discrepancy-resolve", args=[purchase_discrepancy.id]),
        reverse("supplier-return-complete", args=[supplier_return.id]),
        reverse("transfer-approve", args=[transfer.id]),
        reverse("transfer-dispatch", args=[transfer.id]),
        reverse("transfer-receive", args=[transfer.id]),
        reverse("transfer-discrepancy-resolve", args=[transfer_discrepancy.id]),
        reverse("sale-add-payment", args=[sale.id]),
        reverse("sale-request-return", args=[sale.id]),
        reverse("return-complete", args=[sale_return.id]),
        reverse("expense-resubmit", args=[expense.id]),
        reverse("expense-pay", args=[expense.id]),
        reverse("expense-approve", args=[expense.id]),
        reverse("repair-update", args=[repair.id]),
        reverse("repair-use-part", args=[repair.id]),
        reverse("repair-reverse-part", args=[repair.id, repair_part.id]),
        reverse("repair-add-payment", args=[repair.id]),
        reverse("notification-read", args=[notification.id]),
        reverse("stock-movement-reverse", args=[movement.id]),
        reverse("commission-payout-approve", args=[payout.id]),
        reverse("commission-payout-pay", args=[payout.id]),
        reverse("receivable-installment-schedule", args=[receivable.id]),
        reverse("payable-payment-create", args=[payable.id]),
        reverse("session-close", args=[session.id]),
        reverse("cash-movement-create", args=[session.id]),
        reverse("session-review", args=[session.id]),
        reverse("stock-adjustment-complete", args=[adjustment.id]),
        reverse("aged-stock-action-execute", args=[aged_action.id]),
        reverse("approval-decide", args=[approval.id]),
        reverse("pos-recovery-discard", args=[recovery_draft.id]),
        reverse("repair-reverse-payment", args=[repair.id, repair_payment.id]),
        reverse("payable-payment-reverse", args=[payable_payment.id]),
        reverse("report-export-retry", args=[report_export.id]),
    )

    for route in get_routes:
        response = client.get(route)
        assert response.status_code == 404, route

    for route in post_routes:
        response = client.post(route, {})
        assert response.status_code == 404, route
