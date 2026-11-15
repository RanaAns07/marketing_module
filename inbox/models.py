from django.conf import settings
from django.db import models
from django.utils import timezone as dj_timezone
from core.models import TenantOwnedModel
from messaging.models import Channel


class Conversation(TenantOwnedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PENDING = "pending", "Pending"
        RESOLVED = "resolved", "Resolved"

    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
    )
    assigned_to = models.ForeignKey(
        "accounts.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_conversations",
    )
    last_message_at = models.DateTimeField(null=True, blank=True, default=dj_timezone.now)
    last_inbound_at = models.DateTimeField(null=True, blank=True)
    last_channel = models.CharField(
        max_length=20,
        choices=Channel.choices,
        blank=True,
        default="",
    )
    last_message_preview = models.CharField(max_length=500, blank=True, default="")
    unread_count = models.PositiveIntegerField(default=0)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_message_at", "-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "contact"],
                name="uniq_conv_tenant_contact",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "status", "-last_message_at"],
                name="idx_conv_t_st_lastmsg",
            ),
            models.Index(
                fields=["tenant", "assigned_to", "status", "-last_message_at"],
                name="idx_conv_t_asgn_st_lastmsg",
            ),
            models.Index(
                fields=["tenant", "unread_count"],
                name="idx_conv_t_unread",
            ),
        ]

    def __str__(self) -> str:
        return f"Conversation {self.contact_id} ({self.status})"


class ConversationNote(TenantOwnedModel):
    conversation = models.ForeignKey(
        "inbox.Conversation",
        on_delete=models.CASCADE,
        related_name="notes",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversation_notes",
    )
    body = models.TextField()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["tenant", "conversation", "-created_at", "-id"],
                name="idx_convnote_t_conv_dt",
            ),
        ]

    def __str__(self) -> str:
        return f"Note on {self.conversation_id} by {self.author_id}"


class AssignmentRule(TenantOwnedModel):
    name = models.CharField(max_length=150, blank=True, default="")
    priority = models.PositiveIntegerField(default=1)
    match_page = models.CharField(max_length=255, blank=True, default="")
    match_form = models.CharField(max_length=255, blank=True, default="")
    match_tag = models.ForeignKey(
        "contacts.Tag",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignment_rules",
    )
    match_tag_name = models.CharField(max_length=100, blank=True, default="")
    assignee = models.ForeignKey(
        "accounts.Membership",
        on_delete=models.CASCADE,
        related_name="assignment_rules",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["priority", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "priority"],
                name="uniq_assignrule_tenant_prio",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "is_active", "priority"],
                name="idx_assignrule_t_act_prio",
            ),
        ]

    def __str__(self) -> str:
        return f"Rule #{self.priority} -> {self.assignee_id}"
