from collections import Counter

from celery import shared_task

from apps.notifications.services import notify_business_event
from apps.organizations.models import Organization, OrganizationStatus

from .reconciliation import run_reconciliation


@shared_task
def reconcile_active_organizations():
    organizations_checked = 0
    issues_found = 0
    for organization in Organization.objects.filter(status=OrganizationStatus.ACTIVE).iterator():
        organizations_checked += 1
        issues = run_reconciliation(organization=organization)
        if not issues:
            continue
        issues_found += len(issues)
        areas = Counter(issue.area for issue in issues)
        area_summary = ", ".join(f"{area}: {count}" for area, count in sorted(areas.items()))
        notify_business_event(
            organization=organization,
            title="Operational reconciliation needs review",
            message=(
                f"MobiPOS found {len(issues)} balance or processing issue(s) during the daily control check "
                f"({area_summary}). No records were changed automatically. Contact the platform administrator "
                "and review the referenced operational records before continuing affected work."
            ),
            link="/reports/operational/",
            include_owners=True,
        )
    return {"organizations_checked": organizations_checked, "issues_found": issues_found}
