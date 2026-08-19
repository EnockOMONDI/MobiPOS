import logging
import uuid

from celery import shared_task
from django.core.files.base import ContentFile
from django.db import transaction
from django.test import RequestFactory
from django.utils import timezone

from apps.notifications.services import notify_business_event
from apps.organizations.models import Membership, MembershipStatus

from .models import ReportExport, ReportExportStatus


logger = logging.getLogger(__name__)


@shared_task
def generate_report_export(export_id):
    with transaction.atomic():
        export = ReportExport.objects.select_for_update().select_related(
            "organization", "requested_by"
        ).get(pk=export_id)
        if export.status in {ReportExportStatus.READY, ReportExportStatus.EXPIRED}:
            return export.status
        export.status = ReportExportStatus.PROCESSING
        export.started_at = timezone.now()
        export.error_reference = ""
        export.error_message = ""
        export.save(update_fields=(
            "status", "started_at", "error_reference", "error_message", "updated_at"
        ))

    try:
        from .views import module_overview
        from .workspaces import REGISTER_CONFIG, operational_register_export

        membership = Membership.objects.filter(
            organization=export.organization,
            user=export.requested_by,
            status=MembershipStatus.ACTIVE,
        ).first()
        request = RequestFactory().get(
            f"/overview/{export.module}/",
            {**export.filters, "format": export.export_format},
        )
        request.user = export.requested_by
        request.organization = export.organization
        request.membership = membership
        request._background_export = True
        if export.module in REGISTER_CONFIG and export.export_format in {"csv", "pdf"}:
            response = operational_register_export(request, export.module)
        else:
            response = module_overview(request, export.module)
        if response.status_code != 200:
            raise RuntimeError(f"Export renderer returned HTTP {response.status_code}.")
        filename = f"{export.module}-{timezone.localdate().isoformat()}.{export.export_format}"
        export.file.save(filename, ContentFile(response.content), save=False)
        export.row_count = export.row_count or max(response.content.count(b"\n") - 1, 0)
        export.status = ReportExportStatus.READY
        export.completed_at = timezone.now()
        export.save(update_fields=("file", "row_count", "status", "completed_at", "updated_at"))
        notify_business_event(
            organization=export.organization,
            title="Your report export is ready",
            message=f"The {export.module.replace('-', ' ')} {export.export_format.upper()} export is ready to download.",
            link=f"/reports/exports/{export.id}/",
            users=[export.requested_by],
        )
        return export.status
    except Exception:
        reference = uuid.uuid4().hex[:12].upper()
        logger.exception("Report export failed reference=%s export_id=%s", reference, export_id)
        ReportExport.objects.filter(pk=export_id).update(
            status=ReportExportStatus.FAILED,
            completed_at=timezone.now(),
            error_reference=reference,
            error_message="The export could not be generated. Retry it or contact support with the reference shown.",
        )
        return ReportExportStatus.FAILED


@shared_task
def expire_report_exports():
    expired = ReportExport.objects.filter(
        status=ReportExportStatus.READY,
        expires_at__lte=timezone.now(),
    )
    count = 0
    for export in expired.iterator():
        if export.file:
            export.file.delete(save=False)
        export.status = ReportExportStatus.EXPIRED
        export.file = ""
        export.save(update_fields=("status", "file", "updated_at"))
        count += 1
    return count
