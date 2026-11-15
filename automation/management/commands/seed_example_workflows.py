from typing import Any
import uuid
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from automation.models import Workflow, WorkflowStep
from automation.services import create_workflow, update_workflow
from contacts.models import LeadStatus, Tag
from contacts.services import seed_default_lead_statuses
from core.context import tenant_context
from core.models import Tenant
from messaging.models import Channel, MessageTemplate, WhatsAppCategory


def _resolve_tenant(identifier: str) -> Tenant:
    raw = (identifier or "").strip()
    if not raw:
        raise CommandError("A tenant UUID, slug, or name is required.")
    try:
        t_uuid = uuid.UUID(raw)
        tenant = Tenant.objects.filter(pk=t_uuid).first()
        if tenant is not None:
            return tenant
    except ValueError:
        pass
    tenant = (
        Tenant.objects.filter(slug__iexact=raw).first()
        or Tenant.objects.filter(name__iexact=raw).first()
    )
    if tenant is None:
        raise CommandError(f"Tenant '{raw}' not found.")
    return tenant


def _get_or_create_tag(name: str, color: str = "#3B82F6") -> Tag:
    tag = Tag.objects.filter(name__iexact=name.strip()).first()
    if tag is None:
        tag = Tag.objects.create(name=name.strip(), color=color)
    return tag


def _get_or_create_template(
    *,
    channel: str,
    name: str,
    subject: str = "",
    body: str,
    whatsapp_category: str = WhatsAppCategory.UTILITY,
) -> MessageTemplate:
    tmpl = MessageTemplate.objects.filter(channel=channel, name__iexact=name.strip()).first()
    if tmpl is None:
        tmpl = MessageTemplate.objects.create(
            channel=channel,
            name=name.strip(),
            subject=subject,
            body=body,
            whatsapp_category=whatsapp_category if channel == Channel.WHATSAPP else "",
            approval_status=MessageTemplate.ApprovalStatus.APPROVED,
            active=True,
        )
    return tmpl


