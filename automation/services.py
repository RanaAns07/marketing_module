from datetime import datetime, time, timedelta, timezone as dt_timezone
from typing import Any
import uuid
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status
from accounts.models import Membership, User
from accounts.services import resolve_tenant_lead_assignee, select_next_round_robin_member
from automation.models import StepExecution, Workflow, WorkflowEnrollment, WorkflowStep
from automation.selectors import has_contact_replied_since
from automation.validators import (
    APPOINTMENT_DATE_FIELDS,
    STANDARD_CONTACT_FIELDS,
    resolve_entry_step_key,
    validate_workflow_graph,
)
from contacts.models import Activity, Appointment, Contact, CustomFieldDefinition, LeadStatus, Tag
from contacts.services import (
    ContactService,
    create_task as create_contact_task,
    log_contact_activity,
)
from core.context import require_tenant, require_tenant_id
from core.events import EventName, publish
from core.exceptions import ApplicationError
from messaging.models import Channel, Message, MessageTemplate
from messaging.services import MessagingService, render_template_placeholders
from notifications.models import NotificationType
from notifications.services import notify

MAX_STEPS_PER_PASS = 25
MAX_STEP_ATTEMPTS = 3
STALE_LOCK_SECONDS = 300

EVENT_TO_TRIGGER_TYPE: dict[str, str] = {
    EventName.LEAD_CREATED: Workflow.TriggerType.NEW_LEAD_CREATED,
    EventName.FORM_SUBMITTED: Workflow.TriggerType.FORM_SUBMITTED,
    EventName.TAG_ADDED: Workflow.TriggerType.TAG_ADDED,
    EventName.TAG_REMOVED: Workflow.TriggerType.TAG_REMOVED,
    EventName.EMAIL_RECEIVED: Workflow.TriggerType.EMAIL_REPLIED,
    EventName.SMS_RECEIVED: Workflow.TriggerType.SMS_RECEIVED,
    EventName.WHATSAPP_RECEIVED: Workflow.TriggerType.WHATSAPP_RECEIVED,
    EventName.APPOINTMENT_BOOKED: Workflow.TriggerType.APPOINTMENT_BOOKED,
    EventName.APPOINTMENT_CANCELLED: Workflow.TriggerType.APPOINTMENT_CANCELLED,
    EventName.LEAD_STATUS_CHANGED: Workflow.TriggerType.LEAD_STATUS_CHANGED,
    EventName.LEAD_INACTIVE: Workflow.TriggerType.LEAD_INACTIVE,
    EventName.EXTERNAL_EVENT: Workflow.TriggerType.EXTERNAL_EVENT,
}

PURCHASE_EXTERNAL_TYPES = frozenset(
    {
        "purchase",
        "conversion",
        "order_completed",
        "payment_succeeded",
        "checkout_completed",
    }
)


def _parse_uuid(val: Any) -> uuid.UUID | None:
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _steps_queryset_to_dicts(workflow: Workflow) -> list[dict[str, Any]]:
    steps = list(workflow.steps.all().order_by("created_at", "key"))
    return [
        {
            "key": s.key,
            "type": s.type,
            "subtype": s.subtype,
            "config": s.config or {},
            "next_key": s.next_key or "",
            "true_key": s.true_key or "",
            "false_key": s.false_key or "",
            "position": s.position or {},
        }
        for s in steps
    ]


def validate_workflow_payload(
    *,
    trigger_type: str,
    trigger_config: dict[str, Any] | None,
    steps_data: list[dict[str, Any]],
    entry_step_key: str = "",
    strict_activation: bool = False,
) -> dict[str, Any]:
    errors = validate_workflow_graph(
        trigger_type=trigger_type,
        trigger_config=trigger_config or {},
        steps_data=steps_data,
        entry_step_key=entry_step_key,
        strict_activation=strict_activation,
    )
    return {
        "valid": len(errors) == 0,
        "errors": errors,
    }


def validate_workflow_instance(
    workflow: Workflow,
    *,
    strict_activation: bool = True,
) -> dict[str, Any]:
    steps_data = _steps_queryset_to_dicts(workflow)
    return validate_workflow_payload(
        trigger_type=workflow.trigger_type,
        trigger_config=workflow.trigger_config,
        steps_data=steps_data,
        entry_step_key=workflow.entry_step_key,
        strict_activation=strict_activation,
    )


def _sync_workflow_steps(
    *,
    workflow: Workflow,
    steps_data: list[dict[str, Any]],
) -> list[WorkflowStep]:
    tenant = workflow.tenant
    existing_by_key = {
        step.key: step
        for step in WorkflowStep.objects.select_for_update().filter(workflow=workflow)
    }
    incoming_keys: set[str] = set()
    synced: list[WorkflowStep] = []

    for idx, step_dict in enumerate(steps_data):
        key = str(step_dict["key"]).strip()
        incoming_keys.add(key)
        raw_pos = step_dict.get("position")
        if isinstance(raw_pos, int):
            pos_val: dict[str, Any] = {"order": raw_pos}
        elif isinstance(raw_pos, dict):
            pos_val = dict(raw_pos)
        else:
            pos_val = {"order": idx}

        if key in existing_by_key:
            step_obj = existing_by_key[key]
            step_obj.type = step_dict["type"]
            step_obj.subtype = step_dict["subtype"]
            step_obj.config = dict(step_dict.get("config") or {})
            step_obj.next_key = str(step_dict.get("next_key") or "").strip()
            step_obj.true_key = str(step_dict.get("true_key") or "").strip()
            step_obj.false_key = str(step_dict.get("false_key") or "").strip()
            step_obj.position = pos_val
            step_obj.save()
            synced.append(step_obj)
        else:
            step_obj = WorkflowStep.objects.create(
                tenant=tenant,
                workflow=workflow,
                key=key,
                type=step_dict["type"],
                subtype=step_dict["subtype"],
                config=dict(step_dict.get("config") or {}),
                next_key=str(step_dict.get("next_key") or "").strip(),
                true_key=str(step_dict.get("true_key") or "").strip(),
                false_key=str(step_dict.get("false_key") or "").strip(),
                position=pos_val,
            )
            synced.append(step_obj)

    keys_to_delete = set(existing_by_key.keys()) - incoming_keys
    if keys_to_delete:
        WorkflowStep.objects.filter(workflow=workflow, key__in=keys_to_delete).delete()

    return synced


