from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from django.conf import settings
from django.db.models import Count, QuerySet, Sum
from django.utils.dateparse import parse_date, parse_datetime
from core.context import require_tenant
from core.models import Tenant
from core.constants import COST_ESTIMATE_DISCLAIMER
from messaging.models import Channel
from usage.models import RateCard, UsageRecord

USAGE_DISCLAIMER_TEXT = COST_ESTIMATE_DISCLAIMER


def _clean_country(raw: Any) -> str:
    return str(raw or "").strip().upper()[:2]


def _clean_category(raw: Any) -> str:
    return str(raw or "").strip().lower()


def resolve_rate_card(
    *,
    tenant: Tenant | None,
    channel: str,
    country: str = "",
    category: str = "",
) -> RateCard | None:
    norm_ch = (channel or "").strip().lower()
    norm_ct = _clean_country(country)
    norm_cat = _clean_category(category)

    base_qs = RateCard.objects.filter(channel=norm_ch, is_active=True)

    if tenant is not None:
        if norm_ct and norm_cat:
            match = base_qs.filter(
                tenant=tenant,
                destination_country=norm_ct,
                whatsapp_category=norm_cat,
            ).first()
            if match is not None:
                return match

        if norm_ct:
            match = base_qs.filter(
                tenant=tenant,
                destination_country=norm_ct,
                whatsapp_category="",
            ).first()
            if match is not None:
                return match

        match = base_qs.filter(
            tenant=tenant,
            destination_country="",
            whatsapp_category="",
        ).first()
        if match is not None:
            return match

    if norm_ct and norm_cat:
        match = base_qs.filter(
            tenant__isnull=True,
            destination_country=norm_ct,
            whatsapp_category=norm_cat,
        ).first()
        if match is not None:
            return match

    if norm_ct:
        match = base_qs.filter(
            tenant__isnull=True,
            destination_country=norm_ct,
            whatsapp_category="",
        ).first()
        if match is not None:
            return match

    return base_qs.filter(
        tenant__isnull=True,
        destination_country="",
        whatsapp_category="",
    ).first()


def get_usage_records_queryset(params: Mapping[str, Any] | None = None) -> QuerySet[UsageRecord]:
    qs = UsageRecord.objects.select_related("message", "tenant").order_by("-occurred_at", "-id")
    if not params:
        return qs

    ch = params.get("channel")
    if ch:
        qs = qs.filter(channel=str(ch).strip().lower())

    st = params.get("status")
    if st:
        qs = qs.filter(status=str(st).strip().lower())

    ct = params.get("country") or params.get("destination_country")
    if ct:
        qs = qs.filter(destination_country=_clean_country(ct))

    from_dt = params.get("from_date") or params.get("start_date") or params.get("occurred_after")
    if from_dt:
        parsed_from = parse_datetime(str(from_dt).strip()) or parse_date(str(from_dt).strip())
        if parsed_from is not None:
            qs = qs.filter(occurred_at__gte=parsed_from)

    to_dt = params.get("to_date") or params.get("end_date") or params.get("occurred_before")
    if to_dt:
        parsed_to = parse_datetime(str(to_dt).strip()) or parse_date(str(to_dt).strip())
        if parsed_to is not None:
            qs = qs.filter(occurred_at__lte=parsed_to)

    return qs


def get_tenant_rate_cards_queryset() -> QuerySet[RateCard]:
    return RateCard.objects.filter(tenant=require_tenant()).order_by(
        "channel", "destination_country", "whatsapp_category"
    )


def compute_usage_summary(
    queryset: QuerySet[UsageRecord],
    *,
    period: str = "monthly",
) -> dict[str, Any]:
    channels = [Channel.EMAIL, Channel.SMS, Channel.WHATSAPP]
    by_channel: dict[str, Any] = {}
    default_currency = getattr(settings, "DEFAULT_USAGE_CURRENCY", "USD")

    total_records = 0
    total_segments = 0
    total_delivered = 0
    total_failed = 0
    total_bounced = 0
    total_cost = Decimal("0.00000")

    delivered_statuses = ("delivered", "read")
    failed_statuses = ("failed", "undelivered")

    for ch in channels:
        sub_qs = queryset.filter(channel=ch)
        msg_count = sub_qs.count()
        segments_sum = (
            sub_qs.aggregate(s=Sum("quantity"))["s"] or 0
            if ch == Channel.SMS
            else 0
        )
        delivered_count = sub_qs.filter(status__in=delivered_statuses).count()
        failed_count = sub_qs.filter(status__in=failed_statuses).count()
        bounced_count = sub_qs.filter(status="bounced").count()
        cost_sum = sub_qs.aggregate(c=Sum("total_cost"))["c"] or Decimal("0.00000")

        total_records += msg_count
        total_segments += segments_sum
        total_delivered += delivered_count
        total_failed += failed_count
        total_bounced += bounced_count
        total_cost += cost_sum

        by_channel[ch] = {
            "message_count": msg_count,
            "sms_segments": segments_sum,
            "delivered_count": delivered_count,
            "failed_count": failed_count,
            "bounced_count": bounced_count,
            "estimated_cost": float(cost_sum),
            "currency": default_currency,
            "is_estimate": True,
            "estimate": True,
            "disclaimer": COST_ESTIMATE_DISCLAIMER,
        }

    status_breakdown = {
        row["status"]: row["count"]
        for row in queryset.values("status").annotate(count=Count("id")).order_by("-count")
        if row["status"]
    }

    return {
        "period": period,
        "is_estimate": True,
        "estimate": True,
        "disclaimer": USAGE_DISCLAIMER_TEXT,
        "currency": default_currency,
        "total_messages": total_records,
        "total_sms_segments": total_segments,
        "total_delivered": total_delivered,
        "total_failed": total_failed,
        "total_bounced": total_bounced,
        "total_estimated_cost": float(total_cost),
        "by_channel": by_channel,
        "delivery_outcomes": status_breakdown,
    }
