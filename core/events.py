from datetime import datetime, timezone
from typing import Any, Callable, Optional, Union
import uuid
from django.db import transaction


class EventName:
    LEAD_CREATED = "lead_created"
    FORM_SUBMITTED = "form_submitted"
    TAG_ADDED = "tag_added"
    TAG_REMOVED = "tag_removed"
    LEAD_STATUS_CHANGED = "lead_status_changed"
    LEAD_INACTIVE = "lead_inactive"
    APPOINTMENT_BOOKED = "appointment_booked"
    APPOINTMENT_CANCELLED = "appointment_cancelled"
    EMAIL_RECEIVED = "email_received"
    SMS_RECEIVED = "sms_received"
    WHATSAPP_RECEIVED = "whatsapp_received"
    MESSAGE_SENT = "message_sent"
    MESSAGE_STATUS_CHANGED = "message_status_changed"
    CONVERSION = "conversion"
    EXTERNAL_EVENT = "external_event"
    ASSIGNMENT_CHANGED = "assignment_changed"

    ALL = frozenset(
        {
            LEAD_CREATED,
            FORM_SUBMITTED,
            TAG_ADDED,
            TAG_REMOVED,
            LEAD_STATUS_CHANGED,
            LEAD_INACTIVE,
            APPOINTMENT_BOOKED,
            APPOINTMENT_CANCELLED,
            EMAIL_RECEIVED,
            SMS_RECEIVED,
            WHATSAPP_RECEIVED,
            MESSAGE_SENT,
            MESSAGE_STATUS_CHANGED,
            CONVERSION,
            EXTERNAL_EVENT,
            ASSIGNMENT_CHANGED,
        }
    )


SUBSCRIBER_TASKS = (
    ("automation", "automation.tasks.handle_domain_event", "automation"),
    ("inbox", "inbox.tasks.handle_domain_event", "default"),
    ("notifications", "notifications.tasks.handle_domain_event", "default"),
    ("webhooks", "webhooks.tasks.handle_domain_event", "webhooks"),
)

_in_process_subscribers: list[Callable[[dict[str, Any]], None]] = []


def register_subscriber(handler: Callable[[dict[str, Any]], None]) -> None:
    if handler not in _in_process_subscribers:
        _in_process_subscribers.append(handler)


def publish(
    name: str,
    tenant_id: Union[uuid.UUID, str],
    contact_id: Optional[Union[uuid.UUID, str]] = None,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if name not in EventName.ALL:
        raise ValueError(f"Unsupported event name: {name}")

    normalized_tenant_id = str(uuid.UUID(str(tenant_id)))
    normalized_contact_id = str(uuid.UUID(str(contact_id))) if contact_id is not None else None

    event_envelope: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "name": name,
        "tenant_id": normalized_tenant_id,
        "contact_id": normalized_contact_id,
        "payload": dict(payload or {}),
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }

    def _dispatch():
        from core.tasks import fanout_domain_event
        try:
            fanout_domain_event.delay(normalized_tenant_id, event_envelope)
        except Exception:
            fanout_domain_event(normalized_tenant_id, event_envelope)

        for handler in list(_in_process_subscribers):
            handler(event_envelope)

    transaction.on_commit(_dispatch)
    return event_envelope
