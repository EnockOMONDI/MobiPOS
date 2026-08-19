import csv
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, Q, Sum
from django.http import HttpResponse
from django.shortcuts import render

from apps.audit.models import AuditEvent
from apps.commissions.models import CommissionAccrual
from apps.inventory.models import StockUnit
from apps.organizations.models import AgentDocumentType, AgentProfile, AgentProfileStatus, AgentProfileType
from apps.organizations.permissions import organization_owner_required
from apps.sales.models import Sale, SaleLine, SaleStatus
from .exporting import safe_csv_row
from .pagination import paginate_section


def _decimal(value):
    return value or Decimal("0")


@login_required
@organization_owner_required
def agent_network_report(request):
    organization = request.organization
    profiles = AgentProfile.objects.filter(organization=organization).select_related(
        "membership__user",
        "branch",
        "supervisor__membership__user",
        "registered_by",
        "verified_by",
    ).prefetch_related("documents")
    status_summary = profiles.values("status").annotate(total=Count("id")).order_by("status")
    type_summary = profiles.values("profile_type").annotate(total=Count("id")).order_by("profile_type")
    missing_national_id = profiles.exclude(
        documents__document_type=AgentDocumentType.NATIONAL_ID,
    ).distinct()
    pending_profiles = profiles.filter(status=AgentProfileStatus.PENDING)
    active_agents = profiles.filter(profile_type=AgentProfileType.AGENT, status=AgentProfileStatus.ACTIVE)
    dsa_profiles = profiles.filter(profile_type=AgentProfileType.DSA)
    profile_list = list(profiles.order_by("profile_type", "legal_name"))
    user_ids = [profile.membership.user_id for profile in profile_list]
    completed_statuses = [SaleStatus.COMPLETED, SaleStatus.PART_PAID, SaleStatus.PAID]
    sales_by_agent = {
        row["agent_id"]: row
        for row in Sale.objects.filter(
            organization=organization,
            agent_id__in=user_ids,
            status__in=completed_statuses,
        ).values("agent_id").annotate(
            sale_count=Count("id"),
            sales_total=Sum("total"),
            collected_total=Sum("paid_total"),
        )
    }
    gross_profit_by_agent = {
        row["sale__agent_id"]: row
        for row in SaleLine.objects.filter(
            organization=organization,
            sale__agent_id__in=user_ids,
            sale__status__in=completed_statuses,
        ).values("sale__agent_id").annotate(
            revenue=Sum("line_total"),
            cost=Sum(F("unit_cost") * F("quantity")),
        )
    }
    commissions_by_agent = {
        row["agent_id"]: row
        for row in CommissionAccrual.objects.filter(
            organization=organization,
            agent_id__in=user_ids,
        ).values("agent_id").annotate(
            commission_total=Sum("amount"),
            payable_total=Sum("amount", filter=Q(is_payable=True, paid_at__isnull=True)),
            paid_total=Sum("amount", filter=Q(paid_at__isnull=False)),
        )
    }
    stock_by_membership = {
        row["location__custodian_membership_id"]: row["stock_count"]
        for row in StockUnit.objects.filter(
            organization=organization,
            location__custodian_membership_id__in=[profile.membership_id for profile in profile_list],
        ).values("location__custodian_membership_id").annotate(stock_count=Count("id"))
    }
    profile_rows = []
    for profile in profile_list:
        sales = sales_by_agent.get(profile.membership.user_id, {})
        profit = gross_profit_by_agent.get(profile.membership.user_id, {})
        commissions = commissions_by_agent.get(profile.membership.user_id, {})
        revenue = _decimal(profit.get("revenue"))
        cost = _decimal(profit.get("cost"))
        profile_rows.append({
            "profile": profile,
            "sale_count": sales.get("sale_count", 0),
            "sales_total": _decimal(sales.get("sales_total")),
            "collected_total": _decimal(sales.get("collected_total")),
            "gross_profit": revenue - cost,
            "commission_total": _decimal(commissions.get("commission_total")),
            "payable_commission": _decimal(commissions.get("payable_total")),
            "paid_commission": _decimal(commissions.get("paid_total")),
            "stock_count": stock_by_membership.get(profile.membership_id, 0),
        })
    top_performers = sorted(profile_rows, key=lambda row: row["sales_total"], reverse=True)[:10]
    recent_activity = AuditEvent.objects.filter(
        organization=organization,
    ).filter(
        Q(action__startswith="agent_profile.") | Q(action__startswith="agent_document.")
    ).select_related("actor").order_by("-created_at")[:25]

    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="agent-network-report.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "Name", "Type", "Status", "Branch", "Supervisor", "National ID", "Documents",
            "Stock Held", "Sales Count", "Sales Total", "Collected", "Gross Profit",
            "Commission", "Payable Commission", "Paid Commission", "Registered By",
        ])
        for row in profile_rows:
            profile = row["profile"]
            writer.writerow(safe_csv_row([
                profile.legal_name,
                profile.get_profile_type_display(),
                profile.get_status_display(),
                profile.branch.name,
                profile.supervisor.legal_name if profile.supervisor else "",
                profile.national_id_number,
                profile.documents.count(),
                row["stock_count"],
                row["sale_count"],
                row["sales_total"],
                row["collected_total"],
                row["gross_profit"],
                row["commission_total"],
                row["payable_commission"],
                row["paid_commission"],
                profile.registered_by.get_username() if profile.registered_by else "",
            ]))
        return response

    profile_page, profile_pagination = paginate_section(
        request, profile_rows, parameter="profiles_page"
    )
    dsa_page, dsa_pagination = paginate_section(
        request,
        dsa_profiles.order_by("supervisor__legal_name", "legal_name"),
        parameter="dsa_page",
    )
    pending_page, pending_pagination = paginate_section(
        request, pending_profiles.order_by("created_at"), parameter="pending_page"
    )
    missing_page, missing_pagination = paginate_section(
        request, missing_national_id.order_by("legal_name"), parameter="missing_page"
    )

    context = {
        "summary": {
            "total": profiles.count(),
            "active_agents": active_agents.count(),
            "dsa_count": dsa_profiles.count(),
            "pending": pending_profiles.count(),
            "missing_national_id": missing_national_id.count(),
            "sales_total": sum((row["sales_total"] for row in profile_rows), Decimal("0")),
            "payable_commission": sum((row["payable_commission"] for row in profile_rows), Decimal("0")),
            "stock_held": sum((row["stock_count"] for row in profile_rows), 0),
        },
        "status_summary": status_summary,
        "type_summary": type_summary,
        "profile_rows": profile_page,
        "profile_pagination": profile_pagination,
        "top_performers": top_performers,
        "dsas": dsa_page,
        "dsa_pagination": dsa_pagination,
        "pending_profiles": pending_page,
        "pending_pagination": pending_pagination,
        "missing_documents": missing_page,
        "missing_pagination": missing_pagination,
        "recent_activity": recent_activity,
    }
    return render(request, "reports/agent_network.html", context)
