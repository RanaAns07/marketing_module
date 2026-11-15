from typing import Any
from celery import shared_task
from core.context import TenantAwareTask
from inbox.services import handle_inbox_domain_event


@shared_task(base=TenantAwareTask, bind=True, name="inbox.tasks.handle_domain_event")
def handle_domain_event(self, tenant_id: str, event: dict[str, Any]) -> dict[str, Any]:
    result = handle_inbox_domain_event(event)
    return {
        "subscriber": "inbox",
        "event_id": event.get("event_id"),
        "tenant_id": tenant_id,
        **result,
    }
