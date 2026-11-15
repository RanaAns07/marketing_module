from typing import Any
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from accounts.models import Membership
from core.context import TenantAwareTask
from core.events import EventName
from notifications.models import Notification, NotificationType
from notifications.services import notify


@shared_task(base=TenantAwareTask, bind=True, name="notifications.tasks.send_notification_emails_task")
def send_notification_emails_task(self, tenant_id: str, notification_ids: list[str]) -> int:
    notifications = list(
        Notification.objects.select_related("recipient").filter(
            id__in=notification_ids,
            email_sent=False,
        )
    )
    sent_count = 0
    for item in notifications:
        if not item.recipient.email:
            continue
        try:
            send_mail(
                subject=item.title,
                message=item.body or item.title,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[item.recipient.email],
                fail_silently=True,
            )
            item.email_sent = True
            item.save(update_fields=["email_sent", "updated_at"])
            sent_count += 1
        except Exception:
            continue
    return sent_count


@shared_task(base=TenantAwareTask, bind=True, name="notifications.tasks.handle_domain_event")
def handle_domain_event(self, tenant_id: str, event: dict[str, Any]) -> dict[str, Any]:
    event_name = event.get("name")
    payload = event.get("payload") or {}
    contact_id = event.get("contact_id")

    if event_name == EventName.LEAD_CREATED:
        recipients = []
        assigned_user_id = payload.get("assigned_user_id")
        if assigned_user_id:
            recipients.append(assigned_user_id)
        admin_members = Membership.objects.filter(
            role__in=[Membership.Role.OWNER, Membership.Role.ADMIN],
            is_active=True,
        )
        if not recipients or getattr(admin_members.first(), "tenant", None) and admin_members.first().tenant.notify_admins_on_new_lead:
            recipients.extend([m.user_id for m in admin_members])
        if recipients:
            notify(
                tenant=tenant_id,
                users=recipients,
                type=NotificationType.NEW_LEAD,
                payload=payload,
                link=f"/contacts/{contact_id}" if contact_id else "/contacts",
            )

    elif event_name in (EventName.EMAIL_RECEIVED, EventName.SMS_RECEIVED, EventName.WHATSAPP_RECEIVED):
        assigned_user_id = payload.get("assigned_user_id")
        if assigned_user_id:
            recipients = [assigned_user_id]
        else:
            recipients = list(
                Membership.objects.filter(is_active=True).values_list("user_id", flat=True)
            )
        conversation_id = payload.get("conversation_id")
        notify(
            tenant=tenant_id,
            users=recipients,
            type=NotificationType.INCOMING_MESSAGE,
            payload=payload,
            link=f"/inbox/{conversation_id}" if conversation_id else "/inbox",
        )

    elif event_name == EventName.ASSIGNMENT_CHANGED:
        new_user_id = payload.get("assigned_user_id")
        previous_user_id = payload.get("previous_assigned_user_id")
        if new_user_id:
            notif_type = (
                NotificationType.CONVERSATION_REASSIGNED
                if previous_user_id
                else NotificationType.CONVERSATION_ASSIGNED
            )
            conversation_id = payload.get("conversation_id")
            link = (
                f"/inbox/{conversation_id}"
                if conversation_id
                else (f"/contacts/{contact_id}" if contact_id else "/inbox")
            )
            notify(
                tenant=tenant_id,
                users=[new_user_id],
                type=notif_type,
                payload=payload,
                link=link,
            )

    return {"subscriber": "notifications", "event_id": event.get("event_id")}
