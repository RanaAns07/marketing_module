from collections.abc import Iterable
from typing import Any
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from accounts.models import Membership, User
from accounts.services import AssignmentService
from automation.services import EnrollmentService
from contacts.models import Activity, Contact, LeadStatus, Tag
from contacts.services import ContactService, log_contact_activity
from core.context import require_tenant_id
from core.events import EventName
from core.exceptions import ApplicationError
from inbox.models import AssignmentRule, Conversation, ConversationNote
from messaging.models import Channel, Message, MessageTemplate
from messaging.services import MessagingService
from notifications.models import NotificationType
from notifications.services import notify


@transaction.atomic
def get_or_create_conversation(*, contact: Contact) -> tuple[Conversation, bool]:
    existing = (
        Conversation.objects.select_for_update(of=("self",))
        .select_related("contact", "assigned_to", "assigned_to__user", "tenant")
        .filter(contact=contact)
        .first()
    )
    if existing is not None:
        if existing.assigned_to_id is None and contact.assigned_to_id is not None:
            existing.assigned_to = contact.assigned_to
            existing.save(update_fields=["assigned_to", "updated_at"])
        return existing, False

    try:
        with transaction.atomic():
            created = Conversation.objects.create(
                contact=contact,
                status=Conversation.Status.OPEN,
                assigned_to=contact.assigned_to,
                last_message_at=contact.last_activity_at or timezone.now(),
            )
            return (
                Conversation.objects.select_for_update(of=("self",))
                .select_related("contact", "assigned_to", "assigned_to__user", "tenant")
                .get(pk=created.pk),
                True,
            )
    except IntegrityError:
        conv = (
            Conversation.objects.select_for_update(of=("self",))
            .select_related("contact", "assigned_to", "assigned_to__user", "tenant")
            .get(contact=contact)
        )
        return conv, False


@transaction.atomic
def process_inbound_domain_event(event: dict[str, Any]) -> Conversation | None:
    tenant_id = require_tenant_id()
    event_name = str(event.get("name") or "")
    contact_id = event.get("contact_id")
    payload = event.get("payload") or {}

    if not contact_id:
        return None

    contact = (
        Contact.objects.select_for_update(of=("self",))
        .select_related("tenant", "assigned_to", "assigned_to__user", "lead_status")
        .prefetch_related("tags")
        .filter(pk=contact_id)
        .first()
    )
    if contact is None:
        return None

    event_to_channel = {
        EventName.EMAIL_RECEIVED: Channel.EMAIL,
        EventName.SMS_RECEIVED: Channel.SMS,
        EventName.WHATSAPP_RECEIVED: Channel.WHATSAPP,
    }
    channel = str(payload.get("channel") or event_to_channel.get(event_name) or "").strip().lower()
    preview = str(payload.get("body") or payload.get("subject") or "").strip()[:500]
    now = timezone.now()

    conversation, created = get_or_create_conversation(contact=contact)
    previous_status = conversation.status
    reopened = (not created) and previous_status in (
        Conversation.Status.RESOLVED,
        Conversation.Status.PENDING,
    )

    conversation.status = Conversation.Status.OPEN
    conversation.resolved_at = None
    conversation.last_message_at = now
    conversation.last_inbound_at = now
    if channel:
        conversation.last_channel = channel
    conversation.last_message_preview = preview
    conversation.unread_count = (conversation.unread_count or 0) + 1
    if conversation.assigned_to_id is None and contact.assigned_to_id is not None:
        conversation.assigned_to = contact.assigned_to

    conversation.save(
        update_fields=[
            "status",
            "resolved_at",
            "last_message_at",
            "last_inbound_at",
            "last_channel",
            "last_message_preview",
            "unread_count",
            "assigned_to",
            "updated_at",
        ]
    )

    if reopened:
        log_contact_activity(
            contact=contact,
            activity_type=Activity.ActivityType.STATUS_CHANGE,
            description=f"Conversation reopened from '{previous_status}' to 'open' on incoming {channel.upper() or 'customer'} reply.",
            metadata={
                "conversation_id": str(conversation.id),
                "old_conversation_status": previous_status,
                "new_conversation_status": Conversation.Status.OPEN,
                "channel": channel,
            },
            touch_activity=False,
        )

    if contact.tenant.handoff_add_interested_tag:
        has_interested = contact.tags.filter(name__iexact="Interested").exists()
        if not has_interested:
            ContactService.add_tags(
                contact=contact,
                tags=["Interested"],
                create_missing=True,
            )

    newly_assigned = False
    if conversation.assigned_to_id is None:
        assignee, newly_assigned = AssignmentService.auto_assign_on_inbound(
            contact=contact,
            conversation=conversation,
            notify_assignee=False,
        )
        if assignee is not None:
            conversation.assigned_to = assignee

    effective_assignee = conversation.assigned_to
    contact_label = contact.full_name or contact.email or contact.phone or "Contact"
    ch_label = (channel or "message").upper()

    if effective_assignee is not None:
        if newly_assigned:
            notif_type = NotificationType.CONVERSATION_ASSIGNED
            title = f"New {ch_label} reply from {contact_label} — Assigned to you"
        else:
            notif_type = NotificationType.INCOMING_MESSAGE
            title = f"New {ch_label} reply from {contact_label}"

        notify(
            tenant=tenant_id,
            users=[effective_assignee.user],
            type=notif_type,
            payload={
                **payload,
                "title": title,
                "body": preview or title,
                "contact_id": str(contact.id),
                "conversation_id": str(conversation.id),
                "channel": channel,
                "assigned_membership_id": str(effective_assignee.id),
                "assigned_user_id": str(effective_assignee.user_id),
            },
            link=f"/inbox/{conversation.id}",
        )
    else:
        owner_admin_user_ids = list(
            Membership.objects.filter(
                role__in=[Membership.Role.OWNER, Membership.Role.ADMIN],
                is_active=True,
                user__is_active=True,
            ).values_list("user_id", flat=True)
        )
        if owner_admin_user_ids:
            notify(
                tenant=tenant_id,
                users=owner_admin_user_ids,
                type=NotificationType.INCOMING_MESSAGE,
                payload={
                    **payload,
                    "title": f"Unassigned {ch_label} reply from {contact_label}",
                    "body": preview or f"Incoming {ch_label} message in shared queue.",
                    "contact_id": str(contact.id),
                    "conversation_id": str(conversation.id),
                    "channel": channel,
                },
                link=f"/inbox/{conversation.id}",
            )

    return conversation


