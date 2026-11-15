from typing import Any
import uuid
from django.core.validators import URLValidator
from django.utils.dateparse import parse_date, parse_datetime
from accounts.models import Membership
from automation.models import Workflow, WorkflowStep
from contacts.models import CustomFieldDefinition, LeadStatus, Tag
from messaging.models import Channel, MessageTemplate

CONDITION_SUBTYPES = frozenset(
    {
        WorkflowStep.Subtype.HAS_TAG,
        WorkflowStep.Subtype.NOT_HAS_TAG,
        WorkflowStep.Subtype.STATUS_IS,
        WorkflowStep.Subtype.SOURCE_MATCHES,
        WorkflowStep.Subtype.REPLIED,
        WorkflowStep.Subtype.NOT_REPLIED,
        WorkflowStep.Subtype.CUSTOM_FIELD_COMPARE,
        WorkflowStep.Subtype.CAMPAIGN_MATCHES,
        WorkflowStep.Subtype.APPOINTMENT_NOT_CANCELLED,
    }
)

ACTION_SUBTYPES = frozenset(
    {
        WorkflowStep.Subtype.SEND_EMAIL,
        WorkflowStep.Subtype.SEND_SMS,
        WorkflowStep.Subtype.SEND_WHATSAPP,
        WorkflowStep.Subtype.ADD_TAG,
        WorkflowStep.Subtype.REMOVE_TAG,
        WorkflowStep.Subtype.UPDATE_CONTACT_FIELD,
        WorkflowStep.Subtype.UPDATE_LEAD_STATUS,
        WorkflowStep.Subtype.ASSIGN_MARKETER,
        WorkflowStep.Subtype.CREATE_TASK,
        WorkflowStep.Subtype.SEND_INTERNAL_NOTIFICATION,
        WorkflowStep.Subtype.SEND_OUTBOUND_WEBHOOK,
        WorkflowStep.Subtype.PAUSE_AUTOMATION,
        WorkflowStep.Subtype.END_AUTOMATION,
    }
)

DELAY_SUBTYPES = frozenset(
    {
        WorkflowStep.Subtype.FIXED,
        WorkflowStep.Subtype.UNTIL_DATETIME,
        WorkflowStep.Subtype.RELATIVE_TO_FIELD,
        WorkflowStep.Subtype.WAIT_FOR_EVENT,
    }
)

STANDARD_CONTACT_FIELDS = frozenset(
    {
        "first_name",
        "last_name",
        "full_name",
        "email",
        "phone",
        "city",
        "country",
        "timezone",
        "source_channel",
        "source_page",
        "source_form",
        "campaign",
        "ad_set",
        "ad",
        "external_id",
    }
)

APPOINTMENT_DATE_FIELDS = frozenset(
    {
        "appointment.starts_at",
        "next_appointment",
        "starts_at",
        "appointment_starts_at",
        "appointment_date",
    }
)

VALID_COMPARE_OPERATORS = frozenset({"eq", "neq", "gt", "lt", "contains", "is_empty"})
VALID_TIME_UNITS = frozenset({"seconds", "minutes", "hours", "days"})
VALID_WAIT_EVENTS = frozenset(
    {
        "reply",
        "email_replied",
        "email_received",
        "sms_received",
        "whatsapp_received",
        "tag_added",
        "tag_removed",
        "lead_status_changed",
        "appointment_booked",
        "appointment_cancelled",
        "external_event",
    }
)


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _tag_exists(tag_ref: Any) -> bool:
    if not tag_ref:
        return False
    parsed = _parse_uuid(tag_ref)
    if parsed is not None:
        return Tag.objects.filter(pk=parsed).exists()
    return Tag.objects.filter(name__iexact=str(tag_ref).strip()).exists()


def _status_exists(status_ref: Any) -> bool:
    if not status_ref:
        return False
    parsed = _parse_uuid(status_ref)
    if parsed is not None:
        return LeadStatus.objects.filter(pk=parsed).exists()
    return LeadStatus.objects.filter(name__iexact=str(status_ref).strip()).exists()


def _member_exists(member_ref: Any) -> bool:
    parsed = _parse_uuid(member_ref)
    if parsed is None:
        return False
    return (
        Membership.objects.filter(pk=parsed, is_active=True).exists()
        or Membership.objects.filter(user_id=parsed, is_active=True).exists()
    )


