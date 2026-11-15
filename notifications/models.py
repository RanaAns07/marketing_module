from django.conf import settings
from django.db import models
from core.models import TenantOwnedModel


class NotificationType(models.TextChoices):
    NEW_LEAD = "new_lead", "New Lead Notification"
    INCOMING_MESSAGE = "incoming_message", "Incoming Message Notification"
    CONVERSATION_ASSIGNED = "conversation_assigned", "Conversation Assignment Notification"
    CONVERSATION_REASSIGNED = "conversation_reassigned", "Conversation Reassignment Notification"
    INTERNAL_TASK = "internal_task", "Internal Task Notification"
    WORKFLOW_INTEGRATION_ALERT = "workflow_integration_alert", "Workflow or Integration Alert"


class Notification(TenantOwnedModel):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    notification_type = models.CharField(
        max_length=40,
        choices=NotificationType.choices,
    )
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    link = models.CharField(max_length=500, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    email_sent = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["tenant", "recipient", "is_read", "-created_at"],
                name="idx_notif_tenant_rec_read_dt",
            ),
            models.Index(
                fields=["tenant", "notification_type", "-created_at"],
                name="idx_notif_tenant_type_dt",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.notification_type} -> {self.recipient_id}"
