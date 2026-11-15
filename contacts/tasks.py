from datetime import timedelta
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from contacts.models import Activity, Contact, Task
from core.context import TenantAwareTask, require_tenant
from core.events import EventName, publish
from core.models import Tenant
from notifications.models import NotificationType
from notifications.services import notify


@shared_task(base=TenantAwareTask, bind=True, name="contacts.tasks.detect_tenant_inactive_leads_task")
def detect_tenant_inactive_leads_task(self, tenant_id: str, inactivity_days: int | None = None) -> int:
    tenant = require_tenant()
    days = (
        inactivity_days
        if inactivity_days is not None
        else int((tenant.settings or {}).get("inactivity_days", 7))
    )
    if days <= 0:
        return 0

    now = timezone.now()
    cutoff = now - timedelta(days=days)
    count = 0

    with transaction.atomic():
        contacts = list(
            Contact.objects.select_for_update().filter(
                last_activity_at__lte=cutoff,
                inactive_notified_at__isnull=True,
            )
        )
        for contact in contacts:
            contact.inactive_notified_at = now
            contact.save(update_fields=["inactive_notified_at", "updated_at"])
            Activity.objects.create(
                contact=contact,
                activity_type=Activity.ActivityType.EVENT,
                description=f"Contact became inactive after {days} days without interaction.",
                metadata={
                    "inactivity_days": days,
                    "last_activity_at": contact.last_activity_at.isoformat(),
                },
            )
            publish(
                EventName.LEAD_INACTIVE,
                tenant_id=tenant_id,
                contact_id=contact.id,
                payload={
                    "inactivity_days": days,
                    "last_activity_at": contact.last_activity_at.isoformat(),
                },
            )
            count += 1
    return count


@shared_task(name="contacts.tasks.detect_inactive_leads_beat_task")
def detect_inactive_leads_beat_task() -> dict[str, int]:
    tenants = list(Tenant.objects.filter(is_active=True))
    processed = 0
    for tenant in tenants:
        tid = str(tenant.id)
        try:
            detect_tenant_inactive_leads_task.delay(tid)
        except Exception:
            detect_tenant_inactive_leads_task(tid)
        processed += 1
    return {"tenants_processed": processed}


@shared_task(base=TenantAwareTask, bind=True, name="contacts.tasks.scan_tenant_due_soon_tasks_task")
def scan_tenant_due_soon_tasks_task(self, tenant_id: str, window_minutes: int | None = None) -> int:
    tenant = require_tenant()
    minutes = (
        window_minutes
        if window_minutes is not None
        else int((tenant.settings or {}).get("task_due_soon_minutes", 30))
    )
    now = timezone.now()
    cutoff = now + timedelta(minutes=minutes)
    notified_count = 0

    with transaction.atomic():
        tasks = list(
            Task.objects.select_for_update()
            .select_related("assignee", "assignee__user", "contact")
            .filter(
                status__in=[Task.Status.OPEN, Task.Status.IN_PROGRESS],
                assignee__isnull=False,
                due_at__isnull=False,
                due_at__lte=cutoff,
                due_soon_notified_at__isnull=True,
            )
        )
        for task in tasks:
            task.due_soon_notified_at = now
            task.save(update_fields=["due_soon_notified_at", "updated_at"])
            notify(
                tenant=tenant_id,
                users=[task.assignee.user],
                type=NotificationType.INTERNAL_TASK,
                payload={
                    "title": f"Task Due Soon: {task.title}",
                    "body": task.description or f"Task '{task.title}' is approaching its due time.",
                    "task_id": str(task.id),
                    "contact_id": str(task.contact_id) if task.contact_id else None,
                    "due_at": task.due_at.isoformat(),
                },
                link=f"/contacts/{task.contact_id}" if task.contact_id else "/tasks",
            )
            notified_count += 1
    return notified_count


@shared_task(name="contacts.tasks.scan_due_soon_tasks_beat_task")
def scan_due_soon_tasks_beat_task() -> dict[str, int]:
    tenants = list(Tenant.objects.filter(is_active=True))
    processed = 0
    for tenant in tenants:
        tid = str(tenant.id)
        try:
            scan_tenant_due_soon_tasks_task.delay(tid)
        except Exception:
            scan_tenant_due_soon_tasks_task(tid)
        processed += 1
    return {"tenants_processed": processed}
