from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import POSSession

admin.site.register(POSSession, OrganizationOwnedAdmin)

# Register your models here.
