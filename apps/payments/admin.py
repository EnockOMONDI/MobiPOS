from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import Payment, Refund

admin.site.register(Payment, OrganizationOwnedAdmin)
admin.site.register(Refund, OrganizationOwnedAdmin)

# Register your models here.
