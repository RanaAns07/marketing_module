from decimal import Decimal
from typing import Any
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from core.context import require_tenant
from core.exceptions import ApplicationError
from messaging.models import Channel, Message, WhatsAppCategory
from usage.models import RateCard, UsageRecord
from usage.selectors import resolve_rate_card


@transaction.atomic
def record_message_usage(*, message: Message | str) -> UsageRecord | None:
    if isinstance(message, (str, type(None))):
        msg_obj = (
            Message.objects.select_for_update(of=("self",))
            .select_related("tenant", "template")
            .filter(pk=message)
            .first()
        )
    else:
        msg_obj = (
            Message.objects.select_for_update(of=("self",))
            .select_related("tenant", "template")
            .filter(pk=message.pk)
            .first()
        )

    if msg_obj is None:
        return None

    if msg_obj.channel == Channel.EMAIL and msg_obj.direction == Message.Direction.IN:
        return None

    existing = (
        UsageRecord.objects.select_for_update()
        .filter(message=msg_obj)
        .first()
    )
    if existing is not None:
        return existing

    channel = msg_obj.channel
    direction = (
        UsageRecord.Direction.INBOUND
        if msg_obj.direction == Message.Direction.IN
        else UsageRecord.Direction.OUTBOUND
    )
    country = (
        msg_obj.destination_country
        or (msg_obj.metadata or {}).get("destination_country")
        or ""
    ).strip().upper()[:2]

    wa_category = (
        msg_obj.whatsapp_category
        or (msg_obj.metadata or {}).get("category")
        or (msg_obj.metadata or {}).get("whatsapp_category")
        or ""
    ).strip().lower()

    if channel == Channel.WHATSAPP:
        if direction == UsageRecord.Direction.INBOUND:
            wa_category = WhatsAppCategory.SERVICE
        elif not wa_category:
            if msg_obj.template and msg_obj.template.whatsapp_category:
                wa_category = str(msg_obj.template.whatsapp_category).strip().lower()
            elif msg_obj.template:
                wa_category = WhatsAppCategory.MARKETING
            else:
                wa_category = WhatsAppCategory.SERVICE

    if channel == Channel.SMS:
        raw_seg = msg_obj.segments or (msg_obj.metadata or {}).get("segments") or 1
        try:
            quantity = max(1, int(raw_seg))
        except (TypeError, ValueError):
            quantity = 1
    else:
        quantity = 1

    failed_before_provider = (
        direction == UsageRecord.Direction.OUTBOUND
        and msg_obj.status in (Message.Status.FAILED, Message.Status.UNDELIVERED)
        and not bool(msg_obj.provider_message_id)
    )

    default_currency = getattr(settings, "DEFAULT_USAGE_CURRENCY", "USD")

    if failed_before_provider:
        unit_cost = Decimal("0.00000")
        total_cost = Decimal("0.00000")
        currency = default_currency
    else:
        matched_rate = resolve_rate_card(
            tenant=msg_obj.tenant,
            channel=channel,
            direction=direction,
            country=country,
            category=wa_category,
        )
        if matched_rate is not None:
            unit_cost = matched_rate.unit_cost
            currency = matched_rate.currency or default_currency
        else:
            unit_cost = Decimal("0.00000")
            currency = default_currency

        total_cost = (Decimal(quantity) * Decimal(unit_cost)).quantize(Decimal("0.00001"))

    occurred_at = msg_obj.sent_at or msg_obj.created_at or timezone.now()

    try:
        usage_record = UsageRecord.objects.create(
            tenant=msg_obj.tenant,
            message=msg_obj,
            channel=channel,
            direction=direction,
            quantity=quantity,
            destination_country=country,
            whatsapp_category=wa_category,
            unit_cost=unit_cost,
            total_cost=total_cost,
            currency=currency,
            status=msg_obj.status,
            occurred_at=occurred_at,
        )
        return usage_record
    except IntegrityError:
        return UsageRecord.objects.filter(message=msg_obj).first()


@transaction.atomic
def update_message_usage_status(
    *,
    message_id: Any,
    new_status: str,
    provider_message_id: str | None = None,
) -> UsageRecord | None:
    rec = (
        UsageRecord.objects.select_for_update()
        .filter(message_id=message_id)
        .first()
    )
    if rec is None:
        return None

    clean_status = str(new_status or "").strip().lower()
    rec.status = clean_status
    update_fields = ["status", "updated_at"]

    if clean_status in ("failed", "undelivered") and not provider_message_id and not rec.message.provider_message_id:
        rec.unit_cost = Decimal("0.00000")
        rec.total_cost = Decimal("0.00000")
        update_fields.extend(["unit_cost", "total_cost"])

    rec.save(update_fields=update_fields)
    return rec


@transaction.atomic
def create_tenant_rate_card(
    *,
    channel: str,
    unit_cost: Decimal | float | str,
    direction: str = RateCard.Direction.OUTBOUND,
    currency: str = "USD",
    destination_country: str = "",
    whatsapp_category: str = "",
    is_active: bool = True,
) -> RateCard:
    tenant = require_tenant()
    norm_ch = (channel or "").strip().lower()
    norm_dir = (direction or RateCard.Direction.OUTBOUND).strip().lower()
    norm_ct = (destination_country or "").strip().upper()[:2]
    norm_cat = (whatsapp_category or "").strip().lower()
    cost_dec = Decimal(str(unit_cost)).quantize(Decimal("0.00001"))

    if RateCard.objects.filter(
        tenant=tenant,
        channel=norm_ch,
        direction=norm_dir,
        destination_country=norm_ct,
        whatsapp_category=norm_cat,
    ).exists():
        raise ApplicationError(
            detail="A rate card override with this channel, direction, country, and category already exists.",
            code="duplicate_rate_card",
        )

    return RateCard.objects.create(
        tenant=tenant,
        channel=norm_ch,
        direction=norm_dir,
        destination_country=norm_ct,
        whatsapp_category=norm_cat,
        unit_cost=cost_dec,
        currency=(currency or "USD").strip().upper(),
        is_active=is_active,
    )


@transaction.atomic
def update_tenant_rate_card(
    *,
    rate_card: RateCard,
    **changes: Any,
) -> RateCard:
    tenant = require_tenant()
    locked = RateCard.objects.select_for_update().get(pk=rate_card.pk, tenant=tenant)

    if "unit_cost" in changes and changes["unit_cost"] is not None:
        locked.unit_cost = Decimal(str(changes["unit_cost"])).quantize(Decimal("0.00001"))
    if "direction" in changes and changes["direction"] is not None:
        locked.direction = str(changes["direction"]).strip().lower()
    if "currency" in changes and changes["currency"] is not None:
        locked.currency = str(changes["currency"]).strip().upper()
    if "destination_country" in changes and changes["destination_country"] is not None:
        locked.destination_country = str(changes["destination_country"]).strip().upper()[:2]
    if "whatsapp_category" in changes and changes["whatsapp_category"] is not None:
        locked.whatsapp_category = str(changes["whatsapp_category"]).strip().lower()
    if "is_active" in changes and changes["is_active"] is not None:
        locked.is_active = bool(changes["is_active"])

    locked.save()
    return locked


@transaction.atomic
def delete_tenant_rate_card(*, rate_card: RateCard) -> None:
    tenant = require_tenant()
    RateCard.objects.filter(pk=rate_card.pk, tenant=tenant).delete()
