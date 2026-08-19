from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.integrations.models import IntegrationEvent, IntegrationStatus
from apps.inventory.models import StockBalance, StockMovement
from apps.operations.models import Payable, PayablePayment, Receivable
from apps.payments.models import Payment, PaymentStatus, Refund
from apps.purchasing.models import PurchaseOrderLine, SupplierReturn, SupplierReturnStatus
from apps.sales.models import Sale


ZERO = Decimal("0")


@dataclass(frozen=True)
class ReconciliationIssue:
    area: str
    organization_id: str
    record_type: str
    record_id: str
    message: str
    expected: str = ""
    actual: str = ""

    def as_dict(self):
        return asdict(self)


def _decimal(value):
    return Decimal(str(value or ZERO))


def _money(value):
    return _decimal(value).quantize(Decimal("0.01"))


def _add_mismatch(issues, *, area, organization_id, record_type, record_id, message, expected, actual):
    issues.append(ReconciliationIssue(
        area=area,
        organization_id=str(organization_id),
        record_type=record_type,
        record_id=str(record_id),
        message=message,
        expected=str(expected),
        actual=str(actual),
    ))


def reconcile_stock(*, organization=None):
    issues = []
    movements = StockMovement.objects.all()
    balances = StockBalance.objects.all()
    if organization:
        movements = movements.filter(organization=organization)
        balances = balances.filter(organization=organization)

    expected = {
        (row["organization_id"], row["product_id"], row["location_id"]): _decimal(row["quantity"])
        for row in movements.values("organization_id", "product_id", "location_id").annotate(quantity=Sum("quantity"))
    }
    actual = {
        (balance.organization_id, balance.product_id, balance.location_id): balance
        for balance in balances.only("id", "organization_id", "product_id", "location_id", "quantity")
    }
    for key in expected.keys() | actual.keys():
        expected_quantity = expected.get(key, ZERO)
        balance = actual.get(key)
        actual_quantity = _decimal(balance.quantity if balance else ZERO)
        if expected_quantity != actual_quantity:
            _add_mismatch(
                issues,
                area="stock",
                organization_id=key[0],
                record_type="StockBalance",
                record_id=balance.id if balance else f"{key[1]}:{key[2]}",
                message="Stock balance does not match the append-only movement ledger.",
                expected=expected_quantity,
                actual=actual_quantity,
            )
    return issues


def reconcile_sales_and_receivables(*, organization=None):
    issues = []
    sales = Sale.objects.all().only("id", "organization_id", "total", "paid_total")
    payments = Payment.objects.filter(status__in=(PaymentStatus.CONFIRMED, PaymentStatus.REFUNDED), sale__isnull=False)
    refunds = Refund.objects.filter(status=PaymentStatus.REFUNDED, payment__sale__isnull=False)
    receivables = Receivable.objects.select_related("sale")
    if organization:
        sales = sales.filter(organization=organization)
        payments = payments.filter(organization=organization)
        refunds = refunds.filter(organization=organization)
        receivables = receivables.filter(organization=organization)

    payment_totals = {
        row["sale_id"]: _money(row["amount"])
        for row in payments.values("sale_id").annotate(amount=Sum("amount"))
    }
    refund_totals = {
        row["payment__sale_id"]: _money(row["amount"])
        for row in refunds.values("payment__sale_id").annotate(amount=Sum("amount"))
    }
    for sale in sales:
        expected_paid = max(payment_totals.get(sale.id, ZERO) - refund_totals.get(sale.id, ZERO), ZERO)
        if _money(sale.paid_total) != _money(expected_paid):
            _add_mismatch(
                issues,
                area="payments",
                organization_id=sale.organization_id,
                record_type="Sale",
                record_id=sale.id,
                message="Sale paid total does not match confirmed payments less completed refunds.",
                expected=_money(expected_paid),
                actual=_money(sale.paid_total),
            )

    for receivable in receivables:
        expected_outstanding = ZERO if receivable.is_written_off else max(
            _money(receivable.sale.total) - _money(receivable.sale.paid_total), ZERO
        )
        if _money(receivable.outstanding_amount) != _money(expected_outstanding):
            _add_mismatch(
                issues,
                area="receivables",
                organization_id=receivable.organization_id,
                record_type="Receivable",
                record_id=receivable.id,
                message="Customer receivable does not match the current sale balance.",
                expected=_money(expected_outstanding),
                actual=_money(receivable.outstanding_amount),
            )
    return issues


