from decimal import Decimal
import uuid
from django.db import models
from django.db.models import Q
from core.models import TenantOwnedModel
from messaging.models import Channel, WhatsAppCategory


class Direction(models.TextChoices):
    OUTBOUND = "outbound", "Outbound"
    INBOUND = "inbound", "Inbound"


class UsageRecord(TenantOwnedModel):
    Direction = Direction

    message = models.OneToOneField(
        "messaging.Message",
        on_delete=models.CASCADE,
        related_name="usage_record",
        unique=True,
    )
    channel = models.CharField(
        max_length=20,
        choices=Channel.choices,
    )
    direction = models.CharField(
        max_length=10,
        choices=Direction.choices,
        default=Direction.OUTBOUND,
    )
    quantity = models.PositiveIntegerField(default=1)
    destination_country = models.CharField(max_length=2, blank=True, default="")
    whatsapp_category = models.CharField(
        max_length=20,
        blank=True,
        default="",
        choices=WhatsAppCategory.choices,
    )
    unit_cost = models.DecimalField(max_digits=10, decimal_places=5)
    total_cost = models.DecimalField(max_digits=12, decimal_places=5)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(max_length=20, blank=True, default="")
    occurred_at = models.DateTimeField()

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(
                fields=["tenant", "occurred_at"],
                name="idx_usagerec_t_occurred",
            ),
            models.Index(
                fields=["tenant", "channel", "occurred_at"],
                name="idx_usagerec_t_ch_occurred",
            ),
            models.Index(
                fields=["tenant", "status", "occurred_at"],
                name="idx_usagerec_t_st_occurred",
            ),
            models.Index(
                fields=["tenant", "direction", "occurred_at"],
                name="idx_usagerec_t_dir_occ",
            ),
            models.Index(
                fields=["tenant", "channel", "direction", "occurred_at"],
                name="idx_usagerec_t_ch_d_occ",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.unit_cost is not None and self.quantity is not None:
            self.total_cost = (Decimal(self.quantity) * Decimal(self.unit_cost)).quantize(
                Decimal("0.00001")
            )
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"UsageRecord({self.channel}, {self.direction}, qty={self.quantity}, total={self.total_cost} {self.currency})"


class RateCard(models.Model):
    Direction = Direction

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="rate_cards",
    )
    objects = models.Manager()

    channel = models.CharField(
        max_length=20,
        choices=Channel.choices,
    )
    direction = models.CharField(
        max_length=10,
        choices=Direction.choices,
        default=Direction.OUTBOUND,
    )
    destination_country = models.CharField(max_length=2, blank=True, default="")
    whatsapp_category = models.CharField(
        max_length=20,
        blank=True,
        default="",
        choices=WhatsAppCategory.choices,
    )
    unit_cost = models.DecimalField(max_digits=10, decimal_places=5)
    currency = models.CharField(max_length=3, default="USD")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["channel", "direction", "destination_country", "whatsapp_category"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "channel", "direction", "destination_country", "whatsapp_category"],
                condition=Q(tenant__isnull=False),
                name="uniq_ratecard_tenant_scope",
            ),
            models.UniqueConstraint(
                fields=["channel", "direction", "destination_country", "whatsapp_category"],
                condition=Q(tenant__isnull=True),
                name="uniq_ratecard_global_scope",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "channel", "direction", "destination_country"],
                name="idx_ratecard_t_ch_d_ctry",
            ),
            models.Index(
                fields=["channel", "direction", "destination_country"],
                name="idx_ratecard_ch_d_ctry",
            ),
        ]

    def __str__(self) -> str:
        scope = self.tenant.name if self.tenant else "GLOBAL"
        return f"RateCard({scope}, {self.channel}, {self.direction}, {self.destination_country or '*'}, {self.whatsapp_category or '*'}: {self.unit_cost} {self.currency})"
