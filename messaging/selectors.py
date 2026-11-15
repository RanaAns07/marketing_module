from collections.abc import Mapping
from datetime import timedelta
from typing import Any
import uuid
from django.db.models import QuerySet
from django.utils import timezone
from accounts.models import Membership
from contacts.models import Contact
from contacts.selectors import filter_contacts_queryset
from messaging.models import Broadcast, Channel, Message, MessageTemplate


def get_message_templates_queryset(
    *,
    channel: str | None = None,
    active: bool | None = None,
    approval_status: str | None = None,
) -> QuerySet[MessageTemplate]:
    qs = MessageTemplate.objects.select_related("tenant").order_by("channel", "name", "-created_at")
    if channel:
        qs = qs.filter(channel=channel.strip().lower())
    if active is not None:
        qs = qs.filter(active=active)
    if approval_status:
        qs = qs.filter(approval_status=approval_status.strip().lower())
    return qs


def get_message_template_by_id(template_id: uuid.UUID | str) -> MessageTemplate | None:
    return MessageTemplate.objects.select_related("tenant").filter(pk=template_id).first()


def get_messages_queryset(
    *,
    membership: Membership | None = None,
    contact: Contact | None = None,
    contact_id: uuid.UUID | str | None = None,
    channel: str | None = None,
    direction: str | None = None,
    status_filter: str | None = None,
    broadcast_id: uuid.UUID | str | None = None,
) -> QuerySet[Message]:
    qs = Message.objects.select_related(
        "contact",
        "template",
        "broadcast",
        "sent_by",
        "tenant",
    ).order_by("-created_at", "-id")

    if (
        membership is not None
        and membership.role == Membership.Role.MARKETER
        and not membership.tenant.marketer_can_view_all_contacts
    ):
        qs = qs.filter(contact__assigned_to=membership)

    if contact is not None:
        qs = qs.filter(contact=contact)
    elif contact_id is not None:
        qs = qs.filter(contact_id=contact_id)

    if channel:
        qs = qs.filter(channel=channel.strip().lower())
    if direction:
        qs = qs.filter(direction=direction.strip().lower())
    if status_filter:
        qs = qs.filter(status=status_filter.strip().lower())
    if broadcast_id:
        qs = qs.filter(broadcast_id=broadcast_id)

    return qs


def get_broadcasts_queryset(status_filter: str | None = None) -> QuerySet[Broadcast]:
    qs = Broadcast.objects.select_related("template", "created_by", "tenant").order_by("-created_at", "-id")
    if status_filter:
        qs = qs.filter(status=status_filter.strip().lower())
    return qs


def get_broadcast_by_id(broadcast_id: uuid.UUID | str) -> Broadcast | None:
    return Broadcast.objects.select_related("template", "created_by", "tenant").filter(pk=broadcast_id).first()


def get_last_inbound_whatsapp_at(contact: Contact):
    latest = (
        Message.objects.filter(
            contact=contact,
            channel=Channel.WHATSAPP,
            direction=Message.Direction.IN,
        )
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    return latest


def is_whatsapp_window_open(contact: Contact, now=None) -> bool:
    ref_time = now or timezone.now()
    cutoff = ref_time - timedelta(hours=24)
    return Message.objects.filter(
        contact=contact,
        channel=Channel.WHATSAPP,
        direction=Message.Direction.IN,
        created_at__gte=cutoff,
    ).exists()


def get_broadcast_audience_queryset(
    audience_filter: Mapping[str, Any] | None,
    channel: str,
) -> QuerySet[Contact]:
    qs = filter_contacts_queryset(membership=None, params=audience_filter or {})
    norm_channel = (channel or "").strip().lower()
    if norm_channel == Channel.EMAIL:
        qs = qs.filter(
            email_subscription=Contact.SubscriptionStatus.SUBSCRIBED,
            email__isnull=False,
        ).exclude(email="")
    elif norm_channel == Channel.SMS:
        qs = qs.filter(
            sms_subscription=Contact.SubscriptionStatus.SUBSCRIBED,
            phone__isnull=False,
        ).exclude(phone="")
    elif norm_channel == Channel.WHATSAPP:
        qs = qs.filter(
            whatsapp_subscription=Contact.SubscriptionStatus.SUBSCRIBED,
            phone__isnull=False,
        ).exclude(phone="")
    return qs.order_by("id")
