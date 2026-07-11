from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.payments.models import Payment, PaymentMethod, PaymentStatus
from apps.payments.services import record_sale_payments
from apps.operations.models import ApprovalRequest, ApprovalStatus, Receivable
from apps.operations.services import request_approval, user_can_decide_approval
from apps.audit.services import record_audit_event
from apps.contacts.models import Contact
from apps.organizations.permissions import accessible_locations_for, organization_owner_required, organization_permission_required
from apps.sales.models import Sale, SaleChannel, SaleLine
from apps.sales.services import calculate_sale_line_amounts, complete_sale, create_credit_receivable
from .forms import CartCompleteForm, CartItemForm, CashMovementForm, CheckoutForm, CloseSessionForm, OpenSessionForm
from .models import CashMovement, CashMovementType, POSSession, SessionStatus


def _apply_cash_change(*, allocations, paid_amount, total):
    change_due = Decimal("0")
    if paid_amount <= total:
        return allocations, paid_amount, change_due
    overage = paid_amount - total
    for allocation in allocations:
        if allocation["method"] == PaymentMethod.CASH and allocation["amount"] >= overage:
            allocation["amount"] -= overage
            change_due = overage
            paid_amount = total
            break
    if change_due <= 0:
        raise ValueError("Only cash payments can exceed the sale total for change.")
    return [allocation for allocation in allocations if allocation["amount"] > 0], paid_amount, change_due


def _inline_customer_from_form(*, form, organization, actor=None, request=None):
    name = (form.cleaned_data.get("new_customer_name") or "").strip()
    if not name:
        return form.cleaned_data.get("customer"), False
    contact = Contact.objects.create(
        organization=organization,
        contact_type="customer",
        name=name,
        phone_number=(form.cleaned_data.get("new_customer_phone") or "").strip(),
        email=(form.cleaned_data.get("new_customer_email") or "").strip(),
    )
    record_audit_event(
        action="contact.created",
        actor=actor,
        organization=organization,
        target=contact,
        request=request,
    )
    return contact, True


def _sale_context_from_form(form, *, is_credit=False):
    sale_channel = form.cleaned_data.get("sale_channel") or SaleChannel.CASH
    if is_credit and sale_channel == SaleChannel.CASH:
        sale_channel = SaleChannel.CREDIT
    return {
        "sale_channel": sale_channel,
        "credit_agency": form.cleaned_data.get("credit_agency") or "",
        "customer_national_id": (form.cleaned_data.get("customer_national_id") or "").strip(),
        "next_of_kin_name": (form.cleaned_data.get("next_of_kin_name") or "").strip(),
        "next_of_kin_phone": (form.cleaned_data.get("next_of_kin_phone") or "").strip(),
    }


def _assigned_pos_location(request):
    if not request.organization or not request.membership:
        return None
    location_id = request.membership.branches.filter(
        is_active=True, locations__location_type="pos", locations__is_active=True
    ).values_list("locations", flat=True).first()
    if not location_id:
        return None
    from apps.organizations.models import Location
    return Location.objects.get(pk=location_id)


def _open_session(request, location):
    existing = POSSession.objects.filter(
        organization=request.organization,
        cashier=request.user,
        location=location,
        status=SessionStatus.OPEN,
    ).first()
    if existing or not settings.POS_AUTO_OPEN_SESSION:
        return existing
    now_key = timezone.now().strftime("%Y%m%d%H%M%S%f")
    return POSSession.objects.create(
        organization=request.organization,
        cashier=request.user,
        location=location,
        number=f"SES-{now_key}",
        opening_float=Decimal("0"),
    )


@login_required
@organization_permission_required("sales.add_sale")
@transaction.atomic
def open_session(request):
    location = _assigned_pos_location(request)
    if not location:
        messages.error(request, "Your account has no assigned POS location.")
        return redirect("dashboard")
    existing = POSSession.objects.filter(
        organization=request.organization, cashier=request.user, location=location, status=SessionStatus.OPEN
    ).first()
    if existing:
        return redirect("session-detail", session_id=existing.id)
    form = OpenSessionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        session = POSSession.objects.create(
            organization=request.organization,
            cashier=request.user,
            location=location,
            number=f"SES-{timezone.now():%Y%m%d%H%M%S%f}",
            opening_float=form.cleaned_data["opening_float"],
        )
        record_audit_event(action="pos_session.opened", actor=request.user, organization=request.organization, target=session, request=request)
        messages.success(request, "Register opened.")
        return redirect("pos-cart")
    return render(request, "pos/session_open.html", {"form": form, "location": location})


