from typing import Any
from celery import shared_task
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from accounts.models import Membership
from contacts.models import Contact
from core.context import TenantAwareTask, tenant_context
from core.events import EventName, publish
from core.exceptions import ApplicationError
from core.models import Tenant
from integrations.providers.registry import (
    EmailProvider,
    ProviderDispatchError,
    SMSProvider,
    WhatsAppProvider,
    resolve_tenant_provider,
)
from messaging.models import Broadcast, Channel, Message
from messaging.selectors import get_broadcast_audience_queryset
from messaging.services import MessagingService
from notifications.models import NotificationType
from notifications.services import notify

BROADCAST_BATCH_SIZE = 100


def _mark_message_failed(
    *,
    tenant_id: str,
    message: Message,
    error_text: str,
) -> None:
    message.status = Message.Status.FAILED
    message.error = error_text[:1000]
    message.save(update_fields=["status", "error", "updated_at"])

    if message.broadcast_id:
        Broadcast.objects.filter(pk=message.broadcast_id).update(
            failed_count=F("failed_count") + 1,
        )

    publish(
        EventName.MESSAGE_STATUS_CHANGED,
        tenant_id=tenant_id,
        contact_id=message.contact_id,
        payload={
            "message_id": str(message.id),
            "channel": message.channel,
            "status": message.status,
            "error": message.error,
            "broadcast_id": str(message.broadcast_id) if message.broadcast_id else None,
        },
    )


@shared_task(
    base=TenantAwareTask,
    bind=True,
    max_retries=3,
    name="messaging.tasks.dispatch_outbound_message_task",
)
def dispatch_outbound_message_task(self, tenant_id: str, message_id: str) -> dict[str, Any]:
    with transaction.atomic():
        message = (
            Message.objects.select_for_update(of=("self",))
            .select_related("contact", "template")
            .filter(pk=message_id)
            .first()
        )
        if message is None:
            return {"status": "not_found", "message_id": message_id}

        if message.status in (
            Message.Status.SENT,
            Message.Status.DELIVERED,
            Message.Status.READ,
        ) and message.provider_message_id:
            return {
                "status": "already_sent",
                "message_id": str(message.id),
                "provider_message_id": message.provider_message_id,
            }

    try:
        connection, provider = resolve_tenant_provider(
            message.channel,
            tenant_or_id=tenant_id,
            require_active=True,
        )
    except ApplicationError as exc:
        with transaction.atomic():
            msg_locked = Message.objects.select_for_update().filter(pk=message_id).first()
            if msg_locked is not None:
                _mark_message_failed(
                    tenant_id=tenant_id,
                    message=msg_locked,
                    error_text=str(exc.detail),
                )
        return {"status": "failed", "message_id": message_id, "error": str(exc.detail)}

    contact = message.contact
    metadata = message.metadata or {}

    try:
        if message.channel == Channel.EMAIL and isinstance(provider, EmailProvider):
            headers = {}
            unsub_url = metadata.get("unsubscribe_url")
            if unsub_url:
                headers["List-Unsubscribe"] = f"<{unsub_url}>"
            send_result = provider.send(
                to_email=contact.email or "",
                subject=message.subject or "(No Subject)",
                html_body=message.body,
                text_body=message.body,
                from_email=connection.sender_identity,
                from_name=connection.sender_name,
                custom_args={
                    "tenant_id": str(tenant_id),
                    "message_id": str(message.id),
                },
                headers=headers,
            )
        elif message.channel == Channel.SMS and isinstance(provider, SMSProvider):
            send_result = provider.send(
                to_phone=contact.phone or "",
                body=message.body,
                from_phone=connection.sender_identity,
            )
        elif message.channel == Channel.WHATSAPP and isinstance(provider, WhatsAppProvider):
            content_sid = str(
                metadata.get("content_sid")
                or (message.template.whatsapp_content_sid if message.template else "")
                or ""
            ).strip()
            content_vars = metadata.get("content_variables")
            send_result = provider.send(
                to_phone=contact.phone or "",
                body=message.body,
                content_sid=content_sid,
                content_variables=content_vars if isinstance(content_vars, dict) else None,
                from_phone=connection.sender_identity,
            )
        else:
            raise ProviderDispatchError(
                f"Incompatible provider instance for channel '{message.channel}'.",
                is_transient=False,
            )
    except ProviderDispatchError as exc:
        if exc.is_transient and self.request.retries < self.max_retries:
            backoff = min(300, 15 * (2 ** self.request.retries))
            raise self.retry(exc=exc, countdown=backoff)

        with transaction.atomic():
            msg_locked = Message.objects.select_for_update().filter(pk=message_id).first()
            if msg_locked is not None:
                _mark_message_failed(
                    tenant_id=tenant_id,
                    message=msg_locked,
                    error_text=exc.message,
                )

            admin_ids = list(
                Membership.objects.filter(
                    role__in=[Membership.Role.OWNER, Membership.Role.ADMIN],
                    is_active=True,
                ).values_list("user_id", flat=True)
            )
            if admin_ids:
                notify(
                    tenant=tenant_id,
                    users=admin_ids,
                    type=NotificationType.WORKFLOW_OR_INTEGRATION_ALERT,
                    payload={
                        "title": f"Message Delivery Failed ({message.channel.upper()})",
                        "body": exc.message,
                        "message_id": str(message_id),
                        "channel": message.channel,
                    },
                    link="/settings/integrations",
                )
        return {"status": "failed", "message_id": message_id, "error": exc.message}

    now = timezone.now()
    with transaction.atomic():
        msg_locked = Message.objects.select_for_update().filter(pk=message_id).first()
        if msg_locked is None:
            return {"status": "not_found", "message_id": message_id}

        msg_locked.provider = send_result.provider
        msg_locked.provider_message_id = send_result.provider_message_id
        msg_locked.status = (
            send_result.status
            if send_result.status in {c[0] for c in Message.Status.choices}
            else Message.Status.SENT
        )
        msg_locked.sent_at = now
        msg_locked.error = send_result.error or ""
        msg_locked.save(
            update_fields=[
                "provider",
                "provider_message_id",
                "status",
                "sent_at",
                "error",
                "updated_at",
            ]
        )

        publish(
            EventName.MESSAGE_STATUS_CHANGED,
            tenant_id=tenant_id,
            contact_id=msg_locked.contact_id,
            payload={
                "message_id": str(msg_locked.id),
                "channel": msg_locked.channel,
                "status": msg_locked.status,
                "provider": msg_locked.provider,
                "provider_message_id": msg_locked.provider_message_id,
                "broadcast_id": str(msg_locked.broadcast_id) if msg_locked.broadcast_id else None,
            },
        )

    return {
        "status": msg_locked.status,
        "message_id": str(msg_locked.id),
        "provider_message_id": msg_locked.provider_message_id,
    }


