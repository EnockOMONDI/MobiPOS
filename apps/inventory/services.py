from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from .models import SerialStatus, StockAdjustmentStatus, StockBalance, StockMovement, StockMovementType


@transaction.atomic
def post_stock_movement(*, organization, product, location, quantity, movement_type, actor=None, stock_unit=None, unit_cost=0, reference_type="", reference_id="", reason="", reversed_movement=None):
    quantity = Decimal(quantity)
    if quantity == 0:
        raise ValidationError("Stock movement quantity cannot be zero.")
    if product.organization_id != organization.id or location.organization_id != organization.id:
        raise ValidationError("Stock movement entities must belong to the same organization.")
    if stock_unit:
        if not product.is_serialized or abs(quantity) != 1:
            raise ValidationError("Serialized stock movements must have quantity 1 or -1.")
        if stock_unit.organization_id != organization.id or stock_unit.product_id != product.id:
            raise ValidationError("Stock unit must belong to the same organization and product.")
        if quantity < 0 and stock_unit.location_id != location.id:
            raise ValidationError("Stock unit is not at the movement source location.")
    elif product.is_serialized:
        raise ValidationError("Serialized stock movements require a stock unit.")
    balance, _ = StockBalance.objects.select_for_update().get_or_create(
        organization=organization, product=product, location=location
    )
    if balance.quantity + quantity < 0:
        raise ValidationError("Insufficient available stock.")
    StockBalance.objects.filter(pk=balance.pk).update(quantity=F("quantity") + quantity)
    return StockMovement.objects.create(
        organization=organization, movement_type=movement_type, product=product,
        stock_unit=stock_unit, location=location, quantity=quantity, unit_cost=unit_cost,
        reference_type=reference_type, reference_id=str(reference_id), reason=reason, actor=actor,
        reversed_movement=reversed_movement,
    )


@transaction.atomic
def complete_stock_adjustment(*, adjustment, actor):
    if adjustment.status != StockAdjustmentStatus.REQUESTED:
        raise ValidationError("Only requested stock adjustments can be completed.")
    movement = post_stock_movement(
        organization=adjustment.organization,
        product=adjustment.product,
        location=adjustment.location,
        quantity=adjustment.quantity,
        movement_type=StockMovementType.ADJUSTMENT,
        actor=actor,
        unit_cost=adjustment.product.cost_price,
        reference_type="stock_adjustment",
        reference_id=adjustment.id,
        reason=adjustment.reason,
    )
    adjustment.status = StockAdjustmentStatus.COMPLETED
    adjustment.approved_by = actor
    adjustment.movement = movement
    adjustment.save(update_fields=["status", "approved_by", "movement", "updated_at"])
    return adjustment


@transaction.atomic
def reverse_stock_movement(*, movement, actor, reason):
    movement = StockMovement.objects.select_for_update().get(pk=movement.pk)
    if movement.movement_type == StockMovementType.REVERSAL:
        raise ValidationError("A reversal movement cannot be reversed.")
    if movement.reversals.exists():
        raise ValidationError("This stock movement has already been reversed.")
    reversal = post_stock_movement(
        organization=movement.organization,
        product=movement.product,
        location=movement.location,
        quantity=-movement.quantity,
        movement_type=StockMovementType.REVERSAL,
        actor=actor,
        stock_unit=movement.stock_unit,
        unit_cost=movement.unit_cost,
        reference_type="stock_movement_reversal",
        reference_id=movement.id,
        reason=reason,
        reversed_movement=movement,
    )
    if movement.stock_unit:
        if reversal.quantity > 0:
            movement.stock_unit.status = SerialStatus.AVAILABLE
            movement.stock_unit.location = movement.location
        else:
            movement.stock_unit.status = SerialStatus.WRITTEN_OFF
            movement.stock_unit.location = None
        movement.stock_unit.save(update_fields=["status", "location", "updated_at"])
    return reversal
