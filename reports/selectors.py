import re
from collections.abc import Mapping
from datetime import date as dt_date, datetime, time as dt_time, timedelta
from decimal import Decimal
from typing import Any
import zoneinfo
from django.conf import settings
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate, TruncWeek
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from accounts.models import Membership
from automation.models import StepExecution, Workflow, WorkflowEnrollment
from contacts.models import Activity, Contact, LeadStatus, Tag
from contacts.selectors import apply_marketer_visibility, get_base_contacts_queryset
from core.context import require_tenant
from inbox.models import Conversation
from inbox.selectors import apply_inbox_visibility
from core.constants import COST_ESTIMATE_DISCLAIMER, ROAS_SCOPE_NOTE
from integrations.models import ChannelConnection
from leads.models import MetaConnection
from messaging.models import Message
from usage.models import UsageRecord
from usage.selectors import compute_usage_summary

USAGE_DISCLAIMER_TEXT = COST_ESTIMATE_DISCLAIMER
REPORT_ROAS_NOTE = ROAS_SCOPE_NOTE


def resolve_tenant_timezone(tenant_tz_name: str | None = None) -> zoneinfo.ZoneInfo:
    if tenant_tz_name:
        try:
            return zoneinfo.ZoneInfo(tenant_tz_name)
        except Exception:
            pass
    try:
        tenant = require_tenant()
        tz_str = tenant.timezone or "UTC"
        return zoneinfo.ZoneInfo(tz_str)
    except Exception:
        return zoneinfo.ZoneInfo("UTC")


def _robust_parse_date(raw: Any) -> dt_date | None:
    if not raw:
        return None
    val_str = str(raw).strip()
    d = parse_date(val_str)
    if d:
        return d
    dt = parse_datetime(val_str)
    if dt:
        return dt.date()
    if len(val_str) >= 10:
        d = parse_date(val_str[:10])
        if d:
            return d
    return None


