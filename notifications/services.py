from collections.abc import Iterable
from typing import Any
import uuid
from django.db import transaction
from django.utils import timezone
from accounts.models import Membership, User
from core.context import require_tenant_id, tenant_context
from notifications.models import Notification, NotificationType

DEFAULT_TITLES = {
    NotificationType.NEW_LEAD: "New Lead Captured",
    NotificationType.INCOMING_MESSAGE: "New Incoming Customer Reply",
    NotificationType.CONVERSATION_ASSIGNED: "Conversation Assigned to You",
    NotificationType.CONVERSATION_REASSIGNED: "Conversation Transferred to You",
    NotificationType.INTERNAL_TASK: "Follow-Up Task Reminder",
    NotificationType.WORKFLOW_INTEGRATION_ALERT: "Workflow or Integration Alert",
}


def _extract_user_ids(users: Any) -> list[uuid.UUID]:
    if users is None:
        return []
    if isinstance(users, (User, Membership, uuid.UUID, str)):
        items: Iterable[Any] = [users]
    else:
        items = users

    user_ids: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for item in items:
        uid: uuid.UUID | None = None
        if isinstance(item, User):
            uid = item.pk
        elif isinstance(item, Membership):
            uid = item.user_id
        elif isinstance(item, uuid.UUID):
            uid = item
        elif isinstance(item, str):
            uid = uuid.UUID(item)
        if uid is not None and uid not in seen:
            seen.add(uid)
            user_ids.append(uid)
    return user_ids


def _should_send_email(membership: Membership, notification_type: str) -> bool:
    if not membership.user.email_notifications_enabled:
        return False
    if not membership.email_notifications_enabled:
        return False
    prefs = membership.notification_preferences or {}
    type_pref = prefs.get(notification_type)
    if isinstance(type_pref, bool):
        return type_pref
    if isinstance(type_pref, dict):
        return bool(type_pref.get("email", True))
    return True


def notify(
    tenant: Any,
    users: Any,
    type: str,
    payload: dict[str, Any] | None = None,
    link: str = "",
) -> list[Notification]:
    if type not in NotificationType.values:
        raise ValueError(f"Unsupported notification type: {type}")

    user_ids = _extract_user_ids(users)
    if not user_ids:
        return []

    normalized_payload = dict(payload or {})
    title = str(normalized_payload.get("title") or DEFAULT_TITLES.get(type, "Notification"))
    body = str(normalized_payload.get("body") or normalized_payload.get("message") or "")

    with tenant_context(tenant):
        tenant_id = require_tenant_id()
        with transaction.atomic():
            memberships = list(
                Membership.objects.select_related("user", "tenant").filter(
                    user_id__in=user_ids,
                    is_active=True,
                    user__is_active=True,
                )
            )
            if not memberships:
                return []

            to_create: list[Notification] = []
            email_recipient_user_ids: set[uuid.UUID] = set()

            for membership in memberships:
                to_create.append(
                    Notification(
                        tenant_id=tenant_id,
                        recipient=membership.user,
                        notification_type=type,
                        title=title,
                        body=body,
                        payload=normalized_payload,
                        link=link or "",
                    )
                )
                if _should_send_email(membership, type):
                    email_recipient_user_ids.add(membership.user_id)

            created = Notification.objects.bulk_create(to_create)
            email_notif_ids = [
                str(n.id) for n in created if n.recipient_id in email_recipient_user_ids
            ]

            if email_notif_ids:
                def _enqueue_emails():
                    from notifications.tasks import send_notification_emails_task
                    try:
                        send_notification_emails_task.delay(str(tenant_id), email_notif_ids)
                    except Exception:
                        send_notification_emails_task(str(tenant_id), email_notif_ids)

                transaction.on_commit(_enqueue_emails)

            return created


@transaction.atomic
def mark_notification_read(*, notification: Notification) -> Notification:
    if not notification.is_read:
        notification.is_read = True
        notification.read_at = timezone.now()
        notification.save(update_fields=["is_read", "read_at", "updated_at"])
    return notification


@transaction.atomic
def mark_all_notifications_read(*, user: User) -> int:
    now = timezone.now()
    return Notification.objects.filter(recipient=user, is_read=False).update(
        is_read=True,
        read_at=now,
        updated_at=now,
    )


@transaction.atomic
def update_notification_preferences(
    *,
    membership: Membership,
    email_notifications_enabled: bool | None = None,
    notification_preferences: dict[str, Any] | None = None,
) -> Membership:
    update_fields = ["updated_at"]
    if email_notifications_enabled is not None:
        membership.email_notifications_enabled = email_notifications_enabled
        update_fields.append("email_notifications_enabled")
        membership.user.email_notifications_enabled = email_notifications_enabled
        membership.user.save(update_fields=["email_notifications_enabled", "updated_at"])
    if notification_preferences is not None:
        merged = dict(membership.notification_preferences or {})
        for key, val in notification_preferences.items():
            if key in NotificationType.values:
                merged[key] = val
        membership.notification_preferences = merged
        update_fields.append("notification_preferences")
    membership.save(update_fields=update_fields)
    return membership
