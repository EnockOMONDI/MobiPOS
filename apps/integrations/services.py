from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    FiscalDevice,
    FiscalDocument,
    FiscalDocumentLine,
    FiscalDocumentStatus,
    FiscalDocumentType,
    FiscalProvider,
    IntegrationEvent,
)


def queue_integration_event(*, organization, provider, event_type, idempotency_key, payload):
    event, _ = IntegrationEvent.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            "organization": organization,
            "provider": provider,
            "event_type": event_type,
            "payload": payload,
        },
    )
    return event


def active_etims_device_for_sale(sale):
    queryset = FiscalDevice.objects.filter(
        organization=sale.organization,
        provider=FiscalProvider.ETIMS,
    ).order_by("-location_id", "-company_id", "-updated_at")
    for location_device in queryset.filter(location=sale.location):
        if location_device.can_submit_etims:
            return location_device
    for company_device in queryset.filter(company=sale.location.branch.company, location__isnull=True):
        if company_device.can_submit_etims:
            return company_device
    for organization_device in queryset.filter(company__isnull=True, location__isnull=True):
        if organization_device.can_submit_etims:
            return organization_device
    return None


def _decimal(value):
    return Decimal(value or 0).quantize(Decimal("0.01"))


def _payment_summary(sale):
    return [
        {
            "method": payment.method,
            "status": payment.status,
            "amount": str(_decimal(payment.amount)),
            "provider_reference": payment.provider_reference,
        }
        for payment in sale.payments.all().order_by("received_at")
    ]


def _line_imei(line):
    if not line.stock_unit_id:
        return ""
    return line.stock_unit.secondary_serial or line.stock_unit.serial_number


def build_etims_sale_payload(*, document):
    sale = document.sale
    if not sale:
        raise ValidationError("A sale fiscal document requires a sale.")
    device = document.device
    lines = []
    for index, line in enumerate(sale.lines.select_related("product", "stock_unit"), start=1):
        tax_rate = line.product.tax_rate
        total_amount = line.total_after_tax
        lines.append({
            "line_number": index,
            "item_code": line.product.sku or line.product.barcode or str(line.product_id),
            "item_name": line.product.name,
            "quantity": str(line.quantity),
            "unit_price": str(_decimal(line.unit_price)),
            "discount": str(_decimal(line.discount)),
            "tax_rate": str(tax_rate),
            "tax_type_code": "B" if tax_rate else "A",
            "tax_amount": str(_decimal(line.tax)),
            "supply_amount": str(_decimal(line.line_total - line.discount)),
            "total_amount": str(_decimal(total_amount)),
            "imei_or_serial": _line_imei(line),
        })
    return {
        "provider": FiscalProvider.ETIMS,
        "environment": device.environment,
        "mode": device.mode,
        "taxpayer_pin": device.taxpayer_pin,
        "branch_office_id": device.branch_office_id,
        "device_serial": device.device_serial,
        "internal_receipt_number": sale.number,
        "document_number": document.internal_number,
        "receipt_type_code": document.receipt_type_code,
        "transaction_type_code": document.transaction_type_code,
        "issued_at": (sale.completed_at or sale.created_at).isoformat(),
        "seller": {
            "organization": sale.organization.name,
            "branch": sale.location.branch.name,
            "location": sale.location.name,
        },
        "buyer": {
            "name": sale.customer.name if sale.customer else "Walk-in Customer",
            "pin": sale.customer.tax_number if sale.customer else "",
            "phone": sale.customer.phone_number if sale.customer else "",
        },
        "totals": {
            "subtotal": str(_decimal(sale.subtotal)),
            "discount": str(_decimal(sale.discount_total)),
            "tax": str(_decimal(sale.tax_total)),
            "total": str(_decimal(sale.total)),
            "paid": str(_decimal(sale.paid_total)),
            "balance": str(_decimal(sale.balance_due)),
        },
        "payments": _payment_summary(sale),
        "lines": lines,
    }


def build_etims_credit_note_payload(*, document):
    sale_return = document.sale_return
    if not sale_return:
        raise ValidationError("A credit note fiscal document requires a sale return.")
    original_document = sale_return.sale.fiscal_documents.filter(
        provider=FiscalProvider.ETIMS,
        document_type=FiscalDocumentType.SALE,
    ).first()
    return {
        "provider": FiscalProvider.ETIMS,
        "environment": document.device.environment,
        "mode": document.device.mode,
        "taxpayer_pin": document.device.taxpayer_pin,
        "branch_office_id": document.device.branch_office_id,
        "device_serial": document.device.device_serial,
        "internal_receipt_number": sale_return.number,
        "document_number": document.internal_number,
        "receipt_type_code": document.receipt_type_code,
        "transaction_type_code": document.transaction_type_code,
        "original_sale_number": sale_return.sale.number,
        "original_etims_invoice_number": original_document.etims_invoice_number if original_document else "",
        "issued_at": sale_return.updated_at.isoformat(),
        "reason": sale_return.reason,
        "totals": {
            "refund_amount": str(_decimal(sale_return.refund_amount)),
        },
        "lines": [
            {
                "line_number": index,
                "item_code": line.sale_line.product.sku or line.sale_line.product.barcode or str(line.sale_line.product_id),
                "item_name": line.sale_line.product.name,
                "quantity": str(line.quantity),
                "tax_amount": str(_decimal(line.sale_line.tax)),
                "refundable_amount": str(_decimal(line.refundable_amount)),
                "imei_or_serial": _line_imei(line.sale_line),
            }
            for index, line in enumerate(
                sale_return.lines.select_related("sale_line__product", "sale_line__stock_unit"),
                start=1,
            )
        ],
    }


