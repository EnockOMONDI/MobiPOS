from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.inventory.models import SerialStatus, StockMovementType, StockUnit
from apps.inventory.services import post_stock_movement
from .models import (
    PurchaseDiscrepancyStatus,
    PurchaseStatus,
    PurchaseReceipt,
    SupplierReturnStatus,
)


@transaction.atomic
def approve_purchase_order(*, order, actor):
    order = order.__class__.objects.select_for_update().get(pk=order.pk)
    if order.status != PurchaseStatus.DRAFT:
        raise ValidationError("Only draft purchase orders can be approved.")
    if not order.lines.exists():
        raise ValidationError("A purchase order requires at least one line.")
    order.status = PurchaseStatus.APPROVED
    order.save(update_fields=["status", "updated_at"])
    return order


@transaction.atomic
def receive_purchase_line(
    *, line, quantity, actor, request_id, serial_numbers=None, damaged_quantity=0,
    close_with_discrepancy=False, discrepancy_reason=""
):
    line_identity = line.__class__.objects.only("order_id").get(pk=line.pk)
    order = line.order.__class__.objects.select_for_update().select_related(
        "supplier", "destination"
    ).get(pk=line_identity.order_id)
    line = line.__class__.objects.select_for_update().select_related("product").get(pk=line.pk)
    line.order = order
    existing_receipt = PurchaseReceipt.objects.filter(
        organization=line.organization,
        request_id=request_id,
    ).first()
    if existing_receipt:
        if existing_receipt.line_id != line.id:
            raise ValidationError("This receiving request was already used for another purchase line.")
        if (
            existing_receipt.quantity != Decimal(quantity)
            or existing_receipt.damaged_quantity != Decimal(damaged_quantity)
        ):
            raise ValidationError(
                "This receiving request was already completed with different quantities. Refresh the purchase."
            )
        return line
    quantity = Decimal(quantity)
    damaged_quantity = Decimal(damaged_quantity)
    if line.order.status not in {PurchaseStatus.APPROVED, PurchaseStatus.PART_RECEIVED, PurchaseStatus.DISCREPANCY}:
        raise ValidationError("Purchase order is not ready to receive.")
    remaining = line.quantity - line.received_quantity
    if quantity <= 0 or damaged_quantity < 0 or quantity + damaged_quantity > remaining:
        raise ValidationError("Invalid receipt quantity.")
    serial_numbers = serial_numbers or []
    if line.product.is_serialized and len(serial_numbers) != int(quantity):
        raise ValidationError("Each serialized item requires one serial number.")
    normalized_serials = [serial.strip().upper() for serial in serial_numbers]
    duplicate_serials = sorted({serial for serial in normalized_serials if normalized_serials.count(serial) > 1})
    if duplicate_serials:
        raise ValidationError(f"Duplicate IMEI or serial in this receipt: {', '.join(duplicate_serials)}.")
    existing_serials = list(
        StockUnit.objects.select_for_update()
        .filter(organization=line.organization, serial_number__in=normalized_serials)
        .values_list("serial_number", flat=True)
    )
    if existing_serials:
        raise ValidationError(
            f"These IMEI or serial numbers are already registered: {', '.join(sorted(existing_serials))}."
        )
    receipt = PurchaseReceipt.objects.create(
        organization=line.organization,
        request_id=request_id,
        line=line,
        quantity=quantity,
        damaged_quantity=damaged_quantity,
        received_by=actor,
    )
    for serial in normalized_serials:
        unit = StockUnit.objects.create(
            organization=line.organization, product=line.product, serial_number=serial,
            location=line.order.destination, status=SerialStatus.AVAILABLE, unit_cost=line.unit_cost,
        )
        post_stock_movement(
            organization=line.organization, product=line.product, location=line.order.destination,
            quantity=1, movement_type=StockMovementType.PURCHASE_RECEIPT, actor=actor,
            stock_unit=unit, unit_cost=line.unit_cost, reference_type="purchase_receipt", reference_id=receipt.id,
        )
    if not line.product.is_serialized:
        post_stock_movement(
            organization=line.organization, product=line.product, location=line.order.destination,
            quantity=quantity, movement_type=StockMovementType.PURCHASE_RECEIPT, actor=actor,
            unit_cost=line.unit_cost, reference_type="purchase_receipt", reference_id=receipt.id,
        )
    line.received_quantity += quantity
    line.save(update_fields=["received_quantity", "updated_at"])
    has_remaining = line.order.lines.filter(received_quantity__lt=models.F("quantity")).exists()
    if close_with_discrepancy:
        missing = remaining - quantity - damaged_quantity
        if not damaged_quantity and not missing:
            raise ValidationError("No receipt discrepancy was detected.")
        from .models import PurchaseDiscrepancy
        PurchaseDiscrepancy.objects.create(
            organization=line.organization,
            number=f"POD-{timezone.now():%Y%m%d%H%M%S%f}",
            line=line,
            expected_quantity=remaining,
            accepted_quantity=quantity,
            damaged_quantity=damaged_quantity,
            missing_quantity=missing,
            reason=discrepancy_reason,
        )
        line.order.status = PurchaseStatus.DISCREPANCY
    else:
        line.order.status = PurchaseStatus.PART_RECEIVED if has_remaining else PurchaseStatus.RECEIVED
    line.order.save(update_fields=["status", "updated_at"])
    from apps.operations.payables import ensure_payable_for_order
    ensure_payable_for_order(purchase_order=line.order)
    return line


