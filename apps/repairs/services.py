from decimal import Decimal
import uuid

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.inventory.models import SerialStatus, StockMovementType, StockUnit
from apps.inventory.services import post_stock_movement

from .models import (
    RepairPartUsage,
    RepairPayment,
    RepairPaymentMethod,
    RepairStatus,
    RepairTicket,
    RepairTransition,
    WarrantyType,
)


REPAIR_TRANSITIONS = {
    RepairStatus.RECEIVED: (RepairStatus.DIAGNOSING,),
    RepairStatus.DIAGNOSING: (RepairStatus.AWAITING_APPROVAL,),
    RepairStatus.AWAITING_APPROVAL: (RepairStatus.IN_REPAIR,),
    RepairStatus.IN_REPAIR: (RepairStatus.QUALITY_CHECK,),
    RepairStatus.QUALITY_CHECK: (RepairStatus.READY, RepairStatus.IN_REPAIR),
    RepairStatus.READY: (RepairStatus.CLOSED,),
    RepairStatus.CLOSED: (),
}


def allowed_repair_transitions(status):
    return REPAIR_TRANSITIONS.get(status, ())


def _active_payment_total(ticket):
    return sum(
        (
            payment.amount
            for payment in ticket.payments.select_for_update().filter(reversed_at__isnull=True)
        ),
        start=Decimal("0.00"),
    )


@transaction.atomic
def transition_repair(
    *,
    ticket,
    to_status,
    actor,
    request_id,
    diagnosis="",
    warranty_type=WarrantyType.NONE,
    warranty_decision_notes="",
    quoted_amount=0,
    notes="",
):
    ticket = RepairTicket.objects.select_for_update().get(pk=ticket.pk)
    existing = RepairTransition.objects.filter(
        organization=ticket.organization,
        request_id=request_id,
    ).first()
    if existing:
        if existing.ticket_id != ticket.id:
            raise ValidationError("This repair request identifier has already been used.")
        return existing

    if to_status not in allowed_repair_transitions(ticket.status):
        raise ValidationError(
            f"{ticket.get_status_display()} cannot move directly to {dict(RepairStatus.choices).get(to_status, to_status)}."
        )

    diagnosis = (diagnosis or "").strip()
    notes = (notes or "").strip()
    warranty_decision_notes = (warranty_decision_notes or "").strip()
    quoted_amount = Decimal(quoted_amount or 0)
    warranty = warranty_type != WarrantyType.NONE

    if ticket.status not in (RepairStatus.RECEIVED, RepairStatus.DIAGNOSING):
        if warranty_type != ticket.warranty_type or quoted_amount != ticket.quoted_amount:
            raise ValidationError(
                "The approved warranty decision and quote cannot change after approval. "
                "Create a reviewed correction before continuing."
            )

    if ticket.status == RepairStatus.DIAGNOSING and not diagnosis:
        raise ValidationError("Record the diagnosis before requesting customer approval.")
    if ticket.status == RepairStatus.AWAITING_APPROVAL and not notes:
        raise ValidationError("Record the customer approval or authorization before starting the repair.")
    if ticket.status == RepairStatus.QUALITY_CHECK and to_status == RepairStatus.IN_REPAIR and not notes:
        raise ValidationError("Explain what failed quality check before returning the device to repair.")
    if to_status == RepairStatus.CLOSED:
        amount_due = Decimal("0.00") if warranty else quoted_amount
        if _active_payment_total(ticket) < amount_due:
            raise ValidationError("This repair cannot be collected until the outstanding balance is paid.")

    from_status = ticket.status
    ticket.status = to_status
    ticket.diagnosis = diagnosis
    ticket.warranty_type = warranty_type
    ticket.warranty = warranty
    ticket.warranty_decision_notes = warranty_decision_notes
    ticket.quoted_amount = quoted_amount
    ticket.collected_at = timezone.now() if to_status == RepairStatus.CLOSED else None
    ticket.save(
        update_fields=[
            "status",
            "diagnosis",
            "warranty_type",
            "warranty",
            "warranty_decision_notes",
            "quoted_amount",
            "collected_at",
            "updated_at",
        ]
    )
    return RepairTransition.objects.create(
        organization=ticket.organization,
        request_id=request_id,
        ticket=ticket,
        from_status=from_status,
        to_status=to_status,
        notes=notes,
        transitioned_by=actor,
        transitioned_at=timezone.now(),
    )


@transaction.atomic
def use_repair_part(*, ticket, product, location, quantity, actor, request_id, stock_unit=None):
    ticket = RepairTicket.objects.select_for_update().get(pk=ticket.pk)
    existing = RepairPartUsage.objects.filter(
        organization=ticket.organization,
        request_id=request_id,
    ).first()
    if existing:
        if existing.ticket_id != ticket.id:
            raise ValidationError("This repair part request identifier has already been used.")
        return existing
    if ticket.status != RepairStatus.IN_REPAIR:
        raise ValidationError("Parts can only be issued while the ticket is in the In repair stage.")
    if product.organization_id != ticket.organization_id or location.organization_id != ticket.organization_id:
        raise ValidationError("The part and stock location must belong to this organization.")
    if ticket.branch_id != location.branch_id:
        raise ValidationError("Repair parts must be issued from the ticket branch.")

    if stock_unit:
        stock_unit = StockUnit.objects.select_for_update().get(pk=stock_unit.pk)
        if stock_unit.status != SerialStatus.AVAILABLE or stock_unit.location_id != location.id:
            raise ValidationError("This serialized part is no longer available at the selected location.")

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
        reason=f"Part issued to {ticket.number}",
    )
    if stock_unit:
        StockUnit.objects.filter(pk=stock_unit.pk).update(
            status=SerialStatus.WARRANTY_REPAIR,
            location=None,
            updated_at=timezone.now(),
        )
    return RepairPartUsage.objects.create(
        organization=ticket.organization,
        request_id=request_id,
        ticket=ticket,
        product=product,
        stock_unit=stock_unit,
        location=location,
        quantity=quantity,
        unit_cost=product.cost_price,
        used_by=actor,
        movement=movement,
    )


