from django.core.exceptions import ValidationError
from django.db import transaction

from apps.inventory.models import SerialStatus, StockMovementType, StockUnit
from apps.inventory.services import post_stock_movement
from .models import (
    DiscrepancyStatus,
    StockTransfer,
    StockTransferLine,
    TransferDiscrepancy,
    TransferStatus,
    TransferTransition,
)


def _locked_transfer(transfer):
    return StockTransfer.objects.select_for_update().select_related(
        "source", "destination"
    ).get(pk=transfer.pk)


def _locked_lines(transfer):
    lines = list(
        StockTransferLine.objects.select_for_update()
        .filter(transfer=transfer)
        .select_related("product", "stock_unit")
        .order_by("id")
    )
    unit_ids = [line.stock_unit_id for line in lines if line.stock_unit_id]
    locked_units = {
        unit.id: unit
        for unit in StockUnit.objects.select_for_update().filter(id__in=unit_ids).order_by("id")
    }
    for line in lines:
        if line.stock_unit_id:
            line.stock_unit = locked_units[line.stock_unit_id]
    return lines


def _already_applied(transfer, transition):
    return TransferTransition.objects.filter(transfer=transfer, transition=transition).exists()


def _record_transition(transfer, transition, actor):
    TransferTransition.objects.create(
        organization=transfer.organization,
        transfer=transfer,
        transition=transition,
        performed_by=actor,
    )


@transaction.atomic
def approve_transfer(*, transfer, actor):
    transfer = _locked_transfer(transfer)
    if _already_applied(transfer, "approve"):
        return transfer
    if transfer.status != TransferStatus.REQUESTED:
        raise ValidationError("Only requested transfers can be approved.")
    lines = _locked_lines(transfer)
    if not lines:
        raise ValidationError("A transfer requires at least one line.")
    transfer.status = TransferStatus.APPROVED
    transfer.approved_by = actor
    transfer.save(update_fields=["status", "approved_by", "updated_at"])
    _record_transition(transfer, "approve", actor)
    return transfer


@transaction.atomic
def dispatch_transfer(*, transfer, actor):
    transfer = _locked_transfer(transfer)
    if _already_applied(transfer, "dispatch"):
        return transfer
    if transfer.status != TransferStatus.APPROVED:
        raise ValidationError("Only approved transfers can be dispatched.")
    lines = _locked_lines(transfer)
    for line in lines:
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
    _record_transition(transfer, "dispatch", actor)
    return transfer


@transaction.atomic
def receive_transfer(*, transfer, actor, received_quantities=None, discrepancy_reason=""):
    transfer = _locked_transfer(transfer)
    if _already_applied(transfer, "receive"):
        return transfer
    if transfer.status != TransferStatus.IN_TRANSIT:
        raise ValidationError("Only in-transit transfers can be received.")
    received_quantities = received_quantities or {}
    has_discrepancy = False
    lines = _locked_lines(transfer)
    for line in lines:
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
    _record_transition(transfer, "receive", actor)
    return transfer


@transaction.atomic
def resolve_transfer_discrepancy(*, discrepancy, actor, resolution):
    transfer_identity = TransferDiscrepancy.objects.only("transfer_id").get(pk=discrepancy.pk)
    transfer = _locked_transfer(StockTransfer(pk=transfer_identity.transfer_id))
    transition = f"resolve:{discrepancy.pk}:{resolution}"
    if _already_applied(transfer, transition):
        return TransferDiscrepancy.objects.get(pk=discrepancy.pk)
    discrepancy = TransferDiscrepancy.objects.select_for_update().select_related(
        "line__product", "line__stock_unit"
    ).get(pk=discrepancy.pk)
    if discrepancy.status != DiscrepancyStatus.PENDING:
        raise ValidationError("Only pending discrepancies can be resolved.")
    if resolution not in {"receive", "writeoff"}:
        raise ValidationError("Choose receive or writeoff.")
    line = StockTransferLine.objects.select_for_update().select_related("product", "stock_unit").get(
        pk=discrepancy.line_id
    )
    if line.stock_unit_id:
        line.stock_unit = StockUnit.objects.select_for_update().get(pk=line.stock_unit_id)
    if resolution == "receive":
        post_stock_movement(
            organization=discrepancy.organization, product=line.product, location=transfer.destination,
            quantity=discrepancy.difference, movement_type=StockMovementType.TRANSFER_RECEIPT,
            actor=actor, stock_unit=line.stock_unit, reference_type="transfer_discrepancy", reference_id=discrepancy.id,
        )
        line.received_quantity = discrepancy.expected_quantity
        line.save(update_fields=["received_quantity", "updated_at"])
        if line.stock_unit:
            line.stock_unit.status = SerialStatus.AVAILABLE
            line.stock_unit.location = transfer.destination
            line.stock_unit.save(update_fields=["status", "location", "updated_at"])
    elif line.stock_unit:
        line.stock_unit.status = SerialStatus.WRITTEN_OFF
        line.stock_unit.location = None
        line.stock_unit.save(update_fields=["status", "location", "updated_at"])
    discrepancy.status = DiscrepancyStatus.RESOLVED
    discrepancy.resolution = resolution
    discrepancy.resolved_by = actor
    discrepancy.save(update_fields=["status", "resolution", "resolved_by", "updated_at"])
    if not transfer.discrepancies.filter(status=DiscrepancyStatus.PENDING).exists():
        transfer.status = TransferStatus.RECEIVED
        transfer.save(update_fields=["status", "updated_at"])
    _record_transition(transfer, transition, actor)
    return discrepancy
