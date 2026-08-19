from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import EmailOutbox, Notification


@admin.register(Notification)
class NotificationAdmin(OrganizationOwnedAdmin):
    list_display = ("title", "recipient", "kind", "read_at", "created_at")
    list_filter = ("kind", "read_at", "created_at")
    search_fields = ("title", "message", "recipient__email")
    readonly_fields = tuple(field.name for field in Notification._meta.fields)


@admin.register(EmailOutbox)
class EmailOutboxAdmin(admin.ModelAdmin):
    list_display = ("recipient", "subject", "status", "attempts", "sent_at", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("recipient", "subject", "provider_message_id")
    readonly_fields = tuple(field.name for field in EmailOutbox._meta.fields)

# Register your models here.
