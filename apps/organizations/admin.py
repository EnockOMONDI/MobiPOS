from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import (
    Announcement,
    Branch,
    Company,
    Location,
    Membership,
    Organization,
    OrganizationSetting,
    Plan,
    Role,
    Subscription,
    SubscriptionInvoice,
)


class OrganizationOwnedAdmin(ModelAdmin):
    list_filter = ("organization",)

    def _active_membership(self, request, obj=None):
        organization = getattr(obj, "organization", None)
        queryset = request.user.memberships.filter(status="active")
        if organization:
            queryset = queryset.filter(organization=organization)
        return queryset.first()

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser or request.user.is_platform_admin:
            return queryset
        return queryset.filter(
            organization__membership__user=request.user,
            organization__membership__status="active",
        ).distinct()

    def has_view_or_change_permission(self, request, obj):
        if obj is None or request.user.is_superuser or request.user.is_platform_admin:
            return True
        return bool(self._active_membership(request, obj))

    def has_view_permission(self, request, obj=None):
        return self.has_view_or_change_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser or request.user.is_platform_admin:
            return True
        if obj is None:
            return request.user.memberships.filter(status="active", is_owner=True).exists()
        membership = self._active_membership(request, obj)
        return bool(membership and membership.is_owner)

    def has_add_permission(self, request):
        if request.user.is_superuser or request.user.is_platform_admin:
            return True
        return request.user.memberships.filter(status="active", is_owner=True).exists()

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser or request.user.is_platform_admin


@admin.register(Organization)
class OrganizationAdmin(ModelAdmin):
    list_display = ("name", "status", "currency", "timezone", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("name", "slug", "email", "phone_number")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser or request.user.is_platform_admin:
            return queryset
        return queryset.filter(membership__user=request.user, membership__status="active").distinct()

    def has_add_permission(self, request):
        return request.user.is_superuser or request.user.is_platform_admin


@admin.register(Company)
class CompanyAdmin(OrganizationOwnedAdmin):
    list_display = ("name", "code", "organization", "is_active")
    search_fields = ("name", "code", "legal_name", "tax_number")


@admin.register(Branch)
class BranchAdmin(OrganizationOwnedAdmin):
    list_display = ("name", "code", "company", "organization", "is_active")
    search_fields = ("name", "code")


@admin.register(Location)
class LocationAdmin(OrganizationOwnedAdmin):
    list_display = ("name", "code", "location_type", "branch", "organization", "is_active")
    list_filter = ("organization", "location_type", "is_active")
    search_fields = ("name", "code")


@admin.register(Role)
class RoleAdmin(OrganizationOwnedAdmin):
    list_display = ("name", "code", "organization", "is_active")
    filter_horizontal = ("permissions",)


@admin.register(Membership)
class MembershipAdmin(OrganizationOwnedAdmin):
    list_display = ("user", "organization", "status", "is_owner")
    list_filter = ("organization", "status", "is_owner")
    filter_horizontal = ("roles", "branches")


@admin.register(Plan)
class PlanAdmin(ModelAdmin):
    list_display = ("name", "code", "monthly_price", "is_active")

    def has_module_permission(self, request):
        return request.user.is_authenticated and (
            request.user.is_superuser or request.user.is_platform_admin
        )


@admin.register(Subscription)
class SubscriptionAdmin(OrganizationOwnedAdmin):
    list_display = ("organization", "plan", "status", "renews_on", "grace_ends_on")
    list_filter = ("organization", "status", "plan")


admin.site.register(SubscriptionInvoice, OrganizationOwnedAdmin)


@admin.register(OrganizationSetting)
class OrganizationSettingAdmin(OrganizationOwnedAdmin):
    list_display = ("key", "organization", "description", "updated_at")
    search_fields = ("key", "description")


@admin.register(Announcement)
class AnnouncementAdmin(ModelAdmin):
    list_display = ("title", "organization", "is_active", "starts_at", "ends_at")
    list_filter = ("organization", "is_active")
    search_fields = ("title", "body")

# Register your models here.
