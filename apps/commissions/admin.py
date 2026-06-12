from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import CommissionAccrual, CommissionPayout, CommissionPayoutLine, CommissionRule

admin.site.register(CommissionRule, OrganizationOwnedAdmin)
admin.site.register(CommissionAccrual, OrganizationOwnedAdmin)
admin.site.register(CommissionPayout, OrganizationOwnedAdmin)
admin.site.register(CommissionPayoutLine, OrganizationOwnedAdmin)

# Register your models here.