@transaction.atomic
def process_outbound_domain_event(event: dict[str, Any]) -> Conversation | None:
    contact_id = event.get("contact_id")
    payload = event.get("payload") or {}
    if not contact_id:
        return None

    contact = (
        Contact.objects.select_for_update(of=("self",))
        .select_related("assigned_to", "assigned_to__user", "tenant")
        .filter(pk=contact_id)
        .first()
    )
    if contact is None:
        return None

    message_id = payload.get("message_id")
    message = (
        Message.objects.select_related("sent_by")
        .filter(pk=message_id)
        .first()
        if message_id
        else None
    )

    channel = str(
        payload.get("channel") or (message.channel if message else "")
    ).strip().lower()
    source = str(
        payload.get("source") or (message.source if message else Message.Source.MANUAL)
    ).strip().lower()
    preview = (
        (message.body or message.subject)
        if message is not None
        else str(payload.get("body") or payload.get("subject") or "")
    ).strip()[:500]
    msg_time = (
        (message.sent_at or message.created_at)
        if message is not None
        else timezone.now()
    )

    conversation, _ = get_or_create_conversation(contact=contact)
    update_fields = ["last_message_at", "last_message_preview", "updated_at"]
    conversation.last_message_at = msg_time
    conversation.last_message_preview = preview
    if channel:
        conversation.last_channel = channel
        update_fields.append("last_channel")

    if source == Message.Source.MANUAL:
        msg_meta = (message.metadata if message is not None else None) or {}
        if not msg_meta.get("inbox_reply_handled"):
            explicit_status = msg_meta.get("set_conversation_status") or payload.get("set_conversation_status")
            if explicit_status in Conversation.Status.values:
                if conversation.status != explicit_status:
                    old_st = conversation.status
                    conversation.status = explicit_status
                    conversation.resolved_at = (
                        timezone.now()
                        if explicit_status == Conversation.Status.RESOLVED
                        else None
                    )
                    update_fields.extend(["status", "resolved_at"])
                    log_contact_activity(
                        contact=contact,
                        actor=message.sent_by if message else None,
                        activity_type=Activity.ActivityType.STATUS_CHANGE,
                        description=f"Conversation status changed from '{old_st}' to '{explicit_status}'.",
                        metadata={
                            "conversation_id": str(conversation.id),
                            "old_conversation_status": old_st,
                            "new_conversation_status": explicit_status,
                        },
                        touch_activity=False,
                    )
            elif message is not None and message.sent_by_id is not None:
                sender_membership = Membership.objects.filter(
                    user_id=message.sent_by_id,
                    is_active=True,
                ).first()
                if (
                    sender_membership is not None
                    and sender_membership.role == Membership.Role.MARKETER
                    and conversation.status == Conversation.Status.OPEN
                ):
                    conversation.status = Conversation.Status.PENDING
                    conversation.resolved_at = None
                    update_fields.extend(["status", "resolved_at"])
                    log_contact_activity(
                        contact=contact,
                        actor=message.sent_by,
                        activity_type=Activity.ActivityType.STATUS_CHANGE,
                        description="Conversation status moved from 'open' to 'pending' on manual reply.",
                        metadata={
                            "conversation_id": str(conversation.id),
                            "old_conversation_status": Conversation.Status.OPEN,
                            "new_conversation_status": Conversation.Status.PENDING,
                        },
                        touch_activity=False,
                    )

    conversation.save(update_fields=list(dict.fromkeys(update_fields)))
    return conversation


