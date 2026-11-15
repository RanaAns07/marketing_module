from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone as dt_timezone
import hashlib
import hmac
import ipaddress
import json
import random
import secrets
import socket
from typing import Any
from urllib.parse import urlsplit
import uuid
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
import requests
from rest_framework import status
from accounts.models import Membership, TenantAPIKey
from automation.services import EnrollmentService
from contacts.models import Activity, Appointment, Contact, LeadStatus
from contacts.services import (
    ContactService,
    book_appointment,
    cancel_appointment,
    complete_appointment,
    create_task,
    log_contact_activity,
    normalize_email,
    normalize_phone_e164,
    reschedule_appointment,
)
from core.context import require_tenant, require_tenant_id
from core.events import EventName, publish
from core.exceptions import ApplicationError
from notifications.models import NotificationType
from notifications.services import notify
from webhooks.models import ExternalEvent, WebhookDelivery, WebhookEndpoint

MAX_EXTERNAL_EVENT_ATTEMPTS = 3
MAX_WEBHOOK_DELIVERY_ATTEMPTS = 8
AUTO_DISABLE_FAILURE_THRESHOLD = 8
WEBHOOK_TIMEOUT_SECONDS = 10

_FORBIDDEN_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata.google.internal",
        "metadata",
        "instance-data",
    }
)

_METADATA_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fe80::/10"),
)

_SENSITIVE_KEY_SUBSTRINGS = (
    "secret",
    "password",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
)


def _check_ip_not_ssrf(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address, url: str) -> None:
    if getattr(settings, "ALLOW_LOCAL_WEBHOOKS", False):
        return
    if (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
        or any(ip_obj in net for net in _METADATA_NETWORKS)
    ):
        raise ApplicationError(
            detail=f"Webhook URL '{url}' resolves to a forbidden private, loopback, link-local, or metadata address.",
            code="webhook_ssrf_blocked",
            status_code=status.HTTP_400_BAD_REQUEST,
        )