@transaction.atomic
def reverse_repair_part(*, usage, actor, reason):
    usage = RepairPartUsage.objects.select_for_update().select_related("ticket", "movement").get(pk=usage.pk)
    reason = (reason or "").strip()
    if usage.reversed_at:
        raise ValidationError("This repair part issue has already been reversed.")
    if usage.ticket.status == RepairStatus.CLOSED:
        raise ValidationError("A part on a collected repair cannot be reversed.")
    if not usage.movement_id:
        raise ValidationError("This legacy part issue has no linked stock movement and cannot be reversed automatically.")
    if not reason:
        raise ValidationError("Enter a reason for reversing the repair part issue.")

    stock_unit = None
    if usage.stock_unit_id:
        stock_unit = StockUnit.objects.select_for_update().get(pk=usage.stock_unit_id)
        if stock_unit.status != SerialStatus.WARRANTY_REPAIR or stock_unit.location_id is not None:
            raise ValidationError("The serialized part is not in the expected repair-issued state.")

    reversal = post_stock_movement(
        organization=usage.organization,
        product=usage.product,
        location=usage.location,
        quantity=usage.quantity,
        movement_type=StockMovementType.REVERSAL,
        actor=actor,
        stock_unit=stock_unit,
        unit_cost=usage.unit_cost,
        reference_type="repair_part_reversal",
        reference_id=usage.id,
        reason=reason,
        reversed_movement=usage.movement,
    )
    if stock_unit:
        StockUnit.objects.filter(pk=stock_unit.pk).update(
            status=SerialStatus.AVAILABLE,
            location=usage.location,
            updated_at=timezone.now(),
        )
    RepairPartUsage.objects.filter(pk=usage.pk).update(
        reversed_at=timezone.now(),
        reversed_by=actor,
        reversal_reason=reason,
        reversal_movement=reversal,
        updated_at=timezone.now(),
    )
    usage.refresh_from_db()
    return usage


@transaction.atomic
def record_repair_payment(*, ticket, amount, method, reference, notes, actor, request_id):
    ticket = RepairTicket.objects.select_for_update().get(pk=ticket.pk)
    existing = RepairPayment.objects.filter(
        organization=ticket.organization,
        request_id=request_id,
    ).first()
    if existing:
        if existing.ticket_id != ticket.id:
            raise ValidationError("This repair payment request identifier has already been used.")
        return existing

    amount = Decimal(amount)
    reference = (reference or "").strip()
    if method != RepairPaymentMethod.CASH and not reference:
        raise ValidationError("Enter the provider or bank payment reference.")
    if reference and RepairPayment.objects.filter(organization=ticket.organization, reference=reference).exists():
        raise ValidationError("This repair payment reference has already been recorded.")
    amount_due = Decimal("0.00") if ticket.warranty else ticket.quoted_amount
    outstanding = amount_due - _active_payment_total(ticket)
    if amount_due <= 0:
        raise ValidationError("This repair has no customer balance to pay.")
    if amount > outstanding:
        raise ValidationError(f"Payment exceeds the outstanding repair balance of {outstanding:.2f}.")

    try:
        with transaction.atomic():
            return RepairPayment.objects.create(
                organization=ticket.organization,
                request_id=request_id,
                number=f"RPAY-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}",
                ticket=ticket,
                amount=amount,
                method=method,
                reference=reference,
                notes=(notes or "").strip(),
                received_by=actor,
                received_at=timezone.now(),
            )
    except IntegrityError as error:
        raise ValidationError("This repair payment has already been recorded.") from error


@transaction.atomic
def reverse_repair_payment(*, payment, actor, reason):
    payment = RepairPayment.objects.select_for_update().select_related("ticket").get(pk=payment.pk)
    RepairTicket.objects.select_for_update().get(pk=payment.ticket_id)
    reason = (reason or "").strip()
    if payment.reversed_at:
        raise ValidationError("This repair payment has already been reversed.")
    if payment.ticket.status == RepairStatus.CLOSED:
        raise ValidationError("A payment on a collected repair cannot be reversed until the ticket is formally reopened.")
    if not reason:
        raise ValidationError("Enter a reason for reversing the repair payment.")
    RepairPayment.objects.filter(pk=payment.pk).update(
        reversed_at=timezone.now(),
        reversed_by=actor,
        reversal_reason=reason,
        updated_at=timezone.now(),
    )
    payment.refresh_from_db()
    return payment
