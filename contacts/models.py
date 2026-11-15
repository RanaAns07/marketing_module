from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Q
from django.utils import timezone as dj_timezone
from core.models import TenantOwnedModel

DEFAULT_LEAD_STATUSES = (
    ("New", 1, True),
    ("Contacted", 2, False),
    ("Engaged", 3, False),
    ("Qualified/Hot", 4, False),
    ("Appointment Booked", 5, False),
    ("Customer", 6, False),
    ("Unresponsive", 7, False),
)


class LeadStatus(TenantOwnedModel):
    name = models.CharField(max_length=100)
    order = models.PositiveIntegerField(default=0)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["order", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "name"],
                name="uniq_leadstatus_tenant_name",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "order"],
                name="idx_leadstatus_tenant_order",
            ),
            models.Index(
                fields=["tenant", "is_default"],
                name="idx_leadstatus_tenant_def",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class CustomFieldDefinition(TenantOwnedModel):
    class FieldType(models.TextChoices):
        TEXT = "text", "Text"
        NUMBER = "number", "Number"
        DATE = "date", "Date"
        SELECT = "select", "Select"
        BOOL = "bool", "Boolean"

    key = models.SlugField(max_length=100)
    label = models.CharField(max_length=150)
    field_type = models.CharField(
        max_length=20,
        choices=FieldType.choices,
        default=FieldType.TEXT,
    )
    options = models.JSONField(default=list, blank=True)
    is_required = models.BooleanField(default=False)

    class Meta:
        ordering = ["label", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "key"],
                name="uniq_customfield_tenant_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "key"],
                name="idx_customfield_tenant_key",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label} ({self.key})"


class Tag(TenantOwnedModel):
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=32, default="#3B82F6")

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "name"],
                name="uniq_tag_tenant_name",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "name"],
                name="idx_tag_tenant_name",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Contact(TenantOwnedModel):
    class SubscriptionStatus(models.TextChoices):
        SUBSCRIBED = "subscribed", "Subscribed"
        UNSUBSCRIBED = "unsubscribed", "Unsubscribed"
        BOUNCED = "bounced", "Bounced"

    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    full_name = models.CharField(max_length=300, blank=True)
    email = models.EmailField(null=True, blank=True)
    phone = models.CharField(max_length=32, null=True, blank=True)
    city = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=120, blank=True)
    timezone = models.CharField(max_length=64, default="UTC", blank=True)
    lead_status = models.ForeignKey(
        "contacts.LeadStatus",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="contacts",
    )
    assigned_to = models.ForeignKey(
        "accounts.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_contacts",
    )
    source_channel = models.CharField(max_length=100, blank=True)
    source_page = models.CharField(max_length=255, blank=True)
    source_form = models.CharField(max_length=255, blank=True)
    campaign = models.CharField(max_length=255, blank=True)
    ad_set = models.CharField(max_length=255, blank=True)
    ad = models.CharField(max_length=255, blank=True)
    acquired_at = models.DateTimeField(default=dj_timezone.now)
    converted_at = models.DateTimeField(null=True, blank=True)
    external_id = models.CharField(max_length=255, blank=True)
    last_activity_at = models.DateTimeField(default=dj_timezone.now)
    inactive_notified_at = models.DateTimeField(null=True, blank=True)
    email_subscription = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.SUBSCRIBED,
    )
    sms_subscription = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.SUBSCRIBED,
    )
    whatsapp_subscription = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.SUBSCRIBED,
    )
    custom_fields = models.JSONField(default=dict, blank=True)
    tags = models.ManyToManyField(
        "contacts.Tag",
        through="contacts.ContactTag",
        related_name="contacts",
        blank=True,
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "email"],
                condition=Q(email__isnull=False) & ~Q(email=""),
                name="uniq_contact_tenant_email_notnull",
            ),
            models.UniqueConstraint(
                fields=["tenant", "phone"],
                condition=Q(phone__isnull=False) & ~Q(phone=""),
                name="uniq_contact_tenant_phone_notnull",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "lead_status"],
                name="idx_contact_tenant_status",
            ),
            models.Index(
                fields=["tenant", "assigned_to"],
                name="idx_contact_tenant_assignee",
            ),
            models.Index(
                fields=["tenant", "-created_at"],
                name="idx_contact_tenant_created",
            ),
            models.Index(
                fields=["tenant", "-last_activity_at"],
                name="idx_contact_tenant_activity",
            ),
            models.Index(
                fields=["tenant", "source_channel"],
                name="idx_contact_tenant_source",
            ),
            models.Index(
                fields=["tenant", "campaign"],
                name="idx_contact_tenant_campaign",
            ),
            models.Index(
                fields=["tenant", "-acquired_at"],
                name="idx_contact_tenant_acquired",
            ),
            GinIndex(
                fields=["custom_fields"],
                name="idx_contact_custom_fields_gin",
            ),
        ]

    def __str__(self) -> str:
        return self.full_name or self.email or self.phone or str(self.id)


