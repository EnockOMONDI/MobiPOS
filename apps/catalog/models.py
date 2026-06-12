import uuid

from django.db import models

from apps.organizations.models import OrganizationOwnedModel


class Category(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    code = models.SlugField(max_length=80)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "categories"
        constraints = [models.UniqueConstraint(fields=("organization", "code"), name="unique_category_code_per_org")]

    def __str__(self):
        return self.name


class Brand(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("organization", "name"), name="unique_brand_name_per_org")]

    def __str__(self):
        return self.name


class Product(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name="products", null=True, blank=True)
    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=80)
    barcode = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    is_serialized = models.BooleanField(default=False)
    is_stocked = models.BooleanField(default=True)
    is_sellable = models.BooleanField(default=True)
    is_purchasable = models.BooleanField(default=True)
    warranty_days = models.PositiveIntegerField(default=0)
    reorder_level = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    cost_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    selling_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)
        constraints = [models.UniqueConstraint(fields=("organization", "sku"), name="unique_product_sku_per_org")]

    def __str__(self):
        return f"{self.name} ({self.sku})"

# Create your models here.
