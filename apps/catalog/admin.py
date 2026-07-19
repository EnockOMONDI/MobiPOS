from django.contrib import admin
from apps.organizations.admin import OrganizationOwnedAdmin
from .models import Brand, Category, Product

admin.site.register(Brand, OrganizationOwnedAdmin)
admin.site.register(Category, OrganizationOwnedAdmin)


@admin.register(Product)
class ProductAdmin(OrganizationOwnedAdmin):
    list_display = ("name", "sku", "organization", "is_serialized", "selling_price", "has_image", "is_active")
    list_filter = ("organization", "is_serialized", "is_active", "category")
    search_fields = ("name", "sku", "barcode")

    @admin.display(boolean=True, description="Image")
    def has_image(self, obj):
        return bool(obj.display_image_url)

# Register your models here.