def parse_date_range(
    params: Mapping[str, Any] | None = None,
    tenant_tz_name: str | None = None,
) -> tuple[datetime, datetime, str]:
    tz = resolve_tenant_timezone(tenant_tz_name)
    now_local = timezone.now().astimezone(tz)
    today_local = now_local.date()

    params_dict = dict(params or {})
    raw_preset = (
        params_dict.get("preset")
        or params_dict.get("range")
        or params_dict.get("period")
        or params_dict.get("filter")
        or params_dict.get("timeframe")
        or params_dict.get("date_range")
        or ""
    )
    if not raw_preset and params_dict.get("days"):
        raw_preset = str(params_dict.get("days"))

    preset = str(raw_preset).strip().lower().replace("-", "_").replace(" ", "_")

    start_str = (
        params_dict.get("start_date")
        or params_dict.get("from_date")
        or params_dict.get("startDate")
        or params_dict.get("fromDate")
        or params_dict.get("start")
        or params_dict.get("from")
        or params_dict.get("date_from")
    )
    end_str = (
        params_dict.get("end_date")
        or params_dict.get("to_date")
        or params_dict.get("endDate")
        or params_dict.get("toDate")
        or params_dict.get("end")
        or params_dict.get("to")
        or params_dict.get("date_to")
    )

    if start_str and end_str:
        s_date = _robust_parse_date(start_str)
        e_date = _robust_parse_date(end_str)
        if s_date and e_date:
            start_local = datetime.combine(s_date, dt_time.min, tzinfo=tz)
            end_local = datetime.combine(e_date, dt_time.max, tzinfo=tz)
            return (
                start_local.astimezone(zoneinfo.ZoneInfo("UTC")),
                end_local.astimezone(zoneinfo.ZoneInfo("UTC")),
                "custom",
            )

    if preset in ("today", "1d", "1_day", "1day"):
        start_local = datetime.combine(today_local, dt_time.min, tzinfo=tz)
        end_local = datetime.combine(today_local, dt_time.max, tzinfo=tz)
        return (
            start_local.astimezone(zoneinfo.ZoneInfo("UTC")),
            end_local.astimezone(zoneinfo.ZoneInfo("UTC")),
            "today",
        )

    if preset in ("yesterday",):
        y_date = today_local - timedelta(days=1)
        start_local = datetime.combine(y_date, dt_time.min, tzinfo=tz)
        end_local = datetime.combine(y_date, dt_time.max, tzinfo=tz)
        return (
            start_local.astimezone(zoneinfo.ZoneInfo("UTC")),
            end_local.astimezone(zoneinfo.ZoneInfo("UTC")),
            "yesterday",
        )

    if preset in ("this_month", "current_month"):
        start_date = datetime(today_local.year, today_local.month, 1, 0, 0, 0, tzinfo=tz)
        end_local = datetime.combine(today_local, dt_time.max, tzinfo=tz)
        return (
            start_date.astimezone(zoneinfo.ZoneInfo("UTC")),
            end_local.astimezone(zoneinfo.ZoneInfo("UTC")),
            "this_month",
        )

    if preset in ("last_month", "previous_month"):
        first_of_this_month = datetime(today_local.year, today_local.month, 1, 0, 0, 0, tzinfo=tz)
        end_of_last_month = first_of_this_month - timedelta(microseconds=1)
        start_of_last_month = datetime(end_of_last_month.year, end_of_last_month.month, 1, 0, 0, 0, tzinfo=tz)
        return (
            start_of_last_month.astimezone(zoneinfo.ZoneInfo("UTC")),
            end_of_last_month.astimezone(zoneinfo.ZoneInfo("UTC")),
            "last_month",
        )

    if preset in ("quarter", "this_quarter", "last_quarter", "3_months", "3months"):
        start_date = today_local - timedelta(days=89)
        start_local = datetime.combine(start_date, dt_time.min, tzinfo=tz)
        end_local = datetime.combine(today_local, dt_time.max, tzinfo=tz)
        return (
            start_local.astimezone(zoneinfo.ZoneInfo("UTC")),
            end_local.astimezone(zoneinfo.ZoneInfo("UTC")),
            "last_90_days",
        )

    if preset in ("year", "this_year", "1_year", "1y", "12_months", "12months"):
        start_date = today_local - timedelta(days=364)
        start_local = datetime.combine(start_date, dt_time.min, tzinfo=tz)
        end_local = datetime.combine(today_local, dt_time.max, tzinfo=tz)
        return (
            start_local.astimezone(zoneinfo.ZoneInfo("UTC")),
            end_local.astimezone(zoneinfo.ZoneInfo("UTC")),
            "last_365_days",
        )

    m = re.match(r"^(?:last_?)?(\d+)(?:_?days?|_?d)?$", preset)
    if m:
        n_days = int(m.group(1))
        if n_days > 0:
            start_date = today_local - timedelta(days=n_days - 1)
            start_local = datetime.combine(start_date, dt_time.min, tzinfo=tz)
            end_local = datetime.combine(today_local, dt_time.max, tzinfo=tz)
            return (
                start_local.astimezone(zoneinfo.ZoneInfo("UTC")),
                end_local.astimezone(zoneinfo.ZoneInfo("UTC")),
                f"last_{n_days}_days",
            )

    start_date = today_local - timedelta(days=29)
    start_local = datetime.combine(start_date, dt_time.min, tzinfo=tz)
    end_local = datetime.combine(today_local, dt_time.max, tzinfo=tz)
    return (
        start_local.astimezone(zoneinfo.ZoneInfo("UTC")),
        end_local.astimezone(zoneinfo.ZoneInfo("UTC")),
        "last_30_days",
    )