def _template_exists(template_ref: Any, channel: str) -> bool:
    if not template_ref:
        return False
    parsed = _parse_uuid(template_ref)
    if parsed is not None:
        return MessageTemplate.objects.filter(pk=parsed, channel=channel, active=True).exists()
    return MessageTemplate.objects.filter(
        name__iexact=str(template_ref).strip(),
        channel=channel,
        active=True,
    ).exists()


def _field_exists(field_key: str) -> bool:
    clean = (field_key or "").strip()
    if not clean:
        return False
    if clean.startswith("custom_fields."):
        clean = clean[len("custom_fields."):]
    if clean in STANDARD_CONTACT_FIELDS:
        return True
    return CustomFieldDefinition.objects.filter(key=clean).exists()


def validate_trigger_config(trigger_type: str, trigger_config: Any) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if trigger_type not in Workflow.TriggerType.values:
        errors.append(
            {
                "field": "trigger_type",
                "message": f"Unsupported trigger_type '{trigger_type}'.",
            }
        )
        return errors

    if trigger_config is None:
        trigger_config = {}
    if not isinstance(trigger_config, dict):
        errors.append(
            {
                "field": "trigger_config",
                "message": "trigger_config must be a JSON object.",
            }
        )
        return errors

    if trigger_type in (Workflow.TriggerType.TAG_ADDED, Workflow.TriggerType.TAG_REMOVED):
        tag_ref = (
            trigger_config.get("tag_id")
            or trigger_config.get("tag_name")
            or trigger_config.get("tag")
        )
        tags_list = trigger_config.get("tags") or []
        if tag_ref and not _tag_exists(tag_ref):
            errors.append(
                {
                    "field": "trigger_config.tag",
                    "message": f"Referenced trigger tag '{tag_ref}' does not exist in this tenant.",
                }
            )
        for item in tags_list:
            if not _tag_exists(item):
                errors.append(
                    {
                        "field": "trigger_config.tags",
                        "message": f"Referenced trigger tag '{item}' does not exist in this tenant.",
                    }
                )

    elif trigger_type == Workflow.TriggerType.LEAD_STATUS_CHANGED:
        from_ref = (
            trigger_config.get("from_status_id")
            or trigger_config.get("from_status_name")
            or trigger_config.get("from_status")
            or trigger_config.get("from")
        )
        to_ref = (
            trigger_config.get("to_status_id")
            or trigger_config.get("to_status_name")
            or trigger_config.get("to_status")
            or trigger_config.get("to")
        )
        if from_ref and not _status_exists(from_ref):
            errors.append(
                {
                    "field": "trigger_config.from_status",
                    "message": f"Referenced lead status '{from_ref}' does not exist in this tenant.",
                }
            )
        if to_ref and not _status_exists(to_ref):
            errors.append(
                {
                    "field": "trigger_config.to_status",
                    "message": f"Referenced lead status '{to_ref}' does not exist in this tenant.",
                }
            )

    return errors


