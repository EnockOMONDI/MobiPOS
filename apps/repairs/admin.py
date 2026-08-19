from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import RepairPartUsage, RepairPayment, RepairTicket, RepairTransition


@admin.register(RepairTicket)
class RepairTicketAdmin(OrganizationOwnedAdmin):
    list_display = ("number", "organization", "branch", "customer", "status", "quoted_amount")
    list_filter = ("organization", "status", "warranty_type")
    search_fields = ("number", "customer__name", "stock_unit__serial_number")

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj:
            readonly.extend(("status", "collected_at"))
        return tuple(readonly)

    def has_delete_permission(self, request, obj=None):
        return False


class RepairEvidenceAdmin(OrganizationOwnedAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in ("GET", "HEAD", "OPTIONS") and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(RepairPartUsage, RepairEvidenceAdmin)
admin.site.register(RepairTransition, RepairEvidenceAdmin)
admin.site.register(RepairPayment, RepairEvidenceAdmin)

# Register your models here.
