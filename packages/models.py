from django.db import models
from core.models import TenantOwnedModel


class Package(TenantOwnedModel):
    class BillingInterval(models.TextChoices):
        ONE_TIME = "one_time", "One-Time"
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        BIANNUAL = "biannual", "Bi-Annual"
        ANNUAL = "annual", "Annual"
        CUSTOM = "custom", "Custom"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"
        DRAFT = "draft", "Draft"

    external_id = models.CharField(max_length=255, db_index=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=10, default="USD")
    billing_interval = models.CharField(
        max_length=32,
        choices=BillingInterval.choices,
        default=BillingInterval.ONE_TIME,
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    external_url = models.URLField(max_length=500, blank=True)
    features = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "external_id"],
                name="uniq_package_tenant_extid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "status"],
                name="idx_package_tenant_status",
            ),
            models.Index(
                fields=["tenant", "external_id"],
                name="idx_package_tenant_extid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.external_id})"
