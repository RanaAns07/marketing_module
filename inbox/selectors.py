import base64
from collections.abc import Iterable, Mapping
from datetime import timedelta
import json
from typing import Any
import uuid
from django.db.models import Count, Q, QuerySet, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from accounts.models import Membership, User
from automation.models import WorkflowEnrollment
from contacts.models import Activity, Contact
from inbox.models import AssignmentRule, Conversation, ConversationNote
from messaging.models import Message
from messaging.selectors import get_last_inbound_whatsapp_at


def _parse_uuid_or_none(val: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(val).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _split_csv(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(raw, Iterable):
        out: list[str] = []
        for item in raw:
            if isinstance(item, str) and "," in item:
                out.extend([p.strip() for p in item.split(",") if p.strip()])
            elif item is not None and str(item).strip():
                out.append(str(item).strip())
        return out
    return [str(raw).strip()]


def _parse_bool_param(raw: Any) -> bool | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None


def get_base_conversations_queryset() -> QuerySet[Conversation]:
    return Conversation.objects.select_related(
        "contact",
        "contact__lead_status",
        "contact__assigned_to",
        "contact__assigned_to__user",
        "assigned_to",
        "assigned_to__user",
        "tenant",
    ).prefetch_related("contact__tags")


def apply_inbox_visibility(
    queryset: QuerySet[Conversation],
    membership: Membership | None,
) -> QuerySet[Conversation]:
    if membership is None:
        return queryset
    if membership.role == Membership.Role.MARKETER:
        if not membership.tenant.marketer_can_view_all_contacts:
            return queryset.filter(assigned_to=membership)
        return queryset.filter(
            Q(assigned_to=membership) | Q(assigned_to__isnull=True)
        )
    return queryset


def can_member_access_conversation(
    conversation: Conversation,
    membership: Membership | None,
) -> bool:
    if membership is None:
        return True
    if membership.role in (Membership.Role.OWNER, Membership.Role.ADMIN):
        return True
    if not membership.tenant.marketer_can_view_all_contacts:
        return conversation.assigned_to_id == membership.id
    return conversation.assigned_to_id in (None, membership.id)


def _build_contact_tag_q(values: list[str]) -> Q:
    uuids = []
    names = []
    for val in values:
        parsed = _parse_uuid_or_none(val)
        if parsed is not None:
            uuids.append(parsed)
        else:
            names.append(val)
    q = Q()
    if uuids:
        q |= Q(contact__tags__id__in=uuids)
    for name in names:
        q |= Q(contact__tags__name__iexact=name)
    return q


def filter_conversations_queryset(
    membership: Membership | None = None,
    params: Mapping[str, Any] | None = None,
    queryset: QuerySet[Conversation] | None = None,
) -> QuerySet[Conversation]:
    qs = queryset if queryset is not None else get_base_conversations_queryset()
    qs = apply_inbox_visibility(qs, membership)

    if not params:
        return qs.order_by("-last_message_at", "-created_at", "-id")

    needs_distinct = False

    unassigned_flag = _parse_bool_param(params.get("unassigned"))
    if unassigned_flag is True:
        qs = qs.filter(assigned_to__isnull=True)
    elif unassigned_flag is False:
        qs = qs.filter(assigned_to__isnull=False)

    assignee_raw = params.get("assignee") or params.get("assigned_to")
    if assignee_raw is not None and str(assignee_raw).strip() != "":
        assignee_str = str(assignee_raw).strip()
        if assignee_str.lower() in ("unassigned", "none", "null"):
            qs = qs.filter(assigned_to__isnull=True)
        elif assignee_str.lower() == "me" and membership is not None:
            qs = qs.filter(assigned_to=membership)
        else:
            assignee_uuid = _parse_uuid_or_none(assignee_str)
            if assignee_uuid is not None:
                qs = qs.filter(
                    Q(assigned_to_id=assignee_uuid) | Q(assigned_to__user_id=assignee_uuid)
                )

    status_vals = _split_csv(params.get("status"))
    if status_vals:
        qs = qs.filter(status__in=[s.lower() for s in status_vals])

    channel_vals = _split_csv(params.get("channel") or params.get("last_channel"))
    if channel_vals:
        qs = qs.filter(last_channel__in=[c.lower() for c in channel_vals])

    unread_flag = _parse_bool_param(params.get("unread"))
    if unread_flag is True:
        qs = qs.filter(unread_count__gt=0)
    elif unread_flag is False:
        qs = qs.filter(unread_count=0)

    contact_raw = params.get("contact_id") or params.get("contact")
    if contact_raw:
        c_uuid = _parse_uuid_or_none(contact_raw)
        if c_uuid is not None:
            qs = qs.filter(contact_id=c_uuid)

    tag_match = str(params.get("tag_match") or "any").lower()
    include_tags = _split_csv(
        params.get("tags") or params.get("include_tags") or params.get("tags_any")
    )
    tags_all = _split_csv(params.get("tags_all"))
    if tag_match in ("all", "and") and include_tags and not tags_all:
        tags_all = include_tags
        include_tags = []

    if include_tags:
        qs = qs.filter(_build_contact_tag_q(include_tags))
        needs_distinct = True

    if tags_all:
        for val in tags_all:
            parsed = _parse_uuid_or_none(val)
            if parsed is not None:
                qs = qs.filter(contact__tags__id=parsed)
            else:
                qs = qs.filter(contact__tags__name__iexact=val)
        needs_distinct = True

    exclude_tags = _split_csv(params.get("exclude_tags") or params.get("tags_exclude"))
    if exclude_tags:
        qs = qs.exclude(_build_contact_tag_q(exclude_tags))
        needs_distinct = True

    search_val = params.get("search") or params.get("q")
    if search_val and str(search_val).strip():
        term = str(search_val).strip()
        qs = qs.filter(
            Q(contact__full_name__icontains=term)
            | Q(contact__first_name__icontains=term)
            | Q(contact__last_name__icontains=term)
            | Q(contact__email__icontains=term)
            | Q(contact__phone__icontains=term)
        )

    ordering = params.get("ordering")
    if ordering == "last_message_at":
        qs = qs.order_by("last_message_at", "created_at", "id")
    else:
        qs = qs.order_by("-last_message_at", "-created_at", "-id")

    if needs_distinct:
        qs = qs.distinct()

    return qs


def get_conversation_by_id(
    conversation_id: uuid.UUID | str,
    membership: Membership | None = None,
) -> Conversation | None:
    qs = apply_inbox_visibility(get_base_conversations_queryset(), membership)
    return qs.filter(pk=conversation_id).first()


def get_conversation_by_contact(
    contact: Contact,
    membership: Membership | None = None,
) -> Conversation | None:
    qs = apply_inbox_visibility(get_base_conversations_queryset(), membership)
    return qs.filter(contact=contact).first()


def get_whatsapp_window_state(contact: Contact, now=None) -> dict[str, Any]:
    ref_time = now or timezone.now()
    last_inbound = get_last_inbound_whatsapp_at(contact)
    if last_inbound is None:
        return {
            "whether_whatsapp_window_open": False,
            "whatsapp_window_open": False,
            "window_expires_at": None,
            "last_inbound_whatsapp_at": None,
        }
    expires_at = last_inbound + timedelta(hours=24)
    is_open = expires_at >= ref_time
    return {
        "whether_whatsapp_window_open": is_open,
        "whatsapp_window_open": is_open,
        "window_expires_at": expires_at.isoformat(),
        "last_inbound_whatsapp_at": last_inbound.isoformat(),
    }


def get_contact_active_enrollments(contact: Contact) -> list[dict[str, Any]]:
    enrollments = list(
        WorkflowEnrollment.objects.select_related("workflow")
        .filter(
            contact=contact,
            status__in=[
                WorkflowEnrollment.Status.ACTIVE,
                WorkflowEnrollment.Status.PAUSED,
            ],
        )
        .order_by("-created_at")
    )
    return [
        {
            "id": str(enr.id),
            "workflow_id": str(enr.workflow_id),
            "workflow_name": enr.workflow.name,
            "status": enr.status,
            "current_step_key": enr.current_step_key,
            "paused_reason": enr.paused_reason,
            "started_at": enr.started_at.isoformat() if enr.started_at else None,
            "next_run_at": enr.next_run_at.isoformat() if enr.next_run_at else None,
        }
        for enr in enrollments
    ]


def encode_thread_cursor(created_at: Any, item_id: uuid.UUID | str) -> str:
    iso_dt = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
    payload = json.dumps({"created_at": iso_dt, "id": str(item_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_thread_cursor(cursor_str: str | None) -> tuple[Any, uuid.UUID] | None:
    if not cursor_str or not str(cursor_str).strip():
        return None
    raw = str(cursor_str).strip()

    if "|" in raw:
        parts = raw.split("|", 1)
        dt = parse_datetime(parts[0])
        uid = _parse_uuid_or_none(parts[1])
        if dt is not None and uid is not None:
            return dt, uid

    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        if decoded.startswith("{"):
            data = json.loads(decoded)
            dt = parse_datetime(str(data.get("created_at") or ""))
            uid = _parse_uuid_or_none(data.get("id"))
            if dt is not None and uid is not None:
                return dt, uid
        if "|" in decoded:
            parts = decoded.split("|", 1)
            dt = parse_datetime(parts[0])
            uid = _parse_uuid_or_none(parts[1])
            if dt is not None and uid is not None:
                return dt, uid
    except Exception:
        return None

    return None


def _serialize_user_mini(user: User | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return {
        "id": str(user.id),
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": user.full_name,
    }


THREAD_SYSTEM_ACTIVITY_TYPES = (
    Activity.ActivityType.ASSIGNMENT,
    Activity.ActivityType.STATUS_CHANGE,
    Activity.ActivityType.TAG_CHANGE,
    Activity.ActivityType.WORKFLOW_ACTION,
    Activity.ActivityType.FORM_SUBMISSION,
)


def get_conversation_thread(
    *,
    conversation: Conversation,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    effective_limit = max(1, min(int(limit or 50), 200))
    fetch_count = effective_limit + 1
    parsed_cursor = decode_thread_cursor(cursor)

    cursor_q = Q()
    if parsed_cursor is not None:
        cursor_dt, cursor_id = parsed_cursor
        cursor_q = Q(created_at__lt=cursor_dt) | Q(created_at=cursor_dt, id__lt=cursor_id)

    messages_qs = (
        Message.objects.select_related("sent_by", "template", "broadcast")
        .filter(contact_id=conversation.contact_id)
        .filter(cursor_q)
        .order_by("-created_at", "-id")[:fetch_count]
    )

    notes_qs = (
        ConversationNote.objects.select_related("author")
        .filter(conversation=conversation)
        .filter(cursor_q)
        .order_by("-created_at", "-id")[:fetch_count]
    )

    activities_qs = (
        Activity.objects.select_related("actor")
        .filter(
            contact_id=conversation.contact_id,
            activity_type__in=THREAD_SYSTEM_ACTIVITY_TYPES,
        )
        .filter(cursor_q)
        .order_by("-created_at", "-id")[:fetch_count]
    )

    items: list[dict[str, Any]] = []

    for msg in messages_qs:
        msg_source = (
            "inbound"
            if msg.direction == Message.Direction.IN
            else (msg.source or Message.Source.MANUAL)
        )
        items.append(
            {
                "id": str(msg.id),
                "entry_type": "message",
                "activity_type": None,
                "created_at": msg.created_at,
                "channel": msg.channel,
                "direction": msg.direction,
                "status": msg.status,
                "delivery_status": msg.status,
                "source": msg_source,
                "subject": msg.subject,
                "body": msg.body,
                "description": msg.body,
                "segments": msg.segments,
                "destination_country": msg.destination_country,
                "whatsapp_category": msg.whatsapp_category,
                "template_id": str(msg.template_id) if msg.template_id else None,
                "broadcast_id": str(msg.broadcast_id) if msg.broadcast_id else None,
                "sent_by": _serialize_user_mini(msg.sent_by),
                "author": _serialize_user_mini(msg.sent_by),
                "actor": _serialize_user_mini(msg.sent_by),
                "error": msg.error,
                "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
                "delivered_at": msg.delivered_at.isoformat() if msg.delivered_at else None,
                "read_at": msg.read_at.isoformat() if msg.read_at else None,
                "metadata": msg.metadata or {},
                "seen_by_customer": True,
            }
        )

    for note in notes_qs:
        author_data = _serialize_user_mini(note.author)
        items.append(
            {
                "id": str(note.id),
                "entry_type": "note",
                "activity_type": "note",
                "created_at": note.created_at,
                "channel": "note",
                "direction": None,
                "status": None,
                "delivery_status": None,
                "source": "manual",
                "subject": "",
                "body": note.body,
                "description": note.body,
                "segments": None,
                "destination_country": "",
                "whatsapp_category": "",
                "template_id": None,
                "broadcast_id": None,
                "sent_by": None,
                "author": author_data,
                "actor": author_data,
                "error": "",
                "sent_at": None,
                "delivered_at": None,
                "read_at": None,
                "metadata": {},
                "seen_by_customer": False,
            }
        )

    for act in activities_qs:
        actor_data = _serialize_user_mini(act.actor)
        items.append(
            {
                "id": str(act.id),
                "entry_type": "activity",
                "activity_type": act.activity_type,
                "created_at": act.created_at,
                "channel": "system",
                "direction": None,
                "status": None,
                "delivery_status": None,
                "source": "system",
                "subject": "",
                "body": act.description,
                "description": act.description,
                "segments": None,
                "destination_country": "",
                "whatsapp_category": "",
                "template_id": None,
                "broadcast_id": None,
                "sent_by": None,
                "author": actor_data,
                "actor": actor_data,
                "error": "",
                "sent_at": None,
                "delivered_at": None,
                "read_at": None,
                "metadata": act.metadata or {},
                "seen_by_customer": False,
            }
        )

    items.sort(key=lambda x: (x["created_at"], str(x["id"])), reverse=True)

    has_more = len(items) > effective_limit
    page_items = items[:effective_limit]

    next_cursor = None
    if has_more and page_items:
        last_entry = page_items[-1]
        next_cursor = encode_thread_cursor(last_entry["created_at"], last_entry["id"])

    serialized_items = []
    for entry in page_items:
        row = dict(entry)
        if hasattr(row["created_at"], "isoformat"):
            row["created_at"] = row["created_at"].isoformat()
        serialized_items.append(row)

    window_info = get_whatsapp_window_state(conversation.contact)

    return {
        "results": serialized_items,
        "next_cursor": next_cursor,
        "has_more": has_more,
        **window_info,
    }


def get_conversation_notes_queryset(conversation: Conversation) -> QuerySet[ConversationNote]:
    return (
        ConversationNote.objects.select_related("author", "conversation", "tenant")
        .filter(conversation=conversation)
        .order_by("-created_at", "-id")
    )


def get_assignment_rules_queryset() -> QuerySet[AssignmentRule]:
    return (
        AssignmentRule.objects.select_related(
            "assignee",
            "assignee__user",
            "match_tag",
            "tenant",
        )
        .order_by("priority", "created_at")
    )


def get_assignment_rule_by_id(rule_id: uuid.UUID | str) -> AssignmentRule | None:
    return get_assignment_rules_queryset().filter(pk=rule_id).first()


def get_inbox_unread_counts(membership: Membership | None = None) -> dict[str, Any]:
    qs = apply_inbox_visibility(Conversation.objects.all(), membership)

    totals = qs.aggregate(
        total_conversations=Count("id"),
        unread_conversations=Count("id", filter=Q(unread_count__gt=0)),
        unread_messages=Sum("unread_count"),
        unassigned_conversations=Count("id", filter=Q(assigned_to__isnull=True)),
        unassigned_unread_conversations=Count(
            "id", filter=Q(assigned_to__isnull=True, unread_count__gt=0)
        ),
        unassigned_unread_messages=Sum(
            "unread_count", filter=Q(assigned_to__isnull=True)
        ),
    )

    status_rows = list(
        qs.values("status").annotate(
            conversations_count=Count("id"),
            unread_conversations_count=Count("id", filter=Q(unread_count__gt=0)),
            unread_messages_count=Sum("unread_count"),
        )
    )
    by_status: dict[str, dict[str, int]] = {
        s: {
            "conversations_count": 0,
            "unread_conversations_count": 0,
            "unread_messages_count": 0,
        }
        for s in Conversation.Status.values
    }
    for row in status_rows:
        st = row["status"]
        if st in by_status:
            by_status[st] = {
                "conversations_count": int(row["conversations_count"] or 0),
                "unread_conversations_count": int(row["unread_conversations_count"] or 0),
                "unread_messages_count": int(row["unread_messages_count"] or 0),
            }

    assignee_rows = list(
        qs.filter(assigned_to__isnull=False)
        .values(
            "assigned_to_id",
            "assigned_to__user_id",
            "assigned_to__user__first_name",
            "assigned_to__user__last_name",
            "assigned_to__user__email",
        )
        .annotate(
            conversations_count=Count("id"),
            open_conversations_count=Count("id", filter=Q(status=Conversation.Status.OPEN)),
            pending_conversations_count=Count("id", filter=Q(status=Conversation.Status.PENDING)),
            resolved_conversations_count=Count("id", filter=Q(status=Conversation.Status.RESOLVED)),
            unread_conversations_count=Count("id", filter=Q(unread_count__gt=0)),
            unread_messages_count=Sum("unread_count"),
        )
        .order_by("assigned_to__user__email")
    )

    by_assignee = []
    for row in assignee_rows:
        fn = (row.get("assigned_to__user__first_name") or "").strip()
        ln = (row.get("assigned_to__user__last_name") or "").strip()
        email = row.get("assigned_to__user__email") or ""
        full_name = f"{fn} {ln}".strip() or email
        by_assignee.append(
            {
                "membership_id": str(row["assigned_to_id"]),
                "user_id": str(row["assigned_to__user_id"]),
                "full_name": full_name,
                "email": email,
                "conversations_count": int(row["conversations_count"] or 0),
                "open_conversations_count": int(row["open_conversations_count"] or 0),
                "pending_conversations_count": int(row["pending_conversations_count"] or 0),
                "resolved_conversations_count": int(row["resolved_conversations_count"] or 0),
                "unread_conversations_count": int(row["unread_conversations_count"] or 0),
                "unread_messages_count": int(row["unread_messages_count"] or 0),
            }
        )

    return {
        "total_conversations": int(totals["total_conversations"] or 0),
        "unread_conversations": int(totals["unread_conversations"] or 0),
        "unread_messages": int(totals["unread_messages"] or 0),
        "unassigned": {
            "conversations_count": int(totals["unassigned_conversations"] or 0),
            "unread_conversations_count": int(totals["unassigned_unread_conversations"] or 0),
            "unread_messages_count": int(totals["unassigned_unread_messages"] or 0),
        },
        "by_status": by_status,
        "by_assignee": by_assignee,
    }


def get_team_workload_counts(membership: Membership | None = None) -> dict[str, Any]:
    members_qs = Membership.objects.select_related("user", "tenant").filter(is_active=True)
    if (
        membership is not None
        and membership.role == Membership.Role.MARKETER
        and not membership.tenant.marketer_can_view_all_contacts
    ):
        members_qs = members_qs.filter(pk=membership.pk)

    members = list(members_qs.order_by("created_at"))
    member_ids = [m.id for m in members]

    conv_stats = {
        row["assigned_to_id"]: row
        for row in Conversation.objects.filter(assigned_to_id__in=member_ids)
        .values("assigned_to_id")
        .annotate(
            open_count=Count("id", filter=Q(status=Conversation.Status.OPEN)),
            pending_count=Count("id", filter=Q(status=Conversation.Status.PENDING)),
            resolved_count=Count("id", filter=Q(status=Conversation.Status.RESOLVED)),
            unread_count=Count("id", filter=Q(unread_count__gt=0)),
            total_count=Count("id"),
        )
    }

    lead_stats = {
        row["assigned_to_id"]: int(row["leads_count"] or 0)
        for row in Contact.objects.filter(assigned_to_id__in=member_ids)
        .values("assigned_to_id")
        .annotate(leads_count=Count("id"))
    }

    members_workload = []
    for m in members:
        c_row = conv_stats.get(m.id, {})
        members_workload.append(
            {
                "membership_id": str(m.id),
                "user_id": str(m.user_id),
                "full_name": m.user.full_name,
                "email": m.user.email,
                "role": m.role,
                "is_available": m.is_available,
                "assigned_leads_count": lead_stats.get(m.id, 0),
                "open_conversations_count": int(c_row.get("open_count") or 0),
                "pending_conversations_count": int(c_row.get("pending_count") or 0),
                "resolved_conversations_count": int(c_row.get("resolved_count") or 0),
                "unread_conversations_count": int(c_row.get("unread_count") or 0),
                "total_conversations_count": int(c_row.get("total_count") or 0),
            }
        )

    unassigned_conv = Conversation.objects.filter(assigned_to__isnull=True).aggregate(
        open_count=Count("id", filter=Q(status=Conversation.Status.OPEN)),
        pending_count=Count("id", filter=Q(status=Conversation.Status.PENDING)),
        resolved_count=Count("id", filter=Q(status=Conversation.Status.RESOLVED)),
        unread_count=Count("id", filter=Q(unread_count__gt=0)),
    )
    unassigned_leads = Contact.objects.filter(assigned_to__isnull=True).count()

    return {
        "members": members_workload,
        "unassigned": {
            "assigned_leads_count": unassigned_leads,
            "open_conversations_count": int(unassigned_conv["open_count"] or 0),
            "pending_conversations_count": int(unassigned_conv["pending_count"] or 0),
            "resolved_conversations_count": int(unassigned_conv["resolved_count"] or 0),
            "unread_conversations_count": int(unassigned_conv["unread_count"] or 0),
        },
    }
