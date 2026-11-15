from typing import Any
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from core.context import TenantAwareTask
from integrations.models import InboundWebhookEvent
from integrations.providers.registry import (
    build_provider_for_connection,
    instantiate_provider,
)
from messaging.services import process_inbound_message, process_status_event


@shared_task(
    base=TenantAwareTask,
    bind=True,
    max_retries=3,
    name="integrations.tasks.process_webhook_event_task",
)
def process_webhook_event_task(self, tenant_id: str, webhook_event_id: str) -> dict[str, Any]:
    with transaction.atomic():
        event = (
            InboundWebhookEvent.objects.select_for_update()
            .select_related("connection")
            .filter(pk=webhook_event_id)
            .first()
        )
        if event is None:
            return {"status": "not_found", "webhook_event_id": webhook_event_id}
        if event.processed:
            return {"status": "already_processed", "webhook_event_id": webhook_event_id}

    if event.connection is not None:
        provider = build_provider_for_connection(event.connection)
    else:
        provider = instantiate_provider(
            channel=event.channel,
            provider_name=event.provider,
            credentials={},
        )

    inbound_count = 0
    status_count = 0

    try:
        payload = event.raw_payload
        if event.event_type == InboundWebhookEvent.EventType.INBOUND:
            if isinstance(payload, dict):
                inbound_items = provider.parse_inbound(payload)
                for item in inbound_items:
                    process_inbound_message(inbound=item)
                    inbound_count += 1
                if not inbound_items:
                    status_items = provider.parse_status(payload)
                    for st_item in status_items:
                        process_status_event(status_event=st_item)
                        status_count += 1
        else:
            status_items = provider.parse_status(payload)
            for st_item in status_items:
                process_status_event(status_event=st_item)
                status_count += 1
    except Exception as exc:
        with transaction.atomic():
            locked = InboundWebhookEvent.objects.select_for_update().filter(pk=webhook_event_id).first()
            if locked is not None:
                locked.error = str(exc)[:1000]
                locked.save(update_fields=["error", "updated_at"])
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=10 * (2 ** self.request.retries))
        return {"status": "error", "webhook_event_id": webhook_event_id, "error": str(exc)}

    with transaction.atomic():
        locked = InboundWebhookEvent.objects.select_for_update().filter(pk=webhook_event_id).first()
        if locked is not None:
            locked.processed = True
            locked.processed_at = timezone.now()
            locked.error = ""
            locked.save(update_fields=["processed", "processed_at", "error", "updated_at"])

    return {
        "status": "processed",
        "webhook_event_id": webhook_event_id,
        "inbound_processed": inbound_count,
        "status_processed": status_count,
    }
