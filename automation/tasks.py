from datetime import timedelta
from typing import Any
from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from automation.models import Workflow, WorkflowEnrollment
from automation.services import (
    STALE_LOCK_SECONDS,
    execute_single_enrollment,
    process_domain_event_for_automation,
)
from core.context import TenantAwareTask


@shared_task(base=TenantAwareTask, bind=True, name="automation.tasks.handle_domain_event")
def handle_domain_event(self, tenant_id: str, event: dict[str, Any]) -> dict[str, Any]:
    result = process_domain_event_for_automation(event)
    return {
        "subscriber": "automation",
        "event_id": event.get("event_id"),
        "tenant_id": tenant_id,
        **result,
    }


@shared_task(base=TenantAwareTask, bind=True, name="automation.tasks.execute_enrollment_task")
def execute_enrollment_task(self, tenant_id: str, enrollment_id: str) -> dict[str, Any]:
    return execute_single_enrollment(enrollment_id)


@shared_task(
    base=TenantAwareTask,
    bind=True,
    name="automation.tasks.process_due_enrollments_batch_task",
)
def process_due_enrollments_batch_task(
    self,
    tenant_id: str,
    batch_size: int = 100,
) -> dict[str, Any]:
    now = timezone.now()
    stale_cutoff = now - timedelta(seconds=STALE_LOCK_SECONDS)

    with transaction.atomic():
        stale_qs = WorkflowEnrollment.objects.select_for_update(skip_locked=True).filter(
            locked_at__isnull=False,
            locked_at__lt=stale_cutoff,
        )
        stale_ids = [row.id for row in stale_qs]
        if stale_ids:
            WorkflowEnrollment.objects.filter(id__in=stale_ids).update(
                locked_at=None,
                updated_at=now,
            )

    claimed_ids: list[str] = []
    with transaction.atomic():
        due_qs = (
            WorkflowEnrollment.objects.select_for_update(skip_locked=True)
            .filter(
                status=WorkflowEnrollment.Status.ACTIVE,
                workflow__status=Workflow.Status.ACTIVE,
                locked_at__isnull=True,
                next_run_at__lte=now,
            )
            .order_by("next_run_at", "id")[:batch_size]
        )
        claimed = list(due_qs)
        if claimed:
            ids = [enr.id for enr in claimed]
            WorkflowEnrollment.objects.filter(id__in=ids).update(
                locked_at=now,
                updated_at=now,
            )
            claimed_ids = [str(pk) for pk in ids]

    processed = 0
    for enr_id in claimed_ids:
        execute_single_enrollment(enr_id)
        processed += 1

    return {
        "tenant_id": tenant_id,
        "stale_recovered": len(stale_ids),
        "claimed": len(claimed_ids),
        "processed": processed,
    }


@shared_task(bind=True, name="automation.tasks.dispatch_due_enrollments_beat_task")
def dispatch_due_enrollments_beat_task(self) -> dict[str, Any]:
    now = timezone.now()
    stale_cutoff = now - timedelta(seconds=STALE_LOCK_SECONDS)

    tenant_ids = list(
        WorkflowEnrollment.unscoped_objects.filter(
            tenant__is_active=True,
            status=WorkflowEnrollment.Status.ACTIVE,
            workflow__status=Workflow.Status.ACTIVE,
        )
        .filter(
            Q(locked_at__isnull=True, next_run_at__lte=now)
            | Q(locked_at__isnull=False, locked_at__lt=stale_cutoff)
        )
        .values_list("tenant_id", flat=True)
        .distinct()
    )

    dispatched = 0
    for tid in tenant_ids:
        tid_str = str(tid)
        try:
            process_due_enrollments_batch_task.apply_async(
                args=[tid_str],
                queue="automation",
            )
        except Exception:
            process_due_enrollments_batch_task(tid_str)
        dispatched += 1

    return {"tenants_dispatched": dispatched}
