import math
import re
from typing import Any
import uuid
from django.conf import settings
from django.core import signing
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import status
from accounts.models import User
from contacts.models import Activity, Appointment, Contact, CustomFieldDefinition
from contacts.services import ContactService, log_contact_activity, normalize_phone_e164
from core.context import require_tenant_id, tenant_context
from core.events import EventName, publish
from core.exceptions import ApplicationError
from integrations.providers.registry import (
    NormalizedInboundMessage,
    NormalizedStatusEvent,
)
from messaging.models import Broadcast, Channel, Message, MessageTemplate, WhatsAppCategory
from messaging.selectors import get_broadcast_audience_queryset, is_whatsapp_window_open

_GSM7_BASIC_CHARS = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM7_EXTENDED_CHARS = set("^{}\\[~]|€\f")

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.\-\s]+?)\s*\}\}")
_STOP_KEYWORDS = frozenset({"stop", "stopall", "unsubscribe", "cancel", "end", "quit"})
_START_KEYWORDS = frozenset({"start", "unstop", "subscribe", "yes"})
_UNSUBSCRIBE_SIGNING_SALT = "messaging.email.unsubscribe"

_CA_AREA_CODES = frozenset(
    {
        "204", "226", "236", "249", "250", "263", "289", "306", "343", "354",
        "365", "367", "368", "382", "403", "416", "418", "431", "437", "438",
        "450", "468", "474", "506", "514", "519", "548", "579", "581", "584",
        "587", "604", "613", "639", "647", "672", "683", "705", "709", "742",
        "753", "778", "780", "782", "807", "819", "825", "867", "873", "902",
        "905",
    }
)

_E164_PREFIX_TO_COUNTRY: tuple[tuple[str, str], ...] = (
    ("+971", "AE"),
    ("+966", "SA"),
    ("+965", "KW"),
    ("+974", "QA"),
    ("+973", "BH"),
    ("+968", "OM"),
    ("+962", "JO"),
    ("+961", "LB"),
    ("+972", "IL"),
    ("+880", "BD"),
    ("+886", "TW"),
    ("+852", "HK"),
    ("+358", "FI"),
    ("+353", "IE"),
    ("+351", "PT"),
    ("+420", "CZ"),
    ("+380", "UA"),
    ("+234", "NG"),
    ("+254", "KE"),
    ("+212", "MA"),
    ("+44", "GB"),
    ("+61", "AU"),
    ("+64", "NZ"),
    ("+91", "IN"),
    ("+92", "PK"),
    ("+49", "DE"),
    ("+33", "FR"),
    ("+34", "ES"),
    ("+39", "IT"),
    ("+31", "NL"),
    ("+32", "BE"),
    ("+41", "CH"),
    ("+43", "AT"),
    ("+46", "SE"),
    ("+47", "NO"),
    ("+45", "DK"),
    ("+48", "PL"),
    ("+40", "RO"),
    ("+36", "HU"),
    ("+30", "GR"),
    ("+90", "TR"),
    ("+55", "BR"),
    ("+52", "MX"),
    ("+54", "AR"),
    ("+56", "CL"),
    ("+57", "CO"),
    ("+51", "PE"),
    ("+65", "SG"),
    ("+60", "MY"),
    ("+62", "ID"),
    ("+63", "PH"),
    ("+66", "TH"),
    ("+84", "VN"),
    ("+81", "JP"),
    ("+82", "KR"),
    ("+86", "CN"),
    ("+27", "ZA"),
    ("+20", "EG"),
)


def calculate_sms_segments(body: str) -> dict[str, Any]:
    text = body or ""
    char_count = len(text)
    if char_count == 0:
        return {
            "encoding": "GSM-7",
            "char_count": 0,
            "effective_length": 0,
            "segments": 0,
            "per_segment_limit": 160,
            "remaining_in_segment": 160,
            "has_special_characters": False,
        }

    is_gsm7 = all((ch in _GSM7_BASIC_CHARS or ch in _GSM7_EXTENDED_CHARS) for ch in text)
    if is_gsm7:
        septets = sum(2 if ch in _GSM7_EXTENDED_CHARS else 1 for ch in text)
        if septets <= 160:
            segments = 1
            per_segment = 160
            remaining = 160 - septets
        else:
            segments = math.ceil(septets / 153)
            per_segment = 153
            remaining = (segments * 153) - septets
        return {
            "encoding": "GSM-7",
            "char_count": char_count,
            "effective_length": septets,
            "segments": segments,
            "per_segment_limit": per_segment,
            "remaining_in_segment": remaining,
            "has_special_characters": False,
        }

    if char_count <= 70:
        segments = 1
        per_segment = 70
        remaining = 70 - char_count
    else:
        segments = math.ceil(char_count / 67)
        per_segment = 67
        remaining = (segments * 67) - char_count

    return {
        "encoding": "UCS-2",
        "char_count": char_count,
        "effective_length": char_count,
        "segments": segments,
        "per_segment_limit": per_segment,
        "remaining_in_segment": remaining,
        "has_special_characters": True,
    }