@transaction.atomic
def create_workflow(
    *,
    name: str,
    description: str = "",
    status_value: str = Workflow.Status.DRAFT,
    trigger_type: str = Workflow.TriggerType.NEW_LEAD_CREATED,
    trigger_config: dict[str, Any] | None = None,
    re_entry: str = Workflow.ReEntry.ONCE,
    stop_on_reply: bool = True,
    is_promotional: bool = False,
    exit_events: list[str] | None = None,
    entry_step_key: str = "",
    steps_data: list[dict[str, Any]] | None = None,
) -> Workflow:
    tenant = require_tenant()
    normalized_steps = list(steps_data or [])
    resolved_entry = resolve_entry_step_key(normalized_steps, entry_step_key)

    validation = validate_workflow_payload(
        trigger_type=trigger_type,
        trigger_config=trigger_config,
        steps_data=normalized_steps,
        entry_step_key=resolved_entry,
        strict_activation=(status_value == Workflow.Status.ACTIVE),
    )
    if not validation["valid"]:
        first_msg = validation["errors"][0]["message"]
        raise ApplicationError(
            detail=first_msg,
            code="workflow_validation_failed",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    workflow = Workflow.objects.create(
        tenant=tenant,
        name=name.strip(),
        description=description.strip(),
        status=status_value,
        trigger_type=trigger_type,
        trigger_config=dict(trigger_config or {}),
        re_entry=re_entry,
        stop_on_reply=stop_on_reply,
        is_promotional=is_promotional,
        exit_events=list(exit_events or []),
        entry_step_key=resolved_entry,
        version=1,
    )
    if normalized_steps:
        _sync_workflow_steps(workflow=workflow, steps_data=normalized_steps)

    return Workflow.objects.prefetch_related("steps").get(pk=workflow.pk)


@transaction.atomic
def update_workflow(
    *,
    workflow: Workflow,
    **changes: Any,
) -> Workflow:
    workflow = Workflow.objects.select_for_update().get(pk=workflow.pk)
    steps_data = changes.pop("steps", None)
    new_trigger_type = changes.get("trigger_type", workflow.trigger_type)
    new_trigger_config = changes.get("trigger_config", workflow.trigger_config)
    new_status = changes.get("status", workflow.status)
    new_entry_key = changes.get("entry_step_key", workflow.entry_step_key)

    effective_steps = (
        list(steps_data)
        if steps_data is not None
        else _steps_queryset_to_dicts(workflow)
    )
    resolved_entry = resolve_entry_step_key(effective_steps, new_entry_key)

    validation = validate_workflow_payload(
        trigger_type=new_trigger_type,
        trigger_config=new_trigger_config,
        steps_data=effective_steps,
        entry_step_key=resolved_entry,
        strict_activation=(new_status == Workflow.Status.ACTIVE),
    )
    if not validation["valid"]:
        first_msg = validation["errors"][0]["message"]
        raise ApplicationError(
            detail=first_msg,
            code="workflow_validation_failed",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    bump_version = steps_data is not None or any(
        k in changes
        for k in (
            "trigger_type",
            "trigger_config",
            "re_entry",
            "stop_on_reply",
            "is_promotional",
            "exit_events",
            "entry_step_key",
        )
    )

    for field in (
        "name",
        "description",
        "status",
        "trigger_type",
        "trigger_config",
        "re_entry",
        "stop_on_reply",
        "is_promotional",
        "exit_events",
    ):
        if field in changes and changes[field] is not None:
            val = changes[field].strip() if field in ("name", "description") and isinstance(changes[field], str) else changes[field]
            setattr(workflow, field, val)

    workflow.entry_step_key = resolved_entry
    if bump_version:
        workflow.version += 1
    workflow.save()

    if steps_data is not None:
        _sync_workflow_steps(workflow=workflow, steps_data=effective_steps)

    return Workflow.objects.prefetch_related("steps").get(pk=workflow.pk)


@transaction.atomic
def activate_workflow(*, workflow: Workflow) -> Workflow:
    workflow = Workflow.objects.select_for_update().prefetch_related("steps").get(pk=workflow.pk)
    validation = validate_workflow_instance(workflow, strict_activation=True)
    if not validation["valid"]:
        first_msg = validation["errors"][0]["message"]
        raise ApplicationError(
            detail=first_msg,
            code="workflow_validation_failed",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not workflow.entry_step_key:
        workflow.entry_step_key = resolve_entry_step_key(_steps_queryset_to_dicts(workflow))
    workflow.status = Workflow.Status.ACTIVE
    workflow.save(update_fields=["status", "entry_step_key", "updated_at"])
    return workflow


@transaction.atomic
def pause_workflow(*, workflow: Workflow) -> Workflow:
    workflow = Workflow.objects.select_for_update().get(pk=workflow.pk)
    workflow.status = Workflow.Status.PAUSED
    workflow.save(update_fields=["status", "updated_at"])
    return workflow


@transaction.atomic
def resume_workflow(*, workflow: Workflow) -> Workflow:
    return activate_workflow(workflow=workflow)


@transaction.atomic
def duplicate_workflow(*, workflow: Workflow, name: str = "") -> Workflow:
    steps_data = _steps_queryset_to_dicts(workflow)
    copy_name = (name or "").strip() or f"{workflow.name} (Copy)"
    return create_workflow(
        name=copy_name,
        description=workflow.description,
        status_value=Workflow.Status.DRAFT,
        trigger_type=workflow.trigger_type,
        trigger_config=dict(workflow.trigger_config or {}),
        re_entry=workflow.re_entry,
        stop_on_reply=workflow.stop_on_reply,
        is_promotional=workflow.is_promotional,
        exit_events=list(workflow.exit_events or []),
        entry_step_key=workflow.entry_step_key,
        steps_data=steps_data,
    )


def _enqueue_enrollment_execution(tenant_id: str, enrollment_id: str) -> None:
    from automation.tasks import execute_enrollment_task

    try:
        execute_enrollment_task.apply_async(
            args=[tenant_id, enrollment_id],
            queue="automation",
        )
    except Exception:
        execute_enrollment_task(tenant_id, enrollment_id)


class EnrollmentService:
    @staticmethod
    @transaction.atomic
    def enroll(
        *,
        workflow: Workflow,
        contact: Contact,
        allow_inactive_workflow: bool = False,
        actor: User | None = None,
    ) -> tuple[WorkflowEnrollment | None, bool]:
        tenant_id = require_tenant_id()
        if not allow_inactive_workflow and workflow.status != Workflow.Status.ACTIVE:
            return None, False

        steps_data = _steps_queryset_to_dicts(workflow)
        if not steps_data:
            return None, False

        start_key = workflow.entry_step_key or resolve_entry_step_key(steps_data)
        if not start_key:
            return None, False

        existing_open = (
            WorkflowEnrollment.objects.filter(
                workflow=workflow,
                contact=contact,
                status__in=[WorkflowEnrollment.Status.ACTIVE, WorkflowEnrollment.Status.PAUSED],
            )
            .first()
        )
        if existing_open is not None:
            return existing_open, False

        if workflow.re_entry == Workflow.ReEntry.ONCE:
            if WorkflowEnrollment.objects.filter(workflow=workflow, contact=contact).exists():
                return None, False

        prior_count = WorkflowEnrollment.objects.filter(workflow=workflow, contact=contact).count()
        now = timezone.now()

        try:
            with transaction.atomic():
                enrollment = WorkflowEnrollment.objects.create(
                    workflow=workflow,
                    contact=contact,
                    status=WorkflowEnrollment.Status.ACTIVE,
                    current_step_key=start_key,
                    next_run_at=now,
                    started_at=now,
                    iteration=prior_count + 1,
                )
        except IntegrityError:
            existing = (
                WorkflowEnrollment.objects.filter(
                    workflow=workflow,
                    contact=contact,
                    status__in=[WorkflowEnrollment.Status.ACTIVE, WorkflowEnrollment.Status.PAUSED],
                )
                .first()
            )
            return existing, False

        log_contact_activity(
            contact=contact,
            actor=actor,
            activity_type=Activity.ActivityType.WORKFLOW_ACTION,
            description=f"Enrolled in workflow '{workflow.name}'.",
            metadata={
                "workflow_id": str(workflow.id),
                "workflow_name": workflow.name,
                "enrollment_id": str(enrollment.id),
                "iteration": enrollment.iteration,
            },
            touch_activity=False,
        )

        tid_str = str(tenant_id)
        eid_str = str(enrollment.id)
        transaction.on_commit(lambda: _enqueue_enrollment_execution(tid_str, eid_str))
        return enrollment, True

    @staticmethod
    @transaction.atomic
    def unenroll(
        *,
        enrollment: WorkflowEnrollment,
        reason: str = "manual_unenroll",
        actor: User | None = None,
    ) -> WorkflowEnrollment:
        locked = (
            WorkflowEnrollment.objects.select_for_update()
            .select_related("workflow", "contact")
            .get(pk=enrollment.pk)
        )
        if locked.status in (
            WorkflowEnrollment.Status.COMPLETED,
            WorkflowEnrollment.Status.STOPPED,
            WorkflowEnrollment.Status.FAILED,
        ):
            return locked

        now = timezone.now()
        locked.status = WorkflowEnrollment.Status.STOPPED
        locked.paused_reason = reason
        locked.waiting_for_event = {}
        locked.next_run_at = None
        locked.locked_at = None
        locked.finished_at = now
        locked.save(
            update_fields=[
                "status",
                "paused_reason",
                "waiting_for_event",
                "next_run_at",
                "locked_at",
                "finished_at",
                "updated_at",
            ]
        )

        log_contact_activity(
            contact=locked.contact,
            actor=actor,
            activity_type=Activity.ActivityType.WORKFLOW_ACTION,
            description=f"Workflow '{locked.workflow.name}' stopped ({reason}).",
            metadata={
                "workflow_id": str(locked.workflow_id),
                "workflow_name": locked.workflow.name,
                "enrollment_id": str(locked.id),
                "reason": reason,
            },
            touch_activity=False,
        )
        return locked

    @staticmethod
    @transaction.atomic
    def pause_enrollment(
        *,
        enrollment: WorkflowEnrollment,
        reason: str = "manual",
        actor: User | None = None,
    ) -> WorkflowEnrollment:
        locked = (
            WorkflowEnrollment.objects.select_for_update()
            .select_related("workflow", "contact")
            .get(pk=enrollment.pk)
        )
        if locked.status != WorkflowEnrollment.Status.ACTIVE:
            return locked

        locked.status = WorkflowEnrollment.Status.PAUSED
        locked.paused_reason = reason
        locked.locked_at = None
        locked.save(update_fields=["status", "paused_reason", "locked_at", "updated_at"])

        log_contact_activity(
            contact=locked.contact,
            actor=actor,
            activity_type=Activity.ActivityType.WORKFLOW_ACTION,
            description=f"Workflow '{locked.workflow.name}' paused ({reason}).",
            metadata={
                "workflow_id": str(locked.workflow_id),
                "workflow_name": locked.workflow.name,
                "enrollment_id": str(locked.id),
                "reason": reason,
            },
            touch_activity=False,
        )
        return locked

    @staticmethod
    @transaction.atomic
    def resume_enrollment(
        *,
        enrollment: WorkflowEnrollment,
        actor: User | None = None,
    ) -> WorkflowEnrollment:
        tenant_id = require_tenant_id()
        locked = (
            WorkflowEnrollment.objects.select_for_update()
            .select_related("workflow", "contact")
            .get(pk=enrollment.pk)
        )
        if locked.status != WorkflowEnrollment.Status.PAUSED:
            return locked

        now = timezone.now()
        locked.status = WorkflowEnrollment.Status.ACTIVE
        locked.paused_reason = ""
        locked.locked_at = None
        if locked.next_run_at is None or locked.next_run_at < now:
            locked.next_run_at = now
        locked.save(
            update_fields=[
                "status",
                "paused_reason",
                "locked_at",
                "next_run_at",
                "updated_at",
            ]
        )

        log_contact_activity(
            contact=locked.contact,
            actor=actor,
            activity_type=Activity.ActivityType.WORKFLOW_ACTION,
            description=f"Workflow '{locked.workflow.name}' resumed.",
            metadata={
                "workflow_id": str(locked.workflow_id),
                "workflow_name": locked.workflow.name,
                "enrollment_id": str(locked.id),
            },
            touch_activity=False,
        )

        tid_str = str(tenant_id)
        eid_str = str(locked.id)
        transaction.on_commit(lambda: _enqueue_enrollment_execution(tid_str, eid_str))
        return locked

    @staticmethod
    @transaction.atomic
    def pause_all_for_contact(
        *,
        contact: Contact,
        reason: str = "manual_pause_all",
        only_stop_on_reply: bool = False,
        exclude_enrollment_id: uuid.UUID | None = None,
        actor: User | None = None,
    ) -> list[WorkflowEnrollment]:
        qs = (
            WorkflowEnrollment.objects.select_for_update()
            .select_related("workflow", "contact")
            .filter(
                contact=contact,
                status=WorkflowEnrollment.Status.ACTIVE,
            )
        )
        if only_stop_on_reply:
            qs = qs.filter(workflow__stop_on_reply=True)
        if exclude_enrollment_id is not None:
            qs = qs.exclude(pk=exclude_enrollment_id)

        enrollments = list(qs)
        paused: list[WorkflowEnrollment] = []
        for enr in enrollments:
            enr.status = WorkflowEnrollment.Status.PAUSED
            enr.paused_reason = reason
            enr.locked_at = None
            enr.save(update_fields=["status", "paused_reason", "locked_at", "updated_at"])
            log_contact_activity(
                contact=contact,
                actor=actor,
                activity_type=Activity.ActivityType.WORKFLOW_ACTION,
                description=f"Workflow '{enr.workflow.name}' paused ({reason}).",
                metadata={
                    "workflow_id": str(enr.workflow_id),
                    "workflow_name": enr.workflow.name,
                    "enrollment_id": str(enr.id),
                    "reason": reason,
                },
                touch_activity=False,
            )
            paused.append(enr)
        return paused

    @staticmethod
    @transaction.atomic
    def resume_all_for_contact(
        *,
        contact: Contact,
        actor: User | None = None,
    ) -> list[WorkflowEnrollment]:
        tenant_id = require_tenant_id()
        enrollments = list(
            WorkflowEnrollment.objects.select_for_update()
            .select_related("workflow", "contact")
            .filter(
                contact=contact,
                status=WorkflowEnrollment.Status.PAUSED,
            )
        )
        now = timezone.now()
        resumed: list[WorkflowEnrollment] = []
        for enr in enrollments:
            enr.status = WorkflowEnrollment.Status.ACTIVE
            enr.paused_reason = ""
            enr.locked_at = None
            if enr.next_run_at is None or enr.next_run_at < now:
                enr.next_run_at = now
            enr.save(
                update_fields=[
                    "status",
                    "paused_reason",
                    "locked_at",
                    "next_run_at",
                    "updated_at",
                ]
            )
            log_contact_activity(
                contact=contact,
                actor=actor,
                activity_type=Activity.ActivityType.WORKFLOW_ACTION,
                description=f"Workflow '{enr.workflow.name}' resumed.",
                metadata={
                    "workflow_id": str(enr.workflow_id),
                    "workflow_name": enr.workflow.name,
                    "enrollment_id": str(enr.id),
                },
                touch_activity=False,
            )
            resumed.append(enr)
            tid_str = str(tenant_id)
            eid_str = str(enr.id)
            transaction.on_commit(lambda t=tid_str, e=eid_str: _enqueue_enrollment_execution(t, e))
        return resumed


def _matches_tag_config(config: dict[str, Any], payload: dict[str, Any]) -> bool:
    tag_id_cfg = str(config.get("tag_id") or "").strip()
    tag_name_cfg = str(config.get("tag_name") or config.get("tag") or "").strip()
    tags_cfg = config.get("tags") or []

    event_tag_id = str(payload.get("tag_id") or "").strip()
    event_tag_name = str(payload.get("tag_name") or "").strip()

    if not tag_id_cfg and not tag_name_cfg and not tags_cfg:
        return True

    if tag_id_cfg and event_tag_id and tag_id_cfg == event_tag_id:
        return True
    if tag_name_cfg and event_tag_name and tag_name_cfg.lower() == event_tag_name.lower():
        return True
    if tag_name_cfg and event_tag_id and _parse_uuid(tag_name_cfg) is not None and tag_name_cfg == event_tag_id:
        return True

    for item in tags_cfg:
        item_str = str(item).strip()
        if item_str and (
            item_str == event_tag_id or item_str.lower() == event_tag_name.lower()
        ):
            return True

    return False


def _matches_status_ref(expected_ref: Any, status_id: str, status_name: str) -> bool:
    if not expected_ref:
        return True
    exp_str = str(expected_ref).strip()
    if not exp_str:
        return True
    if status_id and exp_str == status_id:
        return True
    if status_name and exp_str.lower() == status_name.lower():
        return True
    return False


def matches_trigger_config(
    *,
    workflow: Workflow,
    event_name: str,
    payload: dict[str, Any],
    contact: Contact,
) -> bool:
    cfg = workflow.trigger_config or {}
    if not isinstance(cfg, dict):
        return True

    trig = workflow.trigger_type
    if trig == Workflow.TriggerType.NEW_LEAD_CREATED:
        req_channel = str(cfg.get("source_channel") or cfg.get("source") or "").strip()
        if req_channel:
            actual_channel = str(payload.get("source_channel") or contact.source_channel or "").strip()
            if req_channel.lower() != actual_channel.lower():
                return False
        return True

    if trig == Workflow.TriggerType.FORM_SUBMITTED:
        req_form = str(cfg.get("form_id") or cfg.get("form_name") or cfg.get("source_form") or "").strip()
        req_page = str(cfg.get("page_id") or cfg.get("page_name") or cfg.get("source_page") or "").strip()
        req_channel = str(cfg.get("source_channel") or cfg.get("platform") or "").strip()
        if req_form:
            actual_form = str(payload.get("source_form") or payload.get("form_id") or contact.source_form or "").strip()
            if req_form.lower() != actual_form.lower():
                return False
        if req_page:
            actual_page = str(payload.get("source_page") or payload.get("page_id") or contact.source_page or "").strip()
            if req_page.lower() != actual_page.lower():
                return False
        if req_channel:
            actual_ch = str(payload.get("source_channel") or contact.source_channel or "").strip()
            if req_channel.lower() not in actual_ch.lower():
                return False
        return True

    if trig in (Workflow.TriggerType.TAG_ADDED, Workflow.TriggerType.TAG_REMOVED):
        return _matches_tag_config(cfg, payload)

    if trig == Workflow.TriggerType.LEAD_STATUS_CHANGED:
        from_ref = (
            cfg.get("from_status_id")
            or cfg.get("from_status_name")
            or cfg.get("from_status")
            or cfg.get("from")
        )
        to_ref = (
            cfg.get("to_status_id")
            or cfg.get("to_status_name")
            or cfg.get("to_status")
            or cfg.get("to")
        )
        old_id = str(payload.get("old_status_id") or "")
        old_name = str(payload.get("old_status_name") or "")
        new_id = str(payload.get("new_status_id") or "")
        new_name = str(payload.get("new_status_name") or "")
        if not _matches_status_ref(from_ref, old_id, old_name):
            return False
        if not _matches_status_ref(to_ref, new_id, new_name):
            return False
        return True

    if trig == Workflow.TriggerType.EXTERNAL_EVENT:
        req_type = str(cfg.get("event_type") or cfg.get("type") or "").strip()
        if req_type:
            actual_type = str(payload.get("event_type") or payload.get("type") or "").strip()
            if req_type.lower() != actual_type.lower():
                return False
        return True

    return True


def _does_wait_for_event_match(
    waiting_meta: dict[str, Any],
    event_name: str,
    payload: dict[str, Any],
) -> bool:
    if not waiting_meta or not isinstance(waiting_meta, dict):
        return False
    wait_type = str(waiting_meta.get("event_type") or "reply").strip().lower()
    cfg = waiting_meta.get("config") or {}

    if wait_type == "reply":
        if event_name not in (
            EventName.EMAIL_RECEIVED,
            EventName.SMS_RECEIVED,
            EventName.WHATSAPP_RECEIVED,
        ):
            return False
        req_ch = str(cfg.get("channel") or "").strip().lower()
        if req_ch and str(payload.get("channel") or "").strip().lower() != req_ch:
            return False
        return True

    if wait_type in ("email_replied", "email_received"):
        return event_name == EventName.EMAIL_RECEIVED
    if wait_type == "sms_received":
        return event_name == EventName.SMS_RECEIVED
    if wait_type == "whatsapp_received":
        return event_name == EventName.WHATSAPP_RECEIVED
    if wait_type == "tag_added":
        return event_name == EventName.TAG_ADDED and _matches_tag_config(cfg, payload)
    if wait_type == "tag_removed":
        return event_name == EventName.TAG_REMOVED and _matches_tag_config(cfg, payload)
    if wait_type == "lead_status_changed":
        if event_name != EventName.LEAD_STATUS_CHANGED:
            return False
        to_ref = cfg.get("to_status_id") or cfg.get("to_status_name") or cfg.get("to_status") or cfg.get("to")
        return _matches_status_ref(
            to_ref,
            str(payload.get("new_status_id") or ""),
            str(payload.get("new_status_name") or ""),
        )
    if wait_type == "appointment_booked":
        return event_name == EventName.APPOINTMENT_BOOKED
    if wait_type == "appointment_cancelled":
        return event_name == EventName.APPOINTMENT_CANCELLED
    if wait_type == "external_event":
        if event_name != EventName.EXTERNAL_EVENT:
            return False
        req_ext = str(cfg.get("external_event_type") or cfg.get("type") or "").strip().lower()
        actual_ext = str(payload.get("event_type") or payload.get("type") or "").strip().lower()
        return not req_ext or req_ext == actual_ext

    return False


@transaction.atomic
def _resume_matching_wait_for_event_enrollments(
    *,
    contact: Contact,
    event_name: str,
    payload: dict[str, Any],
) -> set[uuid.UUID]:
    tenant_id = require_tenant_id()
    resumed_ids: set[uuid.UUID] = set()
    candidates = list(
        WorkflowEnrollment.objects.select_for_update()
        .select_related("workflow")
        .filter(
            contact=contact,
            status=WorkflowEnrollment.Status.ACTIVE,
        )
        .exclude(waiting_for_event={})
    )
    now = timezone.now()
    for enr in candidates:
        waiting_meta = enr.waiting_for_event or {}
        if not _does_wait_for_event_match(waiting_meta, event_name, payload):
            continue

        step_key = str(waiting_meta.get("step_key") or enr.current_step_key)
        step = WorkflowStep.objects.filter(workflow=enr.workflow, key=step_key).first()

        StepExecution.objects.update_or_create(
            tenant_id=tenant_id,
            enrollment=enr,
            step_key=step_key,
            iteration=enr.iteration,
            defaults={
                "status": StepExecution.Status.SUCCESS,
                "attempts": 1,
                "error": "",
                "result": {"event_occurred": True, "event_name": event_name},
                "executed_at": now,
            },
        )

        next_target = ""
        if step is not None:
            next_target = (step.true_key or step.next_key or "").strip()

        enr.waiting_for_event = {}
        enr.locked_at = None
        if next_target:
            enr.current_step_key = next_target
            enr.next_run_at = now
            enr.save(
                update_fields=[
                    "waiting_for_event",
                    "locked_at",
                    "current_step_key",
                    "next_run_at",
                    "updated_at",
                ]
            )
            tid_str = str(tenant_id)
            eid_str = str(enr.id)
            transaction.on_commit(lambda t=tid_str, e=eid_str: _enqueue_enrollment_execution(t, e))
        else:
            enr.current_step_key = ""
            enr.next_run_at = None
            enr.status = WorkflowEnrollment.Status.COMPLETED
            enr.finished_at = now
            enr.save(
                update_fields=[
                    "waiting_for_event",
                    "locked_at",
                    "current_step_key",
                    "next_run_at",
                    "status",
                    "finished_at",
                    "updated_at",
                ]
            )
        resumed_ids.add(enr.id)

    return resumed_ids


@transaction.atomic
def _handle_exit_events_for_contact(
    *,
    contact: Contact,
    event_name: str,
    payload: dict[str, Any],
) -> list[WorkflowEnrollment]:
    active_enrollments = list(
        WorkflowEnrollment.objects.select_for_update()
        .select_related("workflow", "contact")
        .filter(
            contact=contact,
            status__in=[WorkflowEnrollment.Status.ACTIVE, WorkflowEnrollment.Status.PAUSED],
        )
    )
    if not active_enrollments:
        return []

    ext_type = str(payload.get("event_type") or payload.get("type") or "").strip().lower()
    is_purchase_or_conversion = (
        event_name == EventName.CONVERSION
        or (event_name == EventName.EXTERNAL_EVENT and ext_type in PURCHASE_EXTERNAL_TYPES)
    )

    stopped: list[WorkflowEnrollment] = []
    now = timezone.now()

    for enr in active_enrollments:
        wf = enr.workflow
        raw_exit_events = wf.exit_events or []
        exit_set = {str(x).strip().lower() for x in raw_exit_events if str(x).strip()}

        should_stop = False
        reason_label = event_name

        if event_name.lower() in exit_set:
            should_stop = True
        elif event_name == EventName.EXTERNAL_EVENT and ext_type and (
            ext_type in exit_set or f"external_event:{ext_type}" in exit_set
        ):
            should_stop = True
            reason_label = f"external_event:{ext_type}"
        elif is_purchase_or_conversion and wf.is_promotional:
            should_stop = True
            reason_label = f"promotional_exit:{ext_type or event_name}"

        if should_stop:
            enr.status = WorkflowEnrollment.Status.STOPPED
            enr.paused_reason = f"exit_event:{reason_label}"
            enr.waiting_for_event = {}
            enr.next_run_at = None
            enr.locked_at = None
            enr.finished_at = now
            enr.save(
                update_fields=[
                    "status",
                    "paused_reason",
                    "waiting_for_event",
                    "next_run_at",
                    "locked_at",
                    "finished_at",
                    "updated_at",
                ]
            )
            log_contact_activity(
                contact=contact,
                activity_type=Activity.ActivityType.WORKFLOW_ACTION,
                description=f"Workflow '{wf.name}' stopped on exit event ({reason_label}).",
                metadata={
                    "workflow_id": str(wf.id),
                    "workflow_name": wf.name,
                    "enrollment_id": str(enr.id),
                    "exit_event": reason_label,
                },
                touch_activity=False,
            )
            stopped.append(enr)

    return stopped


def process_domain_event_for_automation(event: dict[str, Any]) -> dict[str, Any]:
    event_name = str(event.get("name") or "")
    contact_id = event.get("contact_id")
    payload = event.get("payload") or {}

    if not contact_id or not event_name:
        return {"enrolled": 0, "paused": 0, "resumed": 0, "stopped": 0}

    contact = (
        Contact.objects.select_related("lead_status", "assigned_to", "tenant")
        .prefetch_related("tags")
        .filter(pk=contact_id)
        .first()
    )
    if contact is None:
        return {"enrolled": 0, "paused": 0, "resumed": 0, "stopped": 0}

    resumed_ids = _resume_matching_wait_for_event_enrollments(
        contact=contact,
        event_name=event_name,
        payload=payload,
    )

    paused_count = 0
    if event_name in (
        EventName.EMAIL_RECEIVED,
        EventName.SMS_RECEIVED,
        EventName.WHATSAPP_RECEIVED,
    ):
        qs = (
            WorkflowEnrollment.objects.select_for_update()
            .select_related("workflow", "contact")
            .filter(
                contact=contact,
                status=WorkflowEnrollment.Status.ACTIVE,
                workflow__stop_on_reply=True,
            )
        )
        if resumed_ids:
            qs = qs.exclude(pk__in=resumed_ids)
        with transaction.atomic():
            for enr in list(qs):
                enr.status = WorkflowEnrollment.Status.PAUSED
                enr.paused_reason = "human_handoff"
                enr.locked_at = None
                enr.save(update_fields=["status", "paused_reason", "locked_at", "updated_at"])
                log_contact_activity(
                    contact=contact,
                    activity_type=Activity.ActivityType.WORKFLOW_ACTION,
                    description=f"Workflow '{enr.workflow.name}' paused for human handoff on customer reply.",
                    metadata={
                        "workflow_id": str(enr.workflow_id),
                        "workflow_name": enr.workflow.name,
                        "enrollment_id": str(enr.id),
                        "paused_reason": "human_handoff",
                        "event": event_name,
                    },
                    touch_activity=False,
                )
                paused_count += 1

    stopped_enrollments = _handle_exit_events_for_contact(
        contact=contact,
        event_name=event_name,
        payload=payload,
    )

    trigger_type = EVENT_TO_TRIGGER_TYPE.get(event_name)
    enrolled_count = 0
    if trigger_type:
        matching_workflows = list(
            Workflow.objects.prefetch_related("steps").filter(
                status=Workflow.Status.ACTIVE,
                trigger_type=trigger_type,
            )
        )
        for wf in matching_workflows:
            if matches_trigger_config(
                workflow=wf,
                event_name=event_name,
                payload=payload,
                contact=contact,
            ):
                _, created = EnrollmentService.enroll(workflow=wf, contact=contact)
                if created:
                    enrolled_count += 1

    return {
        "enrolled": enrolled_count,
        "paused": paused_count,
        "resumed": len(resumed_ids),
        "stopped": len(stopped_enrollments),
    }


def evaluate_condition_step(
    *,
    step: WorkflowStep,
    contact: Contact,
    enrollment: WorkflowEnrollment,
) -> bool:
    cfg = step.config or {}
    subtype = step.subtype

    if subtype in (WorkflowStep.Subtype.HAS_TAG, WorkflowStep.Subtype.NOT_HAS_TAG):
        tag_ref = cfg.get("tag_id") or cfg.get("tag_name") or cfg.get("tag")
        tags_list = cfg.get("tags") or ([] if not tag_ref else [tag_ref])
        contact_tags = list(contact.tags.all())
        contact_tag_ids = {str(t.id) for t in contact_tags}
        contact_tag_names = {t.name.lower() for t in contact_tags}

        has_any = False
        for item in tags_list:
            item_str = str(item).strip()
            if item_str in contact_tag_ids or item_str.lower() in contact_tag_names:
                has_any = True
                break

        return has_any if subtype == WorkflowStep.Subtype.HAS_TAG else (not has_any)

    if subtype == WorkflowStep.Subtype.APPOINTMENT_NOT_CANCELLED:
        return Appointment.objects.filter(
            contact=contact,
            status__in=[Appointment.Status.BOOKED, Appointment.Status.RESCHEDULED],
        ).exists()

    if subtype == WorkflowStep.Subtype.STATUS_IS:
        target = str(cfg.get("target") or "contact").lower()
        if target == "appointment" or cfg.get("not_cancelled"):
            if cfg.get("not_cancelled"):
                return Appointment.objects.filter(
                    contact=contact,
                    status__in=[Appointment.Status.BOOKED, Appointment.Status.RESCHEDULED],
                ).exists()
            appt_status = str(cfg.get("appointment_status") or cfg.get("status") or Appointment.Status.BOOKED).lower()
            return Appointment.objects.filter(contact=contact, status=appt_status).exists()

        status_ref = cfg.get("status_id") or cfg.get("status_name") or cfg.get("status")
        if not contact.lead_status_id or not status_ref:
            return False
        ref_str = str(status_ref).strip()
        if str(contact.lead_status_id) == ref_str:
            return True
        return bool(
            contact.lead_status
            and contact.lead_status.name.strip().lower() == ref_str.lower()
        )

    if subtype == WorkflowStep.Subtype.SOURCE_MATCHES:
        sources = cfg.get("sources") or []
        single = cfg.get("source_channel") or cfg.get("source")
        if single:
            sources = [*sources, single]
        if sources:
            actual = (contact.source_channel or "").strip().lower()
            if not any(str(s).strip().lower() == actual for s in sources):
                return False
        req_page = str(cfg.get("source_page") or "").strip().lower()
        if req_page and (contact.source_page or "").strip().lower() != req_page:
            return False
        req_form = str(cfg.get("source_form") or "").strip().lower()
        if req_form and (contact.source_form or "").strip().lower() != req_form:
            return False
        return True

    if subtype in (WorkflowStep.Subtype.REPLIED, WorkflowStep.Subtype.NOT_REPLIED):
        ch = cfg.get("channel")
        replied = has_contact_replied_since(
            contact=contact,
            since_dt=enrollment.started_at,
            channel=str(ch) if ch else None,
        )
        return replied if subtype == WorkflowStep.Subtype.REPLIED else (not replied)

    if subtype == WorkflowStep.Subtype.CUSTOM_FIELD_COMPARE:
        field_key = str(cfg.get("field") or cfg.get("key") or "").strip()
        if field_key.startswith("custom_fields."):
            field_key = field_key[len("custom_fields."):]
        operator = str(cfg.get("operator") or "eq").strip().lower()
        expected = cfg.get("value")

        if field_key in STANDARD_CONTACT_FIELDS:
            actual = getattr(contact, field_key, None)
        else:
            actual = (contact.custom_fields or {}).get(field_key)

        if operator == "is_empty":
            is_emp = actual in (None, "", [], {})
            if isinstance(expected, bool) and expected is False:
                return not is_emp
            return is_emp

        if actual is None:
            return False

        if operator == "eq":
            return str(actual).strip().lower() == str(expected).strip().lower()
        if operator == "neq":
            return str(actual).strip().lower() != str(expected).strip().lower()
        if operator == "contains":
            return str(expected).strip().lower() in str(actual).strip().lower()
        if operator in ("gt", "lt"):
            try:
                num_actual = float(str(actual).replace("$", "").replace(",", "").strip())
                num_expected = float(str(expected).replace("$", "").replace(",", "").strip())
                return num_actual > num_expected if operator == "gt" else num_actual < num_expected
            except (ValueError, TypeError):
                return str(actual) > str(expected) if operator == "gt" else str(actual) < str(expected)
        return False

    if subtype == WorkflowStep.Subtype.CAMPAIGN_MATCHES:
        campaigns = cfg.get("campaigns") or []
        single = cfg.get("campaign") or cfg.get("value")
        if single:
            campaigns = [*campaigns, single]
        op = str(cfg.get("operator") or "eq").strip().lower()
        actual_camp = (contact.campaign or "").strip().lower()
        if not actual_camp:
            return False
        for c in campaigns:
            c_str = str(c).strip().lower()
            if op == "contains" and c_str in actual_camp:
                return True
            if c_str == actual_camp:
                return True
        return False

    return False


def _resolve_template_for_step(config: dict[str, Any], channel: str) -> MessageTemplate | None:
    template_ref = config.get("template_id") or config.get("template_name") or config.get("template")
    if not template_ref:
        return None
    parsed = _parse_uuid(template_ref)
    if parsed is not None:
        return MessageTemplate.objects.filter(pk=parsed, channel=channel, active=True).first()
    return MessageTemplate.objects.filter(
        name__iexact=str(template_ref).strip(),
        channel=channel,
        active=True,
    ).first()


def _resolve_lead_status_from_config(config: dict[str, Any]) -> LeadStatus | None:
    status_ref = config.get("status_id") or config.get("status_name") or config.get("status")
    if not status_ref:
        return None
    parsed = _parse_uuid(status_ref)
    if parsed is not None:
        return LeadStatus.objects.filter(pk=parsed).first()
    return LeadStatus.objects.filter(name__iexact=str(status_ref).strip()).first()


def _resolve_membership_from_ref(member_ref: Any) -> Membership | None:
    parsed = _parse_uuid(member_ref)
    if parsed is None:
        return None
    return (
        Membership.objects.select_related("user", "tenant")
        .filter(Q(pk=parsed) | Q(user_id=parsed), is_active=True)
        .first()
    )


def _compute_duration_delta(config: dict[str, Any], default_minutes: int = 0) -> timedelta:
    if any(k in config for k in ("days", "hours", "minutes", "seconds")):
        return timedelta(
            days=float(config.get("days") or 0),
            hours=float(config.get("hours") or 0),
            minutes=float(config.get("minutes") or 0),
            seconds=float(config.get("seconds") or 0),
        )
    val = float(
        config.get("duration")
        or config.get("value")
        or config.get("offset_value")
        or config.get("timeout_value")
        or default_minutes
    )
    unit = str(
        config.get("unit")
        or config.get("offset_unit")
        or config.get("timeout_unit")
        or "minutes"
    ).strip().lower()
    if unit == "days":
        return timedelta(days=val)
    if unit == "hours":
        return timedelta(hours=val)
    if unit == "seconds":
        return timedelta(seconds=val)
    return timedelta(minutes=val)


def execute_action_step(
    *,
    step: WorkflowStep,
    contact: Contact,
    enrollment: WorkflowEnrollment,
) -> tuple[str, dict[str, Any]]:
    tenant_id = require_tenant_id()
    cfg = step.config or {}
    subtype = step.subtype

    if subtype in (
        WorkflowStep.Subtype.SEND_EMAIL,
        WorkflowStep.Subtype.SEND_SMS,
        WorkflowStep.Subtype.SEND_WHATSAPP,
    ):
        channel_map = {
            WorkflowStep.Subtype.SEND_EMAIL: Channel.EMAIL,
            WorkflowStep.Subtype.SEND_SMS: Channel.SMS,
            WorkflowStep.Subtype.SEND_WHATSAPP: Channel.WHATSAPP,
        }
        channel = channel_map[subtype]
        template = _resolve_template_for_step(cfg, channel)
        body = str(cfg.get("body") or "")
        subject = str(cfg.get("subject") or "")
        idemp_key = f"{enrollment.id}:{step.key}:{enrollment.iteration}"

        if (
            channel == Channel.WHATSAPP
            and template is not None
            and template.approval_status != MessageTemplate.ApprovalStatus.APPROVED
        ):
            if not body:
                body = template.body
            template = None

        try:
            msg = MessagingService.send(
                contact=contact,
                channel=channel,
                body=body,
                subject=subject,
                template=template,
                source=Message.Source.WORKFLOW,
                idempotency_key=idemp_key,
                metadata={
                    "workflow_id": str(enrollment.workflow_id),
                    "enrollment_id": str(enrollment.id),
                    "step_key": step.key,
                    "iteration": enrollment.iteration,
                },
            )
            log_contact_activity(
                contact=contact,
                activity_type=Activity.ActivityType.WORKFLOW_ACTION,
                description=f"Workflow '{enrollment.workflow.name}' executed {subtype} (step '{step.key}').",
                metadata={
                    "workflow_id": str(enrollment.workflow_id),
                    "enrollment_id": str(enrollment.id),
                    "step_key": step.key,
                    "subtype": subtype,
                    "message_id": str(msg.id),
                },
                touch_activity=False,
            )
            return StepExecution.Status.SUCCESS, {"message_id": str(msg.id), "channel": channel}
        except ApplicationError as exc:
            if exc.code in ("contact_opted_out", "contact_email_bounced"):
                log_contact_activity(
                    contact=contact,
                    activity_type=Activity.ActivityType.WORKFLOW_ACTION,
                    description=f"Workflow '{enrollment.workflow.name}' skipped {subtype} because contact opted out or bounced.",
                    metadata={
                        "workflow_id": str(enrollment.workflow_id),
                        "enrollment_id": str(enrollment.id),
                        "step_key": step.key,
                        "subtype": subtype,
                        "skipped_reason": exc.code,
                    },
                    touch_activity=False,
                )
                return StepExecution.Status.SKIPPED, {"skipped_reason": exc.code, "detail": str(exc.detail)}
            raise

    if subtype == WorkflowStep.Subtype.ADD_TAG:
        tag_ref = cfg.get("tag_id") or cfg.get("tag_name") or cfg.get("tag")
        tags_list = cfg.get("tags") or ([] if not tag_ref else [tag_ref])
        added = ContactService.add_tags(
            contact=contact,
            tags=tags_list,
            create_missing=bool(cfg.get("create_missing", True)),
        )
        log_contact_activity(
            contact=contact,
            activity_type=Activity.ActivityType.WORKFLOW_ACTION,
            description=f"Workflow '{enrollment.workflow.name}' added tags: {', '.join(t.name for t in added) or 'none new'}.",
            metadata={
                "workflow_id": str(enrollment.workflow_id),
                "enrollment_id": str(enrollment.id),
                "step_key": step.key,
                "subtype": subtype,
                "tags": [t.name for t in added],
            },
            touch_activity=False,
        )
        return StepExecution.Status.SUCCESS, {"added_tags": [t.name for t in added]}

    if subtype == WorkflowStep.Subtype.REMOVE_TAG:
        tag_ref = cfg.get("tag_id") or cfg.get("tag_name") or cfg.get("tag")
        tags_list = cfg.get("tags") or ([] if not tag_ref else [tag_ref])
        removed = ContactService.remove_tags(contact=contact, tags=tags_list)
        log_contact_activity(
            contact=contact,
            activity_type=Activity.ActivityType.WORKFLOW_ACTION,
            description=f"Workflow '{enrollment.workflow.name}' removed tags: {', '.join(t.name for t in removed) or 'none'}.",
            metadata={
                "workflow_id": str(enrollment.workflow_id),
                "enrollment_id": str(enrollment.id),
                "step_key": step.key,
                "subtype": subtype,
                "tags": [t.name for t in removed],
            },
            touch_activity=False,
        )
        return StepExecution.Status.SUCCESS, {"removed_tags": [t.name for t in removed]}

    if subtype == WorkflowStep.Subtype.UPDATE_CONTACT_FIELD:
        changes: dict[str, Any] = {}
        custom_updates: dict[str, Any] = dict(cfg.get("custom_fields") or {})
        fields_dict = cfg.get("fields") or {}
        if isinstance(fields_dict, dict):
            for k, v in fields_dict.items():
                clean_k = str(k).strip()
                if clean_k.startswith("custom_fields."):
                    custom_updates[clean_k[len("custom_fields."):]] = v
                elif clean_k in STANDARD_CONTACT_FIELDS:
                    changes[clean_k] = v
                else:
                    custom_updates[clean_k] = v

        single_field = str(cfg.get("field") or cfg.get("key") or "").strip()
        if single_field:
            val = cfg.get("value")
            if single_field.startswith("custom_fields."):
                custom_updates[single_field[len("custom_fields."):]] = val
            elif single_field in STANDARD_CONTACT_FIELDS:
                changes[single_field] = val
            else:
                custom_updates[single_field] = val

        if custom_updates:
            changes["custom_fields"] = custom_updates

        ContactService.update_contact(contact=contact, **changes)
        log_contact_activity(
            contact=contact,
            activity_type=Activity.ActivityType.WORKFLOW_ACTION,
            description=f"Workflow '{enrollment.workflow.name}' updated contact fields.",
            metadata={
                "workflow_id": str(enrollment.workflow_id),
                "enrollment_id": str(enrollment.id),
                "step_key": step.key,
                "subtype": subtype,
                "changes": changes,
            },
            touch_activity=False,
        )
        return StepExecution.Status.SUCCESS, {"updated": list(changes.keys())}

    if subtype == WorkflowStep.Subtype.UPDATE_LEAD_STATUS:
        target_status = _resolve_lead_status_from_config(cfg)
        if target_status is None:
            raise ApplicationError(
                detail="Target LeadStatus for update_lead_status not found.",
                code="lead_status_not_found",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        ContactService.change_status(contact=contact, new_status=target_status)
        log_contact_activity(
            contact=contact,
            activity_type=Activity.ActivityType.WORKFLOW_ACTION,
            description=f"Workflow '{enrollment.workflow.name}' updated lead status to '{target_status.name}'.",
            metadata={
                "workflow_id": str(enrollment.workflow_id),
                "enrollment_id": str(enrollment.id),
                "step_key": step.key,
                "subtype": subtype,
                "status_id": str(target_status.id),
                "status_name": target_status.name,
            },
            touch_activity=False,
        )
        return StepExecution.Status.SUCCESS, {"status_id": str(target_status.id), "status_name": target_status.name}

    if subtype == WorkflowStep.Subtype.ASSIGN_MARKETER:
        from accounts.services import AssignmentService

        mode = str(cfg.get("mode") or cfg.get("strategy") or "").strip().lower()
        member_ref = cfg.get("membership_id") or cfg.get("assignee_id") or cfg.get("user_id")
        if not mode:
            mode = "specific" if member_ref else "round_robin"

        assignee = AssignmentService.assign_from_workflow(
            contact=contact,
            mode=mode,
            member_ref=member_ref,
            marketers_only=bool(cfg.get("marketers_only", False)),
        )
        log_contact_activity(
            contact=contact,
            activity_type=Activity.ActivityType.WORKFLOW_ACTION,
            description=f"Workflow '{enrollment.workflow.name}' assigned contact ({mode}).",
            metadata={
                "workflow_id": str(enrollment.workflow_id),
                "enrollment_id": str(enrollment.id),
                "step_key": step.key,
                "subtype": subtype,
                "mode": mode,
                "assigned_membership_id": str(assignee.id) if assignee else None,
            },
            touch_activity=False,
        )
        return StepExecution.Status.SUCCESS, {
            "mode": mode,
            "assigned_membership_id": str(assignee.id) if assignee else None,
        }

    if subtype == WorkflowStep.Subtype.CREATE_TASK:
        raw_title = str(cfg.get("title") or "Follow up with lead")
        raw_desc = str(cfg.get("description") or "")
        rendered_title = render_template_placeholders(raw_title, contact) or raw_title
        rendered_desc = render_template_placeholders(raw_desc, contact)

        due_offset_cfg = cfg.get("due_offset") if isinstance(cfg.get("due_offset"), dict) else {
            "minutes": cfg.get("due_in_minutes") or cfg.get("due_offset_minutes") or 0,
            "hours": cfg.get("due_in_hours") or cfg.get("due_offset_hours") or 0,
            "days": cfg.get("due_in_days") or cfg.get("due_offset_days") or 0,
            "value": cfg.get("due_value") or 0,
            "unit": cfg.get("due_unit") or "minutes",
        }
        delta = _compute_duration_delta(due_offset_cfg, default_minutes=15)
        due_at = timezone.now() + (delta if delta.total_seconds() > 0 else timedelta(minutes=15))

        member_ref = cfg.get("assignee_id") or cfg.get("membership_id") or cfg.get("user_id")
        if member_ref and str(member_ref) not in ("contact_assignee", "assigned_marketer"):
            assignee = _resolve_membership_from_ref(member_ref)
        else:
            assignee = contact.assigned_to

        task = create_contact_task(
            title=rendered_title,
            description=rendered_desc,
            contact=contact,
            assignee=assignee,
            due_at=due_at,
            from_workflow=True,
        )
        return StepExecution.Status.SUCCESS, {"task_id": str(task.id), "due_at": due_at.isoformat()}

    if subtype == WorkflowStep.Subtype.SEND_INTERNAL_NOTIFICATION:
        raw_title = str(cfg.get("title") or f"Workflow Alert: {enrollment.workflow.name}")
        raw_body = str(cfg.get("body") or cfg.get("message") or raw_title)
        rendered_title = render_template_placeholders(raw_title, contact) or raw_title
        rendered_body = render_template_placeholders(raw_body, contact) or raw_body

        recipients: list[Any] = []
        for uid in cfg.get("user_ids") or []:
            member = _resolve_membership_from_ref(uid)
            if member is not None:
                recipients.append(member.user_id)
        for mid in cfg.get("membership_ids") or []:
            member = _resolve_membership_from_ref(mid)
            if member is not None:
                recipients.append(member.user_id)

        roles = cfg.get("roles") or []
        if roles:
            role_members = Membership.objects.filter(
                role__in=[str(r).lower() for r in roles],
                is_active=True,
            ).values_list("user_id", flat=True)
            recipients.extend(list(role_members))

        if bool(cfg.get("notify_assigned", True)) and contact.assigned_to is not None:
            recipients.append(contact.assigned_to.user_id)

        if not recipients:
            admin_ids = list(
                Membership.objects.filter(
                    role__in=[Membership.Role.OWNER, Membership.Role.ADMIN],
                    is_active=True,
                ).values_list("user_id", flat=True)
            )
            recipients.extend(admin_ids)

        notifs = notify(
            tenant=tenant_id,
            users=recipients,
            type=NotificationType.INTERNAL_TASK,
            payload={
                "title": rendered_title,
                "body": rendered_body,
                "workflow_id": str(enrollment.workflow_id),
                "enrollment_id": str(enrollment.id),
                "contact_id": str(contact.id),
            },
            link=f"/contacts/{contact.id}",
        )
        log_contact_activity(
            contact=contact,
            activity_type=Activity.ActivityType.WORKFLOW_ACTION,
            description=f"Workflow '{enrollment.workflow.name}' sent internal notification: {rendered_title}.",
            metadata={
                "workflow_id": str(enrollment.workflow_id),
                "enrollment_id": str(enrollment.id),
                "step_key": step.key,
                "subtype": subtype,
                "notifications_sent": len(notifs),
            },
            touch_activity=False,
        )
        return StepExecution.Status.SUCCESS, {"notifications_sent": len(notifs)}

    if subtype == WorkflowStep.Subtype.SEND_OUTBOUND_WEBHOOK:
        webhook_payload = {
            "url": str(cfg.get("url") or ""),
            "event_type": str(cfg.get("event_type") or cfg.get("webhook_name") or "workflow_webhook"),
            "workflow_id": str(enrollment.workflow_id),
            "workflow_name": enrollment.workflow.name,
            "enrollment_id": str(enrollment.id),
            "step_key": step.key,
            "iteration": enrollment.iteration,
            "contact": {
                "id": str(contact.id),
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "full_name": contact.full_name,
                "email": contact.email,
                "phone": contact.phone,
                "city": contact.city,
                "country": contact.country,
                "lead_status": contact.lead_status.name if contact.lead_status else None,
                "source_channel": contact.source_channel,
                "source_page": contact.source_page,
                "source_form": contact.source_form,
                "campaign": contact.campaign,
                "tags": [t.name for t in contact.tags.all()],
                "custom_fields": contact.custom_fields or {},
            },
            "extra_payload": cfg.get("payload") or cfg.get("extra_payload") or {},
        }
        publish(
            EventName.OUTBOUND_WEBHOOK_REQUESTED,
            tenant_id=tenant_id,
            contact_id=contact.id,
            payload=webhook_payload,
        )
        log_contact_activity(
            contact=contact,
            activity_type=Activity.ActivityType.WORKFLOW_ACTION,
            description=f"Workflow '{enrollment.workflow.name}' requested outbound webhook.",
            metadata={
                "workflow_id": str(enrollment.workflow_id),
                "enrollment_id": str(enrollment.id),
                "step_key": step.key,
                "subtype": subtype,
                "url": webhook_payload["url"],
            },
            touch_activity=False,
        )
        return StepExecution.Status.SUCCESS, {"event": EventName.OUTBOUND_WEBHOOK_REQUESTED}

    if subtype == WorkflowStep.Subtype.PAUSE_AUTOMATION:
        scope = str(cfg.get("scope") or "this").strip().lower()
        reason = str(cfg.get("reason") or "workflow_action").strip()
        if scope in ("all", "contact"):
            EnrollmentService.pause_all_for_contact(contact=contact, reason=reason)
            return StepExecution.Status.SUCCESS, {"paused_scope": "all"}
        if scope == "other":
            EnrollmentService.pause_all_for_contact(
                contact=contact,
                reason=reason,
                exclude_enrollment_id=enrollment.id,
            )
            return StepExecution.Status.SUCCESS, {"paused_scope": "other"}
        return StepExecution.Status.SUCCESS, {"paused_scope": "this", "pause_self": True, "reason": reason}

    if subtype == WorkflowStep.Subtype.END_AUTOMATION:
        scope = str(cfg.get("scope") or "this").strip().lower()
        if scope in ("all", "contact"):
            other_open = list(
                WorkflowEnrollment.objects.select_for_update().filter(
                    contact=contact,
                    status__in=[WorkflowEnrollment.Status.ACTIVE, WorkflowEnrollment.Status.PAUSED],
                ).exclude(pk=enrollment.pk)
            )
            for other in other_open:
                EnrollmentService.unenroll(enrollment=other, reason="end_automation_action")
        log_contact_activity(
            contact=contact,
            activity_type=Activity.ActivityType.WORKFLOW_ACTION,
            description=f"Workflow '{enrollment.workflow.name}' ended by end_automation step '{step.key}'.",
            metadata={
                "workflow_id": str(enrollment.workflow_id),
                "enrollment_id": str(enrollment.id),
                "step_key": step.key,
                "subtype": subtype,
            },
            touch_activity=False,
        )
        return StepExecution.Status.SUCCESS, {"end_self": True}

    return StepExecution.Status.SUCCESS, {}


def _resolve_relative_base_datetime(contact: Contact, field_key: str) -> datetime | None:
    clean_key = (field_key or "appointment.starts_at").strip()
    if clean_key in APPOINTMENT_DATE_FIELDS:
        next_appt = (
            Appointment.objects.filter(
                contact=contact,
                status__in=[Appointment.Status.BOOKED, Appointment.Status.RESCHEDULED],
            )
            .order_by("-created_at", "starts_at")
            .first()
        )
        return next_appt.starts_at if next_appt is not None else None

    if clean_key.startswith("custom_fields."):
        clean_key = clean_key[len("custom_fields."):]
    raw_val = (contact.custom_fields or {}).get(clean_key)
    if not raw_val:
        return None
    raw_str = str(raw_val).strip()
    dt = parse_datetime(raw_str)
    if dt is not None:
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, dt_timezone.utc)
        return dt
    d = parse_date(raw_str)
    if d is not None:
        return datetime.combine(d, time(9, 0), tzinfo=dt_timezone.utc)
    return None


def _notify_workflow_failure(enrollment: WorkflowEnrollment, step_key: str, error_msg: str) -> None:
    tenant_id = enrollment.tenant_id
    admin_ids = list(
        Membership.objects.filter(
            role__in=[Membership.Role.OWNER, Membership.Role.ADMIN],
            is_active=True,
        ).values_list("user_id", flat=True)
    )
    if not admin_ids:
        return
    notify(
        tenant=tenant_id,
        users=admin_ids,
        type=NotificationType.WORKFLOW_INTEGRATION_ALERT,
        payload={
            "title": f"Workflow Step Failed: {enrollment.workflow.name}",
            "body": f"Step '{step_key}' failed after {MAX_STEP_ATTEMPTS} attempts: {error_msg}",
            "workflow_id": str(enrollment.workflow_id),
            "workflow_name": enrollment.workflow.name,
            "enrollment_id": str(enrollment.id),
            "contact_id": str(enrollment.contact_id),
            "step_key": step_key,
            "error": error_msg,
        },
        link=f"/automation/workflows/{enrollment.workflow_id}",
    )


def execute_single_enrollment(enrollment_id: uuid.UUID | str) -> dict[str, Any]:
    tenant_id = require_tenant_id()
    steps_processed = 0

    while steps_processed < MAX_STEPS_PER_PASS:
        with transaction.atomic():
            enrollment = (
                WorkflowEnrollment.objects.select_for_update()
                .select_related("workflow", "contact")
                .filter(pk=enrollment_id)
                .first()
            )
            if enrollment is None:
                return {"status": "not_found", "steps_processed": steps_processed}

            if enrollment.status != WorkflowEnrollment.Status.ACTIVE:
                if enrollment.locked_at is not None:
                    enrollment.locked_at = None
                    enrollment.save(update_fields=["locked_at", "updated_at"])
                return {"status": enrollment.status, "steps_processed": steps_processed}

            if enrollment.workflow.status != Workflow.Status.ACTIVE:
                enrollment.locked_at = None
                enrollment.save(update_fields=["locked_at", "updated_at"])
                return {"status": "workflow_not_active", "steps_processed": steps_processed}

            now = timezone.now()
            if enrollment.next_run_at is not None and enrollment.next_run_at > now:
                enrollment.locked_at = None
                enrollment.save(update_fields=["locked_at", "updated_at"])
                return {"status": "scheduled", "steps_processed": steps_processed}

            step_key = (enrollment.current_step_key or "").strip()
            if not step_key:
                enrollment.status = WorkflowEnrollment.Status.COMPLETED
                enrollment.finished_at = now
                enrollment.next_run_at = None
                enrollment.locked_at = None
                enrollment.waiting_for_event = {}
                enrollment.save(
                    update_fields=[
                        "status",
                        "finished_at",
                        "next_run_at",
                        "locked_at",
                        "waiting_for_event",
                        "updated_at",
                    ]
                )
                return {"status": "completed", "steps_processed": steps_processed}

            step = WorkflowStep.objects.filter(
                workflow=enrollment.workflow,
                key=step_key,
            ).first()
            if step is None:
                enrollment.status = WorkflowEnrollment.Status.COMPLETED
                enrollment.current_step_key = ""
                enrollment.finished_at = now
                enrollment.next_run_at = None
                enrollment.locked_at = None
                enrollment.waiting_for_event = {}
                enrollment.save(
                    update_fields=[
                        "status",
                        "current_step_key",
                        "finished_at",
                        "next_run_at",
                        "locked_at",
                        "waiting_for_event",
                        "updated_at",
                    ]
                )
                log_contact_activity(
                    contact=enrollment.contact,
                    activity_type=Activity.ActivityType.WORKFLOW_ACTION,
                    description=f"Workflow '{enrollment.workflow.name}' completed (step '{step_key}' was removed).",
                    metadata={
                        "workflow_id": str(enrollment.workflow_id),
                        "enrollment_id": str(enrollment.id),
                        "removed_step_key": step_key,
                    },
                    touch_activity=False,
                )
                return {"status": "completed_missing_step", "steps_processed": steps_processed}

            contact = (
                Contact.objects.select_related("lead_status", "assigned_to", "assigned_to__user", "tenant")
                .prefetch_related("tags")
                .get(pk=enrollment.contact_id)
            )

            existing_exec = StepExecution.objects.filter(
                enrollment=enrollment,
                step_key=step.key,
                iteration=enrollment.iteration,
            ).first()
            attempt_num = (existing_exec.attempts + 1) if existing_exec is not None else 1

            try:
                if step.type == WorkflowStep.StepType.CONDITION:
                    matched = evaluate_condition_step(
                        step=step,
                        contact=contact,
                        enrollment=enrollment,
                    )
                    next_target = (step.true_key if matched else step.false_key) or ""
                    StepExecution.objects.update_or_create(
                        tenant_id=tenant_id,
                        enrollment=enrollment,
                        step_key=step.key,
                        iteration=enrollment.iteration,
                        defaults={
                            "status": StepExecution.Status.SUCCESS,
                            "attempts": attempt_num,
                            "error": "",
                            "result": {"matched": matched, "branch": "true" if matched else "false"},
                            "executed_at": now,
                        },
                    )
                    steps_processed += 1
                    if next_target:
                        enrollment.current_step_key = next_target
                        enrollment.next_run_at = now
                        enrollment.locked_at = now
                        enrollment.save(update_fields=["current_step_key", "next_run_at", "locked_at", "updated_at"])
                        continue
                    enrollment.current_step_key = ""
                    enrollment.status = WorkflowEnrollment.Status.COMPLETED
                    enrollment.finished_at = now
                    enrollment.next_run_at = None
                    enrollment.locked_at = None
                    enrollment.save(
                        update_fields=[
                            "current_step_key",
                            "status",
                            "finished_at",
                            "next_run_at",
                            "locked_at",
                            "updated_at",
                        ]
                    )
                    return {"status": "completed", "steps_processed": steps_processed}

                if step.type == WorkflowStep.StepType.ACTION:
                    with transaction.atomic():
                        exec_status, exec_result = execute_action_step(
                            step=step,
                            contact=contact,
                            enrollment=enrollment,
                        )
                    StepExecution.objects.update_or_create(
                        tenant_id=tenant_id,
                        enrollment=enrollment,
                        step_key=step.key,
                        iteration=enrollment.iteration,
                        defaults={
                            "status": exec_status,
                            "attempts": attempt_num,
                            "error": "",
                            "result": exec_result,
                            "executed_at": now,
                        },
                    )
                    steps_processed += 1

                    if exec_result.get("end_self"):
                        enrollment.current_step_key = ""
                        enrollment.status = WorkflowEnrollment.Status.COMPLETED
                        enrollment.finished_at = now
                        enrollment.next_run_at = None
                        enrollment.locked_at = None
                        enrollment.save(
                            update_fields=[
                                "current_step_key",
                                "status",
                                "finished_at",
                                "next_run_at",
                                "locked_at",
                                "updated_at",
                            ]
                        )
                        return {"status": "completed", "steps_processed": steps_processed}

                    if exec_result.get("pause_self"):
                        next_target = (step.next_key or "").strip()
                        enrollment.current_step_key = next_target
                        enrollment.status = WorkflowEnrollment.Status.PAUSED
                        enrollment.paused_reason = str(exec_result.get("reason") or "workflow_action")
                        enrollment.locked_at = None
                        enrollment.save(
                            update_fields=[
                                "current_step_key",
                                "status",
                                "paused_reason",
                                "locked_at",
                                "updated_at",
                            ]
                        )
                        log_contact_activity(
                            contact=contact,
                            activity_type=Activity.ActivityType.WORKFLOW_ACTION,
                            description=f"Workflow '{enrollment.workflow.name}' paused by pause_automation step '{step.key}'.",
                            metadata={
                                "workflow_id": str(enrollment.workflow_id),
                                "enrollment_id": str(enrollment.id),
                                "step_key": step.key,
                            },
                            touch_activity=False,
                        )
                        return {"status": "paused", "steps_processed": steps_processed}

                    next_target = (step.next_key or "").strip()
                    if next_target:
                        enrollment.current_step_key = next_target
                        enrollment.next_run_at = now
                        enrollment.locked_at = now
                        enrollment.save(update_fields=["current_step_key", "next_run_at", "locked_at", "updated_at"])
                        continue

                    enrollment.current_step_key = ""
                    enrollment.status = WorkflowEnrollment.Status.COMPLETED
                    enrollment.finished_at = now
                    enrollment.next_run_at = None
                    enrollment.locked_at = None
                    enrollment.save(
                        update_fields=[
                            "current_step_key",
                            "status",
                            "finished_at",
                            "next_run_at",
                            "locked_at",
                            "updated_at",
                        ]
                    )
                    return {"status": "completed", "steps_processed": steps_processed}

                if step.type == WorkflowStep.StepType.DELAY:
                    cfg = step.config or {}
                    subtype = step.subtype

                    if subtype == WorkflowStep.Subtype.FIXED:
                        delta = _compute_duration_delta(cfg, default_minutes=5)
                        target_dt = now + delta
                        next_target = (step.next_key or "").strip()
                        StepExecution.objects.update_or_create(
                            tenant_id=tenant_id,
                            enrollment=enrollment,
                            step_key=step.key,
                            iteration=enrollment.iteration,
                            defaults={
                                "status": StepExecution.Status.SUCCESS,
                                "attempts": 1,
                                "error": "",
                                "result": {"scheduled_for": target_dt.isoformat()},
                                "executed_at": now,
                            },
                        )
                        steps_processed += 1
                        if next_target:
                            enrollment.current_step_key = next_target
                            enrollment.next_run_at = target_dt
                            enrollment.locked_at = None
                            enrollment.save(
                                update_fields=["current_step_key", "next_run_at", "locked_at", "updated_at"]
                            )
                            return {"status": "delayed", "next_run_at": target_dt.isoformat(), "steps_processed": steps_processed}
                        enrollment.current_step_key = ""
                        enrollment.status = WorkflowEnrollment.Status.COMPLETED
                        enrollment.finished_at = now
                        enrollment.next_run_at = None
                        enrollment.locked_at = None
                        enrollment.save(
                            update_fields=[
                                "current_step_key",
                                "status",
                                "finished_at",
                                "next_run_at",
                                "locked_at",
                                "updated_at",
                            ]
                        )
                        return {"status": "completed", "steps_processed": steps_processed}

                    if subtype == WorkflowStep.Subtype.UNTIL_DATETIME:
                        dt_str = str(cfg.get("datetime") or cfg.get("until_at") or "").strip()
                        parsed_dt = parse_datetime(dt_str)
                        if parsed_dt is None:
                            parsed_d = parse_date(dt_str)
                            if parsed_d is not None:
                                parsed_dt = datetime.combine(parsed_d, time(9, 0), tzinfo=dt_timezone.utc)
                        if parsed_dt is not None and timezone.is_naive(parsed_dt):
                            parsed_dt = timezone.make_aware(parsed_dt, dt_timezone.utc)

                        if parsed_dt is not None and parsed_dt > now:
                            next_target = (step.next_key or "").strip()
                            StepExecution.objects.update_or_create(
                                tenant_id=tenant_id,
                                enrollment=enrollment,
                                step_key=step.key,
                                iteration=enrollment.iteration,
                                defaults={
                                    "status": StepExecution.Status.SUCCESS,
                                    "attempts": 1,
                                    "error": "",
                                    "result": {"scheduled_for": parsed_dt.isoformat()},
                                    "executed_at": now,
                                },
                            )
                            steps_processed += 1
                            enrollment.current_step_key = next_target
                            enrollment.next_run_at = parsed_dt
                            enrollment.locked_at = None
                            enrollment.save(
                                update_fields=["current_step_key", "next_run_at", "locked_at", "updated_at"]
                            )
                            return {"status": "delayed", "next_run_at": parsed_dt.isoformat(), "steps_processed": steps_processed}

                        if_past = str(cfg.get("if_past") or cfg.get("on_past") or "skip").strip().lower()
                        StepExecution.objects.update_or_create(
                            tenant_id=tenant_id,
                            enrollment=enrollment,
                            step_key=step.key,
                            iteration=enrollment.iteration,
                            defaults={
                                "status": StepExecution.Status.SKIPPED,
                                "attempts": 1,
                                "error": "",
                                "result": {"skipped_reason": "already_past", "if_past": if_past},
                                "executed_at": now,
                            },
                        )
                        steps_processed += 1
                        if if_past == "stop":
                            enrollment.current_step_key = ""
                            enrollment.status = WorkflowEnrollment.Status.STOPPED
                            enrollment.paused_reason = "delay_datetime_already_past"
                            enrollment.finished_at = now
                            enrollment.next_run_at = None
                            enrollment.locked_at = None
                            enrollment.save(
                                update_fields=[
                                    "current_step_key",
                                    "status",
                                    "paused_reason",
                                    "finished_at",
                                    "next_run_at",
                                    "locked_at",
                                    "updated_at",
                                ]
                            )
                            return {"status": "stopped", "steps_processed": steps_processed}

                        next_target = (step.next_key or "").strip()
                        if next_target:
                            enrollment.current_step_key = next_target
                            enrollment.next_run_at = now
                            enrollment.locked_at = now
                            enrollment.save(update_fields=["current_step_key", "next_run_at", "locked_at", "updated_at"])
                            continue
                        enrollment.current_step_key = ""
                        enrollment.status = WorkflowEnrollment.Status.COMPLETED
                        enrollment.finished_at = now
                        enrollment.next_run_at = None
                        enrollment.locked_at = None
                        enrollment.save(
                            update_fields=[
                                "current_step_key",
                                "status",
                                "finished_at",
                                "next_run_at",
                                "locked_at",
                                "updated_at",
                            ]
                        )
                        return {"status": "completed", "steps_processed": steps_processed}

                    if subtype == WorkflowStep.Subtype.RELATIVE_TO_FIELD:
                        field_key = str(cfg.get("field") or "appointment.starts_at").strip()
                        base_dt = _resolve_relative_base_datetime(contact, field_key)
                        delta = _compute_duration_delta(cfg, default_minutes=0)
                        direction = str(cfg.get("direction") or "before").strip().lower()
                        target_dt = (
                            (base_dt - delta if direction == "before" else base_dt + delta)
                            if base_dt is not None
                            else None
                        )

                        if target_dt is not None and target_dt > now:
                            next_target = (step.next_key or "").strip()
                            StepExecution.objects.update_or_create(
                                tenant_id=tenant_id,
                                enrollment=enrollment,
                                step_key=step.key,
                                iteration=enrollment.iteration,
                                defaults={
                                    "status": StepExecution.Status.SUCCESS,
                                    "attempts": 1,
                                    "error": "",
                                    "result": {
                                        "base_datetime": base_dt.isoformat() if base_dt else None,
                                        "scheduled_for": target_dt.isoformat(),
                                    },
                                    "executed_at": now,
                                },
                            )
                            steps_processed += 1
                            enrollment.current_step_key = next_target
                            enrollment.next_run_at = target_dt
                            enrollment.locked_at = None
                            enrollment.save(
                                update_fields=["current_step_key", "next_run_at", "locked_at", "updated_at"]
                            )
                            return {"status": "delayed", "next_run_at": target_dt.isoformat(), "steps_processed": steps_processed}

                        if_past = str(cfg.get("if_past") or cfg.get("on_past") or "skip").strip().lower()
                        StepExecution.objects.update_or_create(
                            tenant_id=tenant_id,
                            enrollment=enrollment,
                            step_key=step.key,
                            iteration=enrollment.iteration,
                            defaults={
                                "status": StepExecution.Status.SKIPPED,
                                "attempts": 1,
                                "error": "",
                                "result": {
                                    "skipped_reason": "missing_or_past_relative_datetime",
                                    "if_past": if_past,
                                },
                                "executed_at": now,
                            },
                        )
                        steps_processed += 1
                        if if_past == "stop":
                            enrollment.current_step_key = ""
                            enrollment.status = WorkflowEnrollment.Status.STOPPED
                            enrollment.paused_reason = "relative_datetime_past_or_missing"
                            enrollment.finished_at = now
                            enrollment.next_run_at = None
                            enrollment.locked_at = None
                            enrollment.save(
                                update_fields=[
                                    "current_step_key",
                                    "status",
                                    "paused_reason",
                                    "finished_at",
                                    "next_run_at",
                                    "locked_at",
                                    "updated_at",
                                ]
                            )
                            return {"status": "stopped", "steps_processed": steps_processed}

                        next_target = (
                            (step.false_key or step.next_key)
                            if if_past == "branch_false"
                            else step.next_key
                        ) or ""
                        if next_target:
                            enrollment.current_step_key = next_target
                            enrollment.next_run_at = now
                            enrollment.locked_at = now
                            enrollment.save(update_fields=["current_step_key", "next_run_at", "locked_at", "updated_at"])
                            continue
                        enrollment.current_step_key = ""
                        enrollment.status = WorkflowEnrollment.Status.COMPLETED
                        enrollment.finished_at = now
                        enrollment.next_run_at = None
                        enrollment.locked_at = None
                        enrollment.save(
                            update_fields=[
                                "current_step_key",
                                "status",
                                "finished_at",
                                "next_run_at",
                                "locked_at",
                                "updated_at",
                            ]
                        )
                        return {"status": "completed", "steps_processed": steps_processed}

                    if subtype == WorkflowStep.Subtype.WAIT_FOR_EVENT:
                        waiting_meta = enrollment.waiting_for_event or {}
                        already_waiting = (
                            waiting_meta.get("step_key") == step.key
                            and waiting_meta.get("iteration") == enrollment.iteration
                        )
                        if not already_waiting:
                            timeout_delta = _compute_duration_delta(cfg, default_minutes=60 * 24)
                            if timeout_delta.total_seconds() <= 0:
                                timeout_delta = timedelta(hours=24)
                            timeout_at = now + timeout_delta
                            enrollment.waiting_for_event = {
                                "step_key": step.key,
                                "iteration": enrollment.iteration,
                                "event_type": str(cfg.get("event_type") or cfg.get("event") or "reply").lower(),
                                "config": cfg,
                                "started_waiting_at": now.isoformat(),
                                "timeout_at": timeout_at.isoformat(),
                            }
                            enrollment.next_run_at = timeout_at
                            enrollment.locked_at = None
                            enrollment.save(
                                update_fields=["waiting_for_event", "next_run_at", "locked_at", "updated_at"]
                            )
                            return {"status": "waiting_for_event", "timeout_at": timeout_at.isoformat(), "steps_processed": steps_processed}

                        started_wait_str = str(waiting_meta.get("started_waiting_at") or "")
                        started_wait_dt = parse_datetime(started_wait_str) or enrollment.started_at
                        wait_type = str(waiting_meta.get("event_type") or "reply").lower()
                        occurred = False
                        if wait_type == "reply":
                            occurred = has_contact_replied_since(
                                contact=contact,
                                since_dt=started_wait_dt,
                                channel=cfg.get("channel"),
                            )

                        StepExecution.objects.update_or_create(
                            tenant_id=tenant_id,
                            enrollment=enrollment,
                            step_key=step.key,
                            iteration=enrollment.iteration,
                            defaults={
                                "status": StepExecution.Status.SUCCESS,
                                "attempts": 1,
                                "error": "",
                                "result": {
                                    "event_occurred": occurred,
                                    "timed_out": not occurred,
                                },
                                "executed_at": now,
                            },
                        )
                        steps_processed += 1
                        next_target = (
                            (step.true_key or step.next_key)
                            if occurred
                            else (step.false_key or step.next_key)
                        ) or ""
                        enrollment.waiting_for_event = {}
                        if next_target:
                            enrollment.current_step_key = next_target
                            enrollment.next_run_at = now
                            enrollment.locked_at = now
                            enrollment.save(
                                update_fields=[
                                    "waiting_for_event",
                                    "current_step_key",
                                    "next_run_at",
                                    "locked_at",
                                    "updated_at",
                                ]
                            )
                            continue
                        enrollment.current_step_key = ""
                        enrollment.status = WorkflowEnrollment.Status.COMPLETED
                        enrollment.finished_at = now
                        enrollment.next_run_at = None
                        enrollment.locked_at = None
                        enrollment.save(
                            update_fields=[
                                "waiting_for_event",
                                "current_step_key",
                                "status",
                                "finished_at",
                                "next_run_at",
                                "locked_at",
                                "updated_at",
                            ]
                        )
                        return {"status": "completed", "steps_processed": steps_processed}

            except Exception as exc:
                err_text = str(getattr(exc, "detail", None) or exc)
                if attempt_num >= MAX_STEP_ATTEMPTS:
                    StepExecution.objects.update_or_create(
                        tenant_id=tenant_id,
                        enrollment=enrollment,
                        step_key=step.key,
                        iteration=enrollment.iteration,
                        defaults={
                            "status": StepExecution.Status.FAILED,
                            "attempts": attempt_num,
                            "error": err_text,
                            "result": {"failed": True},
                            "executed_at": now,
                        },
                    )
                    enrollment.status = WorkflowEnrollment.Status.FAILED
                    enrollment.paused_reason = f"step_failed:{step.key}"
                    enrollment.finished_at = now
                    enrollment.next_run_at = None
                    enrollment.locked_at = None
                    enrollment.save(
                        update_fields=[
                            "status",
                            "paused_reason",
                            "finished_at",
                            "next_run_at",
                            "locked_at",
                            "updated_at",
                        ]
                    )
                    log_contact_activity(
                        contact=contact,
                        activity_type=Activity.ActivityType.WORKFLOW_ACTION,
                        description=f"Workflow '{enrollment.workflow.name}' failed on step '{step.key}': {err_text}",
                        metadata={
                            "workflow_id": str(enrollment.workflow_id),
                            "enrollment_id": str(enrollment.id),
                            "step_key": step.key,
                            "error": err_text,
                        },
                        touch_activity=False,
                    )
                    _notify_workflow_failure(enrollment, step.key, err_text)
                    return {"status": "failed", "error": err_text, "steps_processed": steps_processed}

                backoff_seconds = 30 * (2 ** (attempt_num - 1))
                retry_at = now + timedelta(seconds=backoff_seconds)
                StepExecution.objects.update_or_create(
                    tenant_id=tenant_id,
                    enrollment=enrollment,
                    step_key=step.key,
                    iteration=enrollment.iteration,
                    defaults={
                        "status": StepExecution.Status.FAILED,
                        "attempts": attempt_num,
                        "error": err_text,
                        "result": {"retrying_at": retry_at.isoformat()},
                        "executed_at": now,
                    },
                )
                enrollment.next_run_at = retry_at
                enrollment.locked_at = None
                enrollment.save(update_fields=["next_run_at", "locked_at", "updated_at"])
                return {"status": "retry_scheduled", "next_run_at": retry_at.isoformat(), "steps_processed": steps_processed}

    with transaction.atomic():
        enrollment = WorkflowEnrollment.objects.select_for_update().filter(pk=enrollment_id).first()
        if enrollment is not None and enrollment.locked_at is not None:
            enrollment.locked_at = None
            enrollment.next_run_at = timezone.now()
            enrollment.save(update_fields=["locked_at", "next_run_at", "updated_at"])

    return {"status": "max_steps_reached", "steps_processed": steps_processed}
