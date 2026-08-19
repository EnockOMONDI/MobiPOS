from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import AgedStockAction, StockAdjustment, StockBalance, StockMovement, StockUnit, StockUnitOffer


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


@admin.register(AgedStockAction)
class AgedStockActionAdmin(OrganizationOwnedAdmin):
    list_display = ("stock_unit", "action_type", "status", "proposed_by", "approved_by", "organization")
    list_filter = ("organization", "action_type", "status")
    search_fields = ("stock_unit__serial_number", "stock_unit__secondary_serial", "stock_unit__product__name", "reason")


@admin.register(StockUnitOffer)
class StockUnitOfferAdmin(OrganizationOwnedAdmin):
    list_display = ("name", "stock_unit", "offer_type", "promotional_price", "ends_on", "is_active", "organization")
    list_filter = ("organization", "offer_type", "is_active")
    search_fields = ("name", "stock_unit__serial_number", "stock_unit__product__name")

# Register your models here.
