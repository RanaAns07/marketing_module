from typing import Any
from celery import shared_task
from core.context import TenantAwareTask


@shared_task(base=TenantAwareTask, bind=True, name="webhooks.tasks.handle_domain_event")
def handle_domain_event(self, tenant_id: str, event: dict[str, Any]) -> dict[str, Any]:
    return {"subscriber": "webhooks", "event_id": event.get("event_id"), "tenant_id": tenant_id}