def _credit_sale_is_approved(request, sale, balance):
    if balance <= 0:
        return True
    membership = request.membership
    if request.user.is_superuser or request.user.is_platform_admin or (membership and membership.is_owner):
        return True
    if ApprovalRequest.objects.filter(
        organization=request.organization,
        target_type="sales.Sale",
        target_id=str(sale.id),
        request_type="credit_sale",
        status=ApprovalStatus.APPROVED,
    ).exists():
        return True
    request_approval(
        organization=request.organization,
        target=sale,
        request_type="credit_sale",
        reason=f"Credit approval required for KES {balance}.",
        requested_by=request.user,
        amount=balance,
        branch=sale.location.branch,
    )
    return False


@login_required
@organization_permission_required("sales.add_sale")
@transaction.atomic
def checkout(request):
    organization = request.organization
    membership = request.membership
    if not organization or not membership:
        messages.error(request, "An active organization is required.")
        return redirect("dashboard")
    location = _assigned_pos_location(request)
    if not location:
        messages.error(request, "Your account has no assigned POS location.")
        return redirect("dashboard")
    form = CheckoutForm(request.POST or None, organization=organization, location=location)
    if request.method == "POST" and form.is_valid():
        product = form.cleaned_data["product"]
        quantity = form.cleaned_data["quantity"]
        paid_amount = form.cleaned_data["paid_amount"]
        credit_amount = form.cleaned_data["credit_amount"]
        amounts = calculate_sale_line_amounts(product=product, quantity=quantity)
        sale_total = amounts["total"]
        customer = form.cleaned_data["customer"]
        allocated_total = paid_amount + credit_amount
        try:
            allocations, paid_amount, change_due = _apply_cash_change(
                allocations=form.cleaned_data["payment_allocations"],
                paid_amount=paid_amount,
                total=sale_total,
            )
        except ValueError as error:
            form.add_error(None, str(error))
            return render(request, "pos/checkout.html", {"form": form, "location": location})
        allocated_total = paid_amount + credit_amount
        if allocated_total > sale_total:
            form.add_error(None, "Payment and credit allocations cannot exceed the sale total.")
            return render(request, "pos/checkout.html", {"form": form, "location": location})
        if allocated_total < sale_total and credit_amount:
            form.add_error(None, "Explicit payment and credit allocations must equal the sale total.")
            return render(request, "pos/checkout.html", {"form": form, "location": location})
        balance = sale_total - paid_amount
        if balance > 0:
            if not customer:
                form.add_error("customer", "A customer is required when a balance remains.")
                return render(request, "pos/checkout.html", {"form": form, "location": location})
            exposure = Receivable.objects.filter(
                organization=organization, customer=customer, outstanding_amount__gt=0
            ).aggregate(total=Sum("outstanding_amount"))["total"] or Decimal("0")
            if exposure + balance > customer.credit_limit:
                form.add_error("customer", "Customer credit limit would be exceeded.")
                return render(request, "pos/checkout.html", {"form": form, "location": location})
            if not (
                request.user.is_superuser
                or request.user.is_platform_admin
                or (membership and membership.is_owner)
            ):
                form.add_error(None, "Use the sale cart for credit sales so approval can be requested.")
                return render(request, "pos/checkout.html", {"form": form, "location": location})
        now_key = timezone.now().strftime("%Y%m%d%H%M%S%f")
        session = _open_session(request, location)
        if not session:
            messages.error(request, "Open your register before processing a sale.")
            return redirect("session-open")
        sale = Sale.objects.create(
            organization=organization,
            number=f"SALE-{now_key}",
            session=session,
            location=location,
            customer=customer,
            agent=request.user,
            created_by=request.user,
            **_sale_context_from_form(form, is_credit=balance > 0),
        )
        SaleLine.objects.create(
            organization=organization,
            sale=sale,
            product=product,
            stock_unit=form.cleaned_data["stock_unit"],
            quantity=quantity,
            unit_price=product.selling_price,
            unit_cost=product.cost_price,
            tax=amounts["tax"],
            line_total=amounts["gross"],
        )
        complete_sale(sale=sale, actor=request.user)
        record_sale_payments(
            sale=sale,
            allocations=allocations,
            received_by=request.user,
        )
        sale.refresh_from_db()
        if sale.balance_due > 0:
            create_credit_receivable(sale=sale)
        message = f"Sale {sale.number} completed."
        if change_due:
            message += f" Change due: KES {change_due}."
        messages.success(request, message)
        return redirect("sale-detail", sale_id=sale.id)
    return render(request, "pos/checkout.html", {"form": form, "location": location})


