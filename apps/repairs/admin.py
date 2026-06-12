from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import RepairPartUsage, RepairTicket

admin.site.register(RepairTicket, OrganizationOwnedAdmin)
admin.site.register(RepairPartUsage, OrganizationOwnedAdmin)

# Register your models here.
