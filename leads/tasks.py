from datetime import timedelta
from typing import Any
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from accounts.models import Membership
from core.context import TenantAwareTask, tenant_context
from core.exceptions import ApplicationError
from integrations.providers.registry import (
    ProviderDispatchError,
    get_meta_provider,
)
from leads.models import MetaConnection, MetaLeadEvent
from leads.services import ingest_fetched_meta_lead
from notifications.models import NotificationType
from notifications.services import notify


def _notify_tenant_owners_and_admins(
    tenant_id: str,
    *,
    title: str,
    body: str,
    extra_payload: dict[str, Any] | None = None,
) -> None:
    admin_ids = list(
        Membership.objects.filter(
            role__in=[Membership.Role.OWNER, Membership.Role.ADMIN],
            is_active=True,
        ).values_list("user_id", flat=True)
    )
    if not admin_ids:
        return
    payload = {
        "title": title,
        "body": body,
        **(extra_payload or {}),
    }
    notify(
        tenant=tenant_id,
        users=admin_ids,
        type=NotificationType.WORKFLOW_OR_INTEGRATION_ALERT,
        payload=payload,
        link="/settings/integrations/meta",
    )


def _mark_lead_event_failed(
    *,
    tenant_id: str,
    event_id: str,
    error_message: str,
) -> None:
    with transaction.atomic():
        locked = MetaLeadEvent.objects.select_for_update().filter(pk=event_id).first()
        if locked is None:
            return
        locked.status = MetaLeadEvent.Status.FAILED
        locked.error = error_message[:1000]
        locked.save(update_fields=["status", "error", "updated_at"])

    _notify_tenant_owners_and_admins(
        tenant_id,
        title="Meta Lead Ads Sync Failed",
        body=f"Failed to import lead {locked.leadgen_id}: {error_message}",
        extra_payload={
            "lead_event_id": str(event_id),
            "leadgen_id": locked.leadgen_id,
        },
    )


@shared_task(
    base=TenantAwareTask,
    bind=True,
    max_retries=3,
    name="leads.tasks.process_meta_lead_event_task",
)
def process_meta_lead_event_task(self, tenant_id: str, lead_event_id: str) -> dict[str, Any]:
    with transaction.atomic():
        lead_event = (
            MetaLeadEvent.objects.select_for_update(of=("self",))
            .select_related("page", "page__connection", "form")
            .filter(pk=lead_event_id)
            .first()
        )
        if lead_event is None:
            return {"status": "not_found", "lead_event_id": lead_event_id}
        if lead_event.status in (MetaLeadEvent.Status.PROCESSED, MetaLeadEvent.Status.IGNORED):
            return {"status": lead_event.status, "lead_event_id": lead_event_id}

        if lead_event.page is not None and not lead_event.page.selected:
            lead_event.status = MetaLeadEvent.Status.IGNORED
            lead_event.processed_at = timezone.now()
            lead_event.save(update_fields=["status", "processed_at", "updated_at"])
            return {"status": "ignored", "lead_event_id": lead_event_id}

        if lead_event.form is not None and not lead_event.form.selected:
            lead_event.status = MetaLeadEvent.Status.IGNORED
            lead_event.processed_at = timezone.now()
            lead_event.save(update_fields=["status", "processed_at", "updated_at"])
            return {"status": "ignored", "lead_event_id": lead_event_id}

        lead_event.attempts += 1
        lead_event.save(update_fields=["attempts", "updated_at"])

    access_token = ""
    if lead_event.page and lead_event.page.page_access_token:
        access_token = lead_event.page.page_access_token
    elif lead_event.page and lead_event.page.connection:
        access_token = lead_event.page.connection.user_access_token
    else:
        conn = MetaConnection.objects.first()
        if conn:
            access_token = conn.user_access_token

    if not access_token:
        _mark_lead_event_failed(
            tenant_id=tenant_id,
            event_id=lead_event_id,
            error_message="Missing Meta access token to retrieve lead.",
        )
        return {"status": "failed", "lead_event_id": lead_event_id}

    provider = get_meta_provider()
    try:
        lead_dto = provider.fetch_lead(lead_event.leadgen_id, access_token)
    except ProviderDispatchError as exc:
        with transaction.atomic():
            locked = MetaLeadEvent.objects.select_for_update().filter(pk=lead_event_id).first()
            if locked is not None:
                locked.error = exc.message[:1000]
                locked.save(update_fields=["error", "updated_at"])

        if self.request.retries < self.max_retries:
            backoff = min(300, 15 * (2 ** self.request.retries))
            raise self.retry(exc=exc, countdown=backoff)

        _mark_lead_event_failed(
            tenant_id=tenant_id,
            event_id=lead_event_id,
            error_message=exc.message,
        )
        return {"status": "failed", "lead_event_id": lead_event_id, "error": exc.message}

    try:
        with transaction.atomic():
            locked_event = (
                MetaLeadEvent.objects.select_for_update(of=("self",))
                .select_related("page", "form")
                .filter(pk=lead_event_id)
                .first()
            )
            if locked_event is None:
                return {"status": "not_found", "lead_event_id": lead_event_id}
            contact = ingest_fetched_meta_lead(
                lead_event=locked_event,
                lead_dto=lead_dto,
            )
    except ApplicationError as exc:
        _mark_lead_event_failed(
            tenant_id=tenant_id,
            event_id=lead_event_id,
            error_message=str(exc.detail),
        )
        return {"status": "failed", "lead_event_id": lead_event_id, "error": str(exc.detail)}
    except Exception as exc:
        if self.request.retries < self.max_retries:
            backoff = min(300, 15 * (2 ** self.request.retries))
            raise self.retry(exc=exc, countdown=backoff)
        _mark_lead_event_failed(
            tenant_id=tenant_id,
            event_id=lead_event_id,
            error_message=str(exc),
        )
        return {"status": "failed", "lead_event_id": lead_event_id, "error": str(exc)}

    return {
        "status": locked_event.status,
        "lead_event_id": str(locked_event.id),
        "contact_id": str(contact.id) if contact else None,
    }