def get_lead_acquisition_report(
    *,
    start_dt_utc: datetime,
    end_dt_utc: datetime,
    membership: Membership | None,
    bucket: str = "day",
) -> dict[str, Any]:
    base_qs = Contact.objects.filter(
        acquired_at__gte=start_dt_utc,
        acquired_at__lte=end_dt_utc,
    )
    scoped_qs = apply_marketer_visibility(base_qs, membership)

    total_leads = scoped_qs.count()

    by_channel = list(
        scoped_qs.values("source_channel")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    by_campaign = list(
        scoped_qs.exclude(campaign="")
        .values("campaign")
        .annotate(count=Count("id"))
        .order_by("-count")[:20]
    )
    by_form = list(
        scoped_qs.exclude(source_form="")
        .values("source_form")
        .annotate(count=Count("id"))
        .order_by("-count")[:20]
    )

    if bucket == "week":
        trunc_fn = TruncWeek("acquired_at")
    else:
        trunc_fn = TruncDate("acquired_at")

    over_time_raw = (
        scoped_qs.annotate(bucket=trunc_fn)
        .values("bucket")
        .annotate(count=Count("id"))
        .order_by("bucket")
    )
    over_time = [
        {
            "period": row["bucket"].strftime("%Y-%m-%d") if hasattr(row["bucket"], "strftime") else str(row["bucket"]),
            "count": row["count"],
        }
        for row in over_time_raw
        if row["bucket"] is not None
    ]

    return {
        "start_date": start_dt_utc.isoformat(),
        "end_date": end_dt_utc.isoformat(),
        "total_leads": total_leads,
        "by_source_channel": by_channel,
        "by_campaign": by_campaign,
        "by_form": by_form,
        "over_time": over_time,
        "note": ROAS_SCOPE_NOTE,
        "roas_scope_note": ROAS_SCOPE_NOTE,
    }


def get_pipeline_report(
    *,
    membership: Membership | None,
    top_n_tags: int = 10,
) -> dict[str, Any]:
    base_qs = get_base_contacts_queryset()
    scoped_qs = apply_marketer_visibility(base_qs, membership)
    total_leads = scoped_qs.count()

    statuses = list(LeadStatus.objects.all().order_by("order", "name"))
    status_counts = {
        row["lead_status_id"]: row["count"]
        for row in scoped_qs.values("lead_status_id").annotate(count=Count("id"))
    }

    by_status = [
        {
            "status_id": str(s.id),
            "status_name": s.name,
            "order": s.order,
            "count": status_counts.get(s.id, 0),
        }
        for s in statuses
    ]

    by_tag = list(
        Tag.objects.filter(contacts__in=scoped_qs)
        .annotate(count=Count("contacts"))
        .order_by("-count")
        .values("id", "name", "color", "count")[:top_n_tags]
    )

    reached_appt = scoped_qs.filter(
        Q(lead_status__name__iexact="Appointment Booked") | Q(appointments__isnull=False)
    ).distinct().count()

    reached_customer = scoped_qs.filter(
        Q(lead_status__name__iexact="Customer") | Q(converted_at__isnull=False)
    ).distinct().count()

    appointment_rate = round(reached_appt / total_leads, 4) if total_leads else 0.0
    customer_rate = round(reached_customer / total_leads, 4) if total_leads else 0.0

    return {
        "total_leads": total_leads,
        "by_status": by_status,
        "by_tag": by_tag,
        "conversion_counts": {
            "reached_appointment_booked": reached_appt,
            "reached_customer": reached_customer,
        },
        "conversion_rates": {
            "appointment_booked_rate": appointment_rate,
            "customer_rate": customer_rate,
        },
    }


def get_workflow_performance_report(
    *,
    start_dt_utc: datetime,
    end_dt_utc: datetime,
) -> dict[str, Any]:
    workflows = list(Workflow.objects.all().order_by("name"))
    items: list[dict[str, Any]] = []

    for wf in workflows:
        enrollments = WorkflowEnrollment.objects.filter(workflow=wf)
        period_enrollments = enrollments.filter(started_at__gte=start_dt_utc, started_at__lte=end_dt_utc)

        enrolled_count = period_enrollments.count()
        active_count = enrollments.filter(status=WorkflowEnrollment.Status.ACTIVE).count()
        completed_count = enrollments.filter(status=WorkflowEnrollment.Status.COMPLETED).count()
        paused_count = enrollments.filter(status=WorkflowEnrollment.Status.PAUSED).count()
        stopped_count = enrollments.filter(status=WorkflowEnrollment.Status.STOPPED).count()
        failed_count = enrollments.filter(status=WorkflowEnrollment.Status.FAILED).count()

        failed_step_counts = StepExecution.objects.filter(
            enrollment__workflow=wf,
            status=StepExecution.Status.FAILED,
        ).count()

        reply_count = (
            Activity.objects.filter(
                activity_type=Activity.ActivityType.MESSAGE_IN,
                contact__workflow_enrollments__workflow=wf,
            )
            .values("contact_id")
            .distinct()
            .count()
        )

        items.append(
            {
                "workflow_id": str(wf.id),
                "workflow_name": wf.name,
                "status": wf.status,
                "version": wf.version,
                "enrolled_count": enrolled_count,
                "active_count": active_count,
                "completed_count": completed_count,
                "paused_count": paused_count,
                "stopped_count": stopped_count,
                "failed_count": failed_count,
                "failed_step_counts": failed_step_counts,
                "reply_counts": reply_count,
            }
        )

    return {
        "start_date": start_dt_utc.isoformat(),
        "end_date": end_dt_utc.isoformat(),
        "workflows": items,
    }


def get_messaging_report(
    *,
    start_dt_utc: datetime,
    end_dt_utc: datetime,
    membership: Membership | None,
) -> dict[str, Any]:
    base_qs = Message.objects.filter(
        created_at__gte=start_dt_utc,
        created_at__lte=end_dt_utc,
    )
    if membership is not None and membership.role == Membership.Role.MARKETER:
        if not membership.tenant.marketer_can_view_all_contacts:
            base_qs = base_qs.filter(contact__assigned_to=membership)

    by_channel_and_dir = list(
        base_qs.values("channel", "direction")
        .annotate(count=Count("id"))
        .order_by("channel", "direction")
    )

    outbound_qs = base_qs.filter(direction=Message.Direction.OUT)
    inbound_qs = base_qs.filter(direction=Message.Direction.IN)
    sent_count = outbound_qs.count()
    outbound_count = sent_count
    received_count = inbound_qs.count()
    inbound_count = received_count
    delivered_count = outbound_qs.filter(status__in=[Message.Status.DELIVERED, Message.Status.READ]).count()
    failed_count = outbound_qs.filter(status__in=[Message.Status.FAILED, Message.Status.UNDELIVERED]).count()
    bounce_count = outbound_qs.filter(status=Message.Status.BOUNCED).count()

    opt_out_count = (
        Activity.objects.filter(
            created_at__gte=start_dt_utc,
            created_at__lte=end_dt_utc,
            activity_type__in=[Activity.ActivityType.TAG_CHANGE, Activity.ActivityType.STATUS_CHANGE],
            description__icontains="unsubscri",
        ).count()
    )

    delivery_rate = round(delivered_count / sent_count, 4) if sent_count else 0.0
    failure_rate = round(failed_count / sent_count, 4) if sent_count else 0.0

    is_manager = membership is None or membership.role in (Membership.Role.OWNER, Membership.Role.ADMIN)
    if is_manager:
        usage_qs = UsageRecord.objects.filter(occurred_at__gte=start_dt_utc, occurred_at__lte=end_dt_utc)
        cost_summary = compute_usage_summary(usage_qs, period="custom")
    else:
        cost_summary = None

    return {
        "start_date": start_dt_utc.isoformat(),
        "end_date": end_dt_utc.isoformat(),
        "by_channel_and_direction": by_channel_and_dir,
        "sent_count": sent_count,
        "outbound_count": outbound_count,
        "received_count": received_count,
        "inbound_count": inbound_count,
        "delivered_count": delivered_count,
        "failed_count": failed_count,
        "bounce_count": bounce_count,
        "opt_out_count": opt_out_count,
        "delivery_rate": delivery_rate,
        "failure_rate": failure_rate,
        "cost_summary": cost_summary,
        "note": ROAS_SCOPE_NOTE,
        "roas_scope_note": ROAS_SCOPE_NOTE,
        "disclaimer": COST_ESTIMATE_DISCLAIMER if cost_summary is not None else None,
    }


def get_dashboard_data(
    *,
    params: Mapping[str, Any] | None,
    membership: Membership,
) -> dict[str, Any]:
    start_dt_utc, end_dt_utc, range_label = parse_date_range(params, membership.tenant.timezone)

    contact_qs = apply_marketer_visibility(Contact.objects.all(), membership)
    new_leads_qs = contact_qs.filter(acquired_at__gte=start_dt_utc, acquired_at__lte=end_dt_utc)
    new_leads_count = new_leads_qs.count()

    conv_qs = apply_inbox_visibility(Conversation.objects.all(), membership)
    open_conversations_count = conv_qs.filter(status=Conversation.Status.OPEN).count()
    waiting_conversations_count = conv_qs.filter(
        status=Conversation.Status.OPEN,
        unread_count__gt=0,
    ).count()

    unread_replies_count = conv_qs.filter(unread_count__gt=0).aggregate(u=Sum("unread_count"))["u"] or 0

    leads_by_stage = {
        row["lead_status__name"] or "New": row["count"]
        for row in contact_qs.values("lead_status__name").annotate(count=Count("id")).order_by("-count")
    }

    active_workflows_count = Workflow.objects.filter(status=Workflow.Status.ACTIVE).count()

    msg_qs = Message.objects.filter(created_at__gte=start_dt_utc, created_at__lte=end_dt_utc)
    if membership.role == Membership.Role.MARKETER and not membership.tenant.marketer_can_view_all_contacts:
        msg_qs = msg_qs.filter(contact__assigned_to=membership)

    messages_sent_count = msg_qs.filter(direction=Message.Direction.OUT).count()
    messages_received_count = msg_qs.filter(direction=Message.Direction.IN).count()
    delivered_count = msg_qs.filter(
        direction=Message.Direction.OUT,
        status__in=[Message.Status.DELIVERED, Message.Status.READ],
    ).count()
    delivery_rate = round(delivered_count / messages_sent_count, 4) if messages_sent_count else 0.0

    start_iso = start_dt_utc.isoformat()
    end_iso = end_dt_utc.isoformat()

    metrics = {
        "range": range_label,
        "start_date": start_iso,
        "end_date": end_iso,
        "new_leads": {
            "value": new_leads_count,
            "note": ROAS_SCOPE_NOTE,
            "roas_scope_note": ROAS_SCOPE_NOTE,
            "drill_down": {
                "resource": "contacts",
                "params": {"acquired_after": start_iso, "acquired_before": end_iso},
            },
        },
        "open_conversations": {
            "value": open_conversations_count,
            "drill_down": {
                "resource": "conversations",
                "params": {"status": "open"},
            },
        },
        "waiting_conversations": {
            "value": waiting_conversations_count,
            "drill_down": {
                "resource": "conversations",
                "params": {"status": "open", "unread": "true"},
            },
        },
        "unread_replies": {
            "value": unread_replies_count,
            "drill_down": {
                "resource": "conversations",
                "params": {"unread": "true"},
            },
        },
        "leads_by_stage": {
            "value": leads_by_stage,
            "drill_down": {
                "resource": "contacts",
                "params": {},
            },
        },
        "active_workflows": {
            "value": active_workflows_count,
            "drill_down": {
                "resource": "workflows",
                "params": {"status": "active"},
            },
        },
        "messages_sent": {
            "value": messages_sent_count,
            "drill_down": {
                "resource": "messages",
                "params": {"direction": "out", "from_date": start_iso, "to_date": end_iso},
            },
        },
        "messages_received": {
            "value": messages_received_count,
            "drill_down": {
                "resource": "messages",
                "params": {"direction": "in", "from_date": start_iso, "to_date": end_iso},
            },
        },
        "delivery_rate": {
            "value": delivery_rate,
            "delivered_count": delivered_count,
            "drill_down": {
                "resource": "messages",
                "params": {"direction": "out", "status": "delivered", "from_date": start_iso, "to_date": end_iso},
            },
        },
    }

    metrics["roas_scope_note"] = ROAS_SCOPE_NOTE
    metrics["note"] = ROAS_SCOPE_NOTE

    is_manager = membership.role in (Membership.Role.OWNER, Membership.Role.ADMIN)
    if is_manager:
        spend_sum = (
            UsageRecord.objects.filter(occurred_at__gte=start_dt_utc, occurred_at__lte=end_dt_utc).aggregate(
                s=Sum("total_cost")
            )["s"]
            or Decimal("0.00000")
        )
        metrics["estimated_spend"] = {
            "amount": float(spend_sum),
            "currency": getattr(settings, "DEFAULT_USAGE_CURRENCY", "USD"),
            "is_estimate": True,
            "estimate": True,
            "disclaimer": COST_ESTIMATE_DISCLAIMER,
            "cost_estimate_disclaimer": COST_ESTIMATE_DISCLAIMER,
            "drill_down": {
                "resource": "usage_records",
                "params": {"from_date": start_iso, "to_date": end_iso},
            },
        }
        metrics["disclaimer"] = COST_ESTIMATE_DISCLAIMER
        metrics["cost_estimate_disclaimer"] = COST_ESTIMATE_DISCLAIMER

        meta_conn = MetaConnection.objects.first()
        meta_info = {
            "status": meta_conn.status if meta_conn else "not_connected",
            "token_expires_at": meta_conn.token_expires_at.isoformat() if meta_conn and meta_conn.token_expires_at else None,
        }

        channel_conns = {
            conn.channel: {
                "provider": conn.provider,
                "status": conn.status,
                "sender_identity": conn.sender_identity,
            }
            for conn in ChannelConnection.objects.all()
        }
        metrics["integration_health"] = {
            "meta": meta_info,
            "channels": channel_conns,
        }

    return metrics