def resolve_destination_country(phone: str | None, fallback_country: str = "") -> str:
    raw = (phone or "").strip()
    if raw.startswith("+1") and len(raw) >= 5:
        area_code = raw[2:5]
        if area_code in _CA_AREA_CODES:
            return "CA"
        return "US"

    for prefix, iso_code in _E164_PREFIX_TO_COUNTRY:
        if raw.startswith(prefix):
            return iso_code

    fb = (fallback_country or "").strip().upper()
    if len(fb) == 2 and fb.isalpha():
        return fb
    return "US" if raw.startswith("+") else ""


def extract_template_variables(*texts: str) -> list[str]:
    seen: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in _PLACEHOLDER_RE.findall(text):
            norm = match.strip()
            if norm and norm not in seen:
                seen.append(norm)
    return seen


def _normalize_var_key(raw: str) -> str:
    cleaned = raw.strip().lower()
    if cleaned.startswith("contact."):
        cleaned = cleaned[len("contact."):]
    if cleaned.startswith("custom_fields."):
        cleaned = cleaned[len("custom_fields."):]
    return re.sub(r"[\s\-]+", "_", cleaned)


def build_contact_placeholder_map(
    contact: Contact,
    extra_context: dict[str, Any] | None = None,
) -> dict[str, str]:
    tenant_name = ""
    try:
        if getattr(contact, "tenant", None):
            tenant_name = contact.tenant.name or ""
        elif getattr(contact, "tenant_id", None):
            from core.models import Tenant
            tenant_obj = Tenant.objects.filter(pk=contact.tenant_id).only("name").first()
            if tenant_obj:
                tenant_name = tenant_obj.name or ""
    except Exception:
        tenant_name = ""

    assigned_to_name = ""
    assigned_to_email = ""
    try:
        membership = getattr(contact, "assigned_to", None)
        if membership:
            user = getattr(membership, "user", None)
            if user:
                assigned_to_name = (user.full_name or user.first_name or user.email or "").strip()
                assigned_to_email = (user.email or "").strip()
    except Exception:
        assigned_to_name = ""
        assigned_to_email = ""

    values: dict[str, str] = {
        "first_name": contact.first_name or "",
        "last_name": contact.last_name or "",
        "full_name": contact.full_name or f"{contact.first_name} {contact.last_name}".strip(),
        "name": contact.full_name or contact.first_name or "",
        "email": contact.email or "",
        "phone": contact.phone or "",
        "city": contact.city or "",
        "country": contact.country or "",
        "timezone": contact.timezone or "UTC",
        "source_channel": contact.source_channel or "",
        "source_page": contact.source_page or "",
        "source_form": contact.source_form or "",
        "campaign": contact.campaign or "",
        "ad_set": contact.ad_set or "",
        "ad": contact.ad or "",
        "tenant_name": tenant_name,
        "tenant": tenant_name,
        "gym_name": tenant_name,
        "business_name": tenant_name,
        "company_name": tenant_name,
        "tenant.name": tenant_name,
        "assigned_to": assigned_to_name,
        "assigned_to_name": assigned_to_name,
        "assignee": assigned_to_name,
        "assignee_name": assigned_to_name,
        "assigned_to.name": assigned_to_name,
        "assigned_to.full_name": assigned_to_name,
        "assigned_to_email": assigned_to_email,
        "assigned_to.email": assigned_to_email,
    }

    try:
        next_appt = (
            Appointment.objects.filter(
                contact=contact,
                status__in=[Appointment.Status.BOOKED, Appointment.Status.RESCHEDULED],
            )
            .order_by("starts_at")
            .first()
        )
        if next_appt is not None:
            iso_dt = next_appt.starts_at.strftime("%Y-%m-%d %H:%M UTC")
            values["appointment_date"] = iso_dt
            values["appointment_time"] = next_appt.starts_at.strftime("%H:%M UTC")
            values["appointment_location"] = next_appt.location or ""
            values["starts_at"] = iso_dt
    except Exception:
        pass

    custom_data = contact.custom_fields or {}
    if isinstance(custom_data, dict):
        for k, v in custom_data.items():
            if v is not None:
                str_val = str(v)
                values[str(k)] = str_val
                values[_normalize_var_key(str(k))] = str_val

        try:
            definitions = CustomFieldDefinition.objects.filter(tenant_id=contact.tenant_id)
            for defn in definitions:
                if defn.key in custom_data and custom_data[defn.key] is not None:
                    values[_normalize_var_key(defn.label)] = str(custom_data[defn.key])
        except Exception:
            pass

    if extra_context:
        for k, v in extra_context.items():
            if v is not None:
                str_val = str(v)
                values[str(k)] = str_val
                values[_normalize_var_key(str(k))] = str_val

    return values


