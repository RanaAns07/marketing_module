import uuid
from django.db.models import Q, QuerySet
from leads.models import MetaConnection, MetaLeadEvent, MetaLeadForm, MetaPage


def _parse_uuid(val: str | uuid.UUID | None) -> uuid.UUID | None:
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def get_meta_connection() -> MetaConnection | None:
    return MetaConnection.objects.select_related("tenant").first()


def get_meta_pages_queryset(selected: bool | None = None) -> QuerySet[MetaPage]:
    qs = MetaPage.objects.select_related("connection", "tenant").order_by("name", "-created_at")
    if selected is not None:
        qs = qs.filter(selected=selected)
    return qs


def get_meta_page_by_identifier(identifier: str | uuid.UUID) -> MetaPage | None:
    qs = get_meta_pages_queryset()
    parsed = _parse_uuid(identifier)
    if parsed is not None:
        return qs.filter(Q(pk=parsed) | Q(page_id=str(identifier).strip())).first()
    return qs.filter(page_id=str(identifier).strip()).first()


def get_meta_forms_queryset(
    *,
    page: MetaPage | None = None,
    page_identifier: str | uuid.UUID | None = None,
    selected: bool | None = None,
) -> QuerySet[MetaLeadForm]:
    qs = (
        MetaLeadForm.objects.select_related("page", "tenant")
        .prefetch_related("default_tags")
        .order_by("name", "-created_at")
    )
    if page is not None:
        qs = qs.filter(page=page)
    elif page_identifier is not None:
        parsed = _parse_uuid(page_identifier)
        if parsed is not None:
            qs = qs.filter(Q(page_id=parsed) | Q(page__page_id=str(page_identifier).strip()))
        else:
            qs = qs.filter(page__page_id=str(page_identifier).strip())
    if selected is not None:
        qs = qs.filter(selected=selected)
    return qs


def get_meta_form_by_identifier(identifier: str | uuid.UUID) -> MetaLeadForm | None:
    qs = get_meta_forms_queryset()
    parsed = _parse_uuid(identifier)
    if parsed is not None:
        return qs.filter(Q(pk=parsed) | Q(form_id=str(identifier).strip())).first()
    return qs.filter(form_id=str(identifier).strip()).first()


def get_meta_lead_events_queryset(status_filter: str | None = None) -> QuerySet[MetaLeadEvent]:
    qs = MetaLeadEvent.objects.select_related(
        "page",
        "form",
        "contact",
        "tenant",
    ).order_by("-created_at", "-id")
    if status_filter:
        qs = qs.filter(status=status_filter.strip().lower())
    return qs


def get_meta_lead_event_by_id(event_id: str | uuid.UUID) -> MetaLeadEvent | None:
    return get_meta_lead_events_queryset().filter(pk=event_id).first()
