import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_audit_event
from apps.sales.models import Sale, SaleStatus

from .models import OfflineInvoiceQueue, OfflineInvoiceQueueStatus


OFFLINE_DRAFT_SCHEMA_VERSION = 1
OFFLINE_DRAFT_MAX_LINES = 100


def safe_recovery_payload(invoice):
    lines = invoice.get("lines") if isinstance(invoice.get("lines"), list) else []
    if len(lines) > OFFLINE_DRAFT_MAX_LINES:
        raise ValidationError(
            f"This recovery draft has more than {OFFLINE_DRAFT_MAX_LINES} items. "
            "Keep it on the device and ask a manager to split or review it before upload."
        )
    safe_lines = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        safe_lines.append({
            "product": str(line.get("product") or "")[:200],
            "quantity": str(line.get("quantity") or "")[:40],
            "total": str(line.get("total") or "")[:40],
        })
    return {
        "client_reference": str(invoice.get("client_reference") or invoice.get("clientReference") or "").strip()[:120],
        "created_at": str(invoice.get("created_at") or "")[:80],
        "location": str(invoice.get("location") or "")[:120],
        "cart_id": str(invoice.get("cart_id") or "")[:36],
        "schema_version": invoice.get("schema_version") or OFFLINE_DRAFT_SCHEMA_VERSION,
        "device_id": str(invoice.get("device_id") or "")[:120],
        "lines": safe_lines,
    }


def recovery_payload_digest(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@transaction.atomic
def upload_recovery_draft(*, organization, session, location, cashier, invoice, request=None):
    payload = safe_recovery_payload(invoice)
    client_reference = payload["client_reference"]
    if not client_reference:
        raise ValidationError("The recovery draft has no client reference.")
    if payload["schema_version"] != OFFLINE_DRAFT_SCHEMA_VERSION:
        raise ValidationError("This recovery draft uses an unsupported format. Keep it on the device for assisted review.")

    draft_sale = None
    if payload["cart_id"]:
        draft_sale = Sale.objects.filter(
            id=payload["cart_id"],
            organization=organization,
            session=session,
            location=location,
            created_by=cashier,
            status=SaleStatus.DRAFT,
        ).first()
        if not draft_sale:
            raise ValidationError("The linked live cart is no longer available. Keep this draft for assisted review.")

    digest = recovery_payload_digest(payload)
    existing = OfflineInvoiceQueue.objects.select_for_update().filter(
        organization=organization,
        client_reference=client_reference,
    ).first()
    if existing:
        if existing.payload_digest != digest:
            raise ValidationError("This recovery reference already exists with different contents.")
        return existing, False

    recovery = OfflineInvoiceQueue.objects.create(
        organization=organization,
        client_reference=client_reference,
        session=session,
        location=location,
        cashier=cashier,
        draft_sale=draft_sale,
        schema_version=payload["schema_version"],
        device_id=payload["device_id"],
        payload_digest=digest,
        payload=payload,
        status=OfflineInvoiceQueueStatus.QUEUED,
        synced_at=timezone.now(),
    )
    record_audit_event(
        action="pos.recovery_draft_uploaded",
        actor=cashier,
        organization=organization,
        target=recovery,
        request=request,
        metadata={"linked_cart": str(draft_sale.id) if draft_sale else ""},
    )
    return recovery, True


@transaction.atomic
def reconcile_recovery_drafts(*, sale, actor, request=None):
    recoveries = OfflineInvoiceQueue.objects.select_for_update().filter(
        organization=sale.organization,
        draft_sale=sale,
        status__in=(OfflineInvoiceQueueStatus.QUEUED, OfflineInvoiceQueueStatus.FAILED),
    )
    now = timezone.now()
    updated = list(recoveries)
    recoveries.update(
        status=OfflineInvoiceQueueStatus.COMPLETED,
        sale_number=sale.number,
        reviewed_by=actor,
        reviewed_at=now,
        review_notes="Reconciled automatically when the linked live cart completed.",
        updated_at=now,
    )
    for recovery in updated:
        record_audit_event(
            action="pos.recovery_draft_reconciled",
            actor=actor,
            organization=sale.organization,
            target=recovery,
            request=request,
            metadata={"sale_number": sale.number},
        )
    return len(updated)


@transaction.atomic
def discard_recovery_draft(*, recovery, actor, reason, request=None):
    recovery = OfflineInvoiceQueue.objects.select_for_update().get(pk=recovery.pk)
    reason = (reason or "").strip()
    if recovery.status not in (OfflineInvoiceQueueStatus.QUEUED, OfflineInvoiceQueueStatus.FAILED):
        raise ValidationError("Only a draft awaiting review or in conflict can be discarded.")
    if len(reason) < 5:
        raise ValidationError("Enter a clear reason for discarding this recovery draft.")
    recovery.status = OfflineInvoiceQueueStatus.DISCARDED
    recovery.reviewed_by = actor
    recovery.reviewed_at = timezone.now()
    recovery.review_notes = reason
    recovery.save(update_fields=("status", "reviewed_by", "reviewed_at", "review_notes", "updated_at"))
    record_audit_event(
        action="pos.recovery_draft_discarded",
        actor=actor,
        organization=recovery.organization,
        target=recovery,
        request=request,
        metadata={"reason": reason},
    )
    return recovery
