from django.conf import settings
from django.db import models
from django.db.models import Q
from core.models import TenantOwnedModel


class Channel(models.TextChoices):
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"
    WHATSAPP = "whatsapp", "WhatsApp"


class WhatsAppCategory(models.TextChoices):
    MARKETING = "marketing", "Marketing"
    UTILITY = "utility", "Utility"
    AUTHENTICATION = "authentication", "Authentication"
    SERVICE = "service", "Service"


class MessageTemplate(TenantOwnedModel):
    class ApprovalStatus(models.TextChoices):
        APPROVED = "approved", "Approved"
        PENDING = "pending", "Pending"
        REJECTED = "rejected", "Rejected"
        NOT_REQUIRED = "not_required", "Not Required"

    channel = models.CharField(max_length=20, choices=Channel.choices)
    name = models.CharField(max_length=200)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    variables = models.JSONField(default=list, blank=True)
    whatsapp_content_sid = models.CharField(max_length=128, blank=True)
    whatsapp_category = models.CharField(
        max_length=32,
        choices=WhatsAppCategory.choices,
        default=WhatsAppCategory.UTILITY,
        blank=True,
    )
    approval_status = models.CharField(
        max_length=32,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.APPROVED,
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["channel", "name", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "channel", "name"],
                name="uniq_msgtemplate_tenant_ch_name",
            ),
            models.UniqueConstraint(
                fields=["tenant", "whatsapp_content_sid"],
                condition=Q(whatsapp_content_sid__isnull=False) & ~Q(whatsapp_content_sid=""),
                name="uniq_msgtemplate_tenant_wasid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "channel", "active"],
                name="idx_msgtemplate_tenant_ch_act",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.channel}: {self.name}"


class Broadcast(TenantOwnedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SCHEDULED = "scheduled", "Scheduled"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    name = models.CharField(max_length=200, blank=True)
    template = models.ForeignKey(
        "messaging.MessageTemplate",
        on_delete=models.PROTECT,
        related_name="broadcasts",
    )
    audience_filter = models.JSONField(default=dict, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    total_recipients = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    delivered_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_broadcasts",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["tenant", "status", "scheduled_at"],
                name="idx_broadcast_tenant_st_sched",
            ),
            models.Index(
                fields=["tenant", "-created_at"],
                name="idx_broadcast_tenant_created",
            ),
        ]

    def __str__(self) -> str:
        return self.name or f"Broadcast {self.id}"


class Message(TenantOwnedModel):
    class Direction(models.TextChoices):
        IN = "in", "Inbound"
        OUT = "out", "Outbound"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"
        UNDELIVERED = "undelivered", "Undelivered"
        FAILED = "failed", "Failed"
        BOUNCED = "bounced", "Bounced"

    class Source(models.TextChoices):
        WORKFLOW = "workflow", "Workflow"
        BROADCAST = "broadcast", "Broadcast"
        MANUAL = "manual", "Manual"

    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.CASCADE,
        related_name="messages",
    )
    channel = models.CharField(max_length=20, choices=Channel.choices)
    direction = models.CharField(max_length=10, choices=Direction.choices)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    body = models.TextField(blank=True)
    subject = models.CharField(max_length=255, blank=True)
    provider = models.CharField(max_length=50, blank=True)
    provider_message_id = models.CharField(max_length=255, null=True, blank=True)
    segments = models.PositiveIntegerField(default=1)
    destination_country = models.CharField(max_length=16, blank=True)
    whatsapp_category = models.CharField(
        max_length=32,
        choices=WhatsAppCategory.choices,
        blank=True,
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.MANUAL,
    )
    template = models.ForeignKey(
        "messaging.MessageTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )
    broadcast = models.ForeignKey(
        "messaging.Broadcast",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_messages",
    )
    error = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=255, null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "provider", "provider_message_id"],
                condition=Q(provider_message_id__isnull=False) & ~Q(provider_message_id=""),
                name="uniq_msg_tenant_prov_msgid",
            ),
            models.UniqueConstraint(
                fields=["tenant", "idempotency_key"],
                condition=Q(idempotency_key__isnull=False) & ~Q(idempotency_key=""),
                name="uniq_msg_tenant_idemp_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "contact", "-created_at"],
                name="idx_msg_tenant_contact_created",
            ),
            models.Index(
                fields=["tenant", "status", "-created_at"],
                name="idx_msg_tenant_status_created",
            ),
            models.Index(
                fields=["tenant", "contact", "channel", "direction", "-created_at"],
                name="idx_msg_tenant_ct_ch_dir_dt",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.channel}:{self.direction} ({self.status})"
