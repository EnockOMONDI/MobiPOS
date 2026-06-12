from django.core.exceptions import ValidationError
from django.db import transaction

from apps.inventory.models import SerialStatus, StockMovementType
from apps.inventory.services import post_stock_movement

from .models import RepairPartUsage


@transaction.atomic
def use_repair_part(*, ticket, product, location, quantity, actor, stock_unit=None):
    if ticket.branch_id != location.branch_id:
        raise ValidationError("Repair parts must be issued from the ticket branch.")
    movement = post_stock_movement(
        organization=ticket.organization,
        product=product,
        location=location,
        quantity=-quantity,
        movement_type=StockMovementType.REPAIR_ISSUE,
        actor=actor,
        stock_unit=stock_unit,
        unit_cost=product.cost_price,
        reference_type="repair_ticket",
        reference_id=ticket.id,
    )
    if stock_unit:
        stock_unit.status = SerialStatus.WARRANTY_REPAIR
        stock_unit.location = None
        stock_unit.save(update_fields=["status", "location", "updated_at"])
    return RepairPartUsage.objects.create(
        organization=ticket.organization,
        ticket=ticket,
        product=product,
        stock_unit=stock_unit,
        location=location,
        quantity=quantity,
        unit_cost=product.cost_price,
        used_by=actor,
    )