def validate_webhook_target_url(url: str, *, require_dns_resolution: bool = False) -> str:
    clean_url = (url or "").strip()
    if not clean_url:
        raise ApplicationError(
            detail="Webhook URL is required.",
            code="webhook_url_required",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    parsed = urlsplit(clean_url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ApplicationError(
            detail="Webhook URL must use http or https scheme.",
            code="invalid_webhook_url_scheme",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not getattr(settings, "DEBUG", False) and scheme != "https":
        if not getattr(settings, "ALLOW_LOCAL_WEBHOOKS", False):
            raise ApplicationError(
                detail="Webhook URL must use https in production.",
                code="https_required",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise ApplicationError(
            detail="Webhook URL must include a valid hostname.",
            code="invalid_webhook_url_host",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if (
        hostname in _FORBIDDEN_HOSTNAMES
        or hostname.endswith(".local")
        or hostname.endswith(".internal")
        or hostname.endswith(".localhost")
    ):
        if not getattr(settings, "ALLOW_LOCAL_WEBHOOKS", False):
            raise ApplicationError(
                detail=f"Webhook URL hostname '{hostname}' is not allowed.",
                code="webhook_ssrf_blocked",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        _check_ip_not_ssrf(literal_ip, clean_url)
        return clean_url

    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        addr_infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        if require_dns_resolution:
            raise ApplicationError(
                detail=f"Unable to resolve webhook hostname '{hostname}'.",
                code="webhook_dns_resolution_failed",
                status_code=status.HTTP_400_BAD_REQUEST,
            ) from exc
        return clean_url

    for info in addr_infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_str = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        _check_ip_not_ssrf(ip_obj, clean_url)

    return clean_url


def _parse_datetime_or_now(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        if timezone.is_naive(raw):
            return timezone.make_aware(raw, dt_timezone.utc)
        return raw
    if raw:
        parsed = parse_datetime(str(raw).strip())
        if parsed is not None:
            if timezone.is_naive(parsed):
                return timezone.make_aware(parsed, dt_timezone.utc)
            return parsed
    return timezone.now()


def _get_or_create_customer_status() -> LeadStatus:
    existing = LeadStatus.objects.filter(name__iexact="Customer").first()
    if existing is not None:
        return existing
    return LeadStatus.objects.create(name="Customer", order=6, is_default=False)


def _match_or_upsert_contact_for_event(
    *,
    contact_info: dict[str, Any],
    source_channel: str,
) -> tuple[Contact, bool]:
    ext_id = str(contact_info.get("external_id") or "").strip()
    norm_email = normalize_email(contact_info.get("email"))
    norm_phone = normalize_phone_e164(contact_info.get("phone"))

    if not ext_id and not norm_email and not norm_phone:
        raise ApplicationError(
            detail="Contact identifier (external_id, email, or phone) is required.",
            code="missing_contact_identifier",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    matched: Contact | None = None
    if ext_id:
        matched = (
            Contact.objects.select_for_update(of=("self",))
            .select_related("lead_status", "assigned_to", "assigned_to__user", "tenant")
            .prefetch_related("tags")
            .filter(external_id=ext_id)
            .first()
        )
    if matched is None and norm_email:
        matched = (
            Contact.objects.select_for_update(of=("self",))
            .select_related("lead_status", "assigned_to", "assigned_to__user", "tenant")
            .prefetch_related("tags")
            .filter(email=norm_email)
            .first()
        )
    if matched is None and norm_phone:
        matched = (
            Contact.objects.select_for_update(of=("self",))
            .select_related("lead_status", "assigned_to", "assigned_to__user", "tenant")
            .prefetch_related("tags")
            .filter(phone=norm_phone)
            .first()
        )

    first_name = str(contact_info.get("first_name") or "").strip()
    last_name = str(contact_info.get("last_name") or "").strip()
    full_name = str(contact_info.get("full_name") or contact_info.get("name") or "").strip()
    city = str(contact_info.get("city") or "").strip()
    country = str(contact_info.get("country") or "").strip()
    timezone_name = str(contact_info.get("timezone") or "UTC").strip() or "UTC"
    custom_fields = contact_info.get("custom_fields") if isinstance(contact_info.get("custom_fields"), dict) else None
    tags = contact_info.get("tags") if isinstance(contact_info.get("tags"), list) else None

    if matched is None:
        contact, created = ContactService.upsert_from_source(
            first_name=first_name,
            last_name=last_name,
            full_name=full_name,
            email=norm_email,
            phone=norm_phone,
            city=city,
            country=country,
            timezone_name=timezone_name,
            source_channel=source_channel or "external_api",
            external_id=ext_id,
            custom_fields=custom_fields,
            tags=tags,
        )
        return contact, created

    updates: dict[str, Any] = {}
    if ext_id and matched.external_id != ext_id:
        updates["external_id"] = ext_id
    if norm_email and not matched.email:
        if not Contact.objects.filter(email=norm_email).exclude(pk=matched.pk).exists():
            updates["email"] = norm_email
    if norm_phone and not matched.phone:
        if not Contact.objects.filter(phone=norm_phone).exclude(pk=matched.pk).exists():
            updates["phone"] = norm_phone
    if first_name:
        updates["first_name"] = first_name
    if last_name:
        updates["last_name"] = last_name
    if full_name:
        updates["full_name"] = full_name
    if city:
        updates["city"] = city
    if country:
        updates["country"] = country
    if timezone_name and timezone_name != "UTC":
        updates["timezone"] = timezone_name
    if custom_fields:
        updates["custom_fields"] = custom_fields

    if updates:
        matched = ContactService.update_contact(contact=matched, **updates)
    if tags:
        ContactService.add_tags(contact=matched, tags=tags, create_missing=True)

    return matched, False


def _extract_purchase_custom_fields(
    data: dict[str, Any],
    occurred_at: datetime,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if isinstance(data.get("custom_fields"), dict):
        fields.update(data["custom_fields"])
    for key in ("package", "plan", "amount", "currency"):
        if key in data and data[key] is not None:
            fields[key] = data[key]
    fields["purchased_at"] = str(data.get("purchased_at") or occurred_at.isoformat())
    return fields


def _handle_package_purchased(
    *,
    contact: Contact,
    event: ExternalEvent,
    data: dict[str, Any],
) -> dict[str, Any]:
    custom_updates = _extract_purchase_custom_fields(data, event.occurred_at)
    contact = ContactService.update_contact(contact=contact, custom_fields=custom_updates)

    configured_tags = list(data.get("tags") or [])
    for default_tag in ("Customer", "Converted"):
        if default_tag not in configured_tags:
            configured_tags.append(default_tag)
    added_tags = ContactService.add_tags(contact=contact, tags=configured_tags, create_missing=True)

    customer_status = _get_or_create_customer_status()
    contact = ContactService.change_status(contact=contact, new_status=customer_status)

    return {
        "handler": "package.purchased",
        "lead_status": customer_status.name,
        "converted_at": contact.converted_at.isoformat() if contact.converted_at else None,
        "custom_fields_updated": list(custom_updates.keys()),
        "tags_added": [t.name for t in added_tags],
    }


def _handle_subscription_created(
    *,
    contact: Contact,
    event: ExternalEvent,
    data: dict[str, Any],
) -> dict[str, Any]:
    custom_updates = _extract_purchase_custom_fields(data, event.occurred_at)
    custom_updates["subscription_status"] = str(data.get("subscription_status") or "active")
    contact = ContactService.update_contact(contact=contact, custom_fields=custom_updates)

    configured_tags = list(data.get("tags") or [])
    for default_tag in ("Customer", "Converted"):
        if default_tag not in configured_tags:
            configured_tags.append(default_tag)
    added_tags = ContactService.add_tags(contact=contact, tags=configured_tags, create_missing=True)

    customer_status = _get_or_create_customer_status()
    contact = ContactService.change_status(contact=contact, new_status=customer_status)

    return {
        "handler": "subscription.created",
        "lead_status": customer_status.name,
        "converted_at": contact.converted_at.isoformat() if contact.converted_at else None,
        "custom_fields_updated": list(custom_updates.keys()),
        "tags_added": [t.name for t in added_tags],
    }


def _handle_subscription_updated(
    *,
    contact: Contact,
    event: ExternalEvent,
    data: dict[str, Any],
) -> dict[str, Any]:
    custom_updates: dict[str, Any] = dict(data.get("custom_fields") or {})
    for key in ("package", "plan", "amount", "currency", "subscription_status"):
        if key in data and data[key] is not None:
            custom_updates[key] = data[key]
    custom_updates["subscription_updated_at"] = event.occurred_at.isoformat()
    ContactService.update_contact(contact=contact, custom_fields=custom_updates)

    extra_tags = list(data.get("tags") or [])
    added_tags = (
        ContactService.add_tags(contact=contact, tags=extra_tags, create_missing=True)
        if extra_tags
        else []
    )
    return {
        "handler": "subscription.updated",
        "custom_fields_updated": list(custom_updates.keys()),
        "tags_added": [t.name for t in added_tags],
    }


def _handle_subscription_renewed(
    *,
    contact: Contact,
    event: ExternalEvent,
    data: dict[str, Any],
) -> dict[str, Any]:
    custom_updates: dict[str, Any] = dict(data.get("custom_fields") or {})
    for key in ("package", "plan", "amount", "currency"):
        if key in data and data[key] is not None:
            custom_updates[key] = data[key]
    custom_updates["subscription_status"] = "active"
    custom_updates["renewed_at"] = str(data.get("renewed_at") or event.occurred_at.isoformat())
    ContactService.update_contact(contact=contact, custom_fields=custom_updates)

    extra_tags = list(data.get("tags") or [])
    added_tags = (
        ContactService.add_tags(contact=contact, tags=extra_tags, create_missing=True)
        if extra_tags
        else []
    )
    return {
        "handler": "subscription.renewed",
        "custom_fields_updated": list(custom_updates.keys()),
        "tags_added": [t.name for t in added_tags],
    }


def _handle_subscription_cancelled(
    *,
    contact: Contact,
    event: ExternalEvent,
    data: dict[str, Any],
) -> dict[str, Any]:
    custom_updates: dict[str, Any] = dict(data.get("custom_fields") or {})
    for key in ("package", "plan", "cancellation_reason", "reason"):
        if key in data and data[key] is not None:
            custom_updates[key] = data[key]
    custom_updates["subscription_status"] = "cancelled"
    custom_updates["cancelled_at"] = str(data.get("cancelled_at") or event.occurred_at.isoformat())
    ContactService.update_contact(contact=contact, custom_fields=custom_updates)

    tags_to_add = list(data.get("tags") or [])
    if "Subscription Cancelled" not in tags_to_add:
        tags_to_add.append("Subscription Cancelled")
    added_tags = ContactService.add_tags(contact=contact, tags=tags_to_add, create_missing=True)

    return {
        "handler": "subscription.cancelled",
        "custom_fields_updated": list(custom_updates.keys()),
        "tags_added": [t.name for t in added_tags],
        "converted_at_preserved": contact.converted_at.isoformat() if contact.converted_at else None,
    }


def _handle_payment_succeeded(
    *,
    contact: Contact,
    event: ExternalEvent,
    data: dict[str, Any],
) -> dict[str, Any]:
    custom_updates: dict[str, Any] = dict(data.get("custom_fields") or {})
    for key in ("package", "plan", "amount", "currency"):
        if key in data and data[key] is not None:
            custom_updates[key] = data[key]
    custom_updates["last_payment_at"] = str(data.get("paid_at") or event.occurred_at.isoformat())
    ContactService.update_contact(contact=contact, custom_fields=custom_updates)

    extra_tags = list(data.get("tags") or [])
    added_tags = (
        ContactService.add_tags(contact=contact, tags=extra_tags, create_missing=True)
        if extra_tags
        else []
    )
    return {
        "handler": "payment.succeeded",
        "custom_fields_updated": list(custom_updates.keys()),
        "tags_added": [t.name for t in added_tags],
    }


def _handle_payment_failed(
    *,
    contact: Contact,
    event: ExternalEvent,
    data: dict[str, Any],
) -> dict[str, Any]:
    custom_updates: dict[str, Any] = dict(data.get("custom_fields") or {})
    for key in ("package", "plan", "amount", "currency", "reason", "failure_reason"):
        if key in data and data[key] is not None:
            custom_updates[key] = data[key]
    custom_updates["last_payment_failed_at"] = event.occurred_at.isoformat()
    ContactService.update_contact(contact=contact, custom_fields=custom_updates)

    tags_to_add = list(data.get("tags") or [])
    if "Payment Failed" not in tags_to_add:
        tags_to_add.append("Payment Failed")
    added_tags = ContactService.add_tags(contact=contact, tags=tags_to_add, create_missing=True)

    reason_str = str(data.get("reason") or data.get("failure_reason") or "Payment attempt failed.")
    task = create_task(
        title=f"Follow up on failed payment: {contact.full_name or contact.email or contact.phone}",
        description=reason_str,
        contact=contact,
        assignee=contact.assigned_to,
        due_at=timezone.now() + timedelta(hours=4),
        from_workflow=False,
    )
    return {
        "handler": "payment.failed",
        "task_id": str(task.id),
        "custom_fields_updated": list(custom_updates.keys()),
        "tags_added": [t.name for t in added_tags],
    }


def _handle_refund_issued(
    *,
    contact: Contact,
    event: ExternalEvent,
    data: dict[str, Any],
) -> dict[str, Any]:
    custom_updates: dict[str, Any] = dict(data.get("custom_fields") or {})
    for key in ("package", "plan", "amount", "refund_amount", "currency", "reason", "refund_reason"):
        if key in data and data[key] is not None:
            custom_updates[key] = data[key]
    custom_updates["refunded_at"] = str(data.get("refunded_at") or event.occurred_at.isoformat())
    ContactService.update_contact(contact=contact, custom_fields=custom_updates)

    tags_to_add = list(data.get("tags") or [])
    if "Refund Issued" not in tags_to_add:
        tags_to_add.append("Refund Issued")
    added_tags = ContactService.add_tags(contact=contact, tags=tags_to_add, create_missing=True)

    return {
        "handler": "refund.issued",
        "custom_fields_updated": list(custom_updates.keys()),
        "tags_added": [t.name for t in added_tags],
        "converted_at_preserved": contact.converted_at.isoformat() if contact.converted_at else None,
    }


def _find_contact_appointment(contact: Contact, external_ref: str) -> Appointment | None:
    if external_ref:
        found = (
            Appointment.objects.select_for_update()
            .filter(contact=contact, external_ref=external_ref)
            .order_by("-created_at")
            .first()
        )
        if found is not None:
            return found
    return (
        Appointment.objects.select_for_update()
        .filter(contact=contact)
        .order_by("-starts_at", "-created_at")
        .first()
    )


def _handle_appointment_booked(
    *,
    contact: Contact,
    event: ExternalEvent,
    data: dict[str, Any],
) -> dict[str, Any]:
    starts_at = _parse_datetime_or_now(data.get("starts_at") or event.occurred_at)
    external_ref = str(data.get("external_ref") or data.get("appointment_id") or "").strip()
    location = str(data.get("location") or "").strip()
    notes = str(data.get("notes") or "").strip()

    appt = book_appointment(
        contact=contact,
        starts_at=starts_at,
        external_ref=external_ref,
        location=location,
        notes=notes,
    )
    return {
        "handler": "appointment.booked",
        "appointment_id": str(appt.id),
        "status": appt.status,
        "starts_at": appt.starts_at.isoformat(),
    }


def _handle_appointment_cancelled(
    *,
    contact: Contact,
    event: ExternalEvent,
    data: dict[str, Any],
) -> dict[str, Any]:
    external_ref = str(data.get("external_ref") or data.get("appointment_id") or "").strip()
    appt = _find_contact_appointment(contact, external_ref)
    if appt is None:
        starts_at = _parse_datetime_or_now(data.get("starts_at") or event.occurred_at)
        appt = Appointment.objects.create(
            contact=contact,
            starts_at=starts_at,
            status=Appointment.Status.BOOKED,
            external_ref=external_ref,
            location=str(data.get("location") or "").strip(),
            notes=str(data.get("notes") or "").strip(),
        )
    appt = cancel_appointment(appointment=appt)
    return {
        "handler": "appointment.cancelled",
        "appointment_id": str(appt.id),
        "status": appt.status,
    }


def _handle_appointment_rescheduled(
    *,
    contact: Contact,
    event: ExternalEvent,
    data: dict[str, Any],
) -> dict[str, Any]:
    external_ref = str(data.get("external_ref") or data.get("appointment_id") or "").strip()
    starts_at = _parse_datetime_or_now(data.get("starts_at") or event.occurred_at)
    location = str(data.get("location") or "").strip() if "location" in data else None
    notes = str(data.get("notes") or "").strip() if "notes" in data else None

    appt = _find_contact_appointment(contact, external_ref)
    if appt is None:
        appt = book_appointment(
            contact=contact,
            starts_at=starts_at,
            external_ref=external_ref,
            location=location or "",
            notes=notes or "",
        )
        appt.status = Appointment.Status.RESCHEDULED
        appt.save(update_fields=["status", "updated_at"])
    else:
        appt = reschedule_appointment(
            appointment=appt,
            starts_at=starts_at,
            location=location,
            notes=notes,
        )
    return {
        "handler": "appointment.rescheduled",
        "appointment_id": str(appt.id),
        "status": appt.status,
        "starts_at": appt.starts_at.isoformat(),
    }


def _handle_appointment_completed(
    *,
    contact: Contact,
    event: ExternalEvent,
    data: dict[str, Any],
) -> dict[str, Any]:
    external_ref = str(data.get("external_ref") or data.get("appointment_id") or "").strip()
    appt = _find_contact_appointment(contact, external_ref)
    if appt is None:
        starts_at = _parse_datetime_or_now(data.get("starts_at") or event.occurred_at)
        appt = Appointment.objects.create(
            contact=contact,
            starts_at=starts_at,
            status=Appointment.Status.BOOKED,
            external_ref=external_ref,
            location=str(data.get("location") or "").strip(),
            notes=str(data.get("notes") or "").strip(),
        )
    appt = complete_appointment(appointment=appt)
    return {
        "handler": "appointment.completed",
        "appointment_id": str(appt.id),
        "status": appt.status,
    }


def _handle_customer_updated(
    *,
    contact: Contact,
    event: ExternalEvent,
    data: dict[str, Any],
) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for field in (
        "first_name",
        "last_name",
        "full_name",
        "email",
        "phone",
        "city",
        "country",
        "timezone",
    ):
        if field in data and data[field] is not None:
            changes[field] = data[field]

    if isinstance(data.get("custom_fields"), dict):
        changes["custom_fields"] = data["custom_fields"]

    if changes:
        ContactService.update_contact(contact=contact, **changes)

    if isinstance(data.get("tags"), list) and data["tags"]:
        ContactService.add_tags(contact=contact, tags=data["tags"], create_missing=True)

    return {
        "handler": "customer.updated",
        "updated_fields": list(changes.keys()),
    }


EXTERNAL_EVENT_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "package.purchased": _handle_package_purchased,
    "subscription.created": _handle_subscription_created,
    "subscription.updated": _handle_subscription_updated,
    "subscription.renewed": _handle_subscription_renewed,
    "subscription.cancelled": _handle_subscription_cancelled,
    "payment.succeeded": _handle_payment_succeeded,
    "payment.failed": _handle_payment_failed,
    "refund.issued": _handle_refund_issued,
    "appointment.booked": _handle_appointment_booked,
    "appointment.cancelled": _handle_appointment_cancelled,
    "appointment.rescheduled": _handle_appointment_rescheduled,
    "appointment.completed": _handle_appointment_completed,
    "customer.updated": _handle_customer_updated,
}

SUPPORTED_EXTERNAL_EVENT_TYPES = frozenset(EXTERNAL_EVENT_HANDLERS.keys())


def _enqueue_external_event_processing(tenant_id: str, external_event_id: str) -> None:
    from webhooks.tasks import process_external_event_task

    try:
        process_external_event_task.apply_async(
            args=[tenant_id, external_event_id],
            queue="webhooks",
        )
    except Exception:
        process_external_event_task(tenant_id, external_event_id)


@transaction.atomic
def ingest_external_event(
    *,
    api_key: TenantAPIKey | None,
    event_id: str,
    event_type: str,
    occurred_at: datetime,
    contact_payload: dict[str, Any],
    data: dict[str, Any] | None = None,
    automation: list[dict[str, Any]] | None = None,
) -> tuple[ExternalEvent, bool]:
    tenant_id = require_tenant_id()
    clean_event_id = str(event_id).strip()
    clean_type = str(event_type).strip()

    if clean_type not in SUPPORTED_EXTERNAL_EVENT_TYPES:
        raise ApplicationError(
            detail=f"Unsupported external event type '{clean_type}'.",
            code="unsupported_event_type",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    existing = (
        ExternalEvent.objects.select_for_update(of=("self",))
        .select_related("api_key", "contact", "tenant")
        .filter(event_id=clean_event_id)
        .first()
    )
    if existing is not None:
        return existing, True

    normalized_data = dict(data or {})
    if automation is not None and "automation" not in normalized_data:
        normalized_data["automation"] = list(automation)

    envelope_payload = {
        "event_id": clean_event_id,
        "type": clean_type,
        "occurred_at": occurred_at.isoformat(),
        "contact": dict(contact_payload or {}),
        "data": normalized_data,
    }

    try:
        with transaction.atomic():
            ext_event = ExternalEvent.objects.create(
                api_key=api_key,
                event_id=clean_event_id,
                type=clean_type,
                occurred_at=occurred_at,
                payload=envelope_payload,
                status=ExternalEvent.Status.RECEIVED,
            )
    except IntegrityError:
        dup = (
            ExternalEvent.objects.select_related("api_key", "contact", "tenant")
            .get(event_id=clean_event_id)
        )
        return dup, True

    tid_str = str(tenant_id)
    eid_str = str(ext_event.id)
    transaction.on_commit(lambda: _enqueue_external_event_processing(tid_str, eid_str))
    return ext_event, False


def _notify_external_event_failure(ext_event: ExternalEvent, error_msg: str) -> None:
    admin_ids = list(
        Membership.objects.filter(
            role__in=[Membership.Role.OWNER, Membership.Role.ADMIN],
            is_active=True,
        ).values_list("user_id", flat=True)
    )
    if not admin_ids:
        return
    notify(
        tenant=ext_event.tenant_id,
        users=admin_ids,
        type=NotificationType.WORKFLOW_INTEGRATION_ALERT,
        payload={
            "title": f"External Event Failed: {ext_event.type}",
            "body": f"Event '{ext_event.event_id}' ({ext_event.type}) failed: {error_msg}",
            "external_event_id": str(ext_event.id),
            "event_id": ext_event.event_id,
            "event_type": ext_event.type,
            "error": error_msg,
        },
        link=f"/webhooks/external-events/{ext_event.id}",
    )


def _execute_external_event_transaction(ext_event_id: uuid.UUID | str, *, force_replay: bool = False) -> ExternalEvent:
    tenant_id = require_tenant_id()
    with transaction.atomic():
        locked = (
            ExternalEvent.objects.select_for_update(of=("self",))
            .select_related("api_key", "contact", "tenant")
            .get(pk=ext_event_id)
        )
        if locked.status == ExternalEvent.Status.PROCESSED and not force_replay:
            return locked

        handler = EXTERNAL_EVENT_HANDLERS.get(locked.type)
        if handler is None:
            locked.status = ExternalEvent.Status.IGNORED
            locked.error = f"Unsupported event type '{locked.type}'."
            locked.processed_at = timezone.now()
            locked.save(update_fields=["status", "error", "processed_at", "updated_at"])
            return locked

        envelope = locked.payload or {}
        contact_info = dict(envelope.get("contact") or {})
        data = dict(envelope.get("data") or {})
        source_channel = (
            locked.api_key.name
            if locked.api_key is not None and locked.api_key.name
            else "external_api"
        )

        contact, contact_created = _match_or_upsert_contact_for_event(
            contact_info=contact_info,
            source_channel=source_channel,
        )
        locked.contact = contact

        log_contact_activity(
            contact=contact,
            activity_type=Activity.ActivityType.EVENT,
            description=f"External event received: {locked.type}.",
            metadata={
                "external_event_id": str(locked.id),
                "event_id": locked.event_id,
                "event_type": locked.type,
                "occurred_at": locked.occurred_at.isoformat(),
                "source": source_channel,
                "data": {k: v for k, v in data.items() if k != "automation"},
            },
        )

        handler_result = handler(contact=contact, event=locked, data=data)

        directives = data.get("automation")
        if directives is None:
            directives = envelope.get("automation")

        directive_results: list[dict[str, Any]] = []
        if directives is not None:
            if not isinstance(directives, list):
                raise ApplicationError(
                    detail="Event 'automation' field must be a list of directives.",
                    code="invalid_automation_directives_list",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            for directive in directives:
                outcome = EnrollmentService.apply_directive(
                    contact=contact,
                    directive=directive,
                )
                directive_results.append(outcome)

        publish(
            EventName.EXTERNAL_EVENT,
            tenant_id=tenant_id,
            contact_id=contact.id,
            payload={
                "external_event_id": str(locked.id),
                "event_id": locked.event_id,
                "type": locked.type,
                "event_type": locked.type,
                "occurred_at": locked.occurred_at.isoformat(),
                "source": source_channel,
                "data": {k: v for k, v in data.items() if k != "automation"},
            },
        )

        now = timezone.now()
        locked.status = ExternalEvent.Status.PROCESSED
        locked.processed_at = now
        locked.error = ""
        locked.result = {
            "contact_id": str(contact.id),
            "contact_created": contact_created,
            "event_type": locked.type,
            **handler_result,
            "automation_directives": directive_results,
        }
        locked.save(
            update_fields=[
                "contact",
                "status",
                "processed_at",
                "error",
                "result",
                "updated_at",
            ]
        )
        return locked


def process_external_event(
    external_event_id: uuid.UUID | str,
    *,
    force_replay: bool = False,
) -> tuple[ExternalEvent, bool]:
    with transaction.atomic():
        locked = ExternalEvent.objects.select_for_update(of=("self",)).get(pk=external_event_id)
        if locked.status == ExternalEvent.Status.PROCESSED and not force_replay:
            return locked, False
        locked.attempts = (locked.attempts or 0) + 1
        locked.save(update_fields=["attempts", "updated_at"])
        current_attempts = locked.attempts

    try:
        processed = _execute_external_event_transaction(external_event_id, force_replay=force_replay)
        return processed, False
    except ApplicationError as exc:
        with transaction.atomic():
            failed = ExternalEvent.objects.select_for_update(of=("self",)).get(pk=external_event_id)
            failed.status = ExternalEvent.Status.FAILED
            failed.error = str(exc.detail)
            failed.result = {"error_code": str(getattr(exc, "code", "application_error")), "error": str(exc.detail)}
            failed.save(update_fields=["status", "error", "result", "updated_at"])
            _notify_external_event_failure(failed, failed.error)
        return failed, False
    except Exception as exc:
        err_str = str(exc)
        with transaction.atomic():
            failed = ExternalEvent.objects.select_for_update(of=("self",)).get(pk=external_event_id)
            failed.error = err_str
            if current_attempts >= MAX_EXTERNAL_EVENT_ATTEMPTS:
                failed.status = ExternalEvent.Status.FAILED
                failed.save(update_fields=["status", "error", "updated_at"])
                _notify_external_event_failure(failed, err_str)
                return failed, False
            failed.save(update_fields=["error", "updated_at"])
        return failed, True


def replay_external_event(*, external_event: ExternalEvent) -> ExternalEvent:
    if external_event.status not in (
        ExternalEvent.Status.FAILED,
        ExternalEvent.Status.IGNORED,
        ExternalEvent.Status.RECEIVED,
    ):
        raise ApplicationError(
            detail="Only failed, ignored, or unprocessed external events can be replayed.",
            code="external_event_not_replayable",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    processed, _ = process_external_event(external_event.id, force_replay=True)
    return ExternalEvent.objects.select_related("api_key", "contact", "tenant").get(pk=processed.pk)


def _validate_subscribed_events(events: Iterable[str] | None) -> list[str]:
    if not events:
        return ["*"]
    allowed = set(EventName.ALL) | set(SUPPORTED_EXTERNAL_EVENT_TYPES) | {"*", "all", "webhook.ping"}
    normalized: list[str] = []
    for item in events:
        clean = str(item).strip()
        if not clean:
            continue
        if clean not in allowed and not (
            clean.startswith("external_event:")
            and clean.split(":", 1)[1] in SUPPORTED_EXTERNAL_EVENT_TYPES
        ):
            raise ApplicationError(
                detail=f"Unsupported subscribed event '{clean}'.",
                code="invalid_subscribed_event",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if clean == "all":
            clean = "*"
        if clean not in normalized:
            normalized.append(clean)
    return normalized or ["*"]


@transaction.atomic
def create_webhook_endpoint(
    *,
    name: str,
    url: str,
    subscribed_events: Iterable[str] | None = None,
    active: bool = True,
) -> tuple[WebhookEndpoint, str]:
    tenant = require_tenant()
    clean_name = (name or "").strip()
    if not clean_name:
        raise ApplicationError(
            detail="Webhook endpoint name is required.",
            code="webhook_name_required",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    validated_url = validate_webhook_target_url(url, require_dns_resolution=False)
    validated_events = _validate_subscribed_events(subscribed_events)
    raw_secret = f"whsec_{secrets.token_urlsafe(32)}"

    endpoint = WebhookEndpoint.objects.create(
        tenant=tenant,
        name=clean_name,
        url=validated_url,
        signing_secret=raw_secret,
        subscribed_events=validated_events,
        active=active,
    )
    return endpoint, raw_secret


@transaction.atomic
def update_webhook_endpoint(
    *,
    endpoint: WebhookEndpoint,
    **changes: Any,
) -> WebhookEndpoint:
    locked = WebhookEndpoint.objects.select_for_update().get(pk=endpoint.pk)

    if "name" in changes and changes["name"] is not None:
        clean_name = str(changes["name"]).strip()
        if not clean_name:
            raise ApplicationError(
                detail="Webhook endpoint name cannot be empty.",
                code="webhook_name_required",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        locked.name = clean_name

    if "url" in changes and changes["url"] is not None:
        locked.url = validate_webhook_target_url(str(changes["url"]), require_dns_resolution=False)

    if "subscribed_events" in changes and changes["subscribed_events"] is not None:
        locked.subscribed_events = _validate_subscribed_events(changes["subscribed_events"])

    if "active" in changes and changes["active"] is not None:
        new_active = bool(changes["active"])
        if new_active and not locked.active:
            locked.failure_count = 0
            locked.disabled_at = None
        locked.active = new_active

    locked.save()
    return locked


@transaction.atomic
def rotate_webhook_endpoint_secret(
    *,
    endpoint: WebhookEndpoint,
) -> tuple[WebhookEndpoint, str]:
    locked = WebhookEndpoint.objects.select_for_update().get(pk=endpoint.pk)
    new_secret = f"whsec_{secrets.token_urlsafe(32)}"
    locked.signing_secret = new_secret
    locked.save(update_fields=["signing_secret", "updated_at"])
    return locked, new_secret


@transaction.atomic
def delete_webhook_endpoint(*, endpoint: WebhookEndpoint) -> None:
    endpoint.delete()


def _sanitize_payload_dict(data: Any) -> Any:
    if isinstance(data, dict):
        cleaned: dict[str, Any] = {}
        for k, v in data.items():
            k_str = str(k)
            if any(sub in k_str.lower() for sub in _SENSITIVE_KEY_SUBSTRINGS):
                continue
            cleaned[k_str] = _sanitize_payload_dict(v)
        return cleaned
    if isinstance(data, list):
        return [_sanitize_payload_dict(item) for item in data]
    if isinstance(data, (str, int, float, bool)) or data is None:
        return data
    return str(data)


def _build_contact_summary_for_webhook(contact_id: Any) -> dict[str, Any] | None:
    if not contact_id:
        return None
    contact = (
        Contact.objects.select_related("lead_status")
        .prefetch_related("tags")
        .filter(pk=contact_id)
        .first()
    )
    if contact is None:
        return None
    return {
        "id": str(contact.id),
        "external_id": contact.external_id or None,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "full_name": contact.full_name,
        "email": contact.email,
        "phone": contact.phone,
        "lead_status": contact.lead_status.name if contact.lead_status else None,
        "tags": [t.name for t in contact.tags.all()],
        "custom_fields": _sanitize_payload_dict(contact.custom_fields or {}),
    }


def _endpoint_matches_event(
    endpoint: WebhookEndpoint,
    event_name: str,
    raw_payload: dict[str, Any],
) -> bool:
    sub = endpoint.subscribed_events or []
    sub_set = {str(s).strip() for s in sub if str(s).strip()}
    if not sub_set or "*" in sub_set or "all" in sub_set:
        return True
    if event_name in sub_set:
        return True
    if event_name == EventName.EXTERNAL_EVENT:
        ext_type = str(raw_payload.get("type") or raw_payload.get("event_type") or "").strip()
        if ext_type and (ext_type in sub_set or f"external_event:{ext_type}" in sub_set):
            return True
    return False


def _enqueue_webhook_delivery(tenant_id: str, delivery_id: str, countdown: int = 0) -> None:
    from webhooks.tasks import deliver_webhook_task

    try:
        if countdown > 0:
            deliver_webhook_task.apply_async(
                args=[tenant_id, delivery_id],
                countdown=countdown,
                queue="webhooks",
            )
        else:
            deliver_webhook_task.apply_async(
                args=[tenant_id, delivery_id],
                queue="webhooks",
            )
    except Exception:
        if countdown == 0:
            deliver_webhook_task(tenant_id, delivery_id)


@transaction.atomic
def fanout_outbound_webhooks_for_event(event: dict[str, Any]) -> list[WebhookDelivery]:
    tenant_id = require_tenant_id()
    event_name = str(event.get("name") or "").strip()
    event_id = str(event.get("event_id") or uuid.uuid4())
    occurred_at = str(event.get("occurred_at") or timezone.now().isoformat())
    contact_id = event.get("contact_id")
    raw_payload = dict(event.get("payload") or {})

    if not event_name:
        return []

    active_qs = WebhookEndpoint.objects.filter(active=True)

    if event_name == EventName.OUTBOUND_WEBHOOK_REQUESTED:
        target_ep_id = raw_payload.get("endpoint_id")
        target_url = str(raw_payload.get("url") or "").strip()
        if target_ep_id:
            try:
                parsed_ep = uuid.UUID(str(target_ep_id).strip())
                endpoints = list(active_qs.filter(pk=parsed_ep))
            except ValueError:
                endpoints = []
        elif target_url:
            matched_by_url = list(active_qs.filter(url=target_url))
            endpoints = matched_by_url if matched_by_url else list(active_qs)
        else:
            endpoints = list(active_qs)
    else:
        endpoints = [
            ep
            for ep in active_qs
            if _endpoint_matches_event(ep, event_name, raw_payload)
        ]

    if not endpoints:
        return []

    contact_summary = _build_contact_summary_for_webhook(contact_id)
    sanitized_data = _sanitize_payload_dict(raw_payload)

    delivery_payload = {
        "event": event_name,
        "event_id": event_id,
        "occurred_at": occurred_at,
        "tenant_id": str(tenant_id),
        "contact": contact_summary,
        "data": sanitized_data,
    }

    created_deliveries: list[WebhookDelivery] = []
    tid_str = str(tenant_id)
    for ep in endpoints:
        delivery = WebhookDelivery.objects.create(
            endpoint=ep,
            event_name=event_name,
            event_id=event_id,
            payload=delivery_payload,
            status=WebhookDelivery.Status.PENDING,
        )
        created_deliveries.append(delivery)
        did_str = str(delivery.id)
        transaction.on_commit(lambda t=tid_str, d=did_str: _enqueue_webhook_delivery(t, d))

    return created_deliveries


def _notify_webhook_dead_or_disabled(
    *,
    endpoint: WebhookEndpoint,
    delivery: WebhookDelivery | None,
    auto_disabled: bool,
) -> None:
    admin_ids = list(
        Membership.objects.filter(
            role__in=[Membership.Role.OWNER, Membership.Role.ADMIN],
            is_active=True,
        ).values_list("user_id", flat=True)
    )
    if not admin_ids:
        return

    if auto_disabled:
        title = f"Webhook Endpoint Auto-Disabled: {endpoint.name}"
        body = f"Endpoint '{endpoint.name}' ({endpoint.url}) was disabled after {endpoint.failure_count} consecutive failures."
    else:
        title = f"Webhook Delivery Exhausted Retries: {endpoint.name}"
        body = (
            f"Delivery for event '{delivery.event_name}' to '{endpoint.url}' failed after "
            f"{MAX_WEBHOOK_DELIVERY_ATTEMPTS} attempts."
            if delivery is not None
            else f"Webhook delivery to '{endpoint.url}' failed permanently."
        )

    notify(
        tenant=endpoint.tenant_id,
        users=admin_ids,
        type=NotificationType.WORKFLOW_INTEGRATION_ALERT,
        payload={
            "title": title,
            "body": body,
            "endpoint_id": str(endpoint.id),
            "endpoint_name": endpoint.name,
            "endpoint_url": endpoint.url,
            "delivery_id": str(delivery.id) if delivery else None,
            "auto_disabled": auto_disabled,
        },
        link=f"/webhooks/endpoints/{endpoint.id}",
    )


def compute_webhook_signature(secret: str, timestamp: str, raw_body: str) -> str:
    signing_input = f"{timestamp}.{raw_body}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def attempt_webhook_delivery(
    delivery_id: uuid.UUID | str,
    *,
    schedule_retry: bool = True,
) -> WebhookDelivery:
    tenant_id = require_tenant_id()
    with transaction.atomic():
        delivery = (
            WebhookDelivery.objects.select_for_update(of=("self",))
            .select_related("endpoint", "tenant")
            .get(pk=delivery_id)
        )
        if delivery.status in (WebhookDelivery.Status.SUCCESS, WebhookDelivery.Status.DEAD):
            return delivery
        endpoint = WebhookEndpoint.objects.select_for_update().get(pk=delivery.endpoint_id)
        target_url = endpoint.url
        secret = endpoint.signing_secret
        raw_body = json.dumps(delivery.payload or {}, separators=(",", ":"), sort_keys=True)
        ts_str = str(int(timezone.now().timestamp()))
        signature = compute_webhook_signature(secret, ts_str, raw_body)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "CRM-Webhook-Dispatcher/1.0",
            "X-Webhook-Signature": signature,
            "X-Webhook-Timestamp": ts_str,
            "X-Webhook-Event": delivery.event_name,
            "X-Webhook-Event-Id": delivery.event_id,
            "X-Webhook-Delivery-Id": str(delivery.id),
        }

    response_status: int | None = None
    response_excerpt = ""
    succeeded = False

    try:
        validate_webhook_target_url(target_url, require_dns_resolution=True)
        resp = requests.post(
            target_url,
            data=raw_body.encode("utf-8"),
            headers=headers,
            timeout=WEBHOOK_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        response_status = resp.status_code
        response_excerpt = (resp.text or "")[:1000]
        succeeded = 200 <= resp.status_code < 300
    except ApplicationError as exc:
        response_excerpt = str(exc.detail)[:1000]
        succeeded = False
    except Exception as exc:
        response_excerpt = str(exc)[:1000]
        succeeded = False

    now = timezone.now()
    with transaction.atomic():
        delivery = (
            WebhookDelivery.objects.select_for_update(of=("self",))
            .select_related("endpoint", "tenant")
            .get(pk=delivery_id)
        )
        endpoint = WebhookEndpoint.objects.select_for_update().get(pk=delivery.endpoint_id)
        delivery.attempts = (delivery.attempts or 0) + 1
        delivery.response_status = response_status
        delivery.response_excerpt = response_excerpt

        if succeeded:
            delivery.status = WebhookDelivery.Status.SUCCESS
            delivery.delivered_at = now
            delivery.next_retry_at = None
            delivery.save(
                update_fields=[
                    "attempts",
                    "response_status",
                    "response_excerpt",
                    "status",
                    "delivered_at",
                    "next_retry_at",
                    "updated_at",
                ]
            )
            if endpoint.failure_count > 0:
                endpoint.failure_count = 0
                endpoint.save(update_fields=["failure_count", "updated_at"])
            return delivery

        endpoint.failure_count = (endpoint.failure_count or 0) + 1
        auto_disabled = False
        if endpoint.active and endpoint.failure_count >= AUTO_DISABLE_FAILURE_THRESHOLD:
            endpoint.active = False
            endpoint.disabled_at = now
            auto_disabled = True
            endpoint.save(update_fields=["failure_count", "active", "disabled_at", "updated_at"])
        else:
            endpoint.save(update_fields=["failure_count", "updated_at"])

        if delivery.attempts >= MAX_WEBHOOK_DELIVERY_ATTEMPTS:
            delivery.status = WebhookDelivery.Status.DEAD
            delivery.next_retry_at = None
            delivery.save(
                update_fields=[
                    "attempts",
                    "response_status",
                    "response_excerpt",
                    "status",
                    "next_retry_at",
                    "updated_at",
                ]
            )
            _notify_webhook_dead_or_disabled(
                endpoint=endpoint,
                delivery=delivery,
                auto_disabled=False,
            )
        else:
            backoff_seconds = min(3600, (2 ** delivery.attempts) * 15) + random.randint(1, 15)
            delivery.status = WebhookDelivery.Status.FAILED
            delivery.next_retry_at = now + timedelta(seconds=backoff_seconds)
            delivery.save(
                update_fields=[
                    "attempts",
                    "response_status",
                    "response_excerpt",
                    "status",
                    "next_retry_at",
                    "updated_at",
                ]
            )
            if schedule_retry and endpoint.active:
                tid_str = str(tenant_id)
                did_str = str(delivery.id)
                transaction.on_commit(
                    lambda t=tid_str, d=did_str, b=backoff_seconds: _enqueue_webhook_delivery(
                        t,
                        d,
                        countdown=b,
                    )
                )

        if auto_disabled:
            _notify_webhook_dead_or_disabled(
                endpoint=endpoint,
                delivery=delivery,
                auto_disabled=True,
            )

        return delivery


@transaction.atomic
def ping_webhook_endpoint(*, endpoint: WebhookEndpoint) -> WebhookDelivery:
    tenant_id = require_tenant_id()
    event_id = str(uuid.uuid4())
    payload = {
        "event": "webhook.ping",
        "event_id": event_id,
        "occurred_at": timezone.now().isoformat(),
        "tenant_id": str(tenant_id),
        "contact": None,
        "data": {"ping": True, "endpoint_id": str(endpoint.id), "endpoint_name": endpoint.name},
    }
    delivery = WebhookDelivery.objects.create(
        endpoint=endpoint,
        event_name="webhook.ping",
        event_id=event_id,
        payload=payload,
        status=WebhookDelivery.Status.PENDING,
    )
    return attempt_webhook_delivery(delivery.id, schedule_retry=False)


@transaction.atomic
def redeliver_webhook_delivery(*, delivery: WebhookDelivery) -> WebhookDelivery:
    locked = WebhookDelivery.objects.select_for_update(of=("self",)).get(pk=delivery.pk)
    locked.status = WebhookDelivery.Status.PENDING
    locked.next_retry_at = None
    locked.error = "" if hasattr(locked, "error") else None
    if locked.attempts >= MAX_WEBHOOK_DELIVERY_ATTEMPTS:
        locked.attempts = MAX_WEBHOOK_DELIVERY_ATTEMPTS - 1
    locked.save(update_fields=["status", "next_retry_at", "attempts", "updated_at"])
    return attempt_webhook_delivery(locked.id, schedule_retry=True)
