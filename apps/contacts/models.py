import uuid

from django.db import models

from apps.organizations.models import OrganizationOwnedModel


class ContactType(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    SUPPLIER = "supplier", "Supplier"
    BOTH = "both", "Customer and supplier"


class Contact(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact_type = models.CharField(max_length=20, choices=ContactType.choices)
    name = models.CharField(max_length=200)
    phone_number = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    tax_number = models.CharField(max_length=64, blank=True)
    address = models.TextField(blank=True)
    credit_limit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    payment_terms_days = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name

# Create your models here.
