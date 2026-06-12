import uuid

from django.conf import settings
from django.db import models

from apps.organizations.models import Location, OrganizationOwnedModel


class SessionStatus(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"
    REVIEWED = "reviewed", "Reviewed"


class POSSession(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="pos_sessions")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pos_sessions")
    status = models.CharField(max_length=20, choices=SessionStatus.choices, default=SessionStatus.OPEN)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    opening_float = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    expected_cash = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    actual_cash = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    variance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    closing_note = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("organization", "number"), name="unique_pos_session_number_per_org")]

    def __str__(self):
        return self.number

# Create your models here.