def reconcile_payables(*, organization=None):
    issues = []
    payables = Payable.objects.all().only(
        "id", "organization_id", "purchase_order_id", "original_amount", "outstanding_amount", "credit_balance", "is_settled"
    )
    lines = PurchaseOrderLine.objects.all()
    returns = SupplierReturn.objects.filter(status=SupplierReturnStatus.COMPLETED)
    payments = PayablePayment.objects.filter(reversed_at__isnull=True)
    if organization:
        payables = payables.filter(organization=organization)
        lines = lines.filter(organization=organization)
        returns = returns.filter(organization=organization)
        payments = payments.filter(organization=organization)

    # Decimal multiplication is kept in Python to remain portable across SQLite test runs and PostgreSQL.
    received = {}
    for line in lines.only("order_id", "received_quantity", "unit_cost"):
        received[line.order_id] = received.get(line.order_id, ZERO) + _money(line.received_quantity * line.unit_cost)
    credits = {}
    for supplier_return in returns.select_related("line").only("line__order_id", "line__unit_cost", "quantity"):
        order_id = supplier_return.line.order_id
        credits[order_id] = credits.get(order_id, ZERO) + _money(supplier_return.quantity * supplier_return.line.unit_cost)
    paid = {
        row["payable_id"]: _money(row["amount"])
        for row in payments.values("payable_id").annotate(amount=Sum("amount"))
    }

    for payable in payables:
        original = _money(received.get(payable.purchase_order_id, ZERO))
        net = original - _money(credits.get(payable.purchase_order_id, ZERO)) - paid.get(payable.id, ZERO)
        expected_outstanding = _money(max(net, ZERO))
        expected_credit = _money(max(-net, ZERO))
        expected_settled = expected_outstanding == ZERO
        actual = (
            _money(payable.original_amount),
            _money(payable.outstanding_amount),
            _money(payable.credit_balance),
            payable.is_settled,
        )
        expected_values = (original, expected_outstanding, expected_credit, expected_settled)
        if actual != expected_values:
            _add_mismatch(
                issues,
                area="payables",
                organization_id=payable.organization_id,
                record_type="Payable",
                record_id=payable.id,
                message="Supplier payable does not match receipts, completed returns, and active payments.",
                expected=expected_values,
                actual=actual,
            )
    return issues


def reconcile_integrations(*, organization=None, stale_after=timedelta(minutes=15)):
    issues = []
    events = IntegrationEvent.objects.all().only(
        "id", "organization_id", "status", "next_retry_at", "updated_at", "provider", "event_type"
    )
    if organization:
        events = events.filter(organization=organization)
    stale_before = timezone.now() - stale_after
    for event in events:
        message = ""
        if event.status == IntegrationStatus.PROCESSING and event.updated_at < stale_before:
            message = "Integration event is stuck in processing and needs retry review."
        elif event.status == IntegrationStatus.FAILED and event.next_retry_at is None:
            message = "Failed integration event has no retry schedule."
        elif event.status == IntegrationStatus.DEAD:
            message = "Integration event exhausted retries and needs administrator action."
        if message:
            issues.append(ReconciliationIssue(
                area="integrations",
                organization_id=str(event.organization_id),
                record_type="IntegrationEvent",
                record_id=str(event.id),
                message=message,
                actual=f"{event.provider}:{event.event_type}:{event.status}",
            ))
    return issues


def run_reconciliation(*, organization=None):
    issues = [
        *reconcile_stock(organization=organization),
        *reconcile_sales_and_receivables(organization=organization),
        *reconcile_payables(organization=organization),
        *reconcile_integrations(organization=organization),
    ]
    return sorted(issues, key=lambda issue: (issue.area, issue.organization_id, issue.record_type, issue.record_id))
