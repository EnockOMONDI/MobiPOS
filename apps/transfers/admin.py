from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import StockTransfer, StockTransferLine, TransferDiscrepancy

admin.site.register(StockTransfer, OrganizationOwnedAdmin)
admin.site.register(StockTransferLine, OrganizationOwnedAdmin)
admin.site.register(TransferDiscrepancy, OrganizationOwnedAdmin)

# Register your models here.
