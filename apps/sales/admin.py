from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import Sale, SaleLine, SaleReturn, SaleReturnLine

admin.site.register(Sale, OrganizationOwnedAdmin)
admin.site.register(SaleLine, OrganizationOwnedAdmin)
admin.site.register(SaleReturn, OrganizationOwnedAdmin)
admin.site.register(SaleReturnLine, OrganizationOwnedAdmin)

# Register your models here.