@shared_task(
    base=TenantAwareTask,
    bind=True,
    name="messaging.tasks.send_broadcast_batch_task",
)
def send_broadcast_batch_task(
    self,
    tenant_id: str,
    broadcast_id: str,
    batch_index: int,
    contact_ids: list[str],
    is_last_batch: bool = False,
) -> dict[str, Any]:
    broadcast = (
        Broadcast.objects.select_related("template", "created_by")
        .filter(pk=broadcast_id)
        .first()
    )
    if broadcast is None or broadcast.status == Broadcast.Status.CANCELLED:
        return {"status": "cancelled_or_missing", "broadcast_id": broadcast_id}

    contacts_by_id = {
        str(c.id): c
        for c in Contact.objects.filter(pk__in=contact_ids)
    }

    sent_delta = 0
    skipped_delta = 0
    failed_delta = 0

    for cid in contact_ids:
        broadcast.refresh_from_db(fields=["status"])
        if broadcast.status == Broadcast.Status.CANCELLED:
            break

        contact = contacts_by_id.get(str(cid))
        if contact is None:
            skipped_delta += 1
            continue

        idemp_key = f"broadcast:{broadcast.id}:contact:{contact.id}"
        if Message.objects.filter(idempotency_key=idemp_key).exists():
            continue

        try:
            MessagingService.send(
                contact=contact,
                channel=broadcast.template.channel,
                template=broadcast.template,
                source=Message.Source.BROADCAST,
                sent_by=broadcast.created_by,
                broadcast=broadcast,
                idempotency_key=idemp_key,
                metadata={"broadcast_batch_index": batch_index},
            )
            sent_delta += 1
        except ApplicationError as exc:
            if exc.code in (
                "contact_opted_out",
                "contact_email_bounced",
                "missing_recipient_email",
                "missing_recipient_phone",
            ):
                skipped_delta += 1
            else:
                failed_delta += 1

    with transaction.atomic():
        locked = Broadcast.objects.select_for_update().filter(pk=broadcast_id).first()
        if locked is not None:
            locked.sent_count = F("sent_count") + sent_delta
            locked.skipped_count = F("skipped_count") + skipped_delta
            locked.failed_count = F("failed_count") + failed_delta
            update_fields = ["sent_count", "skipped_count", "failed_count", "updated_at"]
            if is_last_batch and locked.status == Broadcast.Status.RUNNING:
                locked.status = Broadcast.Status.COMPLETED
                locked.completed_at = timezone.now()
                update_fields.extend(["status", "completed_at"])
            locked.save(update_fields=update_fields)

    return {
        "broadcast_id": broadcast_id,
        "batch_index": batch_index,
        "sent": sent_delta,
        "skipped": skipped_delta,
        "failed": failed_delta,
    }