def _sync_document_lines(document):
    document.lines.all().delete()
    if document.document_type == FiscalDocumentType.SALE:
        sale_lines = document.sale.lines.select_related("product", "stock_unit")
        FiscalDocumentLine.objects.bulk_create([
            FiscalDocumentLine(
                organization=document.organization,
                document=document,
                product_name=line.product.name,
                item_code=line.product.sku or line.product.barcode or str(line.product_id),
                quantity=line.quantity,
                unit_price=line.unit_price,
                discount=line.discount,
                tax_rate=line.product.tax_rate,
                tax_amount=line.tax,
                total_amount=line.total_after_tax,
                imei_or_serial=_line_imei(line),
                tax_type_code="B" if line.product.tax_rate else "A",
            )
            for line in sale_lines
        ])
    else:
        FiscalDocumentLine.objects.bulk_create([
            FiscalDocumentLine(
                organization=document.organization,
                document=document,
                product_name=line.sale_line.product.name,
                item_code=line.sale_line.product.sku or line.sale_line.product.barcode or str(line.sale_line.product_id),
                quantity=line.quantity,
                unit_price=line.sale_line.unit_price,
                discount=line.sale_line.discount,
                tax_rate=line.sale_line.product.tax_rate,
                tax_amount=line.sale_line.tax,
                total_amount=line.refundable_amount,
                imei_or_serial=_line_imei(line.sale_line),
                tax_type_code="B" if line.sale_line.product.tax_rate else "A",
            )
            for line in document.sale_return.lines.select_related("sale_line__product", "sale_line__stock_unit")
        ])


@transaction.atomic
def create_sale_fiscal_document(*, sale):
    device = active_etims_device_for_sale(sale)
    if not device:
        return None
    document, _ = FiscalDocument.objects.get_or_create(
        organization=sale.organization,
        sale=sale,
        document_type=FiscalDocumentType.SALE,
        defaults={
            "provider": FiscalProvider.ETIMS,
            "device": device,
            "internal_number": sale.number,
            "receipt_type_code": "NS",
            "transaction_type_code": "S",
        },
    )
    if document.status in {FiscalDocumentStatus.ACCEPTED, FiscalDocumentStatus.SUBMITTED}:
        return document
    document.device = device
    document.payload_snapshot = build_etims_sale_payload(document=document)
    document.status = FiscalDocumentStatus.PENDING
    document.last_error = ""
    document.save(update_fields=["device", "payload_snapshot", "status", "last_error", "updated_at"])
    _sync_document_lines(document)
    queue_integration_event(
        organization=sale.organization,
        provider=FiscalProvider.ETIMS,
        event_type="invoice.submit",
        idempotency_key=f"etims-sale-{sale.id}",
        payload={"fiscal_document_id": str(document.id), **document.payload_snapshot},
    )
    return document


@transaction.atomic
def create_return_fiscal_document(*, sale_return):
    device = active_etims_device_for_sale(sale_return.sale)
    if not device:
        return None
    document, _ = FiscalDocument.objects.get_or_create(
        organization=sale_return.organization,
        sale_return=sale_return,
        document_type=FiscalDocumentType.CREDIT_NOTE,
        defaults={
            "provider": FiscalProvider.ETIMS,
            "device": device,
            "internal_number": sale_return.number,
            "receipt_type_code": "NC",
            "transaction_type_code": "R",
        },
    )
    if document.status in {FiscalDocumentStatus.ACCEPTED, FiscalDocumentStatus.SUBMITTED}:
        return document
    document.device = device
    document.payload_snapshot = build_etims_credit_note_payload(document=document)
    document.status = FiscalDocumentStatus.PENDING
    document.last_error = ""
    document.save(update_fields=["device", "payload_snapshot", "status", "last_error", "updated_at"])
    _sync_document_lines(document)
    queue_integration_event(
        organization=sale_return.organization,
        provider=FiscalProvider.ETIMS,
        event_type="credit_note.submit",
        idempotency_key=f"etims-return-{sale_return.id}",
        payload={"fiscal_document_id": str(document.id), **document.payload_snapshot},
    )
    return document


@transaction.atomic
def apply_etims_event_result(*, event):
    fiscal_document_id = event.payload.get("fiscal_document_id")
    if not fiscal_document_id:
        return None
    document = FiscalDocument.objects.select_for_update().filter(
        organization=event.organization,
        id=fiscal_document_id,
    ).first()
    if not document:
        return None
    document.response_snapshot = event.response or {}
    if event.status == "succeeded":
        document.status = FiscalDocumentStatus.ACCEPTED
        document.submitted_at = document.submitted_at or timezone.now()
        document.accepted_at = timezone.now()
        document.failed_at = None
        document.last_error = ""
        document.etims_invoice_number = event.response.get("invoice_number", event.response.get("reference", ""))
        document.etims_control_code = event.response.get("control_code", "")
        document.etims_signature = event.response.get("signature", "")
        document.qr_payload = event.response.get("qr_payload", "")
    elif event.status in {"failed", "dead"}:
        document.status = FiscalDocumentStatus.DEAD if event.status == "dead" else FiscalDocumentStatus.FAILED
        document.failed_at = timezone.now()
        document.last_error = event.error_message
    else:
        document.status = FiscalDocumentStatus.SUBMITTED
        document.submitted_at = document.submitted_at or timezone.now()
    document.save(update_fields=[
        "response_snapshot", "status", "submitted_at", "accepted_at", "failed_at",
        "last_error", "etims_invoice_number", "etims_control_code", "etims_signature",
        "qr_payload", "updated_at",
    ])
    return document
