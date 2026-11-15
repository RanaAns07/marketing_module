import secrets
from django.db import models
from django.db.models import Q
from core.fields import EncryptedJSONField
from core.models import TenantOwnedModel


def generate_webhook_token() -> str:
    return secrets.token_urlsafe(32)


class ChannelConnection(TenantOwnedModel):
    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"
        WHATSAPP = "whatsapp", "WhatsApp"

    class Provider(models.TextChoices):
        SENDGRID = "sendgrid", "SendGrid"
        TWILIO = "twilio", "Twilio"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DISCONNECTED = "disconnected", "Disconnected"
        ERROR = "error", "Error"
        PENDING = "pending", "Pending"

    channel = models.CharField(max_length=20, choices=Channel.choices)
    provider = models.CharField(max_length=50, choices=Provider.choices)
    credentials = EncryptedJSONField(default=dict, blank=True)
    sender_identity = models.CharField(max_length=255)
    sender_name = models.CharField(max_length=255, blank=True)
    webhook_token = models.CharField(
        max_length=128,
        unique=True,
        default=generate_webhook_token,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["channel", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "channel"],
                name="uniq_channelconn_tenant_channel",
            ),
            models.UniqueConstraint(
                fields=["sender_identity"],
                condition=Q(channel__in=["sms", "whatsapp"]) & ~Q(sender_identity=""),
                name="uniq_channelconn_sender_number",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "channel"],
                name="idx_channelconn_tenant_chan",
            ),
            models.Index(
                fields=["tenant", "status"],
                name="idx_channelconn_tenant_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.channel}:{self.provider} ({self.sender_identity})"


class InboundWebhookEvent(TenantOwnedModel):
    class EventType(models.TextChoices):
        INBOUND = "inbound", "Inbound"
        STATUS = "status", "Status"
        EVENTS = "events", "Events"

    connection = models.ForeignKey(
        "integrations.ChannelConnection",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_events",
    )
    provider = models.CharField(max_length=50)
    channel = models.CharField(max_length=20)
    event_type = models.CharField(
        max_length=32,
        choices=EventType.choices,
        default=EventType.INBOUND,
    )
    raw_payload = models.JSONField(default=dict, blank=True)
    raw_body = models.TextField(blank=True)
    headers = models.JSONField(default=dict, blank=True)
    processed = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["tenant", "processed", "-created_at"],
                name="idx_inwebhook_tenant_proc_dt",
            ),
        ]
