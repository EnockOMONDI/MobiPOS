from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import BackupRun


@admin.register(BackupRun)
class BackupRunAdmin(ModelAdmin):
    list_display = ("created_at", "status", "storage_backend", "size_bytes", "completed_at", "restored_at")
    list_filter = ("status", "storage_backend")
    search_fields = ("object_key", "checksum_sha256")
    readonly_fields = tuple(field.name for field in BackupRun._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