@transaction.atomic
def resolve_purchase_discrepancy(*, discrepancy, actor, resolution):
    discrepancy = discrepancy.__class__.objects.select_for_update().select_related("line__order").get(pk=discrepancy.pk)
    if discrepancy.status != PurchaseDiscrepancyStatus.PENDING:
        raise ValidationError("Only pending purchase discrepancies can be resolved.")
    if resolution not in {"accept_short", "replacement_pending"}:
        raise ValidationError("Choose accept short or replacement pending.")
    discrepancy.status = PurchaseDiscrepancyStatus.RESOLVED
    discrepancy.resolution = resolution
    discrepancy.resolved_by = actor
    discrepancy.save(update_fields=["status", "resolution", "resolved_by", "updated_at"])
    order = discrepancy.line.order
    if resolution == "accept_short":
        order.status = PurchaseStatus.CLOSED
    else:
        order.status = PurchaseStatus.PART_RECEIVED
    order.save(update_fields=["status", "updated_at"])
    return discrepancy


@transaction.atomic
def complete_supplier_return(*, supplier_return, actor):
    supplier_return = supplier_return.__class__.objects.select_for_update().select_related(
        "line__order", "line__product", "stock_unit"
    ).get(pk=supplier_return.pk)
    supplier_return.line.__class__.objects.select_for_update().get(pk=supplier_return.line_id)
    if supplier_return.stock_unit_id:
        StockUnit.objects.select_for_update().get(pk=supplier_return.stock_unit_id)
    if supplier_return.status != SupplierReturnStatus.REQUESTED:
        raise ValidationError("Only requested supplier returns can be completed.")
    completed = supplier_return.line.supplier_returns.filter(
        status=SupplierReturnStatus.COMPLETED
    ).exclude(pk=supplier_return.pk).aggregate(total=models.Sum("quantity"))["total"] or Decimal("0")
    if completed + supplier_return.quantity > supplier_return.line.received_quantity:
        raise ValidationError("Return quantity exceeds received stock.")
    post_stock_movement(
        organization=supplier_return.organization,
        product=supplier_return.line.product,
        location=supplier_return.line.order.destination,
        quantity=-supplier_return.quantity,
        movement_type=StockMovementType.SUPPLIER_RETURN,
        actor=actor,
        stock_unit=supplier_return.stock_unit,
        unit_cost=supplier_return.line.unit_cost,
        reference_type="supplier_return",
        reference_id=supplier_return.id,
        reason=supplier_return.reason,
    )
    if supplier_return.stock_unit:
        supplier_return.stock_unit.status = SerialStatus.WRITTEN_OFF
        supplier_return.stock_unit.location = None
        supplier_return.stock_unit.save(update_fields=["status", "location", "updated_at"])
    supplier_return.status = SupplierReturnStatus.COMPLETED
    supplier_return.approved_by = actor
    supplier_return.save(update_fields=["status", "approved_by", "updated_at"])
    if hasattr(supplier_return.line.order, "payable"):
        from apps.operations.payables import recalculate_payable
        recalculate_payable(payable=supplier_return.line.order.payable)
    return supplier_return