def validate_step_config(
    *,
    step_key: str,
    step_type: str,
    subtype: str,
    config: Any,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    prefix = f"steps[{step_key}]"

    if step_type not in WorkflowStep.StepType.values:
        errors.append({"field": f"{prefix}.type", "message": f"Invalid step type '{step_type}'."})
        return errors

    if subtype not in WorkflowStep.Subtype.values:
        errors.append({"field": f"{prefix}.subtype", "message": f"Invalid step subtype '{subtype}'."})
        return errors

    if step_type == WorkflowStep.StepType.CONDITION and subtype not in CONDITION_SUBTYPES:
        errors.append(
            {
                "field": f"{prefix}.subtype",
                "message": f"Subtype '{subtype}' is not a valid condition subtype.",
            }
        )
        return errors
    if step_type == WorkflowStep.StepType.ACTION and subtype not in ACTION_SUBTYPES:
        errors.append(
            {
                "field": f"{prefix}.subtype",
                "message": f"Subtype '{subtype}' is not a valid action subtype.",
            }
        )
        return errors
    if step_type == WorkflowStep.StepType.DELAY and subtype not in DELAY_SUBTYPES:
        errors.append(
            {
                "field": f"{prefix}.subtype",
                "message": f"Subtype '{subtype}' is not a valid delay subtype.",
            }
        )
        return errors

    if config is None:
        config = {}
    if not isinstance(config, dict):
        errors.append({"field": f"{prefix}.config", "message": "Step config must be a JSON object."})
        return errors

    if subtype in (WorkflowStep.Subtype.HAS_TAG, WorkflowStep.Subtype.NOT_HAS_TAG):
        tag_ref = config.get("tag_id") or config.get("tag_name") or config.get("tag")
        tags_list = config.get("tags") or ([] if not tag_ref else [tag_ref])
        if not tags_list:
            errors.append(
                {
                    "field": f"{prefix}.config.tag",
                    "message": "Tag condition requires a tag_id, tag_name, or tags list.",
                }
            )
        else:
            for item in tags_list:
                if not _tag_exists(item):
                    errors.append(
                        {
                            "field": f"{prefix}.config.tag",
                            "message": f"Tag '{item}' does not exist in this tenant.",
                        }
                    )

    elif subtype == WorkflowStep.Subtype.STATUS_IS:
        target = str(config.get("target") or "contact").lower()
        status_ref = (
            config.get("status_id")
            or config.get("status_name")
            or config.get("status")
        )
        if target == "appointment" or config.get("appointment_status") or config.get("not_cancelled"):
            pass
        elif not status_ref:
            errors.append(
                {
                    "field": f"{prefix}.config.status",
                    "message": "status_is condition requires status_id or status_name.",
                }
            )
        elif not _status_exists(status_ref):
            errors.append(
                {
                    "field": f"{prefix}.config.status",
                    "message": f"LeadStatus '{status_ref}' does not exist in this tenant.",
                }
            )

    elif subtype == WorkflowStep.Subtype.SOURCE_MATCHES:
        src = (
            config.get("source_channel")
            or config.get("source")
            or config.get("sources")
            or config.get("source_page")
            or config.get("source_form")
        )
        if not src:
            errors.append(
                {
                    "field": f"{prefix}.config.source_channel",
                    "message": "source_matches condition requires source_channel, source, or sources.",
                }
            )

    elif subtype in (WorkflowStep.Subtype.REPLIED, WorkflowStep.Subtype.NOT_REPLIED):
        ch = config.get("channel")
        if ch and str(ch).lower() not in Channel.values:
            errors.append(
                {
                    "field": f"{prefix}.config.channel",
                    "message": f"Unsupported channel '{ch}' in reply condition.",
                }
            )

    elif subtype == WorkflowStep.Subtype.CUSTOM_FIELD_COMPARE:
        field_key = str(config.get("field") or config.get("key") or "").strip()
        operator = str(config.get("operator") or "eq").strip().lower()
        if not field_key:
            errors.append(
                {
                    "field": f"{prefix}.config.field",
                    "message": "custom_field_compare requires a field key.",
                }
            )
        elif not _field_exists(field_key):
            errors.append(
                {
                    "field": f"{prefix}.config.field",
                    "message": f"Field '{field_key}' does not exist on Contact or CustomFieldDefinition.",
                }
            )
        if operator not in VALID_COMPARE_OPERATORS:
            errors.append(
                {
                    "field": f"{prefix}.config.operator",
                    "message": f"Operator must be one of {sorted(VALID_COMPARE_OPERATORS)}.",
                }
            )
        if operator != "is_empty" and "value" not in config:
            errors.append(
                {
                    "field": f"{prefix}.config.value",
                    "message": f"Operator '{operator}' requires a comparison value.",
                }
            )

    elif subtype == WorkflowStep.Subtype.CAMPAIGN_MATCHES:
        camp = config.get("campaign") or config.get("campaigns") or config.get("value")
        if not camp:
            errors.append(
                {
                    "field": f"{prefix}.config.campaign",
                    "message": "campaign_matches requires a campaign value.",
                }
            )

    elif subtype in (
        WorkflowStep.Subtype.SEND_EMAIL,
        WorkflowStep.Subtype.SEND_SMS,
        WorkflowStep.Subtype.SEND_WHATSAPP,
    ):
        channel_map = {
            WorkflowStep.Subtype.SEND_EMAIL: Channel.EMAIL,
            WorkflowStep.Subtype.SEND_SMS: Channel.SMS,
            WorkflowStep.Subtype.SEND_WHATSAPP: Channel.WHATSAPP,
        }
        expected_channel = channel_map[subtype]
        template_ref = config.get("template_id") or config.get("template_name") or config.get("template")
        body_text = str(config.get("body") or "").strip()
        if not template_ref and not body_text:
            errors.append(
                {
                    "field": f"{prefix}.config.template",
                    "message": f"{subtype} requires a template_id, template_name, or body.",
                }
            )
        elif template_ref and not _template_exists(template_ref, expected_channel):
            errors.append(
                {
                    "field": f"{prefix}.config.template",
                    "message": f"Active {expected_channel} MessageTemplate '{template_ref}' does not exist in this tenant.",
                }
            )

    elif subtype in (WorkflowStep.Subtype.ADD_TAG, WorkflowStep.Subtype.REMOVE_TAG):
        tag_ref = config.get("tag_id") or config.get("tag_name") or config.get("tag")
        tags_list = config.get("tags") or ([] if not tag_ref else [tag_ref])
        if not tags_list:
            errors.append(
                {
                    "field": f"{prefix}.config.tag",
                    "message": f"{subtype} requires tag_id, tag_name, or tags.",
                }
            )
        else:
            create_missing = bool(config.get("create_missing", False))
            for item in tags_list:
                if _parse_uuid(item) is not None or not create_missing:
                    if not _tag_exists(item):
                        errors.append(
                            {
                                "field": f"{prefix}.config.tag",
                                "message": f"Tag '{item}' does not exist in this tenant.",
                            }
                        )

    elif subtype == WorkflowStep.Subtype.UPDATE_CONTACT_FIELD:
        single_field = config.get("field") or config.get("key")
        fields_dict = config.get("fields") or {}
        custom_dict = config.get("custom_fields") or {}
        if not single_field and not fields_dict and not custom_dict:
            errors.append(
                {
                    "field": f"{prefix}.config",
                    "message": "update_contact_field requires field+value, fields, or custom_fields.",
                }
            )
        if single_field and not _field_exists(str(single_field)):
            errors.append(
                {
                    "field": f"{prefix}.config.field",
                    "message": f"Field '{single_field}' does not exist on Contact or CustomFieldDefinition.",
                }
            )
        if isinstance(fields_dict, dict):
            for fkey in fields_dict:
                if not _field_exists(str(fkey)):
                    errors.append(
                        {
                            "field": f"{prefix}.config.fields.{fkey}",
                            "message": f"Field '{fkey}' does not exist on Contact or CustomFieldDefinition.",
                        }
                    )
        if isinstance(custom_dict, dict):
            for ckey in custom_dict:
                if not CustomFieldDefinition.objects.filter(key=str(ckey)).exists():
                    errors.append(
                        {
                            "field": f"{prefix}.config.custom_fields.{ckey}",
                            "message": f"CustomFieldDefinition '{ckey}' does not exist in this tenant.",
                        }
                    )

    elif subtype == WorkflowStep.Subtype.UPDATE_LEAD_STATUS:
        status_ref = config.get("status_id") or config.get("status_name") or config.get("status")
        if not status_ref:
            errors.append(
                {
                    "field": f"{prefix}.config.status",
                    "message": "update_lead_status requires status_id or status_name.",
                }
            )
        elif not _status_exists(status_ref):
            errors.append(
                {
                    "field": f"{prefix}.config.status",
                    "message": f"LeadStatus '{status_ref}' does not exist in this tenant.",
                }
            )

    elif subtype == WorkflowStep.Subtype.ASSIGN_MARKETER:
        mode = str(config.get("mode") or config.get("strategy") or "").strip().lower()
        member_ref = (
            config.get("membership_id")
            or config.get("assignee_id")
            or config.get("user_id")
        )
        if not mode:
            mode = "specific" if member_ref else "round_robin"
        if mode not in ("specific", "round_robin", "by_rule", "rule"):
            errors.append(
                {
                    "field": f"{prefix}.config.mode",
                    "message": "assign_marketer mode must be 'specific', 'round_robin', or 'by_rule'.",
                }
            )
        elif mode == "specific":
            if not member_ref:
                errors.append(
                    {
                        "field": f"{prefix}.config.membership_id",
                        "message": "assign_marketer with mode='specific' requires membership_id, assignee_id, or user_id.",
                    }
                )
            elif not _member_exists(member_ref):
                errors.append(
                    {
                        "field": f"{prefix}.config.membership_id",
                        "message": f"Referenced team member '{member_ref}' does not exist in this tenant.",
                    }
                )

    elif subtype == WorkflowStep.Subtype.CREATE_TASK:
        title = str(config.get("title") or "").strip()
        if not title:
            errors.append(
                {
                    "field": f"{prefix}.config.title",
                    "message": "create_task requires a non-empty title.",
                }
            )
        member_ref = (
            config.get("assignee_id")
            or config.get("membership_id")
            or config.get("user_id")
        )
        if member_ref and str(member_ref) not in ("contact_assignee", "assigned_marketer"):
            if not _member_exists(member_ref):
                errors.append(
                    {
                        "field": f"{prefix}.config.assignee_id",
                        "message": f"Task assignee '{member_ref}' does not exist in this tenant.",
                    }
                )

    elif subtype == WorkflowStep.Subtype.SEND_INTERNAL_NOTIFICATION:
        msg = str(config.get("body") or config.get("message") or config.get("title") or "").strip()
        if not msg:
            errors.append(
                {
                    "field": f"{prefix}.config.body",
                    "message": "send_internal_notification requires title, body, or message.",
                }
            )
        for uid in config.get("user_ids") or []:
            if not _member_exists(uid):
                errors.append(
                    {
                        "field": f"{prefix}.config.user_ids",
                        "message": f"Notification recipient '{uid}' does not exist in this tenant.",
                    }
                )
        for mid in config.get("membership_ids") or []:
            if not _member_exists(mid):
                errors.append(
                    {
                        "field": f"{prefix}.config.membership_ids",
                        "message": f"Notification recipient '{mid}' does not exist in this tenant.",
                    }
                )

    elif subtype == WorkflowStep.Subtype.SEND_OUTBOUND_WEBHOOK:
        target_url = str(config.get("url") or "").strip()
        if target_url:
            validator = URLValidator()
            try:
                validator(target_url)
            except Exception:
                errors.append(
                    {
                        "field": f"{prefix}.config.url",
                        "message": f"Invalid outbound webhook URL '{target_url}'.",
                    }
                )

    elif subtype == WorkflowStep.Subtype.FIXED:
        val = config.get("duration") or config.get("value")
        unit = str(config.get("unit") or "minutes").lower()
        mins = config.get("minutes")
        hrs = config.get("hours")
        days = config.get("days")
        secs = config.get("seconds")
        has_direct = any(v is not None and float(v) > 0 for v in (mins, hrs, days, secs) if isinstance(v, (int, float)))
        if not has_direct:
            if not isinstance(val, (int, float)) or float(val) <= 0:
                errors.append(
                    {
                        "field": f"{prefix}.config.duration",
                        "message": "fixed delay requires a positive duration value.",
                    }
                )
            if unit not in VALID_TIME_UNITS:
                errors.append(
                    {
                        "field": f"{prefix}.config.unit",
                        "message": f"Delay unit must be one of {sorted(VALID_TIME_UNITS)}.",
                    }
                )

    elif subtype == WorkflowStep.Subtype.UNTIL_DATETIME:
        dt_str = str(config.get("datetime") or config.get("until_at") or "").strip()
        if not dt_str or (parse_datetime(dt_str) is None and parse_date(dt_str) is None):
            errors.append(
                {
                    "field": f"{prefix}.config.datetime",
                    "message": "until_datetime delay requires a valid ISO date or datetime.",
                }
            )

    elif subtype == WorkflowStep.Subtype.RELATIVE_TO_FIELD:
        field_key = str(config.get("field") or "appointment.starts_at").strip()
        if field_key not in APPOINTMENT_DATE_FIELDS:
            clean_key = (
                field_key[len("custom_fields."):]
                if field_key.startswith("custom_fields.")
                else field_key
            )
            defn = CustomFieldDefinition.objects.filter(key=clean_key).first()
            if defn is None:
                errors.append(
                    {
                        "field": f"{prefix}.config.field",
                        "message": f"Relative delay field '{field_key}' is not a valid appointment field or CustomFieldDefinition.",
                    }
                )
        unit = str(config.get("offset_unit") or config.get("unit") or "hours").lower()
        if unit not in VALID_TIME_UNITS:
            errors.append(
                {
                    "field": f"{prefix}.config.unit",
                    "message": f"Offset unit must be one of {sorted(VALID_TIME_UNITS)}.",
                }
            )

    elif subtype == WorkflowStep.Subtype.WAIT_FOR_EVENT:
        ev_type = str(config.get("event_type") or config.get("event") or "reply").strip().lower()
        if ev_type not in VALID_WAIT_EVENTS:
            errors.append(
                {
                    "field": f"{prefix}.config.event_type",
                    "message": f"wait_for_event event_type must be one of {sorted(VALID_WAIT_EVENTS)}.",
                }
            )
        tag_ref = config.get("tag_id") or config.get("tag_name") or config.get("tag")
        if tag_ref and not _tag_exists(tag_ref):
            errors.append(
                {
                    "field": f"{prefix}.config.tag",
                    "message": f"Wait tag '{tag_ref}' does not exist in this tenant.",
                }
            )
        status_ref = config.get("to_status_id") or config.get("to_status_name") or config.get("to_status")
        if status_ref and not _status_exists(status_ref):
            errors.append(
                {
                    "field": f"{prefix}.config.to_status",
                    "message": f"Wait lead status '{status_ref}' does not exist in this tenant.",
                }
            )

    return errors


def resolve_entry_step_key(steps_data: list[dict[str, Any]], explicit_entry: str = "") -> str:
    if not steps_data:
        return ""
    step_keys = [str(s.get("key") or "").strip() for s in steps_data if str(s.get("key") or "").strip()]
    if not step_keys:
        return ""
    key_set = set(step_keys)
    clean_explicit = (explicit_entry or "").strip()
    if clean_explicit and clean_explicit in key_set:
        return clean_explicit

    referenced: set[str] = set()
    for step in steps_data:
        for edge_field in ("next_key", "true_key", "false_key"):
            target = str(step.get(edge_field) or "").strip()
            if target:
                referenced.add(target)

    roots = [k for k in step_keys if k not in referenced]
    if roots:
        return roots[0]
    return step_keys[0]


def validate_workflow_graph(
    *,
    trigger_type: str,
    trigger_config: Any,
    steps_data: list[dict[str, Any]],
    entry_step_key: str = "",
    strict_activation: bool = False,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    errors.extend(validate_trigger_config(trigger_type, trigger_config))

    if not steps_data:
        if strict_activation:
            errors.append(
                {
                    "field": "steps",
                    "message": "Workflow must contain at least one step to be activated.",
                }
            )
        return errors

    steps_by_key: dict[str, dict[str, Any]] = {}
    for idx, step in enumerate(steps_data):
        raw_key = str(step.get("key") or "").strip()
        if not raw_key:
            errors.append({"field": f"steps[{idx}].key", "message": "Step key cannot be blank."})
            continue
        if raw_key in steps_by_key:
            errors.append(
                {
                    "field": f"steps[{raw_key}].key",
                    "message": f"Duplicate step key '{raw_key}' in workflow.",
                }
            )
            continue
        steps_by_key[raw_key] = step

    if not steps_by_key:
        return errors

    for key, step in steps_by_key.items():
        stype = str(step.get("type") or "").strip()
        subtype = str(step.get("subtype") or "").strip()
        cfg = step.get("config")
        errors.extend(
            validate_step_config(
                step_key=key,
                step_type=stype,
                subtype=subtype,
                config=cfg,
            )
        )
        for edge_field in ("next_key", "true_key", "false_key"):
            target = str(step.get(edge_field) or "").strip()
            if target and target not in steps_by_key:
                errors.append(
                    {
                        "field": f"steps[{key}].{edge_field}",
                        "message": f"Step '{key}' references unknown {edge_field} '{target}'.",
                    }
                )

    clean_entry = (entry_step_key or "").strip()
    if clean_entry and clean_entry not in steps_by_key:
        errors.append(
            {
                "field": "entry_step_key",
                "message": f"entry_step_key '{clean_entry}' does not match any step key.",
            }
        )
        return errors

    incoming_counts: dict[str, int] = {k: 0 for k in steps_by_key}
    adjacency: dict[str, list[str]] = {k: [] for k in steps_by_key}
    has_terminal_branch: dict[str, bool] = {k: False for k in steps_by_key}

    for key, step in steps_by_key.items():
        stype = str(step.get("type") or "").strip()
        subtype = str(step.get("subtype") or "").strip()
        next_k = str(step.get("next_key") or "").strip()
        true_k = str(step.get("true_key") or "").strip()
        false_k = str(step.get("false_key") or "").strip()

        if stype == WorkflowStep.StepType.CONDITION:
            for target in (true_k, false_k):
                if target and target in steps_by_key:
                    adjacency[key].append(target)
                    incoming_counts[target] += 1
            if not true_k or not false_k:
                has_terminal_branch[key] = True
        elif subtype == WorkflowStep.Subtype.WAIT_FOR_EVENT:
            branches = [b for b in (true_k, false_k, next_k) if b]
            for target in branches:
                if target in steps_by_key and target not in adjacency[key]:
                    adjacency[key].append(target)
                    incoming_counts[target] += 1
            if not (true_k and false_k) and not next_k:
                has_terminal_branch[key] = True
            elif not true_k or not false_k:
                has_terminal_branch[key] = True
        elif subtype == WorkflowStep.Subtype.END_AUTOMATION:
            has_terminal_branch[key] = True
        else:
            if next_k and next_k in steps_by_key:
                adjacency[key].append(next_k)
                incoming_counts[next_k] += 1
            else:
                has_terminal_branch[key] = True

    if clean_entry:
        start_key = clean_entry
    else:
        roots = [k for k, count in incoming_counts.items() if count == 0]
        if not roots:
            errors.append(
                {
                    "field": "steps",
                    "message": "Workflow graph has no entry step (all steps have incoming transitions).",
                }
            )
            start_key = next(iter(steps_by_key))
        else:
            start_key = roots[0]

    visited: set[str] = set()
    stack = [start_key]
    while stack:
        curr = stack.pop()
        if curr in visited:
            continue
        visited.add(curr)
        for nxt in adjacency.get(curr, []):
            if nxt not in visited:
                stack.append(nxt)

    for key in steps_by_key:
        if key not in visited:
            errors.append(
                {
                    "field": f"steps[{key}]",
                    "message": f"Step '{key}' is an orphan step not reachable from entry step '{start_key}'.",
                }
            )

    color: dict[str, int] = {k: 0 for k in steps_by_key}
    for node in visited:
        if steps_by_key[node].get("type") == WorkflowStep.StepType.DELAY:
            continue
        if color[node] != 0:
            continue

        def _dfs_non_delay(u: str) -> bool:
            color[u] = 1
            for v in adjacency.get(u, []):
                if steps_by_key[v].get("type") == WorkflowStep.StepType.DELAY:
                    continue
                if color[v] == 1:
                    errors.append(
                        {
                            "field": f"steps[{u}]",
                            "message": f"Cycle detected between '{u}' and '{v}' that never reaches a delay step.",
                        }
                    )
                    return True
                if color[v] == 0 and _dfs_non_delay(v):
                    return True
            color[u] = 2
            return False

        _dfs_non_delay(node)

    terminal_nodes = {k for k in visited if has_terminal_branch.get(k, False)}
    if not terminal_nodes:
        errors.append(
            {
                "field": "steps",
                "message": "Workflow graph is missing a terminal step (all paths loop indefinitely).",
            }
        )
    else:
        rev_adj: dict[str, list[str]] = {k: [] for k in steps_by_key}
        for u in visited:
            for v in adjacency.get(u, []):
                if v in visited:
                    rev_adj[v].append(u)
        can_terminate: set[str] = set()
        t_stack = list(terminal_nodes)
        while t_stack:
            curr = t_stack.pop()
            if curr in can_terminate:
                continue
            can_terminate.add(curr)
            for prev in rev_adj.get(curr, []):
                if prev not in can_terminate:
                    t_stack.append(prev)
        for u in visited:
            if u not in can_terminate:
                errors.append(
                    {
                        "field": f"steps[{u}]",
                        "message": f"Step '{u}' has no path to a terminal step.",
                    }
                )

    return errors
