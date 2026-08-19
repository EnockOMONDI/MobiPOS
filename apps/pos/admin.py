from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import CashMovement, OfflineInvoiceQueue, POSSession

admin.site.register(POSSession, OrganizationOwnedAdmin)
admin.site.register(CashMovement, OrganizationOwnedAdmin)

@admin.register(OfflineInvoiceQueue)
class RecoveryDraftAdmin(OrganizationOwnedAdmin):
    list_display = ("client_reference", "organization", "location", "cashier", "status", "draft_sale", "created_at")
    list_filter = ("status", "organization", "location")
    search_fields = ("client_reference", "sale_number", "cashier__email", "device_id")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

# Register your models here.
