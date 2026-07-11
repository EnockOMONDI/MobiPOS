from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import CashMovement, POSSession

admin.site.register(POSSession, OrganizationOwnedAdmin)
admin.site.register(CashMovement, OrganizationOwnedAdmin)

# Register your models here.
