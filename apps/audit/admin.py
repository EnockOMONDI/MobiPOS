from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import AuditEvent


def environment_callback(request):
    return ["Development", "warning"]


@admin.register(AuditEvent)
class AuditEventAdmin(ModelAdmin):
    list_display = ("created_at", "action", "actor", "organization", "target_type", "target_id")
    list_filter = ("action", "organization", "created_at")
    search_fields = ("action", "message", "target_type", "target_id")
    readonly_fields = (
        "id",
        "organization",
        "actor",
        "action",
        "target_type",
        "target_id",
        "message",
        "metadata",
        "ip_address",
        "user_agent",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

# Register your models here.