class ContactTag(TenantOwnedModel):
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.CASCADE,
        related_name="contact_tags",
    )
    tag = models.ForeignKey(
        "contacts.Tag",
        on_delete=models.CASCADE,
        related_name="contact_tags",
    )

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "contact", "tag"],
                name="uniq_contacttag_tenant_ct_tg",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "tag", "contact"],
                name="idx_contacttag_tenant_tg_ct",
            ),
            models.Index(
                fields=["tenant", "contact", "tag"],
                name="idx_contacttag_tenant_ct_tg",
            ),
        ]


class Activity(TenantOwnedModel):
    class ActivityType(models.TextChoices):
        FORM_SUBMISSION = "form_submission", "Form Submission"
        MESSAGE_IN = "message_in", "Incoming Message"
        MESSAGE_OUT = "message_out", "Outgoing Message"
        STATUS_CHANGE = "status_change", "Status Change"
        TAG_CHANGE = "tag_change", "Tag Change"
        WORKFLOW_ACTION = "workflow_action", "Workflow Action"
        NOTE = "note", "Internal Note"
        ASSIGNMENT = "assignment", "Assignment"
        EVENT = "event", "Event"
        APPOINTMENT = "appointment", "Appointment"

    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.CASCADE,
        related_name="activities",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_activities",
    )
    activity_type = models.CharField(
        max_length=32,
        choices=ActivityType.choices,
    )
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["tenant", "contact", "-created_at", "-id"],
                name="idx_activity_tenant_ct_dt",
            ),
            models.Index(
                fields=["tenant", "activity_type", "-created_at"],
                name="idx_activity_tenant_type_dt",
            ),
        ]


class Appointment(TenantOwnedModel):
    class Status(models.TextChoices):
        BOOKED = "booked", "Booked"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"
        RESCHEDULED = "rescheduled", "Rescheduled"

    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.CASCADE,
        related_name="appointments",
    )
    starts_at = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.BOOKED,
    )
    external_ref = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-starts_at", "-created_at"]
        indexes = [
            models.Index(
                fields=["tenant", "contact", "-starts_at"],
                name="idx_appt_tenant_ct_starts",
            ),
            models.Index(
                fields=["tenant", "status", "starts_at"],
                name="idx_appt_tenant_status_start",
            ),
        ]


class Task(TenantOwnedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tasks",
    )
    assignee = models.ForeignKey(
        "accounts.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tasks",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_tasks",
    )
    from_workflow = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    due_soon_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["due_at", "-created_at"]
        indexes = [
            models.Index(
                fields=["tenant", "assignee", "status", "due_at"],
                name="idx_task_tenant_asgn_st_due",
            ),
            models.Index(
                fields=["tenant", "contact", "-created_at"],
                name="idx_task_tenant_ct_dt",
            ),
        ]
