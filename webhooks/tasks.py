from datetime import timedelta
import random
from typing import Any
from celery import shared_task
from django.utils import timezone
from core.context import TenantAwareTask, tenant_context
from webhooks.models import WebhookDelivery
from webhooks.services import (
    MAX_EXTERNAL_EVENT_ATTEMPTS,
    attempt_webhook_delivery,
    fanout_outbound_webhooks_for_event,
    process_external_event,
)


@shared_task(base=TenantAwareTask, bind=True, name="webhooks.tasks.handle_domain_event")
def handle_domain_event(self, tenant_id: str, event: dict[str, Any]) -> dict[str, Any]:
    deliveries = fanout_outbound_webhooks_for_event(event)
    return {
        "subscriber": "webhooks",
        "event_id": event.get("event_id"),
        "tenant_id": tenant_id,
        "deliveries_created": len(deliveries),
    }


@shared_task(
    base=TenantAwareTask,
    bind=True,
    name="webhooks.tasks.process_external_event_task",
    max_retries=MAX_EXTERNAL_EVENT_ATTEMPTS,
)
def process_external_event_task(self, tenant_id: str, external_event_id: str) -> dict[str, Any]:
    ext_event, should_retry = process_external_event(external_event_id)
    if should_retry:
        countdown = min(300, (2 ** max(1, ext_event.attempts)) * 10) + random.randint(1, 5)
        try:
            raise self.retry(
                exc=RuntimeError(ext_event.error or "External event processing failed"),
                countdown=countdown,
            )
        except Exception:
            pass
    return {
        "tenant_id": tenant_id,
        "external_event_id": str(ext_event.id),
        "status": ext_event.status,
        "attempts": ext_event.attempts,
    }


@shared_task(base=TenantAwareTask, bind=True, name="webhooks.tasks.deliver_webhook_task")
def deliver_webhook_task(self, tenant_id: str, delivery_id: str) -> dict[str, Any]:
    delivery = attempt_webhook_delivery(delivery_id, schedule_retry=True)
    return {
        "tenant_id": tenant_id,
        "delivery_id": str(delivery.id),
        "status": delivery.status,
        "attempts": delivery.attempts,
    }


@shared_task(name="webhooks.tasks.retry_due_webhook_deliveries_beat_task")
def retry_due_webhook_deliveries_beat_task() -> dict[str, Any]:
    now = timezone.now()
    due_rows = list(
        WebhookDelivery.objects.unscoped()
        .filter(
            status=WebhookDelivery.Status.FAILED,
            next_retry_at__isnull=False,
            next_retry_at__lte=now,
            endpoint__active=True,
        )
        .values_list("tenant_id", "id")[:200]
    )
    dispatched = 0
    for tid, did in due_rows:
        tid_str = str(tid)
        did_str = str(did)
        try:
            with tenant_context(tid):
                WebhookDelivery.objects.filter(pk=did, status=WebhookDelivery.Status.FAILED).update(
                    next_retry_at=now + timedelta(minutes=5)
                )
            deliver_webhook_task.apply_async(args=[tid_str, did_str], queue="webhooks")
            dispatched += 1
        except Exception:
            pass
    return {"dispatched": dispatched}
