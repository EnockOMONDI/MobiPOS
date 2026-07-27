from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import FiscalDevice, FiscalDocument, FiscalDocumentLine, IntegrationEvent


@admin.register(IntegrationEvent)
class IntegrationEventAdmin(OrganizationOwnedAdmin):
    list_display = ("provider", "event_type", "status", "attempts", "organization", "created_at")
    list_filter = ("organization", "provider", "status")
    search_fields = ("provider", "event_type", "idempotency_key", "error_message")


@admin.register(FiscalDevice)
class FiscalDeviceAdmin(OrganizationOwnedAdmin):
    list_display = ("branch_office_id", "taxpayer_pin", "environment", "status", "receipt_policy", "organization")
    list_filter = ("organization", "provider", "environment", "status", "receipt_policy")
    search_fields = ("taxpayer_pin", "branch_office_id", "device_serial", "device_name")


class FiscalDocumentLineInline(admin.TabularInline):
    model = FiscalDocumentLine
    extra = 0
    readonly_fields = (
        "organization",
        "product_name",
        "item_code",
        "quantity",
        "unit_price",
        "discount",
        "tax_rate",
        "tax_amount",
        "total_amount",
        "imei_or_serial",
        "tax_type_code",
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(FiscalDocument)
class FiscalDocumentAdmin(OrganizationOwnedAdmin):
    list_display = ("internal_number", "document_type", "status", "etims_invoice_number", "organization", "created_at")
    list_filter = ("organization", "provider", "document_type", "status")
    search_fields = ("internal_number", "etims_invoice_number", "etims_control_code")
    readonly_fields = (
        "payload_snapshot",
        "response_snapshot",
        "submitted_at",
        "accepted_at",
        "failed_at",
        "etims_invoice_number",
        "etims_control_code",
        "etims_signature",
        "qr_payload",
    )
    inlines = (FiscalDocumentLineInline,)


@admin.register(FiscalDocumentLine)
class FiscalDocumentLineAdmin(OrganizationOwnedAdmin):
    list_display = ("document", "product_name", "quantity", "total_amount", "imei_or_serial", "organization")
    list_filter = ("organization", "tax_type_code")
    search_fields = ("product_name", "item_code", "imei_or_serial")
