from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import ReportExport


@admin.register(ReportExport)
class ReportExportAdmin(ModelAdmin):
    list_display = ("module", "export_format", "organization", "requested_by", "status", "row_count", "created_at")
    list_filter = ("status", "export_format", "module")
    search_fields = ("module", "requested_by__email", "organization__name")
    readonly_fields = (
        "organization", "requested_by", "module", "export_format", "filters", "status",
        "file", "row_count", "error_reference", "error_message", "started_at",
        "completed_at", "expires_at", "created_at", "updated_at",
    )

# Register your models here.