@transaction.atomic
def seed_example_workflows_for_tenant(tenant: Tenant) -> list[Workflow]:
    with tenant_context(tenant):
        seed_default_lead_statuses(tenant)

        fb_tag = _get_or_create_tag("Facebook Lead", "#1877F2")
        interested_tag = _get_or_create_tag("Interested", "#10B981")
        hot_tag = _get_or_create_tag("Hot Lead", "#EF4444")
        appt_tag = _get_or_create_tag("Appointment Booked", "#8B5CF6")

        contacted_status = LeadStatus.objects.filter(name__iexact="Contacted").first()
        qualified_status = LeadStatus.objects.filter(name__iexact="Qualified/Hot").first()

        welcome_email = _get_or_create_template(
            channel=Channel.EMAIL,
            name="Welcome Email",
            subject="Welcome {{ first_name }}! Let's get started",
            body="Hi {{ first_name }}, thank you for reaching out! Here is an introduction to our services.",
        )
        welcome_wa = _get_or_create_template(
            channel=Channel.WHATSAPP,
            name="Welcome WhatsApp",
            body="Hi {{ first_name }}! We received your inquiry. Do you have 5 minutes for a quick consultation call?",
            whatsapp_category=WhatsAppCategory.UTILITY,
        )
        followup_sms = _get_or_create_template(
            channel=Channel.SMS,
            name="24h Follow-Up SMS",
            body="Hi {{ first_name }}, just checking in to see if you had any questions about your request!",
        )
        appt_confirm_email = _get_or_create_template(
            channel=Channel.EMAIL,
            name="Appointment Confirmation Email",
            subject="Your Appointment Confirmation",
            body="Hi {{ first_name }}, your appointment is confirmed for {{ appointment_date }} at {{ appointment_location }}.",
        )
        appt_confirm_wa = _get_or_create_template(
            channel=Channel.WHATSAPP,
            name="Appointment Confirmation WhatsApp",
            body="Hi {{ first_name }}, you are booked for {{ appointment_date }} ({{ appointment_location }}).",
            whatsapp_category=WhatsAppCategory.UTILITY,
        )
        appt_reminder_sms = _get_or_create_template(
            channel=Channel.SMS,
            name="Appointment Reminder SMS",
            body="Reminder: Hi {{ first_name }}, your appointment is tomorrow at {{ appointment_date }}.",
        )
        appt_reminder_wa = _get_or_create_template(
            channel=Channel.WHATSAPP,
            name="Appointment Reminder WhatsApp",
            body="Hi {{ first_name }}, friendly reminder of your upcoming appointment at {{ appointment_date }}.",
            whatsapp_category=WhatsAppCategory.UTILITY,
        )
        appt_feedback_sms = _get_or_create_template(
            channel=Channel.SMS,
            name="Post-Appointment Feedback SMS",
            body="Thank you for visiting us today, {{ first_name }}! How was your experience?",
        )

        specs: list[dict[str, Any]] = [
            {
                "name": "New Facebook Lead Welcome & 24-Hour Reply Check",
                "description": "Welcomes new Facebook Lead Ad submissions across Email and WhatsApp, waits 24 hours, and branches based on whether the lead replied.",
                "trigger_type": Workflow.TriggerType.FORM_SUBMITTED,
                "trigger_config": {},
                "re_entry": Workflow.ReEntry.ONCE,
                "stop_on_reply": False,
                "is_promotional": True,
                "exit_events": ["conversion", "appointment_booked"],
                "entry_step_key": "add_fb_tag",
                "steps": [
                    {
                        "key": "add_fb_tag",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.ADD_TAG,
                        "config": {"tag_id": str(fb_tag.id), "tag_name": fb_tag.name},
                        "next_key": "send_welcome_email",
                        "position": {"order": 1},
                    },
                    {
                        "key": "send_welcome_email",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.SEND_EMAIL,
                        "config": {
                            "template_id": str(welcome_email.id),
                            "template_name": welcome_email.name,
                        },
                        "next_key": "send_welcome_whatsapp",
                        "position": {"order": 2},
                    },
                    {
                        "key": "send_welcome_whatsapp",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.SEND_WHATSAPP,
                        "config": {
                            "template_id": str(welcome_wa.id),
                            "template_name": welcome_wa.name,
                        },
                        "next_key": "wait_24h",
                        "position": {"order": 3},
                    },
                    {
                        "key": "wait_24h",
                        "type": WorkflowStep.StepType.DELAY,
                        "subtype": WorkflowStep.Subtype.FIXED,
                        "config": {"duration": 24, "unit": "hours"},
                        "next_key": "check_replied",
                        "position": {"order": 4},
                    },
                    {
                        "key": "check_replied",
                        "type": WorkflowStep.StepType.CONDITION,
                        "subtype": WorkflowStep.Subtype.REPLIED,
                        "config": {},
                        "true_key": "tag_interested",
                        "false_key": "send_followup_sms",
                        "position": {"order": 5},
                    },
                    {
                        "key": "tag_interested",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.ADD_TAG,
                        "config": {"tag_id": str(interested_tag.id), "tag_name": interested_tag.name},
                        "next_key": "assign_marketer_replied",
                        "position": {"order": 6},
                    },
                    {
                        "key": "assign_marketer_replied",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.ASSIGN_MARKETER,
                        "config": {"mode": "round_robin"},
                        "next_key": "notify_marketer_replied",
                        "position": {"order": 7},
                    },
                    {
                        "key": "notify_marketer_replied",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.SEND_INTERNAL_NOTIFICATION,
                        "config": {
                            "title": "Lead Replied: {{ full_name }}",
                            "body": "{{ full_name }} replied to the welcome sequence. Please respond personally in the Unified Inbox.",
                            "notify_assigned": True,
                        },
                        "next_key": "pause_further_reminders",
                        "position": {"order": 8},
                    },
                    {
                        "key": "pause_further_reminders",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.PAUSE_AUTOMATION,
                        "config": {"scope": "this", "reason": "lead_replied"},
                        "next_key": "",
                        "position": {"order": 9},
                    },
                    {
                        "key": "send_followup_sms",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.SEND_SMS,
                        "config": {
                            "template_id": str(followup_sms.id),
                            "template_name": followup_sms.name,
                        },
                        "next_key": "set_status_contacted",
                        "position": {"order": 10},
                    },
                    {
                        "key": "set_status_contacted",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.UPDATE_LEAD_STATUS,
                        "config": {
                            "status_id": str(contacted_status.id) if contacted_status else "",
                            "status_name": "Contacted",
                        },
                        "next_key": "",
                        "position": {"order": 11},
                    },
                ],
            },
            {
                "name": "Hot Lead Escalation to Sales Team",
                "description": "Triggered when the Hot Lead tag is applied; assigns a representative, updates status to Qualified/Hot, alerts the team, and creates a 15-minute follow-up task.",
                "trigger_type": Workflow.TriggerType.TAG_ADDED,
                "trigger_config": {"tag_id": str(hot_tag.id), "tag_name": hot_tag.name},
                "re_entry": Workflow.ReEntry.ALLOW,
                "stop_on_reply": False,
                "is_promotional": False,
                "exit_events": [],
                "entry_step_key": "assign_senior_rep",
                "steps": [
                    {
                        "key": "assign_senior_rep",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.ASSIGN_MARKETER,
                        "config": {"mode": "round_robin"},
                        "next_key": "set_status_qualified",
                        "position": {"order": 1},
                    },
                    {
                        "key": "set_status_qualified",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.UPDATE_LEAD_STATUS,
                        "config": {
                            "status_id": str(qualified_status.id) if qualified_status else "",
                            "status_name": "Qualified/Hot",
                        },
                        "next_key": "notify_sales_team",
                        "position": {"order": 2},
                    },
                    {
                        "key": "notify_sales_team",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.SEND_INTERNAL_NOTIFICATION,
                        "config": {
                            "title": "Urgent Hot Lead: {{ full_name }}",
                            "body": "{{ full_name }} was tagged Hot Lead and requires immediate sales follow-up.",
                            "notify_assigned": True,
                            "roles": ["owner", "admin"],
                        },
                        "next_key": "create_followup_task",
                        "position": {"order": 3},
                    },
                    {
                        "key": "create_followup_task",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.CREATE_TASK,
                        "config": {
                            "title": "Call Hot Lead within 15 minutes",
                            "description": "High-priority follow-up call for {{ full_name }} ({{ phone }}).",
                            "due_in_minutes": 15,
                        },
                        "next_key": "",
                        "position": {"order": 4},
                    },
                ],
            },
            {
                "name": "Appointment Confirmation, Reminder & Feedback Sequence",
                "description": "Sends immediate confirmation via Email and WhatsApp, waits until 24h before appointment to send SMS/WhatsApp reminders if not cancelled, and sends a feedback request 2h after the appointment.",
                "trigger_type": Workflow.TriggerType.APPOINTMENT_BOOKED,
                "trigger_config": {"tag_name": appt_tag.name},
                "re_entry": Workflow.ReEntry.ALLOW,
                "stop_on_reply": False,
                "is_promotional": False,
                "exit_events": ["appointment_cancelled"],
                "entry_step_key": "send_confirm_email",
                "steps": [
                    {
                        "key": "send_confirm_email",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.SEND_EMAIL,
                        "config": {
                            "template_id": str(appt_confirm_email.id),
                            "template_name": appt_confirm_email.name,
                        },
                        "next_key": "send_confirm_whatsapp",
                        "position": {"order": 1},
                    },
                    {
                        "key": "send_confirm_whatsapp",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.SEND_WHATSAPP,
                        "config": {
                            "template_id": str(appt_confirm_wa.id),
                            "template_name": appt_confirm_wa.name,
                        },
                        "next_key": "wait_24h_before_appointment",
                        "position": {"order": 2},
                    },
                    {
                        "key": "wait_24h_before_appointment",
                        "type": WorkflowStep.StepType.DELAY,
                        "subtype": WorkflowStep.Subtype.RELATIVE_TO_FIELD,
                        "config": {
                            "field": "appointment.starts_at",
                            "offset_value": 24,
                            "offset_unit": "hours",
                            "direction": "before",
                            "if_past": "skip",
                        },
                        "next_key": "check_appointment_not_cancelled",
                        "position": {"order": 3},
                    },
                    {
                        "key": "check_appointment_not_cancelled",
                        "type": WorkflowStep.StepType.CONDITION,
                        "subtype": WorkflowStep.Subtype.APPOINTMENT_NOT_CANCELLED,
                        "config": {},
                        "true_key": "send_reminder_sms",
                        "false_key": "",
                        "position": {"order": 4},
                    },
                    {
                        "key": "send_reminder_sms",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.SEND_SMS,
                        "config": {
                            "template_id": str(appt_reminder_sms.id),
                            "template_name": appt_reminder_sms.name,
                        },
                        "next_key": "send_reminder_whatsapp",
                        "position": {"order": 5},
                    },
                    {
                        "key": "send_reminder_whatsapp",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.SEND_WHATSAPP,
                        "config": {
                            "template_id": str(appt_reminder_wa.id),
                            "template_name": appt_reminder_wa.name,
                        },
                        "next_key": "wait_2h_after_appointment",
                        "position": {"order": 6},
                    },
                    {
                        "key": "wait_2h_after_appointment",
                        "type": WorkflowStep.StepType.DELAY,
                        "subtype": WorkflowStep.Subtype.RELATIVE_TO_FIELD,
                        "config": {
                            "field": "appointment.starts_at",
                            "offset_value": 2,
                            "offset_unit": "hours",
                            "direction": "after",
                            "if_past": "skip",
                        },
                        "next_key": "send_feedback_sms",
                        "position": {"order": 7},
                    },
                    {
                        "key": "send_feedback_sms",
                        "type": WorkflowStep.StepType.ACTION,
                        "subtype": WorkflowStep.Subtype.SEND_SMS,
                        "config": {
                            "template_id": str(appt_feedback_sms.id),
                            "template_name": appt_feedback_sms.name,
                        },
                        "next_key": "",
                        "position": {"order": 8},
                    },
                ],
            },
        ]

        created_workflows: list[Workflow] = []
        for spec in specs:
            existing = Workflow.objects.filter(name=spec["name"]).first()
            if existing is not None:
                wf = update_workflow(
                    workflow=existing,
                    description=spec["description"],
                    status=Workflow.Status.DRAFT,
                    trigger_type=spec["trigger_type"],
                    trigger_config=spec["trigger_config"],
                    re_entry=spec["re_entry"],
                    stop_on_reply=spec["stop_on_reply"],
                    is_promotional=spec["is_promotional"],
                    exit_events=spec["exit_events"],
                    entry_step_key=spec["entry_step_key"],
                    steps=spec["steps"],
                )
            else:
                wf = create_workflow(
                    name=spec["name"],
                    description=spec["description"],
                    status_value=Workflow.Status.DRAFT,
                    trigger_type=spec["trigger_type"],
                    trigger_config=spec["trigger_config"],
                    re_entry=spec["re_entry"],
                    stop_on_reply=spec["stop_on_reply"],
                    is_promotional=spec["is_promotional"],
                    exit_events=spec["exit_events"],
                    entry_step_key=spec["entry_step_key"],
                    steps_data=spec["steps"],
                )
            created_workflows.append(wf)

        return created_workflows


class Command(BaseCommand):
    help = "Seed the three example automation workflows from Guide Section 7.4 as drafts for a tenant."

    def add_arguments(self, parser):
        parser.add_argument(
            "tenant",
            type=str,
            help="Tenant UUID, slug, or name",
        )

    def handle(self, *args, **options):
        tenant = _resolve_tenant(options["tenant"])
        workflows = seed_example_workflows_for_tenant(tenant)
        for wf in workflows:
            self.stdout.write(
                self.style.SUCCESS(f"Seeded draft workflow: {wf.name} ({wf.id})")
            )
