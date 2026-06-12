from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import Contact


@admin.register(Contact)
class ContactAdmin(OrganizationOwnedAdmin):
    list_display = ("name", "contact_type", "organization", "phone_number", "credit_limit", "is_active")
    list_filter = ("organization", "contact_type", "is_active")
    search_fields = ("name", "phone_number", "email", "tax_number")

# Register your models here.
