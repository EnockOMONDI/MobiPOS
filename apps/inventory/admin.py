from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import StockAdjustment, StockBalance, StockMovement, StockUnit


@admin.register(StockUnit)
class StockUnitAdmin(OrganizationOwnedAdmin):
    list_display = ("serial_number", "product", "status", "location", "organization")
    list_filter = ("organization", "status", "location")
    search_fields = ("serial_number", "secondary_serial", "product__name")


@admin.register(StockMovement)
class StockMovementAdmin(OrganizationOwnedAdmin):
    list_display = ("created_at", "movement_type", "product", "location", "quantity", "organization")
    list_filter = ("organization", "movement_type", "location")
    readonly_fields = [field.name for field in StockMovement._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(StockBalance, OrganizationOwnedAdmin)
admin.site.register(StockAdjustment, OrganizationOwnedAdmin)

# Register your models here.
