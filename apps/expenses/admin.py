from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import Expense

admin.site.register(Expense, OrganizationOwnedAdmin)

# Register your models here.
