from django.core.exceptions import ValidationError
from django.db import transaction

from apps.inventory.models import SerialStatus, StockMovementType
from apps.inventory.services import post_stock_movement
from .models import DiscrepancyStatus, TransferDiscrepancy, TransferStatus


@transaction.atomic
def approve_transfer(*, transfer, actor):
    if transfer.status != TransferStatus.REQUESTED:
        raise ValidationError("Only requested transfers can be approved.")
    if not transfer.lines.exists():
        raise ValidationError("A transfer requires at least one line.")
    transfer.status = TransferStatus.APPROVED
    transfer.approved_by = actor
    transfer.save(update_fields=["status", "approved_by", "updated_at"])
    return transfer


@transaction.atomic
def dispatch_transfer(*, transfer, actor):
    if transfer.status != TransferStatus.APPROVED:
        raise ValidationError("Only approved transfers can be dispatched.")
    for line in transfer.lines.select_related("product", "stock_unit"):
        post_stock_movement(
            organization=transfer.organization, product=line.product, location=transfer.source,
            quantity=-line.quantity, movement_type=StockMovementType.TRANSFER_DISPATCH,
            actor=actor, stock_unit=line.stock_unit, reference_type="stock_transfer", reference_id=transfer.id,
        )
        if line.stock_unit:
            line.stock_unit.status = SerialStatus.IN_TRANSFER
            line.stock_unit.location = None
            line.stock_unit.save(update_fields=["status", "location", "updated_at"])
    transfer.status = TransferStatus.IN_TRANSIT
    transfer.save(update_fields=["status", "updated_at"])
    return transfer


@transaction.atomic
def receive_transfer(*, transfer, actor, received_quantities=None, discrepancy_reason=""):
    if transfer.status != TransferStatus.IN_TRANSIT:
        raise ValidationError("Only in-transit transfers can be received.")
    received_quantities = received_quantities or {}
    has_discrepancy = False
    for line in transfer.lines.select_related("product", "stock_unit"):
        received = received_quantities.get(str(line.id), line.quantity)
        if received < 0 or received > line.quantity:
            raise ValidationError("Received quantity must be between zero and the dispatched quantity.")
        if line.stock_unit and received not in (0, 1):
            raise ValidationError("Serialized transfer receipts must be zero or one.")
        if received:
            post_stock_movement(
                organization=transfer.organization, product=line.product, location=transfer.destination,
                quantity=received, movement_type=StockMovementType.TRANSFER_RECEIPT,
                actor=actor, stock_unit=line.stock_unit, reference_type="stock_transfer", reference_id=transfer.id,
            )
        line.received_quantity = received
        line.save(update_fields=["received_quantity", "updated_at"])
        if received != line.quantity:
            has_discrepancy = True
            TransferDiscrepancy.objects.update_or_create(
                organization=transfer.organization,
                line=line,
                defaults={
                    "transfer": transfer,
                    "expected_quantity": line.quantity,
                    "received_quantity": received,
                    "difference": line.quantity - received,
                    "reason": discrepancy_reason or "Quantity received differs from dispatch.",
                },
            )
        elif line.stock_unit:
            line.stock_unit.status = SerialStatus.AVAILABLE
            line.stock_unit.location = transfer.destination
            line.stock_unit.save(update_fields=["status", "location", "updated_at"])
    transfer.status = TransferStatus.DISCREPANCY if has_discrepancy else TransferStatus.RECEIVED
    transfer.save(update_fields=["status", "updated_at"])
    return transfer


@transaction.atomic
def resolve_transfer_discrepancy(*, discrepancy, actor, resolution):
    if discrepancy.status != DiscrepancyStatus.PENDING:
        raise ValidationError("Only pending discrepancies can be resolved.")
    if resolution not in {"receive", "writeoff"}:
        raise ValidationError("Choose receive or writeoff.")
    line = discrepancy.line
    if resolution == "receive":
        post_stock_movement(
            organization=discrepancy.organization, product=line.product, location=discrepancy.transfer.destination,
            quantity=discrepancy.difference, movement_type=StockMovementType.TRANSFER_RECEIPT,
            actor=actor, stock_unit=line.stock_unit, reference_type="transfer_discrepancy", reference_id=discrepancy.id,
        )
        line.received_quantity = discrepancy.expected_quantity
        line.save(update_fields=["received_quantity", "updated_at"])
        if line.stock_unit:
            line.stock_unit.status = SerialStatus.AVAILABLE
            line.stock_unit.location = discrepancy.transfer.destination
            line.stock_unit.save(update_fields=["status", "location", "updated_at"])
    elif line.stock_unit:
        line.stock_unit.status = SerialStatus.WRITTEN_OFF
        line.stock_unit.location = None
        line.stock_unit.save(update_fields=["status", "location", "updated_at"])
    discrepancy.status = DiscrepancyStatus.RESOLVED
    discrepancy.resolution = resolution
    discrepancy.resolved_by = actor
    discrepancy.save(update_fields=["status", "resolution", "resolved_by", "updated_at"])
    if not discrepancy.transfer.discrepancies.filter(status=DiscrepancyStatus.PENDING).exists():
        discrepancy.transfer.status = TransferStatus.RECEIVED
        discrepancy.transfer.save(update_fields=["status", "updated_at"])
    return discrepancy
