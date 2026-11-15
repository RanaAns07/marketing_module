from collections.abc import Mapping
from typing import Any
import uuid
from django.db.models import QuerySet
from contacts.models import Appointment, Contact
from webhooks.models import ExternalEvent, WebhookDelivery, WebhookEndpoint


def _parse_uuid_or_none(val: Any) -> uuid.UUID | None:
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def get_webhook_endpoints_queryset() -> QuerySet[WebhookEndpoint]:
    return WebhookEndpoint.objects.select_related("tenant").order_by("-created_at", "-id")


def get_webhook_endpoint_by_id(endpoint_id: uuid.UUID | str) -> WebhookEndpoint | None:
    parsed = _parse_uuid_or_none(endpoint_id)
    if parsed is None:
        return None
    return WebhookEndpoint.objects.select_related("tenant").filter(pk=parsed).first()


def get_webhook_deliveries_queryset(
    params: Mapping[str, Any] | None = None,
) -> QuerySet[WebhookDelivery]:
    qs = WebhookDelivery.objects.select_related("endpoint", "tenant").order_by("-created_at", "-id")
    if not params:
        return qs

    endpoint_val = params.get("endpoint") or params.get("endpoint_id")
    if endpoint_val:
        parsed_ep = _parse_uuid_or_none(endpoint_val)
        if parsed_ep is not None:
            qs = qs.filter(endpoint_id=parsed_ep)
        else:
            qs = qs.none()

    status_val = params.get("status")
    if status_val:
        qs = qs.filter(status=str(status_val).strip().lower())

    event_name = params.get("event_name") or params.get("event")
    if event_name:
        qs = qs.filter(event_name=str(event_name).strip())

    event_id = params.get("event_id")
    if event_id:
        qs = qs.filter(event_id=str(event_id).strip())

    return qs


def get_webhook_delivery_by_id(delivery_id: uuid.UUID | str) -> WebhookDelivery | None:
    parsed = _parse_uuid_or_none(delivery_id)
    if parsed is None:
        return None
    return WebhookDelivery.objects.select_related("endpoint", "tenant").filter(pk=parsed).first()


def get_external_events_queryset(
    params: Mapping[str, Any] | None = None,
) -> QuerySet[ExternalEvent]:
    qs = ExternalEvent.objects.select_related("api_key", "contact", "tenant").order_by("-created_at", "-id")
    if not params:
        return qs

    status_val = params.get("status")
    if status_val:
        qs = qs.filter(status=str(status_val).strip().lower())

    type_val = params.get("type") or params.get("event_type")
    if type_val:
        qs = qs.filter(type=str(type_val).strip())

    event_id = params.get("event_id")
    if event_id:
        qs = qs.filter(event_id=str(event_id).strip())

    contact_val = params.get("contact") or params.get("contact_id")
    if contact_val:
        parsed_ct = _parse_uuid_or_none(contact_val)
        if parsed_ct is not None:
            qs = qs.filter(contact_id=parsed_ct)
        else:
            qs = qs.filter(contact__external_id=str(contact_val).strip())

    api_key_val = params.get("api_key") or params.get("api_key_id")
    if api_key_val:
        parsed_key = _parse_uuid_or_none(api_key_val)
        if parsed_key is not None:
            qs = qs.filter(api_key_id=parsed_key)
        else:
            qs = qs.none()

    return qs


def get_external_event_by_id(external_event_id: uuid.UUID | str) -> ExternalEvent | None:
    parsed = _parse_uuid_or_none(external_event_id)
    if parsed is None:
        return None
    return (
        ExternalEvent.objects.select_related("api_key", "contact", "tenant")
        .filter(pk=parsed)
        .first()
    )


def get_contact_by_external_id_or_id(identifier: str) -> Contact | None:
    clean = (identifier or "").strip()
    if not clean:
        return None
    base_qs = Contact.objects.select_related(
        "lead_status",
        "assigned_to",
        "assigned_to__user",
        "tenant",
    ).prefetch_related("tags")
    parsed_uuid = _parse_uuid_or_none(clean)
    if parsed_uuid is not None:
        found = base_qs.filter(pk=parsed_uuid).first()
        if found is not None:
            return found
    return base_qs.filter(external_id=clean).first()


def get_appointment_by_external_ref_or_id(identifier: str) -> Appointment | None:
    clean = (identifier or "").strip()
    if not clean:
        return None
    base_qs = Appointment.objects.select_related("contact", "tenant")
    parsed_uuid = _parse_uuid_or_none(clean)
    if parsed_uuid is not None:
        found = base_qs.filter(pk=parsed_uuid).first()
        if found is not None:
            return found
    return base_qs.filter(external_ref=clean).order_by("-created_at").first()
