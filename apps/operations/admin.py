from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import ApprovalPolicy, ApprovalRequest, Payable, Receivable, ReceivableInstallment

admin.site.register(Receivable, OrganizationOwnedAdmin)
admin.site.register(Payable, OrganizationOwnedAdmin)
admin.site.register(ApprovalRequest, OrganizationOwnedAdmin)
admin.site.register(ReceivableInstallment, OrganizationOwnedAdmin)
admin.site.register(ApprovalPolicy, OrganizationOwnedAdmin)

# Register your models here.
