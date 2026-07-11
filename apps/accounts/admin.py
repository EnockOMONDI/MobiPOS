from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from unfold.admin import ModelAdmin

from .models import RecoveryCode, User, UserSession


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "is_platform_admin",
        "is_active",
    )
    list_filter = ("is_platform_admin", "is_staff", "is_active")
    search_fields = ("username", "email", "first_name", "last_name", "phone_number")
    fieldsets = BaseUserAdmin.fieldsets + (
        ("MobiPOS", {"fields": ("phone_number", "is_platform_admin")}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("MobiPOS", {"fields": ("email", "phone_number", "is_platform_admin")}),
    )

    def has_module_permission(self, request):
        return request.user.is_superuser or getattr(request.user, "is_platform_admin", False)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)

# Register your models here.
admin.site.register(RecoveryCode, ModelAdmin)
admin.site.register(UserSession, ModelAdmin)
