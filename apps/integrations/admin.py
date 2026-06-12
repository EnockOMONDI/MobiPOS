from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import IntegrationEvent

admin.site.register(IntegrationEvent, OrganizationOwnedAdmin)

# Register your models here.
