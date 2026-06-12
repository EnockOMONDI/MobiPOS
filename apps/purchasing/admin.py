from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import PurchaseDiscrepancy, PurchaseOrder, PurchaseOrderLine, SupplierReturn

admin.site.register(PurchaseOrder, OrganizationOwnedAdmin)
admin.site.register(PurchaseOrderLine, OrganizationOwnedAdmin)
admin.site.register(SupplierReturn, OrganizationOwnedAdmin)
admin.site.register(PurchaseDiscrepancy, OrganizationOwnedAdmin)

# Register your models here.