def render_template_placeholders(
    text: str,
    contact: Contact,
    extra_context: dict[str, Any] | None = None,
) -> str:
    if not text:
        return ""
    lookup = build_contact_placeholder_map(contact, extra_context=extra_context)

    def _replace(match: re.Match) -> str:
        raw_token = match.group(1).strip()
        if raw_token in lookup:
            return lookup[raw_token]
        norm_token = _normalize_var_key(raw_token)
        if norm_token in lookup:
            return lookup[norm_token]
        return ""

    return _PLACEHOLDER_RE.sub(_replace, text)


def generate_unsubscribe_token(
    *,
    tenant_id: uuid.UUID | str,
    contact_id: uuid.UUID | str,
    email: str,
) -> str:
    payload = {
        "t": str(tenant_id),
        "c": str(contact_id),
        "e": (email or "").strip().lower(),
    }
    return signing.dumps(payload, salt=_UNSUBSCRIBE_SIGNING_SALT)


def verify_unsubscribe_token(token: str, max_age: int = 60 * 60 * 24 * 365) -> dict[str, str]:
    try:
        data = signing.loads(token, salt=_UNSUBSCRIBE_SIGNING_SALT, max_age=max_age)
        if not isinstance(data, dict) or "t" not in data or "c" not in data:
            raise ValueError("Invalid token payload")
        return {
            "tenant_id": str(data["t"]),
            "contact_id": str(data["c"]),
            "email": str(data.get("e") or ""),
        }
    except Exception as exc:
        raise ApplicationError(
            detail="Invalid or expired unsubscribe token.",
            code="invalid_unsubscribe_token",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc


def build_unsubscribe_url(token: str) -> str:
    base_url = getattr(settings, "PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base_url}/webhooks/unsubscribe/{token}/"


def append_email_unsubscribe_footer(body: str, unsubscribe_url: str) -> str:
    if not unsubscribe_url or unsubscribe_url in body:
        return body
    if "<html" in body.lower() or "<body" in body.lower() or "<p>" in body.lower():
        footer = f'<br><hr><p style="font-size:12px;color:#666;">To stop receiving these emails, <a href="{unsubscribe_url}">unsubscribe here</a>.</p>'
        return f"{body}{footer}"
    return f"{body}\n\n---\nUnsubscribe: {unsubscribe_url}"


@transaction.atomic
def confirm_email_unsubscribe(token: str) -> Contact:
    parsed = verify_unsubscribe_token(token)
    tenant_id = parsed["tenant_id"]
    contact_id = parsed["contact_id"]

    with tenant_context(tenant_id):
        contact = Contact.objects.select_for_update().filter(pk=contact_id).first()
        if contact is None:
            raise ApplicationError(
                detail="Contact not found for this unsubscribe link.",
                code="contact_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return ContactService.update_subscriptions(
            contact=contact,
            email_subscription=Contact.SubscriptionStatus.UNSUBSCRIBED,
        )


def _validate_recipient_and_subscription(contact: Contact, channel: str) -> None:
    if channel == Channel.EMAIL:
        if not contact.email:
            raise ApplicationError(
                detail="Contact does not have an email address.",
                code="missing_recipient_email",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        try:
            validate_email(contact.email)
        except Exception as exc:
            raise ApplicationError(
                detail="Contact email address is invalid.",
                code="invalid_recipient_email",
                status_code=status.HTTP_400_BAD_REQUEST,
            ) from exc

        if contact.email_subscription == Contact.SubscriptionStatus.BOUNCED:
            raise ApplicationError(
                detail="Cannot send email to a contact whose email address has bounced.",
                code="contact_email_bounced",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if contact.email_subscription != Contact.SubscriptionStatus.SUBSCRIBED:
            raise ApplicationError(
                detail="Contact has unsubscribed from email communications.",
                code="contact_opted_out",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    elif channel == Channel.SMS:
        if not contact.phone:
            raise ApplicationError(
                detail="Contact does not have a phone number.",
                code="missing_recipient_phone",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        normalize_phone_e164(contact.phone)
        if contact.sms_subscription != Contact.SubscriptionStatus.SUBSCRIBED:
            raise ApplicationError(
                detail="Contact has opted out of SMS communications.",
                code="contact_opted_out",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    elif channel == Channel.WHATSAPP:
        if not contact.phone:
            raise ApplicationError(
                detail="Contact does not have a phone number.",
                code="missing_recipient_phone",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        normalize_phone_e164(contact.phone)
        if contact.whatsapp_subscription != Contact.SubscriptionStatus.SUBSCRIBED:
            raise ApplicationError(
                detail="Contact has opted out of WhatsApp communications.",
                code="contact_opted_out",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    else:
        raise ApplicationError(
            detail=f"Unsupported messaging channel '{channel}'.",
            code="unsupported_channel",
            status_code=status.HTTP_400_BAD_REQUEST,
        )


class MessagingService:
    @staticmethod
    @transaction.atomic
    def send(
        *,
        contact: Contact,
        channel: str,
        body: str = "",
        subject: str = "",
        template: MessageTemplate | None = None,
        source: str = Message.Source.MANUAL,
        sent_by: User | None = None,
        broadcast: Broadcast | None = None,
        idempotency_key: str | None = None,
        extra_context: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        tenant_id = require_tenant_id()
        if getattr(contact.tenant, "deactivated_at", None) is not None:
            raise ApplicationError(
                detail="Cannot send messages while tenant is deactivated.",
                code="tenant_deactivated",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if sent_by is not None and not getattr(sent_by, "email_verified", False):
            raise ApplicationError(
                detail="Email address must be verified before sending messages.",
                code="email_not_verified",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        norm_channel = (channel or (template.channel if template else "")).strip().lower()
        clean_idemp_key = (idempotency_key or "").strip() or None

        if clean_idemp_key:
            existing_msg = Message.objects.filter(idempotency_key=clean_idemp_key).first()
            if existing_msg is not None:
                return existing_msg

        if template is not None:
            if template.channel != norm_channel:
                raise ApplicationError(
                    detail=f"Template channel '{template.channel}' does not match send channel '{norm_channel}'.",
                    code="template_channel_mismatch",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            if not template.active:
                raise ApplicationError(
                    detail="Cannot send using an inactive message template.",
                    code="inactive_template",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

        _validate_recipient_and_subscription(contact, norm_channel)

        whatsapp_category = ""
        if norm_channel == Channel.WHATSAPP:
            window_open = is_whatsapp_window_open(contact)
            if not window_open:
                if (
                    template is None
                    or template.approval_status != MessageTemplate.ApprovalStatus.APPROVED
                ):
                    raise ApplicationError(
                        detail="Outside the 24-hour WhatsApp customer service window, an approved WhatsApp template is required.",
                        code="whatsapp_template_required",
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )
            if template is not None:
                if template.approval_status != MessageTemplate.ApprovalStatus.APPROVED:
                    raise ApplicationError(
                        detail="WhatsApp template must be approved before sending.",
                        code="whatsapp_template_not_approved",
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )
                whatsapp_category = template.whatsapp_category or WhatsAppCategory.UTILITY
            else:
                whatsapp_category = WhatsAppCategory.SERVICE

        raw_subject = subject or (template.subject if template else "")
        raw_body = body or (template.body if template else "")

        if not raw_body.strip():
            raise ApplicationError(
                detail="Message body cannot be empty.",
                code="empty_message_body",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        rendered_subject = render_template_placeholders(raw_subject, contact, extra_context=extra_context)
        rendered_body = render_template_placeholders(raw_body, contact, extra_context=extra_context)

        msg_metadata: dict[str, Any] = dict(metadata or {})
        if template is not None and norm_channel == Channel.WHATSAPP:
            lookup = build_contact_placeholder_map(contact, extra_context=extra_context)
            content_vars: dict[str, str] = {}
            for idx, var_name in enumerate(template.variables or [], start=1):
                val = lookup.get(var_name) or lookup.get(_normalize_var_key(var_name)) or ""
                content_vars[str(var_name)] = val
                content_vars[str(idx)] = val
            msg_metadata["content_sid"] = template.whatsapp_content_sid
            msg_metadata["content_variables"] = content_vars

        if norm_channel == Channel.EMAIL:
            unsub_token = generate_unsubscribe_token(
                tenant_id=tenant_id,
                contact_id=contact.id,
                email=contact.email or "",
            )
            unsub_url = build_unsubscribe_url(unsub_token)
            rendered_body = append_email_unsubscribe_footer(rendered_body, unsub_url)
            msg_metadata["unsubscribe_url"] = unsub_url

        if norm_channel == Channel.SMS:
            segment_info = calculate_sms_segments(rendered_body)
            segments = max(1, segment_info["segments"])
            msg_metadata["encoding"] = segment_info["encoding"]
        else:
            segments = 1

        dest_country = (
            resolve_destination_country(contact.phone, contact.country)
            if norm_channel in (Channel.SMS, Channel.WHATSAPP)
            else (contact.country[:2].upper() if contact.country else "")
        )

        try:
            message = Message.objects.create(
                contact=contact,
                channel=norm_channel,
                direction=Message.Direction.OUT,
                status=Message.Status.QUEUED,
                body=rendered_body,
                subject=rendered_subject,
                segments=segments,
                destination_country=dest_country,
                whatsapp_category=whatsapp_category,
                source=source,
                template=template,
                broadcast=broadcast,
                sent_by=sent_by,
                idempotency_key=clean_idemp_key,
                metadata=msg_metadata,
            )
        except IntegrityError:
            if clean_idemp_key:
                existing_msg = Message.objects.filter(idempotency_key=clean_idemp_key).first()
                if existing_msg is not None:
                    return existing_msg
            raise

        log_contact_activity(
            contact=contact,
            actor=sent_by,
            activity_type=Activity.ActivityType.MESSAGE_OUT,
            description=f"Outbound {norm_channel.upper()} message queued ({source}).",
            metadata={
                "message_id": str(message.id),
                "channel": norm_channel,
                "source": source,
                "subject": rendered_subject,
                "segments": segments,
                "template_id": str(template.id) if template else None,
                "broadcast_id": str(broadcast.id) if broadcast else None,
            },
        )

        event_payload = {
            "message_id": str(message.id),
            "channel": norm_channel,
            "direction": Message.Direction.OUT,
            "status": message.status,
            "source": source,
            "segments": segments,
            "destination_country": dest_country,
            "whatsapp_category": whatsapp_category,
            "template_id": str(template.id) if template else None,
            "broadcast_id": str(broadcast.id) if broadcast else None,
        }
        publish(
            EventName.MESSAGE_SENT,
            tenant_id=tenant_id,
            contact_id=contact.id,
            payload=event_payload,
        )
        publish(
            EventName.MESSAGE_STATUS_CHANGED,
            tenant_id=tenant_id,
            contact_id=contact.id,
            payload=event_payload,
        )

        def _enqueue_send():
            from messaging.tasks import dispatch_outbound_message_task
            try:
                dispatch_outbound_message_task.apply_async(
                    args=[str(tenant_id), str(message.id)],
                    queue="messaging",
                )
            except Exception:
                dispatch_outbound_message_task(str(tenant_id), str(message.id))

        transaction.on_commit(_enqueue_send)
        return message


@transaction.atomic
def process_inbound_message(
    *,
    inbound: NormalizedInboundMessage,
) -> Message | None:
    tenant_id = require_tenant_id()
    norm_channel = inbound.channel.strip().lower()
    prov_msg_id = (inbound.provider_message_id or "").strip()

    if prov_msg_id:
        existing = Message.objects.filter(
            provider=inbound.provider,
            provider_message_id=prov_msg_id,
        ).first()
        if existing is not None:
            return existing

    if norm_channel == Channel.EMAIL:
        contact, _ = ContactService.upsert_from_source(
            full_name=inbound.sender_name,
            email=inbound.sender,
            source_channel="incoming_message",
        )
    else:
        contact, _ = ContactService.upsert_from_source(
            full_name=inbound.sender_name,
            phone=inbound.sender,
            source_channel="incoming_message",
        )

    cleaned_keyword = re.sub(r"[^a-zA-Z]", "", (inbound.body or "").strip()).lower()
    button_keyword = re.sub(r"[^a-zA-Z]", "", (inbound.button_payload or "").strip()).lower()
    effective_keyword = cleaned_keyword or button_keyword

    if norm_channel in (Channel.SMS, Channel.WHATSAPP) and effective_keyword in _STOP_KEYWORDS:
        if norm_channel == Channel.SMS:
            ContactService.update_subscriptions(
                contact=contact,
                sms_subscription=Contact.SubscriptionStatus.UNSUBSCRIBED,
            )
        else:
            ContactService.update_subscriptions(
                contact=contact,
                whatsapp_subscription=Contact.SubscriptionStatus.UNSUBSCRIBED,
            )
    elif norm_channel in (Channel.SMS, Channel.WHATSAPP) and effective_keyword in _START_KEYWORDS:
        if norm_channel == Channel.SMS:
            ContactService.update_subscriptions(
                contact=contact,
                sms_subscription=Contact.SubscriptionStatus.SUBSCRIBED,
            )
        else:
            ContactService.update_subscriptions(
                contact=contact,
                whatsapp_subscription=Contact.SubscriptionStatus.SUBSCRIBED,
            )

    dest_country = (
        resolve_destination_country(contact.phone, contact.country)
        if norm_channel in (Channel.SMS, Channel.WHATSAPP)
        else (contact.country[:2].upper() if contact.country else "")
    )
    segments = (
        max(1, calculate_sms_segments(inbound.body)["segments"])
        if norm_channel == Channel.SMS
        else 1
    )

    now = timezone.now()
    message = Message.objects.create(
        contact=contact,
        channel=norm_channel,
        direction=Message.Direction.IN,
        status=Message.Status.DELIVERED,
        body=inbound.body,
        subject=inbound.subject,
        provider=inbound.provider,
        provider_message_id=prov_msg_id or None,
        segments=segments,
        destination_country=dest_country,
        whatsapp_category=WhatsAppCategory.SERVICE if norm_channel == Channel.WHATSAPP else "",
        source=Message.Source.MANUAL,
        sent_at=now,
        delivered_at=now,
        metadata={
            "sender": inbound.sender,
            "recipient": inbound.recipient,
            "button_payload": inbound.button_payload,
            "button_text": inbound.button_text,
        },
    )

    log_contact_activity(
        contact=contact,
        activity_type=Activity.ActivityType.MESSAGE_IN,
        description=f"Received {norm_channel.upper()} message.",
        metadata={
            "message_id": str(message.id),
            "channel": norm_channel,
            "subject": inbound.subject,
            "body_preview": (inbound.body or "")[:200],
            "button_payload": inbound.button_payload,
            "button_text": inbound.button_text,
        },
    )

    event_map = {
        Channel.EMAIL: EventName.EMAIL_RECEIVED,
        Channel.SMS: EventName.SMS_RECEIVED,
        Channel.WHATSAPP: EventName.WHATSAPP_RECEIVED,
    }
    event_name = event_map[norm_channel]
    contact_label = contact.full_name or contact.email or contact.phone or "Contact"
    publish(
        event_name,
        tenant_id=tenant_id,
        contact_id=contact.id,
        payload={
            "title": f"New {norm_channel.upper()} reply from {contact_label}",
            "body": (inbound.body or inbound.subject or "")[:240],
            "message_id": str(message.id),
            "channel": norm_channel,
            "subject": inbound.subject,
            "button_payload": inbound.button_payload,
            "button_text": inbound.button_text,
            "assigned_membership_id": str(contact.assigned_to_id) if contact.assigned_to_id else None,
            "assigned_user_id": str(contact.assigned_to.user_id) if contact.assigned_to else None,
        },
    )
    return message


@transaction.atomic
def process_status_event(*, status_event: NormalizedStatusEvent) -> Message | None:
    tenant_id = require_tenant_id()
    prov_id = (status_event.provider_message_id or "").strip()
    mapped_status = (status_event.status or "").strip().lower()

    message: Message | None = None
    if prov_id:
        message = (
            Message.objects.select_for_update()
            .select_related("contact")
            .filter(
                provider=status_event.provider,
                provider_message_id=prov_id,
            )
            .first()
        )
        if message is None:
            message = (
                Message.objects.select_for_update()
                .select_related("contact")
                .filter(
                    provider=status_event.provider,
                    provider_message_id__startswith=f"{prov_id}.",
                )
                .first()
            )

    contact: Contact | None = message.contact if message is not None else None
    if contact is None and status_event.recipient:
        if status_event.channel == Channel.EMAIL:
            contact = Contact.objects.filter(email=status_event.recipient.lower()).first()
        else:
            try:
                norm_phone = normalize_phone_e164(status_event.recipient)
            except Exception:
                norm_phone = status_event.recipient
            contact = Contact.objects.filter(phone=norm_phone).first()

    if mapped_status == "unsubscribed":
        if contact is not None:
            if status_event.channel == Channel.EMAIL:
                ContactService.update_subscriptions(
                    contact=contact,
                    email_subscription=Contact.SubscriptionStatus.UNSUBSCRIBED,
                )
            elif status_event.channel == Channel.SMS:
                ContactService.update_subscriptions(
                    contact=contact,
                    sms_subscription=Contact.SubscriptionStatus.UNSUBSCRIBED,
                )
            elif status_event.channel == Channel.WHATSAPP:
                ContactService.update_subscriptions(
                    contact=contact,
                    whatsapp_subscription=Contact.SubscriptionStatus.UNSUBSCRIBED,
                )
        return message

    if mapped_status == Message.Status.BOUNCED and contact is not None and status_event.channel == Channel.EMAIL:
        ContactService.update_subscriptions(
            contact=contact,
            email_subscription=Contact.SubscriptionStatus.BOUNCED,
        )

    if message is None:
        return None

    valid_statuses = {choice[0] for choice in Message.Status.choices}
    if mapped_status not in valid_statuses:
        return message

    status_rank = {
        Message.Status.QUEUED: 1,
        Message.Status.SENT: 2,
        Message.Status.DELIVERED: 3,
        Message.Status.READ: 4,
        Message.Status.UNDELIVERED: 5,
        Message.Status.FAILED: 5,
        Message.Status.BOUNCED: 6,
    }
    old_status = message.status
    if old_status == mapped_status:
        return message

    if status_rank.get(mapped_status, 0) < status_rank.get(old_status, 0) and old_status not in (
        Message.Status.QUEUED,
        Message.Status.SENT,
    ):
        return message

    now = status_event.occurred_at or timezone.now()
    message.status = mapped_status
    update_fields = ["status", "updated_at"]

    if mapped_status == Message.Status.SENT and message.sent_at is None:
        message.sent_at = now
        update_fields.append("sent_at")
    elif mapped_status == Message.Status.DELIVERED:
        if message.sent_at is None:
            message.sent_at = now
            update_fields.append("sent_at")
        if message.delivered_at is None:
            message.delivered_at = now
            update_fields.append("delivered_at")
    elif mapped_status == Message.Status.READ:
        if message.delivered_at is None:
            message.delivered_at = now
            update_fields.append("delivered_at")
        if message.read_at is None:
            message.read_at = now
            update_fields.append("read_at")

    if status_event.error:
        message.error = status_event.error
        update_fields.append("error")

    message.save(update_fields=update_fields)

    if message.broadcast_id:
        if mapped_status == Message.Status.DELIVERED and old_status != Message.Status.DELIVERED:
            Broadcast.objects.filter(pk=message.broadcast_id).update(
                delivered_count=F("delivered_count") + 1,
            )
        elif (
            mapped_status in (Message.Status.FAILED, Message.Status.UNDELIVERED, Message.Status.BOUNCED)
            and old_status not in (Message.Status.FAILED, Message.Status.UNDELIVERED, Message.Status.BOUNCED)
        ):
            Broadcast.objects.filter(pk=message.broadcast_id).update(
                failed_count=F("failed_count") + 1,
            )

    publish(
        EventName.MESSAGE_STATUS_CHANGED,
        tenant_id=tenant_id,
        contact_id=message.contact_id,
        payload={
            "message_id": str(message.id),
            "channel": message.channel,
            "old_status": old_status,
            "status": message.status,
            "error": message.error,
            "provider_message_id": message.provider_message_id,
            "broadcast_id": str(message.broadcast_id) if message.broadcast_id else None,
        },
    )
    return message


@transaction.atomic
def create_message_template(
    *,
    channel: str,
    name: str,
    subject: str = "",
    body: str,
    variables: list[str] | None = None,
    active: bool = True,
) -> MessageTemplate:
    norm_channel = (channel or "").strip().lower()
    if norm_channel == Channel.WHATSAPP:
        raise ApplicationError(
            detail="WhatsApp templates are read-only and must be synchronized from Twilio Content API.",
            code="whatsapp_template_readonly",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if norm_channel not in (Channel.EMAIL, Channel.SMS):
        raise ApplicationError(
            detail="Template channel must be 'email' or 'sms'.",
            code="invalid_template_channel",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    cleaned_name = name.strip()
    if MessageTemplate.objects.filter(channel=norm_channel, name__iexact=cleaned_name).exists():
        raise ApplicationError(
            detail=f"A {norm_channel} template with this name already exists.",
            code="duplicate_template_name",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    resolved_vars = (
        list(variables)
        if variables is not None
        else extract_template_variables(subject, body)
    )
    return MessageTemplate.objects.create(
        channel=norm_channel,
        name=cleaned_name,
        subject=subject.strip() if norm_channel == Channel.EMAIL else "",
        body=body,
        variables=resolved_vars,
        approval_status=MessageTemplate.ApprovalStatus.NOT_REQUIRED,
        active=active,
    )


@transaction.atomic
def update_message_template(
    *,
    template: MessageTemplate,
    name: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    variables: list[str] | None = None,
    active: bool | None = None,
) -> MessageTemplate:
    if template.channel == Channel.WHATSAPP:
        raise ApplicationError(
            detail="WhatsApp templates are read-only and must be synchronized from Twilio Content API.",
            code="whatsapp_template_readonly",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if name is not None:
        cleaned_name = name.strip()
        if (
            MessageTemplate.objects.filter(channel=template.channel, name__iexact=cleaned_name)
            .exclude(pk=template.pk)
            .exists()
        ):
            raise ApplicationError(
                detail=f"A {template.channel} template with this name already exists.",
                code="duplicate_template_name",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        template.name = cleaned_name

    if subject is not None and template.channel == Channel.EMAIL:
        template.subject = subject.strip()
    if body is not None:
        template.body = body
    if variables is not None:
        template.variables = list(variables)
    elif subject is not None or body is not None:
        template.variables = extract_template_variables(template.subject, template.body)
    if active is not None:
        template.active = active

    template.save()
    return template


@transaction.atomic
def delete_message_template(*, template: MessageTemplate) -> None:
    if template.channel == Channel.WHATSAPP:
        raise ApplicationError(
            detail="WhatsApp templates are read-only and cannot be deleted manually.",
            code="whatsapp_template_readonly",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if Broadcast.objects.filter(template=template).exists():
        raise ApplicationError(
            detail="Cannot delete a template that is referenced by a broadcast.",
            code="template_in_use",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    template.delete()


def preview_template_for_contact(
    *,
    contact: Contact,
    template: MessageTemplate | None = None,
    channel: str = "",
    subject: str = "",
    body: str = "",
    extra_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_channel = (channel or (template.channel if template else "") or Channel.EMAIL).strip().lower()
    raw_subject = subject if subject else (template.subject if template else "")
    raw_body = body if body else (template.body if template else "")

    rendered_subject = render_template_placeholders(raw_subject, contact, extra_context=extra_context)
    rendered_body = render_template_placeholders(raw_body, contact, extra_context=extra_context)

    result: dict[str, Any] = {
        "channel": effective_channel,
        "subject": rendered_subject,
        "body": rendered_body,
        "variables": extract_template_variables(raw_subject, raw_body),
    }
    if effective_channel == Channel.SMS:
        result["sms_segments"] = calculate_sms_segments(rendered_body)
    return result


@transaction.atomic
def create_broadcast(
    *,
    name: str = "",
    template: MessageTemplate,
    audience_filter: dict[str, Any] | None = None,
    scheduled_at: Any = None,
    created_by: User | None = None,
) -> Broadcast:
    if not template.active:
        raise ApplicationError(
            detail="Cannot create a broadcast with an inactive template.",
            code="inactive_template",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if (
        template.channel == Channel.WHATSAPP
        and template.approval_status != MessageTemplate.ApprovalStatus.APPROVED
    ):
        raise ApplicationError(
            detail="WhatsApp broadcast templates must be approved.",
            code="whatsapp_template_not_approved",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    filter_dict = dict(audience_filter or {})
    recipient_count = get_broadcast_audience_queryset(filter_dict, template.channel).count()

    return Broadcast.objects.create(
        name=(name or template.name).strip(),
        template=template,
        audience_filter=filter_dict,
        scheduled_at=scheduled_at,
        status=Broadcast.Status.DRAFT,
        total_recipients=recipient_count,
        created_by=created_by,
    )


@transaction.atomic
def update_broadcast(
    *,
    broadcast: Broadcast,
    name: str | None = None,
    template: MessageTemplate | None = None,
    audience_filter: dict[str, Any] | None = None,
    scheduled_at: Any = None,
) -> Broadcast:
    if broadcast.status != Broadcast.Status.DRAFT:
        raise ApplicationError(
            detail="Only broadcasts in draft status can be updated.",
            code="broadcast_not_editable",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if name is not None:
        broadcast.name = name.strip()
    if template is not None:
        if not template.active:
            raise ApplicationError(
                detail="Cannot assign an inactive template to a broadcast.",
                code="inactive_template",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        broadcast.template = template
    if audience_filter is not None:
        broadcast.audience_filter = dict(audience_filter)
    if scheduled_at is not None:
        broadcast.scheduled_at = scheduled_at

    broadcast.total_recipients = get_broadcast_audience_queryset(
        broadcast.audience_filter,
        broadcast.template.channel,
    ).count()
    broadcast.save()
    return broadcast


@transaction.atomic
def schedule_broadcast(
    *,
    broadcast: Broadcast,
    scheduled_at: Any = None,
) -> Broadcast:
    if broadcast.status not in (Broadcast.Status.DRAFT, Broadcast.Status.SCHEDULED):
        raise ApplicationError(
            detail=f"Cannot schedule a broadcast in '{broadcast.status}' status.",
            code="invalid_broadcast_status",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    target_time = scheduled_at or broadcast.scheduled_at or timezone.now()
    recipient_count = get_broadcast_audience_queryset(
        broadcast.audience_filter,
        broadcast.template.channel,
    ).count()

    broadcast.scheduled_at = target_time
    broadcast.status = Broadcast.Status.SCHEDULED
    broadcast.total_recipients = recipient_count
    broadcast.save(update_fields=["scheduled_at", "status", "total_recipients", "updated_at"])
    return broadcast


@transaction.atomic
def cancel_broadcast(*, broadcast: Broadcast) -> Broadcast:
    if broadcast.status in (Broadcast.Status.COMPLETED, Broadcast.Status.CANCELLED):
        raise ApplicationError(
            detail=f"Broadcast is already {broadcast.status}.",
            code="invalid_broadcast_status",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    broadcast.status = Broadcast.Status.CANCELLED
    broadcast.save(update_fields=["status", "updated_at"])
    return broadcast


def get_broadcast_progress(broadcast: Broadcast) -> dict[str, Any]:
    total = broadcast.total_recipients
    processed = broadcast.sent_count + broadcast.failed_count + broadcast.skipped_count
    pct = round((processed / total) * 100.0, 2) if total > 0 else (100.0 if broadcast.status == Broadcast.Status.COMPLETED else 0.0)
    return {
        "id": str(broadcast.id),
        "name": broadcast.name,
        "channel": broadcast.template.channel,
        "status": broadcast.status,
        "scheduled_at": broadcast.scheduled_at,
        "started_at": broadcast.started_at,
        "completed_at": broadcast.completed_at,
        "total_recipients": total,
        "sent_count": broadcast.sent_count,
        "delivered_count": broadcast.delivered_count,
        "failed_count": broadcast.failed_count,
        "skipped_count": broadcast.skipped_count,
        "progress_percentage": min(100.0, pct),
    }