@shared_task(
    base=TenantAwareTask,
    bind=True,
    name="messaging.tasks.execute_broadcast_task",
)
def execute_broadcast_task(self, tenant_id: str, broadcast_id: str) -> dict[str, Any]:
    broadcast = (
        Broadcast.objects.select_related("template")
        .filter(pk=broadcast_id)
        .first()
    )
    if broadcast is None or broadcast.status == Broadcast.Status.CANCELLED:
        return {"status": "cancelled_or_missing", "broadcast_id": broadcast_id}

    audience_qs = get_broadcast_audience_queryset(
        broadcast.audience_filter,
        broadcast.template.channel,
    )
    contact_ids = [str(pk) for pk in audience_qs.values_list("id", flat=True)]
    total = len(contact_ids)

    Broadcast.objects.filter(pk=broadcast.id).update(total_recipients=total)

    if total == 0:
        Broadcast.objects.filter(
            pk=broadcast.id,
            status=Broadcast.Status.RUNNING,
        ).update(
            status=Broadcast.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        return {"status": "completed_empty", "broadcast_id": broadcast_id, "total_recipients": 0}

    batches = [
        contact_ids[i : i + BROADCAST_BATCH_SIZE]
        for i in range(0, total, BROADCAST_BATCH_SIZE)
    ]

    for idx, batch_cids in enumerate(batches):
        is_last = idx == (len(batches) - 1)
        try:
            send_broadcast_batch_task.apply_async(
                args=[str(tenant_id), str(broadcast.id), idx, batch_cids, is_last],
                queue="messaging",
            )
        except Exception:
            send_broadcast_batch_task(
                str(tenant_id),
                str(broadcast.id),
                idx,
                batch_cids,
                is_last,
            )

    return {
        "status": "dispatched",
        "broadcast_id": broadcast_id,
        "total_recipients": total,
        "batches": len(batches),
    }


@shared_task(name="messaging.tasks.claim_due_broadcasts_beat_task")
def claim_due_broadcasts_beat_task() -> dict[str, int]:
    now = timezone.now()
    tenants = list(Tenant.objects.filter(is_active=True).only("id"))
    claimed_total = 0

    for tenant in tenants:
        with tenant_context(tenant.id):
            with transaction.atomic():
                due_broadcasts = list(
                    Broadcast.objects.select_for_update(skip_locked=True).filter(
                        status=Broadcast.Status.SCHEDULED,
                        scheduled_at__lte=now,
                    )
                )
                for bcast in due_broadcasts:
                    bcast.status = Broadcast.Status.RUNNING
                    bcast.started_at = now
                    bcast.save(update_fields=["status", "started_at", "updated_at"])
                    claimed_total += 1
                    tid_str = str(tenant.id)
                    bid_str = str(bcast.id)
                    transaction.on_commit(
                        lambda t=tid_str, b=bid_str: _enqueue_broadcast_execution(t, b)
                    )

    return {"tenants_scanned": len(tenants), "broadcasts_claimed": claimed_total}


def _enqueue_broadcast_execution(tenant_id: str, broadcast_id: str) -> None:
    try:
        execute_broadcast_task.apply_async(
            args=[tenant_id, broadcast_id],
            queue="messaging",
        )
    except Exception:
        execute_broadcast_task(tenant_id, broadcast_id)
