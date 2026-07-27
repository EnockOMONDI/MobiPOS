from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import (
    Announcement,
    AgentDocument,
    AgentProfile,
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
    immutable_model_labels = {
        "inventory.StockBalance",
        "inventory.StockMovement",
        "sales.SaleLine",
        "sales.SaleReturnLine",
        "payments.Refund",
        "commissions.CommissionAccrual",
        "commissions.CommissionPayoutLine",
        "operations.Payable",
        "operations.Receivable",
        "operations.ReceivableInstallment",
        "integrations.IntegrationEvent",
        "integrations.FiscalDocument",
        "integrations.FiscalDocumentLine",
    }
    editable_statuses = {"draft", "requested", "pending", "open"}

    def _authorized_organizations(self, request):
        if request.user.is_superuser or request.user.is_platform_admin:
            return Organization.objects.all()
        return Organization.objects.filter(
            membership__user=request.user,
            membership__status="active",
            membership__is_owner=True,
        ).distinct()

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

    def has_view_or_change_permission(self, request, obj=None):
        if obj is None or request.user.is_superuser or request.user.is_platform_admin:
            return True
        return bool(self._active_membership(request, obj))

    def has_view_permission(self, request, obj=None):
        return self.has_view_or_change_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj is not None and self._is_immutable_object(obj):
            return False
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
        if obj is not None and self._is_immutable_object(obj):
            return False
        return request.user.is_superuser or request.user.is_platform_admin

    def _is_immutable_object(self, obj):
        if not obj:
            return False
        label = obj._meta.label
        if label in self.immutable_model_labels:
            return True
        status = getattr(obj, "status", None)
        return bool(status and status not in self.editable_statuses)

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None and self._is_immutable_object(obj):
            readonly.extend(field.name for field in obj._meta.fields)
        return tuple(dict.fromkeys(readonly))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        related_model = db_field.remote_field.model
        authorized_orgs = self._authorized_organizations(request)
        if db_field.name == "organization":
            kwargs["queryset"] = authorized_orgs
        elif hasattr(related_model, "organization_id") or any(field.name == "organization" for field in related_model._meta.fields):
            kwargs["queryset"] = related_model.objects.filter(organization__in=authorized_orgs)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        related_model = db_field.remote_field.model
        if hasattr(related_model, "organization_id") or any(field.name == "organization" for field in related_model._meta.fields):
            kwargs["queryset"] = related_model.objects.filter(organization__in=self._authorized_organizations(request))
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        obj.full_clean()
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            instance.full_clean()
            instance.save()
        formset.save_m2m()
        for deleted in formset.deleted_objects:
            deleted.delete()


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


@admin.register(AgentProfile)
class AgentProfileAdmin(OrganizationOwnedAdmin):
    list_display = ("legal_name", "profile_type", "status", "branch", "supervisor", "organization")
    list_filter = ("organization", "profile_type", "status", "branch")
    search_fields = ("legal_name", "national_id_number", "membership__user__username", "membership__user__email")


@admin.register(AgentDocument)
class AgentDocumentAdmin(OrganizationOwnedAdmin):
    list_display = ("profile", "document_type", "original_filename", "uploaded_by", "created_at", "organization")
    list_filter = ("organization", "document_type", "created_at")
    search_fields = ("profile__legal_name", "original_filename", "notes")
    readonly_fields = ("original_filename", "content_type", "size", "uploaded_by")


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
