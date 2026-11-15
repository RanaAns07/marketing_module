from django.db import models
from django.db.models import Q
from django.utils import timezone
from core.models import TenantOwnedModel


class Workflow(TenantOwnedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"

    class TriggerType(models.TextChoices):
        NEW_LEAD_CREATED = "new_lead_created", "New Lead Created"
        FORM_SUBMITTED = "form_submitted", "Form Submitted"
        TAG_ADDED = "tag_added", "Tag Added"
        TAG_REMOVED = "tag_removed", "Tag Removed"
        EMAIL_REPLIED = "email_replied", "Email Received or Replied To"
        SMS_RECEIVED = "sms_received", "SMS Received"
        WHATSAPP_RECEIVED = "whatsapp_received", "WhatsApp Message Received"
        APPOINTMENT_BOOKED = "appointment_booked", "Appointment Booked"
        APPOINTMENT_CANCELLED = "appointment_cancelled", "Appointment Cancelled"
        LEAD_STATUS_CHANGED = "lead_status_changed", "Lead Status Changed"
        LEAD_INACTIVE = "lead_inactive", "Lead Becomes Inactive"
        EXTERNAL_EVENT = "external_event", "External Event"

    class ReEntry(models.TextChoices):
        ONCE = "once", "Once"
        ALLOW = "allow", "Allow"

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    trigger_type = models.CharField(
        max_length=40,
        choices=TriggerType.choices,
        default=TriggerType.NEW_LEAD_CREATED,
    )
    trigger_config = models.JSONField(default=dict, blank=True)
    re_entry = models.CharField(
        max_length=20,
        choices=ReEntry.choices,
        default=ReEntry.ONCE,
    )
    stop_on_reply = models.BooleanField(default=True)
    is_promotional = models.BooleanField(default=False)
    exit_events = models.JSONField(default=list, blank=True)
    entry_step_key = models.CharField(max_length=100, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["tenant", "status", "trigger_type"],
                name="idx_workflow_t_st_trig",
            ),
            models.Index(
                fields=["tenant", "-created_at"],
                name="idx_workflow_t_created",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} (v{self.version})"


class WorkflowStep(TenantOwnedModel):
    class StepType(models.TextChoices):
        ACTION = "action", "Action"
        CONDITION = "condition", "Condition"
        DELAY = "delay", "Delay"

    class Subtype(models.TextChoices):
        HAS_TAG = "has_tag", "Has Tag"
        NOT_HAS_TAG = "not_has_tag", "Does Not Have Tag"
        STATUS_IS = "status_is", "Lead Status Is"
        SOURCE_MATCHES = "source_matches", "Lead Source Matches"
        REPLIED = "replied", "Has Replied"
        NOT_REPLIED = "not_replied", "Has Not Replied"
        CUSTOM_FIELD_COMPARE = "custom_field_compare", "Custom Field Compare"
        CAMPAIGN_MATCHES = "campaign_matches", "Campaign Matches"
        APPOINTMENT_NOT_CANCELLED = "appointment_not_cancelled", "Appointment Not Cancelled"
        SEND_EMAIL = "send_email", "Send Email"
        SEND_SMS = "send_sms", "Send SMS"
        SEND_WHATSAPP = "send_whatsapp", "Send WhatsApp"
        ADD_TAG = "add_tag", "Add Tag"
        REMOVE_TAG = "remove_tag", "Remove Tag"
        UPDATE_CONTACT_FIELD = "update_contact_field", "Update Contact Field"
        UPDATE_LEAD_STATUS = "update_lead_status", "Update Lead Status"
        ASSIGN_MARKETER = "assign_marketer", "Assign Marketer"
        CREATE_TASK = "create_task", "Create Task"
        SEND_INTERNAL_NOTIFICATION = "send_internal_notification", "Send Internal Notification"
        SEND_OUTBOUND_WEBHOOK = "send_outbound_webhook", "Send Outbound Webhook"
        PAUSE_AUTOMATION = "pause_automation", "Pause Automation"
        END_AUTOMATION = "end_automation", "End Automation"
        FIXED = "fixed", "Fixed Duration"
        UNTIL_DATETIME = "until_datetime", "Until Specific Datetime"
        RELATIVE_TO_FIELD = "relative_to_field", "Relative to Contact/Appointment Field"
        WAIT_FOR_EVENT = "wait_for_event", "Wait for Event"

    workflow = models.ForeignKey(
        "automation.Workflow",
        on_delete=models.CASCADE,
        related_name="steps",
    )
    key = models.CharField(max_length=100)
    type = models.CharField(max_length=20, choices=StepType.choices)
    subtype = models.CharField(max_length=40, choices=Subtype.choices)
    config = models.JSONField(default=dict, blank=True)
    next_key = models.CharField(max_length=100, blank=True)
    true_key = models.CharField(max_length=100, blank=True)
    false_key = models.CharField(max_length=100, blank=True)
    position = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at", "key"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "workflow", "key"],
                name="uniq_wfstep_tenant_wf_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "workflow"],
                name="idx_wfstep_tenant_wf",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.workflow_id}:{self.key} ({self.type}/{self.subtype})"


class WorkflowEnrollment(TenantOwnedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"
        STOPPED = "stopped", "Stopped"
        FAILED = "failed", "Failed"

    workflow = models.ForeignKey(
        "automation.Workflow",
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.CASCADE,
        related_name="workflow_enrollments",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    current_step_key = models.CharField(max_length=100, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    paused_reason = models.CharField(max_length=100, blank=True)
    waiting_for_event = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    iteration = models.PositiveIntegerField(default=1)
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "workflow", "contact"],
                condition=Q(status__in=["active", "paused"]),
                name="uniq_wfenroll_t_wf_ct_open",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "status", "next_run_at"],
                name="idx_wfenroll_t_st_nextrun",
            ),
            models.Index(
                fields=["tenant", "contact"],
                name="idx_wfenroll_t_contact",
            ),
            models.Index(
                fields=["tenant", "workflow", "-created_at"],
                name="idx_wfenroll_t_wf_created",
            ),
        ]

    def __str__(self) -> str:
        return f"Enrollment({self.workflow_id}, {self.contact_id}, {self.status})"


class StepExecution(TenantOwnedModel):
    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    enrollment = models.ForeignKey(
        "automation.WorkflowEnrollment",
        on_delete=models.CASCADE,
        related_name="step_executions",
    )
    step_key = models.CharField(max_length=100)
    iteration = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SUCCESS,
    )
    attempts = models.PositiveIntegerField(default=1)
    error = models.TextField(blank=True)
    result = models.JSONField(default=dict, blank=True)
    executed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "enrollment", "step_key", "iteration"],
                name="uniq_stepexec_t_enr_key_iter",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "enrollment", "-executed_at"],
                name="idx_stepexec_t_enr_execdt",
            ),
            models.Index(
                fields=["tenant", "-created_at", "-id"],
                name="idx_stepexec_t_created_id",
            ),
        ]

    def __str__(self) -> str:
        return f"StepExecution({self.enrollment_id}:{self.step_key}#{self.iteration} -> {self.status})"