@login_required
@organization_permission_required("sales.add_sale")
def cart_detail(request):
    location = _assigned_pos_location(request)
    if not location:
        messages.error(request, "Your account has no assigned POS location.")
        return redirect("dashboard")
    session = _open_session(request, location)
    if not session:
        messages.error(request, "Open your register before starting a sale.")
        return redirect("session-open")
    cart = Sale.objects.filter(
        organization=request.organization, session=session, created_by=request.user, status="draft"
    ).order_by("created_at").first()
    cart_total = Decimal("0")
    if cart:
        cart_total = sum((line.total_after_tax for line in cart.lines.all()), Decimal("0"))
    return render(request, "pos/cart.html", {
        "cart": cart,
        "cart_total": cart_total,
        "location": location,
        "item_form": CartItemForm(organization=request.organization, location=location),
        "complete_form": CartCompleteForm(organization=request.organization),
    })


@login_required
@organization_permission_required("sales.add_sale")
@require_POST
@transaction.atomic
def cart_add(request):
    location = _assigned_pos_location(request)
    if not location:
        return redirect("dashboard")
    form = CartItemForm(request.POST, organization=request.organization, location=location)
    if form.is_valid():
        session = _open_session(request, location)
        if not session:
            messages.error(request, "Open your register before adding sale items.")
            return redirect("session-open")
        now_key = timezone.now().strftime("%Y%m%d%H%M%S%f")
        cart = Sale.objects.filter(
            organization=request.organization, session=session, created_by=request.user, status="draft"
        ).order_by("created_at").first()
        if not cart:
            cart = Sale.objects.create(
                organization=request.organization, number=f"SALE-{now_key}", session=session,
                location=location, agent=request.user, created_by=request.user,
            )
        product = form.cleaned_data["product"]
        quantity = form.cleaned_data["quantity"]
        amounts = calculate_sale_line_amounts(
            product=product,
            quantity=quantity,
            discount=form.cleaned_data["discount"],
        )
        SaleLine.objects.create(
            organization=request.organization, sale=cart, product=product,
            stock_unit=form.cleaned_data["stock_unit"], quantity=quantity,
            unit_price=product.selling_price, unit_cost=product.cost_price,
            discount=form.cleaned_data["discount"],
            tax=amounts["tax"],
            line_total=amounts["gross"],
        )
        ApprovalRequest.objects.filter(
            organization=request.organization, target_type="sales.Sale", target_id=str(cart.id)
        ).update(status=ApprovalStatus.PENDING, decided_by=None, decided_at=None)
        messages.success(request, f"{product.name} added to cart.")
    else:
        messages.error(request, "Correct the cart item details.")
    return redirect("pos-cart")


@login_required
@organization_permission_required("sales.add_sale")
@require_POST
@transaction.atomic
def cart_remove(request, line_id):
    line = get_object_or_404(
        SaleLine, id=line_id, organization=request.organization,
        sale__created_by=request.user, sale__status="draft",
    )
    sale_id = line.sale_id
    line.delete()
    ApprovalRequest.objects.filter(
        organization=request.organization, target_type="sales.Sale", target_id=str(sale_id)
    ).update(status=ApprovalStatus.PENDING, decided_by=None, decided_at=None)
    messages.success(request, "Item removed from cart.")
    return redirect("pos-cart")


