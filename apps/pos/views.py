from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.payments.models import Payment, PaymentMethod, PaymentStatus
from apps.payments.services import confirm_payment
from apps.operations.models import ApprovalRequest, ApprovalStatus, Receivable
from apps.audit.services import record_audit_event
from apps.organizations.permissions import accessible_locations_for, organization_owner_required, organization_permission_required
from apps.sales.models import Sale, SaleLine
from apps.sales.services import complete_sale, create_credit_receivable
from .forms import CartCompleteForm, CartItemForm, CheckoutForm, CloseSessionForm
from .models import POSSession, SessionStatus


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
    now_key = timezone.now().strftime("%Y%m%d%H%M%S%f")
    return POSSession.objects.get_or_create(
        organization=request.organization,
        cashier=request.user,
        location=location,
        status=SessionStatus.OPEN,
        defaults={"number": f"SES-{now_key}", "opening_float": Decimal("0")},
    )[0]


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
        amount = form.cleaned_data["amount_received"]
        line_total = product.selling_price * quantity
        customer = form.cleaned_data["customer"]
        if amount > line_total:
            form.add_error("amount_received", "Amount received cannot exceed the sale total.")
            return render(request, "pos/checkout.html", {"form": form, "location": location})
        if amount < line_total:
            if not customer:
                form.add_error("customer", "A customer is required when a balance remains.")
                return render(request, "pos/checkout.html", {"form": form, "location": location})
            exposure = Receivable.objects.filter(
                organization=organization, customer=customer, outstanding_amount__gt=0
            ).aggregate(total=Sum("outstanding_amount"))["total"] or Decimal("0")
            if exposure + line_total - amount > customer.credit_limit:
                form.add_error("customer", "Customer credit limit would be exceeded.")
                return render(request, "pos/checkout.html", {"form": form, "location": location})
        now_key = timezone.now().strftime("%Y%m%d%H%M%S%f")
        session = _open_session(request, location)
        sale = Sale.objects.create(
            organization=organization,
            number=f"SALE-{now_key}",
            session=session,
            location=location,
            customer=customer,
            agent=request.user,
            created_by=request.user,
        )
        SaleLine.objects.create(
            organization=organization,
            sale=sale,
            product=product,
            stock_unit=form.cleaned_data["stock_unit"],
            quantity=quantity,
            unit_price=product.selling_price,
            unit_cost=product.cost_price,
            line_total=line_total,
        )
        complete_sale(sale=sale, actor=request.user)
        if amount and form.cleaned_data["payment_method"] != PaymentMethod.CREDIT:
            payment = Payment.objects.create(
                organization=organization,
                number=f"PAY-{now_key}",
                customer=sale.customer,
                sale=sale,
                method=form.cleaned_data["payment_method"],
                status=PaymentStatus.PENDING,
                amount=amount,
                received_by=request.user,
            )
            confirm_payment(payment=payment)
        sale.refresh_from_db()
        if sale.balance_due > 0:
            create_credit_receivable(sale=sale)
        messages.success(request, f"Sale {sale.number} completed.")
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
    cart = Sale.objects.filter(
        organization=request.organization, session=session, created_by=request.user, status="draft"
    ).order_by("created_at").first()
    return render(request, "pos/cart.html", {
        "cart": cart,
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
        SaleLine.objects.create(
            organization=request.organization, sale=cart, product=product,
            stock_unit=form.cleaned_data["stock_unit"], quantity=quantity,
            unit_price=product.selling_price, unit_cost=product.cost_price,
            discount=form.cleaned_data["discount"],
            line_total=product.selling_price * quantity - form.cleaned_data["discount"],
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
    total = sum((line.line_total for line in sale.lines.all()), Decimal("0"))
    amount = form.cleaned_data["amount_received"]
    customer = form.cleaned_data["customer"]
    discount_total = sale.lines.aggregate(total=Sum("discount"))["total"] or Decimal("0")
    if discount_total > 0 and not ApprovalRequest.objects.filter(
        organization=request.organization,
        target_type="sales.Sale",
        target_id=str(sale.id),
        request_type="discount",
        status=ApprovalStatus.APPROVED,
    ).exists():
        ApprovalRequest.objects.update_or_create(
            organization=request.organization,
            target_type="sales.Sale",
            target_id=str(sale.id),
            request_type="discount",
            defaults={
                "reason": f"Discount approval required for KES {discount_total}.",
                "requested_by": request.user,
                "status": ApprovalStatus.PENDING,
                "decided_by": None,
                "decided_at": None,
            },
        )
        messages.error(request, "Discount approval is required before completing this sale.")
        return redirect("pos-cart")
    if amount > total:
        messages.error(request, "Amount received cannot exceed the sale total.")
        return redirect("pos-cart")
    if amount < total:
        if not customer:
            messages.error(request, "A customer is required when a balance remains.")
            return redirect("pos-cart")
        exposure = Receivable.objects.filter(
            organization=request.organization, customer=customer, outstanding_amount__gt=0
        ).aggregate(total=Sum("outstanding_amount"))["total"] or Decimal("0")
        if exposure + total - amount > customer.credit_limit:
            messages.error(request, "Customer credit limit would be exceeded.")
            return redirect("pos-cart")
    sale.customer = customer
    sale.save(update_fields=["customer", "updated_at"])
    complete_sale(sale=sale, actor=request.user)
    if amount and form.cleaned_data["payment_method"] != PaymentMethod.CREDIT:
        payment = Payment.objects.create(
            organization=request.organization,
            number=f"PAY-{timezone.now():%Y%m%d%H%M%S%f}",
            customer=customer, sale=sale, method=form.cleaned_data["payment_method"],
            status=PaymentStatus.PENDING, amount=amount, received_by=request.user,
        )
        confirm_payment(payment=payment)
    sale.refresh_from_db()
    if sale.balance_due > 0:
        create_credit_receivable(sale=sale)
    messages.success(request, f"Sale {sale.number} completed.")
    return redirect("sale-detail", sale_id=sale.id)


@login_required
def session_detail(request, session_id):
    session = get_object_or_404(
        POSSession, id=session_id, organization=request.organization,
        location__in=accessible_locations_for(request.user, request.organization),
    )
    return render(request, "pos/session_detail.html", {"session": session, "form": CloseSessionForm()})


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
        session.expected_cash = session.opening_float + cash_sales
        session.actual_cash = form.cleaned_data["actual_cash"]
        session.variance = session.actual_cash - session.expected_cash
        session.closing_note = form.cleaned_data["closing_note"]
        session.closed_at = timezone.now()
        session.status = SessionStatus.CLOSED
        session.save(update_fields=["expected_cash", "actual_cash", "variance", "closing_note", "closed_at", "status", "updated_at"])
        messages.success(request, "Cashier session closed.")
    else:
        messages.error(request, "Unable to close this session.")
    return redirect("session-detail", session_id=session.id)


@login_required
@organization_owner_required
@require_POST
def review_session(request, session_id):
    session = get_object_or_404(
        POSSession, id=session_id, organization=request.organization, status=SessionStatus.CLOSED
    )
    session.status = SessionStatus.REVIEWED
    session.save(update_fields=["status", "updated_at"])
    record_audit_event(action="pos_session.reviewed", actor=request.user, organization=request.organization, target=session, request=request)
    messages.success(request, "Cashier session reviewed.")
    return redirect("session-detail", session_id=session.id)
