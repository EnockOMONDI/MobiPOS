import uuid

from django.conf import settings
from django.db import models

from apps.organizations.models import Company, Location, OrganizationOwnedModel
from apps.sales.models import Sale, SaleReturn


class IntegrationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    DEAD = "dead", "Dead letter"


class IntegrationEvent(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=40)
    event_type = models.CharField(max_length=80)
    idempotency_key = models.CharField(max_length=120, unique=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=IntegrationStatus.choices, default=IntegrationStatus.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    response = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("status", "next_retry_at", "provider"),
                name="int_status_retry_prov",
            ),
            models.Index(
                fields=("organization", "status", "created_at"),
                name="int_org_st_created",
            ),
        ]


class FiscalProvider(models.TextChoices):
    ETIMS = "etims", "KRA eTIMS"


class FiscalDeviceMode(models.TextChoices):
    OSCU = "oscu", "OSCU"
    VSCU = "vscu", "VSCU"


class FiscalEnvironment(models.TextChoices):
    SANDBOX = "sandbox", "Sandbox"
    PRODUCTION = "production", "Production"


class FiscalDeviceStatus(models.TextChoices):
    NOT_CONFIGURED = "not_configured", "Not configured"
    SANDBOX_TESTING = "sandbox_testing", "Sandbox testing"
    READY_FOR_PRODUCTION = "ready_for_production", "Ready for production"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    FAILED = "failed", "Failed / needs attention"


class FiscalDocumentType(models.TextChoices):
    SALE = "sale", "Sale tax invoice"
    CREDIT_NOTE = "credit_note", "Credit note"


class FiscalDocumentStatus(models.TextChoices):
    NOT_REQUIRED = "not_required", "Not required"
    PENDING = "pending", "Pending"
    SUBMITTED = "submitted", "Submitted"
    ACCEPTED = "accepted", "Accepted"
    FAILED = "failed", "Failed"
    DEAD = "dead", "Dead letter"


class ReceiptPolicy(models.TextChoices):
    STANDARD_ONLY = "standard_only", "Standard receipt only"
    STANDARD_AND_ETIMS = "standard_and_etims", "Standard receipt plus eTIMS"
    ETIMS_REQUIRED = "etims_required", "Require eTIMS before final tax receipt"


class FiscalDevice(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=32, choices=FiscalProvider.choices, default=FiscalProvider.ETIMS)
    mode = models.CharField(max_length=16, choices=FiscalDeviceMode.choices, default=FiscalDeviceMode.OSCU)
    environment = models.CharField(max_length=16, choices=FiscalEnvironment.choices, default=FiscalEnvironment.SANDBOX)
    status = models.CharField(
        max_length=32,
        choices=FiscalDeviceStatus.choices,
        default=FiscalDeviceStatus.NOT_CONFIGURED,
    )
    receipt_policy = models.CharField(
        max_length=32,
        choices=ReceiptPolicy.choices,
        default=ReceiptPolicy.STANDARD_AND_ETIMS,
    )
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="fiscal_devices", null=True, blank=True)
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="fiscal_devices", null=True, blank=True)
    taxpayer_pin = models.CharField(max_length=32)
    branch_office_id = models.CharField(max_length=32, help_text="KRA branch office ID / bhfId.")
    device_serial = models.CharField(max_length=80, blank=True)
    device_name = models.CharField(max_length=120, blank=True)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="activated_fiscal_devices",
        null=True,
        blank=True,
    )
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ("organization", "provider", "branch_office_id")
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "provider", "branch_office_id", "environment"),
                name="unique_fiscal_device_branch_environment",
            )
        ]

    @property
    def can_submit_etims(self):
        return (
            self.provider == FiscalProvider.ETIMS
            and self.status in {FiscalDeviceStatus.SANDBOX_TESTING, FiscalDeviceStatus.READY_FOR_PRODUCTION, FiscalDeviceStatus.ACTIVE}
            and self.receipt_policy != ReceiptPolicy.STANDARD_ONLY
        )

    @property
    def is_compliant_active(self):
        return self.status == FiscalDeviceStatus.ACTIVE

    def __str__(self):
        return f"{self.get_provider_display()} {self.branch_office_id} ({self.get_environment_display()})"


class FiscalDocument(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=32, choices=FiscalProvider.choices, default=FiscalProvider.ETIMS)
    device = models.ForeignKey(FiscalDevice, on_delete=models.PROTECT, related_name="documents")
    document_type = models.CharField(max_length=24, choices=FiscalDocumentType.choices)
    status = models.CharField(max_length=24, choices=FiscalDocumentStatus.choices, default=FiscalDocumentStatus.PENDING)
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="fiscal_documents", null=True, blank=True)
    sale_return = models.ForeignKey(SaleReturn, on_delete=models.PROTECT, related_name="fiscal_documents", null=True, blank=True)
    internal_number = models.CharField(max_length=48)
    receipt_type_code = models.CharField(max_length=8, blank=True)
    transaction_type_code = models.CharField(max_length=8, blank=True)
    etims_invoice_number = models.CharField(max_length=120, blank=True)
    etims_control_code = models.CharField(max_length=120, blank=True)
    etims_signature = models.CharField(max_length=255, blank=True)
    qr_payload = models.TextField(blank=True)
    payload_snapshot = models.JSONField(default=dict, blank=True)
    response_snapshot = models.JSONField(default=dict, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "sale", "document_type"),
                condition=models.Q(sale__isnull=False),
                name="unique_fiscal_document_per_sale_type",
            ),
            models.UniqueConstraint(
                fields=("organization", "sale_return", "document_type"),
                condition=models.Q(sale_return__isnull=False),
                name="unique_fiscal_document_per_return_type",
            ),
        ]

    @property
    def is_final_tax_receipt_available(self):
        return self.status == FiscalDocumentStatus.ACCEPTED

    def __str__(self):
        return f"{self.internal_number} · {self.get_document_type_display()}"


class FiscalDocumentLine(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(FiscalDocument, on_delete=models.CASCADE, related_name="lines")
    product_name = models.CharField(max_length=255)
    item_code = models.CharField(max_length=80)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    discount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    imei_or_serial = models.CharField(max_length=120, blank=True)
    tax_type_code = models.CharField(max_length=12, default="B")

    class Meta:
        ordering = ("created_at",)
