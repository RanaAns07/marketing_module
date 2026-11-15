from django.db import models
from core.fields import EncryptedTextField
from core.models import TenantOwnedModel


class ExternalEvent(TenantOwnedModel):
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        PROCESSED = "processed", "Processed"
        FAILED = "failed", "Failed"
        IGNORED = "ignored", "Ignored"

    api_key = models.ForeignKey(
        "accounts.TenantAPIKey",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="external_events",
    )
    event_id = models.CharField(max_length=255)
    type = models.CharField(max_length=100)
    occurred_at = models.DateTimeField()
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
    )
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="external_events",
    )
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    attempts = models.PositiveIntegerField(default=0)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "event_id"],
                name="uniq_extevent_tenant_eventid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "status", "-created_at"],
                name="idx_extevent_t_status_dt",
            ),
            models.Index(
                fields=["tenant", "type", "-created_at"],
                name="idx_extevent_t_type_dt",
            ),
        ]

    def __str__(self) -> str:
        return f"ExternalEvent({self.type}, {self.event_id}, {self.status})"


class WebhookEndpoint(TenantOwnedModel):
    name = models.CharField(max_length=150)
    url = models.URLField(max_length=500)
    signing_secret = EncryptedTextField()
    subscribed_events = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    failure_count = models.PositiveIntegerField(default=0)
    disabled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["tenant", "active"],
                name="idx_whendpoint_t_active",
            ),
            models.Index(
                fields=["tenant", "-created_at"],
                name="idx_whendpoint_t_created",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.url})"


class WebhookDelivery(TenantOwnedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        DEAD = "dead", "Dead"

    endpoint = models.ForeignKey(
        "webhooks.WebhookEndpoint",
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    event_name = models.CharField(max_length=100)
    event_id = models.CharField(max_length=255)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    attempts = models.PositiveIntegerField(default=0)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_excerpt = models.TextField(blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["tenant", "status", "next_retry_at"],
                name="idx_whdeliv_t_st_retry",
            ),
            models.Index(
                fields=["tenant", "endpoint", "-created_at"],
                name="idx_whdeliv_t_ep_created",
            ),
            models.Index(
                fields=["tenant", "-created_at", "-id"],
                name="idx_whdeliv_t_created_id",
            ),
        ]

    def __str__(self) -> str:
        return f"WebhookDelivery({self.event_name}, {self.status}, attempts={self.attempts})"