def handle_inbox_domain_event(event: dict[str, Any]) -> dict[str, Any]:
    event_name = str(event.get("name") or "")
    if event_name in (
        EventName.EMAIL_RECEIVED,
        EventName.SMS_RECEIVED,
        EventName.WHATSAPP_RECEIVED,
    ):
        conv = process_inbound_domain_event(event)
        return {
            "subscriber": "inbox",
            "event_name": event_name,
            "conversation_id": str(conv.id) if conv else None,
        }

    if event_name == EventName.MESSAGE_SENT:
        conv = process_outbound_domain_event(event)
        return {
            "subscriber": "inbox",
            "event_name": event_name,
            "conversation_id": str(conv.id) if conv else None,
        }

    return {"subscriber": "inbox", "event_name": event_name, "conversation_id": None}


@transaction.atomic
def claim_conversation(
    *,
    conversation: Conversation,
    actor_membership: Membership,
) -> Conversation:
    locked_conv = (
        Conversation.objects.select_for_update(of=("self",))
        .select_related("contact", "assigned_to", "assigned_to__user")
        .get(pk=conversation.pk)
    )
    if locked_conv.assigned_to_id is not None:
        if locked_conv.assigned_to_id == actor_membership.id:
            return locked_conv
        raise ApplicationError(
            detail="Conversation is already assigned to another team member.",
            code="conversation_already_assigned",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    AssignmentService.assign_contact_and_conversation(
        contact=locked_conv.contact,
        conversation=locked_conv,
        assignee=actor_membership,
        actor=actor_membership.user,
        notify_assignee=False,
        log_activity=True,
        publish_event=True,
    )
    return (
        Conversation.objects.select_related(
            "contact",
            "contact__lead_status",
            "contact__assigned_to",
            "contact__assigned_to__user",
            "assigned_to",
            "assigned_to__user",
            "tenant",
        )
        .prefetch_related("contact__tags")
        .get(pk=locked_conv.pk)
    )


@transaction.atomic
def assign_conversation(
    *,
    conversation: Conversation,
    assignee: Membership | None,
    actor: User | None = None,
) -> Conversation:
    AssignmentService.assign_contact_and_conversation(
        contact=conversation.contact,
        conversation=conversation,
        assignee=assignee,
        actor=actor,
        notify_assignee=True,
        log_activity=True,
        publish_event=True,
    )
    return (
        Conversation.objects.select_related(
            "contact",
            "contact__lead_status",
            "contact__assigned_to",
            "contact__assigned_to__user",
            "assigned_to",
            "assigned_to__user",
            "tenant",
        )
        .prefetch_related("contact__tags")
        .get(pk=conversation.pk)
    )


@transaction.atomic
def set_conversation_status(
    *,
    conversation: Conversation,
    new_status: str,
    actor: User | None = None,
) -> Conversation:
    clean_status = (new_status or "").strip().lower()
    if clean_status not in Conversation.Status.values:
        raise ApplicationError(
            detail=f"Invalid conversation status '{new_status}'.",
            code="invalid_conversation_status",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    locked = (
        Conversation.objects.select_for_update(of=("self",))
        .select_related("contact")
        .get(pk=conversation.pk)
    )
    old_status = locked.status
    if old_status == clean_status:
        return locked

    now = timezone.now()
    locked.status = clean_status
    update_fields = ["status", "resolved_at", "updated_at"]
    if clean_status == Conversation.Status.RESOLVED:
        locked.resolved_at = now
        if locked.unread_count > 0:
            locked.unread_count = 0
            update_fields.append("unread_count")
    else:
        locked.resolved_at = None

    locked.save(update_fields=update_fields)

    log_contact_activity(
        contact=locked.contact,
        actor=actor,
        activity_type=Activity.ActivityType.STATUS_CHANGE,
        description=f"Conversation status changed from '{old_status}' to '{clean_status}'.",
        metadata={
            "conversation_id": str(locked.id),
            "old_conversation_status": old_status,
            "new_conversation_status": clean_status,
        },
        touch_activity=False,
    )
    return (
        Conversation.objects.select_related(
            "contact",
            "contact__lead_status",
            "contact__assigned_to",
            "contact__assigned_to__user",
            "assigned_to",
            "assigned_to__user",
            "tenant",
        )
        .prefetch_related("contact__tags")
        .get(pk=locked.pk)
    )


@transaction.atomic
def mark_conversation_read(*, conversation: Conversation) -> Conversation:
    locked = Conversation.objects.select_for_update().get(pk=conversation.pk)
    if locked.unread_count != 0:
        locked.unread_count = 0
        locked.save(update_fields=["unread_count", "updated_at"])
    return (
        Conversation.objects.select_related(
            "contact",
            "contact__lead_status",
            "contact__assigned_to",
            "contact__assigned_to__user",
            "assigned_to",
            "assigned_to__user",
            "tenant",
        )
        .prefetch_related("contact__tags")
        .get(pk=locked.pk)
    )


@transaction.atomic
def add_conversation_note(
    *,
    conversation: Conversation,
    body: str,
    author: User | None = None,
) -> ConversationNote:
    cleaned_body = (body or "").strip()
    if not cleaned_body:
        raise ApplicationError(
            detail="Note body cannot be empty.",
            code="empty_note_body",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    note = ConversationNote.objects.create(
        conversation=conversation,
        author=author,
        body=cleaned_body,
    )

    log_contact_activity(
        contact=conversation.contact,
        actor=author,
        activity_type=Activity.ActivityType.NOTE,
        description=cleaned_body,
        metadata={
            "conversation_id": str(conversation.id),
            "note_id": str(note.id),
            "author_id": str(author.id) if author else None,
        },
    )
    return ConversationNote.objects.select_related("author", "conversation", "tenant").get(pk=note.pk)


@transaction.atomic
def reply_to_conversation(
    *,
    conversation: Conversation,
    actor_membership: Membership,
    channel: str,
    body: str = "",
    subject: str = "",
    template: MessageTemplate | None = None,
    set_status: str | None = None,
    idempotency_key: str | None = None,
    extra_context: dict[str, Any] | None = None,
) -> tuple[Conversation, Message]:
    locked_conv = (
        Conversation.objects.select_for_update(of=("self",))
        .select_related("contact", "assigned_to", "assigned_to__user", "tenant")
        .get(pk=conversation.pk)
    )
    locked_contact = (
        Contact.objects.select_for_update(of=("self",))
        .select_related("lead_status", "assigned_to", "assigned_to__user", "tenant")
        .get(pk=locked_conv.contact_id)
    )

    if locked_conv.assigned_to_id is None and actor_membership.role == Membership.Role.MARKETER:
        AssignmentService.assign_contact_and_conversation(
            contact=locked_contact,
            conversation=locked_conv,
            assignee=actor_membership,
            actor=actor_membership.user,
            notify_assignee=False,
            log_activity=True,
            publish_event=True,
        )
        locked_conv.assigned_to = actor_membership

    clean_set_status = (set_status or "").strip().lower() or None
    if clean_set_status and clean_set_status not in Conversation.Status.values:
        raise ApplicationError(
            detail=f"Invalid conversation status '{set_status}'.",
            code="invalid_conversation_status",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    msg_meta: dict[str, Any] = {
        "conversation_id": str(locked_conv.id),
        "inbox_reply_handled": True,
    }
    if clean_set_status:
        msg_meta["set_conversation_status"] = clean_set_status

    message = MessagingService.send(
        contact=locked_contact,
        channel=channel,
        body=body,
        subject=subject,
        template=template,
        source=Message.Source.MANUAL,
        sent_by=actor_membership.user,
        idempotency_key=idempotency_key,
        extra_context=extra_context,
        metadata=msg_meta,
    )

    now = timezone.now()
    locked_conv.last_message_at = message.sent_at or message.created_at or now
    locked_conv.last_channel = message.channel
    locked_conv.last_message_preview = (message.body or message.subject or "").strip()[:500]
    locked_conv.unread_count = 0

    old_status = locked_conv.status
    target_status = old_status
    if clean_set_status is not None:
        target_status = clean_set_status
    elif (
        actor_membership.role == Membership.Role.MARKETER
        and old_status == Conversation.Status.OPEN
    ):
        target_status = Conversation.Status.PENDING

    if target_status != old_status:
        locked_conv.status = target_status
        locked_conv.resolved_at = (
            now if target_status == Conversation.Status.RESOLVED else None
        )
        log_contact_activity(
            contact=locked_contact,
            actor=actor_membership.user,
            activity_type=Activity.ActivityType.STATUS_CHANGE,
            description=f"Conversation status changed from '{old_status}' to '{target_status}'.",
            metadata={
                "conversation_id": str(locked_conv.id),
                "old_conversation_status": old_status,
                "new_conversation_status": target_status,
            },
            touch_activity=False,
        )

    locked_conv.save(
        update_fields=[
            "assigned_to",
            "status",
            "resolved_at",
            "last_message_at",
            "last_channel",
            "last_message_preview",
            "unread_count",
            "updated_at",
        ]
    )

    refreshed_conv = (
        Conversation.objects.select_related(
            "contact",
            "contact__lead_status",
            "contact__assigned_to",
            "contact__assigned_to__user",
            "assigned_to",
            "assigned_to__user",
            "tenant",
        )
        .prefetch_related("contact__tags")
        .get(pk=locked_conv.pk)
    )
    return refreshed_conv, message


@transaction.atomic
def update_conversation_contact_status(
    *,
    conversation: Conversation,
    lead_status: LeadStatus,
    actor: User | None = None,
) -> Conversation:
    ContactService.change_status(
        contact=conversation.contact,
        new_status=lead_status,
        actor=actor,
    )
    return (
        Conversation.objects.select_related(
            "contact",
            "contact__lead_status",
            "contact__assigned_to",
            "contact__assigned_to__user",
            "assigned_to",
            "assigned_to__user",
            "tenant",
        )
        .prefetch_related("contact__tags")
        .get(pk=conversation.pk)
    )


@transaction.atomic
def update_conversation_contact_tags(
    *,
    conversation: Conversation,
    add_tags: Iterable[Any] | None = None,
    remove_tags: Iterable[Any] | None = None,
    set_tags: Iterable[Any] | None = None,
    actor: User | None = None,
) -> Conversation:
    contact = conversation.contact
    if set_tags is not None:
        ContactService.update_contact(contact=contact, actor=actor, tags=set_tags)
    else:
        if add_tags:
            ContactService.add_tags(
                contact=contact,
                tags=add_tags,
                actor=actor,
                create_missing=True,
            )
        if remove_tags:
            ContactService.remove_tags(
                contact=contact,
                tags=remove_tags,
                actor=actor,
            )
    return (
        Conversation.objects.select_related(
            "contact",
            "contact__lead_status",
            "contact__assigned_to",
            "contact__assigned_to__user",
            "assigned_to",
            "assigned_to__user",
            "tenant",
        )
        .prefetch_related("contact__tags")
        .get(pk=conversation.pk)
    )


@transaction.atomic
def pause_conversation_automations(
    *,
    conversation: Conversation,
    reason: str = "inbox_manual_pause",
    actor: User | None = None,
) -> list[Any]:
    return EnrollmentService.pause_all_for_contact(
        contact=conversation.contact,
        reason=reason,
        actor=actor,
    )


@transaction.atomic
def resume_conversation_automations(
    *,
    conversation: Conversation,
    actor: User | None = None,
) -> list[Any]:
    return EnrollmentService.resume_all_for_contact(
        contact=conversation.contact,
        actor=actor,
    )


def _validate_rule_criteria(
    *,
    match_page: str,
    match_form: str,
    match_tag: Tag | None,
    match_tag_name: str,
) -> None:
    if not (
        match_page.strip()
        or match_form.strip()
        or match_tag is not None
        or match_tag_name.strip()
    ):
        raise ApplicationError(
            detail="An assignment rule must specify at least one match condition (page, form, or tag).",
            code="assignment_rule_empty_criteria",
            status_code=status.HTTP_400_BAD_REQUEST,
        )


@transaction.atomic
def create_assignment_rule(
    *,
    priority: int,
    assignee: Membership,
    name: str = "",
    match_page: str = "",
    match_form: str = "",
    match_tag: Tag | None = None,
    match_tag_name: str = "",
    is_active: bool = True,
) -> AssignmentRule:
    clean_page = (match_page or "").strip()
    clean_form = (match_form or "").strip()
    clean_tag_name = (
        match_tag.name if match_tag is not None else (match_tag_name or "").strip()
    )
    if match_tag is None and clean_tag_name:
        match_tag = Tag.objects.filter(name__iexact=clean_tag_name).first()
        if match_tag is not None:
            clean_tag_name = match_tag.name

    _validate_rule_criteria(
        match_page=clean_page,
        match_form=clean_form,
        match_tag=match_tag,
        match_tag_name=clean_tag_name,
    )

    if AssignmentRule.objects.filter(priority=priority).exists():
        raise ApplicationError(
            detail=f"An assignment rule with priority {priority} already exists for this tenant.",
            code="duplicate_rule_priority",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    rule = AssignmentRule.objects.create(
        name=(name or "").strip(),
        priority=priority,
        match_page=clean_page,
        match_form=clean_form,
        match_tag=match_tag,
        match_tag_name=clean_tag_name,
        assignee=assignee,
        is_active=is_active,
    )
    return (
        AssignmentRule.objects.select_related("assignee", "assignee__user", "match_tag", "tenant")
        .get(pk=rule.pk)
    )


@transaction.atomic
def update_assignment_rule(
    *,
    rule: AssignmentRule,
    **changes: Any,
) -> AssignmentRule:
    locked = (
        AssignmentRule.objects.select_for_update(of=("self",))
        .select_related("assignee", "match_tag")
        .get(pk=rule.pk)
    )

    new_priority = changes.get("priority", locked.priority)
    if new_priority != locked.priority:
        if AssignmentRule.objects.filter(priority=new_priority).exclude(pk=locked.pk).exists():
            raise ApplicationError(
                detail=f"An assignment rule with priority {new_priority} already exists for this tenant.",
                code="duplicate_rule_priority",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        locked.priority = new_priority

    if "name" in changes and changes["name"] is not None:
        locked.name = str(changes["name"]).strip()
    if "match_page" in changes and changes["match_page"] is not None:
        locked.match_page = str(changes["match_page"]).strip()
    if "match_form" in changes and changes["match_form"] is not None:
        locked.match_form = str(changes["match_form"]).strip()
    if "match_tag" in changes:
        locked.match_tag = changes["match_tag"]
        if locked.match_tag is not None:
            locked.match_tag_name = locked.match_tag.name
    if "match_tag_name" in changes and changes["match_tag_name"] is not None:
        raw_tname = str(changes["match_tag_name"]).strip()
        locked.match_tag_name = raw_tname
        if raw_tname and locked.match_tag is None:
            found_tag = Tag.objects.filter(name__iexact=raw_tname).first()
            if found_tag is not None:
                locked.match_tag = found_tag
                locked.match_tag_name = found_tag.name
        elif not raw_tname and "match_tag" not in changes:
            locked.match_tag = None

    if "assignee" in changes and changes["assignee"] is not None:
        locked.assignee = changes["assignee"]
    if "is_active" in changes and changes["is_active"] is not None:
        locked.is_active = bool(changes["is_active"])

    _validate_rule_criteria(
        match_page=locked.match_page,
        match_form=locked.match_form,
        match_tag=locked.match_tag,
        match_tag_name=locked.match_tag_name,
    )

    locked.save()
    return (
        AssignmentRule.objects.select_related("assignee", "assignee__user", "match_tag", "tenant")
        .get(pk=locked.pk)
    )


@transaction.atomic
def delete_assignment_rule(*, rule: AssignmentRule) -> None:
    rule.delete()
