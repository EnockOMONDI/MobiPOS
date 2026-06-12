from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import ApprovalRequest, Payable, Receivable

admin.site.register(Receivable, OrganizationOwnedAdmin)
admin.site.register(Payable, OrganizationOwnedAdmin)
admin.site.register(ApprovalRequest, OrganizationOwnedAdmin)

# Register your models here.
