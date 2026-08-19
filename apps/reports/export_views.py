from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import ReportExport, ReportExportStatus


def accessible_report_exports(request):
    queryset = ReportExport.objects.select_related("organization", "requested_by")
    if request.user.is_superuser or request.user.is_platform_admin:
        return queryset
    if not request.organization:
        return queryset.none()
    queryset = queryset.filter(organization=request.organization)
    if request.membership and request.membership.is_owner:
        return queryset
    return queryset.filter(requested_by=request.user)


@login_required
def report_export_list(request):
    page_obj = Paginator(accessible_report_exports(request), 25).get_page(request.GET.get("page"))
    return render(request, "reports/export_list.html", {"page_obj": page_obj})


@login_required
def report_export_detail(request, export_id):
    export = get_object_or_404(accessible_report_exports(request), pk=export_id)
    return render(request, "reports/export_detail.html", {"export": export})


@login_required
def report_export_download(request, export_id):
    export = get_object_or_404(accessible_report_exports(request), pk=export_id)
    if export.status != ReportExportStatus.READY or not export.file:
        raise Http404("This export is not ready for download.")
    if export.expires_at <= timezone.now():
        raise Http404("This export has expired.")
    filename = export.file.name.rsplit("/", maxsplit=1)[-1]
    return FileResponse(export.file.open("rb"), as_attachment=True, filename=filename)


@login_required
def report_export_retry(request, export_id):
    if request.method != "POST":
        raise Http404
    export = get_object_or_404(accessible_report_exports(request), pk=export_id)
    if export.status not in {ReportExportStatus.FAILED, ReportExportStatus.EXPIRED}:
        return redirect("report-export-detail", export_id=export.id)
    export.status = ReportExportStatus.PENDING
    export.error_reference = ""
    export.error_message = ""
    export.expires_at = timezone.now() + timedelta(days=settings.REPORT_EXPORT_RETENTION_DAYS)
    export.save(update_fields=("status", "error_reference", "error_message", "expires_at", "updated_at"))

    from .tasks import generate_report_export

    transaction.on_commit(lambda: generate_report_export.delay(str(export.id)))
    return redirect("report-export-detail", export_id=export.id)