@shared_task(name="leads.tasks.check_meta_connections_health_beat_task")
def check_meta_connections_health_beat_task() -> dict[str, int]:
    now = timezone.now()
    expiry_threshold = now + timedelta(days=7)
    provider = get_meta_provider()

    connections = list(
        MetaConnection.unscoped_objects.select_related("tenant").filter(
            tenant__is_active=True,
        ).exclude(status=MetaConnection.Status.DISCONNECTED)
    )

    checked_count = 0
    expired_count = 0

    for conn in connections:
        checked_count += 1
        tid_str = str(conn.tenant_id)
        with tenant_context(conn.tenant_id):
            with transaction.atomic():
                locked = MetaConnection.objects.select_for_update().filter(pk=conn.pk).first()
                if locked is None or locked.status == MetaConnection.Status.DISCONNECTED:
                    continue

                new_status = MetaConnection.Status.CONNECTED
                error_msg = ""

                if not locked.user_access_token:
                    new_status = MetaConnection.Status.EXPIRED
                    error_msg = "Meta access token is missing."
                else:
                    try:
                        token_info = provider.debug_token(locked.user_access_token)
                        if token_info.expires_at is not None:
                            locked.token_expires_at = token_info.expires_at
                        if not token_info.is_valid:
                            new_status = MetaConnection.Status.EXPIRED
                            error_msg = "Meta access token is no longer valid."
                        elif (
                            locked.token_expires_at is not None
                            and locked.token_expires_at <= expiry_threshold
                        ):
                            new_status = MetaConnection.Status.EXPIRED
                            error_msg = "Meta access token is expiring within 7 days."
                    except ProviderDispatchError as exc:
                        new_status = MetaConnection.Status.ERROR
                        error_msg = exc.message

                locked.status = new_status
                locked.last_checked_at = now
                locked.last_error = error_msg

                should_notify = False
                if new_status in (MetaConnection.Status.EXPIRED, MetaConnection.Status.ERROR):
                    expired_count += 1
                    if locked.alert_notified_status != new_status:
                        should_notify = True
                        locked.alert_notified_status = new_status
                elif new_status == MetaConnection.Status.CONNECTED:
                    locked.alert_notified_status = ""

                locked.save(
                    update_fields=[
                        "token_expires_at",
                        "status",
                        "last_checked_at",
                        "last_error",
                        "alert_notified_status",
                        "updated_at",
                    ]
                )

                if should_notify:
                    _notify_tenant_owners_and_admins(
                        tid_str,
                        title="Meta Account Reconnection Required",
                        body=error_msg or "Your Meta Lead Ads connection requires reconnection.",
                        extra_payload={
                            "connection_id": str(locked.id),
                            "status": new_status,
                        },
                    )

    return {
        "connections_checked": checked_count,
        "connections_flagged": expired_count,
    }
