from collections.abc import Iterable, Mapping
from typing import Any
import uuid
from django.db.models import Q, QuerySet
from django.utils.dateparse import parse_date, parse_datetime
from accounts.models import Membership
from contacts.models import (
    Activity,
    Appointment,
    Contact,
    CustomFieldDefinition,
    LeadStatus,
    Tag,
    Task,
)


def _parse_UUID_or_none(val: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(val).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _split_values(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(raw, Iterable):
        result: list[str] = []
        for item in raw:
            if isinstance(item, str) and "," in item:
                result.extend([p.strip() for p in item.split(",") if p.strip()])
            elif item is not None and str(item).strip():
                result.append(str(item).strip())
        return result
    return [str(raw).strip()]


def _parse_dt_or_date(raw: Any, end_of_day: bool = False):
    if not raw:
        return None
    if not isinstance(raw, str):
        return raw
    dt = parse_datetime(raw)
    if dt is not None:
        return dt
    d = parse_date(raw)
    if d is not None:
        from datetime import datetime, time, timezone
        t = time(23, 59, 59, 999999) if end_of_day else time(0, 0, 0)
        return datetime.combine(d, t, tzinfo=timezone.utc)
    return None


def get_base_contacts_queryset() -> QuerySet[Contact]:
    return Contact.objects.select_related(
        "lead_status",
        "assigned_to",
        "assigned_to__user",
        "tenant",
    ).prefetch_related("tags")


def apply_marketer_visibility(
    queryset: QuerySet[Contact],
    membership: Membership | None,
) -> QuerySet[Contact]:
    if membership is None:
        return queryset
    if (
        membership.role == Membership.Role.MARKETER
        and not membership.tenant.marketer_can_view_all_contacts
    ):
        return queryset.filter(assigned_to=membership)
    return queryset


def _build_tag_q(values: list[str]) -> Q:
    uuids = []
    names = []
    for val in values:
        parsed = _parse_UUID_or_none(val)
        if parsed is not None:
            uuids.append(parsed)
        else:
            names.append(val)
    q = Q()
    if uuids:
        q |= Q(tags__id__in=uuids)
    if names:
        q |= Q(tags__name__in=names)
    return q


def _build_status_q(values: list[str]) -> Q:
    uuids = []
    names = []
    for val in values:
        parsed = _parse_UUID_or_none(val)
        if parsed is not None:
            uuids.append(parsed)
        else:
            names.append(val)
    q = Q()
    if uuids:
        q |= Q(lead_status_id__in=uuids)
    if names:
        name_q = Q()
        for name in names:
            name_q |= Q(lead_status__name__iexact=name)
        q |= name_q
    return q


def filter_contacts_queryset(
    membership: Membership | None = None,
    params: Mapping[str, Any] | None = None,
    queryset: QuerySet[Contact] | None = None,
) -> QuerySet[Contact]:
    qs = queryset if queryset is not None else get_base_contacts_queryset()
    qs = apply_marketer_visibility(qs, membership)

    if not params:
        return qs

    needs_distinct = False

    status_vals = _split_values(params.get("status") or params.get("lead_status"))
    if status_vals:
        qs = qs.filter(_build_status_q(status_vals))

    exclude_status_vals = _split_values(params.get("exclude_status"))
    if exclude_status_vals:
        qs = qs.exclude(_build_status_q(exclude_status_vals))

    tag_match = str(params.get("tag_match") or "any").lower()
    raw_tags = _split_values(params.get("tags") or params.get("tags_any"))
    raw_tags_all = _split_values(params.get("tags_all"))
    if tag_match in ("all", "and") and raw_tags and not raw_tags_all:
        raw_tags_all = raw_tags
        raw_tags = []

    if raw_tags:
        qs = qs.filter(_build_tag_q(raw_tags))
        needs_distinct = True

    if raw_tags_all:
        for val in raw_tags_all:
            parsed = _parse_UUID_or_none(val)
            if parsed is not None:
                qs = qs.filter(tags__id=parsed)
            else:
                qs = qs.filter(tags__name__iexact=val)
        needs_distinct = True

    exclude_tags = _split_values(params.get("exclude_tags") or params.get("tags_exclude"))
    if exclude_tags:
        qs = qs.exclude(_build_tag_q(exclude_tags))
        needs_distinct = True

    source_val = params.get("source") or params.get("source_channel")
    if source_val:
        qs = qs.filter(source_channel__iexact=str(source_val).strip())

    source_page = params.get("source_page")
    if source_page:
        qs = qs.filter(source_page__iexact=str(source_page).strip())

    source_form = params.get("source_form")
    if source_form:
        qs = qs.filter(source_form__iexact=str(source_form).strip())

    campaign_val = params.get("campaign")
    if campaign_val:
        qs = qs.filter(campaign__iexact=str(campaign_val).strip())

    assignee_val = params.get("assigned_to") or params.get("assignee")
    if assignee_val:
        assignee_str = str(assignee_val).strip()
        if assignee_str.lower() == "unassigned":
            qs = qs.filter(assigned_to__isnull=True)
        else:
            assignee_uuid = _parse_UUID_or_none(assignee_str)
            if assignee_uuid is not None:
                qs = qs.filter(
                    Q(assigned_to_id=assignee_uuid) | Q(assigned_to__user_id=assignee_uuid)
                )

    created_after = _parse_dt_or_date(params.get("created_after") or params.get("date_from"))
    if created_after is not None:
        qs = qs.filter(created_at__gte=created_after)

    created_before = _parse_dt_or_date(
        params.get("created_before") or params.get("date_to"),
        end_of_day=True,
    )
    if created_before is not None:
        qs = qs.filter(created_at__lte=created_before)

    acquired_after = _parse_dt_or_date(params.get("acquired_after"))
    if acquired_after is not None:
        qs = qs.filter(acquired_at__gte=acquired_after)

    acquired_before = _parse_dt_or_date(params.get("acquired_before"), end_of_day=True)
    if acquired_before is not None:
        qs = qs.filter(acquired_at__lte=acquired_before)

    activity_after = _parse_dt_or_date(params.get("last_activity_after"))
    if activity_after is not None:
        qs = qs.filter(last_activity_at__gte=activity_after)

    activity_before = _parse_dt_or_date(params.get("last_activity_before"), end_of_day=True)
    if activity_before is not None:
        qs = qs.filter(last_activity_at__lte=activity_before)

    for sub_field in ("email_subscription", "sms_subscription", "whatsapp_subscription"):
        sub_val = params.get(sub_field)
        if sub_val:
            qs = qs.filter(**{sub_field: str(sub_val).strip().lower()})

    search_val = params.get("search") or params.get("q")
    if search_val and str(search_val).strip():
        term = str(search_val).strip()
        qs = qs.filter(
            Q(full_name__icontains=term)
            | Q(first_name__icontains=term)
            | Q(last_name__icontains=term)
            | Q(email__icontains=term)
            | Q(phone__icontains=term)
        )

    ordering = params.get("ordering")
    allowed_orderings = {
        "created_at",
        "-created_at",
        "last_activity_at",
        "-last_activity_at",
        "acquired_at",
        "-acquired_at",
        "full_name",
        "-full_name",
    }
    if ordering in allowed_orderings:
        qs = qs.order_by(ordering, "-id")

    if needs_distinct:
        qs = qs.distinct()

    return qs


def get_contact_by_id(
    contact_id: uuid.UUID | str,
    membership: Membership | None = None,
) -> Contact | None:
    qs = apply_marketer_visibility(get_base_contacts_queryset(), membership)
    return qs.filter(pk=contact_id).first()


def get_contacts_by_ids(
    contact_ids: Iterable[uuid.UUID | str],
    membership: Membership | None = None,
) -> QuerySet[Contact]:
    qs = apply_marketer_visibility(get_base_contacts_queryset(), membership)
    return qs.filter(pk__in=list(contact_ids))


def get_contact_timeline_queryset(contact: Contact) -> QuerySet[Activity]:
    return (
        Activity.objects.select_related("actor", "contact", "tenant")
        .filter(contact=contact)
        .order_by("-created_at", "-id")
    )


def get_tags_queryset() -> QuerySet[Tag]:
    return Tag.objects.select_related("tenant").order_by("name")


def get_custom_field_definitions_queryset() -> QuerySet[CustomFieldDefinition]:
    return CustomFieldDefinition.objects.select_related("tenant").order_by("label", "created_at")


def get_lead_statuses_queryset() -> QuerySet[LeadStatus]:
    return LeadStatus.objects.select_related("tenant").order_by("order", "created_at")


def get_default_lead_status() -> LeadStatus | None:
    status_obj = LeadStatus.objects.filter(is_default=True).order_by("order").first()
    if status_obj is not None:
        return status_obj
    return LeadStatus.objects.order_by("order", "created_at").first()


def get_appointments_queryset(
    membership: Membership | None = None,
    contact: Contact | None = None,
) -> QuerySet[Appointment]:
    qs = Appointment.objects.select_related("contact", "tenant").order_by("-starts_at", "-created_at")
    if contact is not None:
        qs = qs.filter(contact=contact)
    if (
        membership is not None
        and membership.role == Membership.Role.MARKETER
        and not membership.tenant.marketer_can_view_all_contacts
    ):
        qs = qs.filter(contact__assigned_to=membership)
    return qs


def get_tasks_queryset(
    membership: Membership | None = None,
    contact: Contact | None = None,
) -> QuerySet[Task]:
    qs = Task.objects.select_related(
        "contact",
        "assignee",
        "assignee__user",
        "created_by",
        "tenant",
    ).order_by("due_at", "-created_at")
    if contact is not None:
        qs = qs.filter(contact=contact)
    if (
        membership is not None
        and membership.role == Membership.Role.MARKETER
        and not membership.tenant.marketer_can_view_all_contacts
    ):
        qs = qs.filter(
            Q(assignee=membership) | Q(contact__assigned_to=membership)
        )
    return qs