@login_required
@organization_permission_required("sales.add_sale")
@require_POST
@transaction.atomic
def cart_complete(request, sale_id):
    sale = get_object_or_404(
        Sale, id=sale_id, organization=request.organization, created_by=request.user, status="draft"
    )
    form = CartCompleteForm(request.POST, organization=request.organization)
    if not form.is_valid() or not sale.lines.exists():
        messages.error(request, "A valid cart and payment details are required.")
        return redirect("pos-cart")
    total = sum((line.line_total + line.tax - line.discount for line in sale.lines.all()), Decimal("0"))
    paid_amount = form.cleaned_data["paid_amount"]
    credit_amount = form.cleaned_data["credit_amount"]
    customer = form.cleaned_data["customer"]
    pending_customer_name = (form.cleaned_data.get("new_customer_name") or "").strip()
    discount_total = sale.lines.aggregate(total=Sum("discount"))["total"] or Decimal("0")
    if discount_total > 0 and not ApprovalRequest.objects.filter(
        organization=request.organization,
        target_type="sales.Sale",
        target_id=str(sale.id),
        request_type="discount",
        status=ApprovalStatus.APPROVED,
    ).exists():
        request_approval(
            organization=request.organization,
            target=sale,
            request_type="discount",
            reason=f"Discount approval required for KES {discount_total}.",
            requested_by=request.user,
            amount=discount_total,
            branch=sale.location.branch,
        )
        messages.error(request, "Discount approval is required before completing this sale.")
        return redirect("pos-cart")
    allocated_total = paid_amount + credit_amount
    try:
        allocations, paid_amount, change_due = _apply_cash_change(
            allocations=form.cleaned_data["payment_allocations"],
            paid_amount=paid_amount,
            total=total,
        )
    except ValueError as error:
        messages.error(request, str(error))
        return redirect("pos-cart")
    allocated_total = paid_amount + credit_amount
    if allocated_total > total:
        messages.error(request, "Payment and credit allocations cannot exceed the sale total.")
        return redirect("pos-cart")
    if allocated_total < total and credit_amount:
        messages.error(request, "Explicit payment and credit allocations must equal the sale total.")
        return redirect("pos-cart")
    balance = total - paid_amount
    if balance > 0:
        if pending_customer_name and not customer:
            messages.error(request, "Create the customer profile before using customer credit.")
            return redirect("pos-cart")
        if not customer:
            messages.error(request, "A customer is required when a balance remains.")
            return redirect("pos-cart")
        exposure = Receivable.objects.filter(
            organization=request.organization, customer=customer, outstanding_amount__gt=0
        ).aggregate(total=Sum("outstanding_amount"))["total"] or Decimal("0")
        if exposure + balance > customer.credit_limit:
            messages.error(request, "Customer credit limit would be exceeded.")
            return redirect("pos-cart")
        if not _credit_sale_is_approved(request, sale, balance):
            messages.error(request, "Credit approval is required before completing this sale.")
            return redirect("pos-cart")
    if pending_customer_name:
        customer, _created = _inline_customer_from_form(
            form=form,
            organization=request.organization,
            actor=request.user,
            request=request,
        )
    sale.customer = customer
    for field, value in _sale_context_from_form(form, is_credit=balance > 0).items():
        setattr(sale, field, value)
    sale.save(update_fields=[
        "customer",
        "sale_channel",
        "credit_agency",
        "customer_national_id",
        "next_of_kin_name",
        "next_of_kin_phone",
        "updated_at",
    ])
    complete_sale(sale=sale, actor=request.user)
    record_sale_payments(
        sale=sale,
        allocations=allocations,
        received_by=request.user,
    )
    sale.refresh_from_db()
    if sale.balance_due > 0:
        create_credit_receivable(sale=sale)
    message = f"Sale {sale.number} completed."
    if change_due:
        message += f" Change due: KES {change_due}."
    messages.success(request, message)
    return redirect("sale-detail", sale_id=sale.id)


