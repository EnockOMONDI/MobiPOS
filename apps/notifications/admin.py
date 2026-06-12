from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import Notification

admin.site.register(Notification, OrganizationOwnedAdmin)

# Register your models here.
