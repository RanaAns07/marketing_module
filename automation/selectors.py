from datetime import datetime
from typing import Iterable
import uuid
from django.db.models import Prefetch, Q, QuerySet
from automation.models import StepExecution, Workflow, WorkflowEnrollment, WorkflowStep
from contacts.models import Contact
from messaging.models import Message


def get_workflows_queryset(
    *,
    status_filter: str | None = None,
    trigger_type: str | None = None,
    search: str | None = None,
) -> QuerySet[Workflow]:
    steps_prefetch = Prefetch(
        "steps",
        queryset=WorkflowStep.objects.order_by("created_at", "key"),
    )
    qs = Workflow.objects.select_related("tenant").prefetch_related(steps_prefetch).order_by("-created_at", "-id")
    if status_filter:
        qs = qs.filter(status=status_filter.strip().lower())
    if trigger_type:
        qs = qs.filter(trigger_type=trigger_type.strip().lower())
    if search:
        term = search.strip()
        qs = qs.filter(Q(name__icontains=term) | Q(description__icontains=term))
    return qs


def get_workflow_by_id(workflow_id: uuid.UUID | str) -> Workflow | None:
    return get_workflows_queryset().filter(pk=workflow_id).first()


def get_active_workflows_for_triggers(trigger_types: Iterable[str]) -> QuerySet[Workflow]:
    steps_prefetch = Prefetch(
        "steps",
        queryset=WorkflowStep.objects.order_by("created_at", "key"),
    )
    return (
        Workflow.objects.select_related("tenant")
        .prefetch_related(steps_prefetch)
        .filter(
            status=Workflow.Status.ACTIVE,
            trigger_type__in=list(trigger_types),
        )
        .order_by("created_at", "id")
    )


def get_workflow_enrollments_queryset(
    *,
    workflow: Workflow | uuid.UUID | str | None = None,
    contact: Contact | uuid.UUID | str | None = None,
    status_filter: str | None = None,
) -> QuerySet[WorkflowEnrollment]:
    qs = WorkflowEnrollment.objects.select_related(
        "workflow",
        "contact",
        "contact__lead_status",
        "contact__assigned_to",
        "tenant",
    ).order_by("-created_at", "-id")
    if workflow is not None:
        wf_id = workflow.pk if isinstance(workflow, Workflow) else workflow
        qs = qs.filter(workflow_id=wf_id)
    if contact is not None:
        ct_id = contact.pk if isinstance(contact, Contact) else contact
        qs = qs.filter(contact_id=ct_id)
    if status_filter:
        statuses = [s.strip().lower() for s in status_filter.split(",") if s.strip()]
        if len(statuses) == 1:
            qs = qs.filter(status=statuses[0])
        elif statuses:
            qs = qs.filter(status__in=statuses)
    return qs


def get_step_executions_queryset(
    *,
    workflow: Workflow | uuid.UUID | str | None = None,
    contact: Contact | uuid.UUID | str | None = None,
    enrollment: WorkflowEnrollment | uuid.UUID | str | None = None,
    status_filter: str | None = None,
) -> QuerySet[StepExecution]:
    qs = StepExecution.objects.select_related(
        "enrollment",
        "enrollment__workflow",
        "enrollment__contact",
        "tenant",
    ).order_by("-created_at", "-id")
    if enrollment is not None:
        enr_id = enrollment.pk if isinstance(enrollment, WorkflowEnrollment) else enrollment
        qs = qs.filter(enrollment_id=enr_id)
    if workflow is not None:
        wf_id = workflow.pk if isinstance(workflow, Workflow) else workflow
        qs = qs.filter(enrollment__workflow_id=wf_id)
    if contact is not None:
        ct_id = contact.pk if isinstance(contact, Contact) else contact
        qs = qs.filter(enrollment__contact_id=ct_id)
    if status_filter:
        qs = qs.filter(status=status_filter.strip().lower())
    return qs


def has_contact_replied_since(
    *,
    contact: Contact | uuid.UUID | str,
    since_dt: datetime,
    channel: str | None = None,
) -> bool:
    ct_id = contact.pk if isinstance(contact, Contact) else contact
    qs = Message.objects.filter(
        contact_id=ct_id,
        direction=Message.Direction.IN,
        created_at__gte=since_dt,
    )
    if channel:
        qs = qs.filter(channel=channel.strip().lower())
    return qs.exists()