@login_required
def session_detail(request, session_id):
    session = get_object_or_404(
        POSSession, id=session_id, organization=request.organization,
        location__in=accessible_locations_for(request.user, request.organization),
    )
    return render(request, "pos/session_detail.html", {"session": session, "form": CloseSessionForm(), "movement_form": CashMovementForm()})


@login_required
@organization_permission_required("sales.add_sale")
@require_POST
@transaction.atomic
def cash_movement_create(request, session_id):
    session = get_object_or_404(
        POSSession.objects.select_for_update(),
        id=session_id,
        organization=request.organization,
        cashier=request.user,
        status=SessionStatus.OPEN,
    )
    form = CashMovementForm(request.POST)
    if form.is_valid():
        movement = CashMovement.objects.create(
            organization=request.organization,
            session=session,
            recorded_by=request.user,
            **form.cleaned_data,
        )
        record_audit_event(action=f"cash.{movement.movement_type}", actor=request.user, organization=request.organization, target=movement, request=request)
        messages.success(request, "Cash movement recorded.")
    else:
        messages.error(request, "Cash movement details are invalid.")
    return redirect("session-detail", session_id=session.id)


@login_required
@require_POST
@transaction.atomic
def close_session(request, session_id):
    session = get_object_or_404(POSSession, id=session_id, organization=request.organization, cashier=request.user)
    form = CloseSessionForm(request.POST)
    if form.is_valid() and session.status == SessionStatus.OPEN:
        cash_sales = Payment.objects.filter(
            organization=request.organization,
            sale__session=session,
            method=PaymentMethod.CASH,
            status=PaymentStatus.CONFIRMED,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        cash_in = session.cash_movements.filter(movement_type=CashMovementType.CASH_IN).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        cash_out = session.cash_movements.filter(movement_type__in=(CashMovementType.CASH_OUT, CashMovementType.DROP)).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        session.expected_cash = session.opening_float + cash_sales + cash_in - cash_out
        session.actual_cash = form.cleaned_data["actual_cash"]
        session.variance = session.actual_cash - session.expected_cash
        session.closing_note = form.cleaned_data["closing_note"]
        session.closed_at = timezone.now()
        session.status = SessionStatus.CLOSED
        session.save(update_fields=["expected_cash", "actual_cash", "variance", "closing_note", "closed_at", "status", "updated_at"])
        if session.variance:
            request_approval(
                organization=request.organization,
                request_type="session_variance",
                target=session,
                requested_by=request.user,
                reason=f"Review register variance of KES {session.variance}.",
                amount=abs(session.variance),
                branch=session.location.branch,
            )
        record_audit_event(action="pos_session.closed", actor=request.user, organization=request.organization, target=session, request=request)
        messages.success(request, "Cashier session closed.")
    else:
        messages.error(request, "Unable to close this session.")
    return redirect("session-detail", session_id=session.id)


@login_required
@require_POST
def review_session(request, session_id):
    session = get_object_or_404(
        POSSession, id=session_id, organization=request.organization, status=SessionStatus.CLOSED
    )
    approval = ApprovalRequest.objects.filter(
        organization=request.organization,
        request_type="session_variance",
        target_type="pos.POSSession",
        target_id=str(session.id),
    ).select_related("policy").first()
    membership = request.membership
    if not approval and not (
        request.user.is_superuser
        or request.user.is_platform_admin
        or (membership and membership.is_owner)
    ):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Organization owner access is required to review a balanced session.")
    if approval and not user_can_decide_approval(user=request.user, approval=approval):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("You are not eligible to review this register variance.")
    if approval and approval.requested_by_id == request.user.id:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("The cashier cannot approve their own register variance.")
    if approval:
        approval.status = ApprovalStatus.APPROVED
        approval.decided_by = request.user
        approval.decided_at = timezone.now()
        approval.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
    session.status = SessionStatus.REVIEWED
    session.save(update_fields=["status", "updated_at"])
    record_audit_event(action="pos_session.reviewed", actor=request.user, organization=request.organization, target=session, request=request)
    messages.success(request, "Cashier session reviewed.")
    return redirect("session-detail", session_id=session.id)
