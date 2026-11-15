from typing import Any
from celery import shared_task
from core.context import TenantAwareTask
from core.events import EventName
from usage.services import record_message_usage, update_message_usage_status


@shared_task(base=TenantAwareTask, bind=True, name="usage.tasks.handle_domain_event")
def handle_domain_event(self, tenant_id: str, event: dict[str, Any]) -> dict[str, Any]:
    event_name = event.get("name")
    payload = event.get("payload") or {}
    message_id = payload.get("message_id")

    if not message_id:
        return {"subscriber": "usage", "status": "ignored_no_message_id"}

    if event_name == EventName.MESSAGE_SENT:
        rec = record_message_usage(message=message_id)
        return {
            "subscriber": "usage",
            "action": "record_usage",
            "message_id": message_id,
            "recorded": rec is not None,
        }

    if event_name == EventName.MESSAGE_STATUS_CHANGED:
        status_val = payload.get("status") or ""
        prov_id = payload.get("provider_message_id")
        rec = update_message_usage_status(
            message_id=message_id,
            new_status=status_val,
            provider_message_id=prov_id,
        )
        return {
            "subscriber": "usage",
            "action": "update_status",
            "message_id": message_id,
            "updated": rec is not None,
        }

    return {"subscriber": "usage", "status": "ignored_unhandled_event"}
