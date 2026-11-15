from collections.abc import Iterable
import re
from typing import Any
import uuid
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status
from accounts.models import Membership, User
from contacts.models import (
    DEFAULT_LEAD_STATUSES,
    Activity,
    Appointment,
    Contact,
    ContactTag,
    CustomFieldDefinition,
    LeadStatus,
    Tag,
    Task,
)
from contacts.selectors import get_default_lead_status
from core.context import require_tenant, require_tenant_id, tenant_context
from core.events import EventName, publish
from core.exceptions import ApplicationError
from notifications.models import NotificationType
from notifications.services import notify

_E164_REGEX = re.compile(r"^\+[1-9]\d{6,14}$")


def normalize_email(email: str | None) -> str | None:
    if email is None:
        return None
    cleaned = email.strip().lower()
    return cleaned or None


def normalize_phone_e164(phone: str | None) -> str | None:
    if phone is None:
        return None
    raw = phone.strip()
    if not raw:
        return None
    cleaned = re.sub(r"[\s\-\(\)\.]", "", raw)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    elif not cleaned.startswith("+") and cleaned.isdigit():
        cleaned = "+" + cleaned
    if not _E164_REGEX.match(cleaned):
        raise ApplicationError(
            detail="Phone number must be in valid E.164 format (e.g. +13125550194).",
            code="invalid_phone_format",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return cleaned


def _compose_full_name(first_name: str, last_name: str, full_name: str) -> tuple[str, str, str]:
    fn = (first_name or "").strip()
    ln = (last_name or "").strip()
    full = (full_name or "").strip()
    if not full and (fn or ln):
        full = f"{fn} {ln}".strip()
    elif full and not fn and not ln:
        parts = full.split(" ", 1)
        fn = parts[0]
        ln = parts[1] if len(parts) > 1 else ""
    return fn, ln, full


@transaction.atomic
def seed_default_lead_statuses(tenant: Any = None) -> list[LeadStatus]:
    target_tenant = tenant or require_tenant()
    with tenant_context(target_tenant):
        existing_names = set(
            LeadStatus.objects.values_list("name", flat=True)
        )
        to_create: list[LeadStatus] = []
        has_default = LeadStatus.objects.filter(is_default=True).exists()
        for name, order_val, is_def in DEFAULT_LEAD_STATUSES:
            if name not in existing_names:
                to_create.append(
                    LeadStatus(
                        tenant=target_tenant if hasattr(target_tenant, "pk") else None,
                        tenant_id=target_tenant.pk if hasattr(target_tenant, "pk") else target_tenant,
                        name=name,
                        order=order_val,
                        is_default=(is_def and not has_default),
                    )
                )
        if to_create:
            LeadStatus.objects.bulk_create(to_create)
        return list(LeadStatus.objects.order_by("order"))


def validate_custom_fields_values(
    incoming_fields: dict[str, Any] | None,
    existing_fields: dict[str, Any] | None = None,
    enforce_required: bool = False,
) -> dict[str, Any]:
    merged = dict(existing_fields or {})
    if incoming_fields is None:
        incoming_fields = {}
    if not isinstance(incoming_fields, dict):
        raise ApplicationError(
            detail="custom_fields must be a JSON object.",
            code="invalid_custom_fields",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    definitions = {
        d.key: d for d in CustomFieldDefinition.objects.all()
    }

    for key, val in incoming_fields.items():
        if val is None:
            merged.pop(key, None)
            continue
        definition = definitions.get(key)
        if definition is not None:
            ftype = definition.field_type
            if ftype == CustomFieldDefinition.FieldType.TEXT:
                if not isinstance(val, str):
                    raise ApplicationError(
                        detail=f"Custom field '{key}' must be a text string.",
                        code="invalid_custom_field_type",
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )
            elif ftype == CustomFieldDefinition.FieldType.NUMBER:
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    raise ApplicationError(
                        detail=f"Custom field '{key}' must be a number.",
                        code="invalid_custom_field_type",
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )
            elif ftype == CustomFieldDefinition.FieldType.BOOL:
                if not isinstance(val, bool):
                    raise ApplicationError(
                        detail=f"Custom field '{key}' must be a boolean.",
                        code="invalid_custom_field_type",
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )
            elif ftype == CustomFieldDefinition.FieldType.DATE:
                if not isinstance(val, str) or (
                    parse_date(val) is None and parse_datetime(val) is None
                ):
                    raise ApplicationError(
                        detail=f"Custom field '{key}' must be a valid ISO date or datetime string.",
                        code="invalid_custom_field_type",
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )
            elif ftype == CustomFieldDefinition.FieldType.SELECT:
                allowed_options = definition.options or []
                if val not in allowed_options:
                    raise ApplicationError(
                        detail=f"Custom field '{key}' must be one of: {', '.join(map(str, allowed_options))}.",
                        code="invalid_custom_field_option",
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )
        merged[key] = val

    if enforce_required:
        for key, definition in definitions.items():
            if definition.is_required and (key not in merged or merged[key] in ("", None)):
                raise ApplicationError(
                    detail=f"Custom field '{definition.label}' ({key}) is required.",
                    code="required_custom_field_missing",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

    return merged


@transaction.atomic
def log_contact_activity(
    *,
    contact: Contact,
    activity_type: str,
    description: str = "",
    metadata: dict[str, Any] | None = None,
    actor: User | None = None,
    touch_activity: bool = True,
) -> Activity:
    now = timezone.now()
    activity = Activity.objects.create(
        contact=contact,
        actor=actor,
        activity_type=activity_type,
        description=description,
        metadata=dict(metadata or {}),
    )
    if touch_activity:
        contact.last_activity_at = now
        contact.inactive_notified_at = None
        contact.save(update_fields=["last_activity_at", "inactive_notified_at", "updated_at"])
    return activity


def _resolve_tags(
    tags_input: Iterable[Any] | None,
    create_missing: bool = False,
) -> list[Tag]:
    if not tags_input:
        return []
    resolved: list[Tag] = []
    seen_ids: set[uuid.UUID] = set()
    for item in tags_input:
        tag_obj: Tag | None = None
        if isinstance(item, Tag):
            tag_obj = item
        elif isinstance(item, uuid.UUID):
            tag_obj = Tag.objects.filter(pk=item).first()
        elif isinstance(item, str):
            raw = item.strip()
            if not raw:
                continue
            try:
                tag_uuid = uuid.UUID(raw)
                tag_obj = Tag.objects.filter(pk=tag_uuid).first()
            except ValueError:
                tag_obj = Tag.objects.filter(name__iexact=raw).first()
                if tag_obj is None and create_missing:
                    tag_obj = Tag.objects.create(name=raw)
        if tag_obj is not None and tag_obj.pk not in seen_ids:
            seen_ids.add(tag_obj.pk)
            resolved.append(tag_obj)
    return resolved


def _check_unique_contact_identifiers(
    *,
    email: str | None,
    phone: str | None,
    exclude_contact_id: uuid.UUID | None = None,
) -> None:
    if email:
        qs = Contact.objects.filter(email=email)
        if exclude_contact_id is not None:
            qs = qs.exclude(pk=exclude_contact_id)
        if qs.exists():
            raise ApplicationError(
                detail="A contact with this email address already exists in this tenant.",
                code="duplicate_contact_email",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    if phone:
        qs = Contact.objects.filter(phone=phone)
        if exclude_contact_id is not None:
            qs = qs.exclude(pk=exclude_contact_id)
        if qs.exists():
            raise ApplicationError(
                detail="A contact with this phone number already exists in this tenant.",
                code="duplicate_contact_phone",
                status_code=status.HTTP_400_BAD_REQUEST,
            )


class ContactService:
    @staticmethod
    @transaction.atomic
    def create_contact(
        *,
        first_name: str = "",
        last_name: str = "",
        full_name: str = "",
        email: str | None = None,
        phone: str | None = None,
        city: str = "",
        country: str = "",
        timezone_name: str = "UTC",
        lead_status: LeadStatus | None = None,
        assigned_to: Membership | None = None,
        source_channel: str = "manual",
        source_page: str = "",
        source_form: str = "",
        campaign: str = "",
        ad_set: str = "",
        ad: str = "",
        external_id: str = "",
        email_subscription: str = Contact.SubscriptionStatus.SUBSCRIBED,
        sms_subscription: str = Contact.SubscriptionStatus.SUBSCRIBED,
        whatsapp_subscription: str = Contact.SubscriptionStatus.SUBSCRIBED,
        custom_fields: dict[str, Any] | None = None,
        tags: Iterable[Any] | None = None,
        actor: User | None = None,
    ) -> Contact:
        tenant_id = require_tenant_id()
        norm_email = normalize_email(email)
        norm_phone = normalize_phone_e164(phone)
        _check_unique_contact_identifiers(email=norm_email, phone=norm_phone)

        fn, ln, full = _compose_full_name(first_name, last_name, full_name)
        validated_custom = validate_custom_fields_values(
            custom_fields,
            existing_fields={},
            enforce_required=True,
        )

        target_status = lead_status or get_default_lead_status()
        now = timezone.now()
        converted_at = (
            now
            if target_status is not None and target_status.name.strip().lower() == "customer"
            else None
        )

        contact = Contact.objects.create(
            first_name=fn,
            last_name=ln,
            full_name=full,
            email=norm_email,
            phone=norm_phone,
            city=city.strip(),
            country=country.strip(),
            timezone=timezone_name or "UTC",
            lead_status=target_status,
            assigned_to=assigned_to,
            source_channel=source_channel.strip() if source_channel else "manual",
            source_page=source_page.strip(),
            source_form=source_form.strip(),
            campaign=campaign.strip(),
            ad_set=ad_set.strip(),
            ad=ad.strip(),
            acquired_at=now,
            converted_at=converted_at,
            external_id=external_id.strip(),
            last_activity_at=now,
            email_subscription=email_subscription,
            sms_subscription=sms_subscription,
            whatsapp_subscription=whatsapp_subscription,
            custom_fields=validated_custom,
        )

        log_contact_activity(
            contact=contact,
            actor=actor,
            activity_type=Activity.ActivityType.EVENT,
            description=f"Contact created ({contact.source_channel or 'manual'}).",
            metadata={"source_channel": contact.source_channel, "campaign": contact.campaign},
        )

        if assigned_to is not None:
            log_contact_activity(
                contact=contact,
                actor=actor,
                activity_type=Activity.ActivityType.ASSIGNMENT,
                description=f"Assigned to {assigned_to.user.full_name}.",
                metadata={
                    "assigned_membership_id": str(assigned_to.id),
                    "assigned_user_id": str(assigned_to.user_id),
                },
            )
            notify(
                tenant=tenant_id,
                users=[assigned_to.user],
                type=NotificationType.CONVERSATION_ASSIGNED,
                payload={
                    "title": f"New Lead Assigned: {contact.full_name or contact.email or contact.phone}",
                    "body": f"{contact.full_name or 'A new contact'} has been assigned to you.",
                    "contact_id": str(contact.id),
                },
                link=f"/contacts/{contact.id}",
            )

        if tags:
            ContactService.add_tags(contact=contact, tags=tags, actor=actor, create_missing=True)

        publish(
            EventName.LEAD_CREATED,
            tenant_id=tenant_id,
            contact_id=contact.id,
            payload={
                "title": f"New Lead: {contact.full_name or contact.email or contact.phone}",
                "source_channel": contact.source_channel,
                "source_form": contact.source_form,
                "campaign": contact.campaign,
                "assigned_membership_id": str(assigned_to.id) if assigned_to else None,
                "assigned_user_id": str(assigned_to.user_id) if assigned_to else None,
            },
        )
        return contact

    @staticmethod
    @transaction.atomic
    def upsert_from_source(
        *,
        first_name: str = "",
        last_name: str = "",
        full_name: str = "",
        email: str | None = None,
        phone: str | None = None,
        city: str = "",
        country: str = "",
        timezone_name: str = "UTC",
        source_channel: str = "",
        source_page: str = "",
        source_form: str = "",
        campaign: str = "",
        ad_set: str = "",
        ad: str = "",
        external_id: str = "",
        custom_fields: dict[str, Any] | None = None,
        tags: Iterable[Any] | None = None,
        assigned_to: Membership | None = None,
        lead_status: LeadStatus | None = None,
        acquired_at: Any = None,
        raw_answers: Any = None,
        is_form_submission: bool = False,
        actor: User | None = None,
    ) -> tuple[Contact, bool]:
        tenant_id = require_tenant_id()
        norm_email = normalize_email(email)
        norm_phone = normalize_phone_e164(phone)
        clean_ext_id = (external_id or "").strip()

        existing: Contact | None = None
        if clean_ext_id:
            existing = Contact.objects.select_for_update().filter(external_id=clean_ext_id).first()
        if existing is None and norm_email:
            existing = Contact.objects.select_for_update().filter(email=norm_email).first()
        if existing is None and norm_phone:
            existing = Contact.objects.select_for_update().filter(phone=norm_phone).first()

        is_form = bool(is_form_submission or source_form)

        if existing is None:
            fn, ln, full = _compose_full_name(first_name, last_name, full_name)
            validated_custom = validate_custom_fields_values(
                custom_fields,
                existing_fields={},
                enforce_required=False,
            )
            target_status = lead_status or get_default_lead_status()
            now = timezone.now()
            effective_acquired_at = acquired_at or now
            contact = Contact.objects.create(
                first_name=fn,
                last_name=ln,
                full_name=full,
                email=norm_email,
                phone=norm_phone,
                city=city.strip(),
                country=country.strip(),
                timezone=timezone_name or "UTC",
                lead_status=target_status,
                assigned_to=assigned_to,
                source_channel=source_channel.strip(),
                source_page=source_page.strip(),
                source_form=source_form.strip(),
                campaign=campaign.strip(),
                ad_set=ad_set.strip(),
                ad=ad.strip(),
                acquired_at=effective_acquired_at,
                external_id=external_id.strip(),
                last_activity_at=now,
                custom_fields=validated_custom,
            )
            if is_form:
                form_meta = {
                    "source_channel": source_channel,
                    "source_page": source_page,
                    "source_form": source_form,
                    "campaign": campaign,
                    "ad_set": ad_set,
                    "ad": ad,
                    "custom_fields": custom_fields or {},
                }
                if raw_answers is not None:
                    form_meta["raw_answers"] = raw_answers
                log_contact_activity(
                    contact=contact,
                    actor=actor,
                    activity_type=Activity.ActivityType.FORM_SUBMISSION,
                    description=f"Submitted Lead Form {source_form or source_channel}".strip(),
                    metadata=form_meta,
                )
            else:
                log_contact_activity(
                    contact=contact,
                    actor=actor,
                    activity_type=Activity.ActivityType.EVENT,
                    description=f"Lead captured from {source_channel or 'external source'}.",
                    metadata={
                        "source_channel": source_channel,
                        "campaign": campaign,
                    },
                )

            if assigned_to is not None:
                notify(
                    tenant=tenant_id,
                    users=[assigned_to.user],
                    type=NotificationType.CONVERSATION_ASSIGNED,
                    payload={
                        "title": f"New Lead Assigned: {contact.full_name or contact.email or contact.phone}",
                        "body": f"{contact.full_name or 'A new lead'} was assigned to you.",
                        "contact_id": str(contact.id),
                    },
                    link=f"/contacts/{contact.id}",
                )

            if tags:
                ContactService.add_tags(contact=contact, tags=tags, actor=actor, create_missing=True)

            event_payload = {
                "title": f"New Lead: {contact.full_name or contact.email or contact.phone}",
                "source_channel": contact.source_channel,
                "source_page": contact.source_page,
                "source_form": contact.source_form,
                "campaign": contact.campaign,
                "ad_set": contact.ad_set,
                "ad": contact.ad,
                "assigned_membership_id": str(assigned_to.id) if assigned_to else None,
                "assigned_user_id": str(assigned_to.user_id) if assigned_to else None,
            }
            publish(
                EventName.LEAD_CREATED,
                tenant_id=tenant_id,
                contact_id=contact.id,
                payload=event_payload,
            )
            if is_form:
                publish(
                    EventName.FORM_SUBMITTED,
                    tenant_id=tenant_id,
                    contact_id=contact.id,
                    payload=event_payload,
                )
            return contact, True

        fn, ln, full = _compose_full_name(
            first_name or existing.first_name,
            last_name or existing.last_name,
            full_name or existing.full_name,
        )
        existing.first_name = fn
        existing.last_name = ln
        existing.full_name = full

        if norm_email and not existing.email:
            if not Contact.objects.filter(email=norm_email).exclude(pk=existing.pk).exists():
                existing.email = norm_email
        if norm_phone and not existing.phone:
            if not Contact.objects.filter(phone=norm_phone).exclude(pk=existing.pk).exists():
                existing.phone = norm_phone

        if city.strip():
            existing.city = city.strip()
        if country.strip():
            existing.country = country.strip()
        if timezone_name and timezone_name != "UTC":
            existing.timezone = timezone_name
        if source_channel.strip() and not existing.source_channel:
            existing.source_channel = source_channel.strip()
        if source_page.strip():
            existing.source_page = source_page.strip()
        if source_form.strip():
            existing.source_form = source_form.strip()
        if campaign.strip():
            existing.campaign = campaign.strip()
        if ad_set.strip():
            existing.ad_set = ad_set.strip()
        if ad.strip():
            existing.ad = ad.strip()
        if external_id.strip():
            existing.external_id = external_id.strip()

        if custom_fields:
            existing.custom_fields = validate_custom_fields_values(
                custom_fields,
                existing_fields=existing.custom_fields,
                enforce_required=False,
            )

        if assigned_to is not None and existing.assigned_to_id is None:
            existing.assigned_to = assigned_to

        existing.last_activity_at = timezone.now()
        existing.inactive_notified_at = None
        existing.save()

        if is_form:
            repeat_meta = {
                "source_channel": source_channel,
                "source_page": source_page,
                "source_form": source_form,
                "campaign": campaign,
                "ad_set": ad_set,
                "ad": ad,
                "custom_fields": custom_fields or {},
                "deduplicated": True,
            }
            if raw_answers is not None:
                repeat_meta["raw_answers"] = raw_answers
            log_contact_activity(
                contact=existing,
                actor=actor,
                activity_type=Activity.ActivityType.FORM_SUBMISSION,
                description=f"Submitted Lead Form {source_form or source_channel}".strip(),
                metadata=repeat_meta,
            )
            publish(
                EventName.FORM_SUBMITTED,
                tenant_id=tenant_id,
                contact_id=existing.id,
                payload={
                    "source_channel": source_channel or existing.source_channel,
                    "source_page": source_page or existing.source_page,
                    "source_form": source_form or existing.source_form,
                    "campaign": campaign or existing.campaign,
                    "deduplicated": True,
                },
            )
        else:
            log_contact_activity(
                contact=existing,
                actor=actor,
                activity_type=Activity.ActivityType.EVENT,
                description=f"Updated contact from {source_channel or 'source'}.",
                metadata={"source_channel": source_channel, "deduplicated": True},
            )

        if tags:
            ContactService.add_tags(contact=existing, tags=tags, actor=actor, create_missing=True)

        if existing.assigned_to is not None and is_form:
            notify(
                tenant=tenant_id,
                users=[existing.assigned_to.user],
                type=NotificationType.NEW_LEAD,
                payload={
                    "title": f"Repeat Form Submission: {existing.full_name or existing.email or existing.phone}",
                    "body": f"{existing.full_name or 'An existing contact'} submitted {source_form or 'a form'}.",
                    "contact_id": str(existing.id),
                },
                link=f"/contacts/{existing.id}",
            )

        return existing, False

    @staticmethod
    @transaction.atomic
    def update_contact(
        *,
        contact: Contact,
        actor: User | None = None,
        **changes: Any,
    ) -> Contact:
        if "email" in changes:
            changes["email"] = normalize_email(changes["email"])
        if "phone" in changes:
            changes["phone"] = normalize_phone_e164(changes["phone"])

        _check_unique_contact_identifiers(
            email=changes.get("email", contact.email),
            phone=changes.get("phone", contact.phone),
            exclude_contact_id=contact.pk,
        )

        new_status = changes.pop("lead_status", None)
        has_assigned_change = "assigned_to" in changes
        new_assignee = changes.pop("assigned_to", None) if has_assigned_change else None
        incoming_tags = changes.pop("tags", None)
        incoming_custom_fields = changes.pop("custom_fields", None)

        if any(k in changes for k in ("first_name", "last_name", "full_name")):
            fn, ln, full = _compose_full_name(
                changes.get("first_name", contact.first_name),
                changes.get("last_name", contact.last_name),
                changes.get("full_name", contact.full_name if "full_name" in changes else ""),
            )
            contact.first_name = fn
            contact.last_name = ln
            contact.full_name = full
            changes.pop("first_name", None)
            changes.pop("last_name", None)
            changes.pop("full_name", None)

        if incoming_custom_fields is not None:
            contact.custom_fields = validate_custom_fields_values(
                incoming_custom_fields,
                existing_fields=contact.custom_fields,
                enforce_required=False,
            )

        direct_fields = {
            "email",
            "phone",
            "city",
            "country",
            "timezone",
            "source_channel",
            "source_page",
            "source_form",
            "campaign",
            "ad_set",
            "ad",
            "external_id",
            "email_subscription",
            "sms_subscription",
            "whatsapp_subscription",
        }
        for key, value in changes.items():
            if key in direct_fields:
                setattr(contact, key, value)

        contact.last_activity_at = timezone.now()
        contact.inactive_notified_at = None
        contact.save()

        if new_status is not None and new_status.pk != contact.lead_status_id:
            ContactService.change_status(contact=contact, new_status=new_status, actor=actor)

        if has_assigned_change and (
            (new_assignee is None and contact.assigned_to_id is not None)
            or (new_assignee is not None and new_assignee.pk != contact.assigned_to_id)
        ):
            ContactService.assign(contact=contact, assignee=new_assignee, actor=actor)

        if incoming_tags is not None:
            resolved_desired = _resolve_tags(incoming_tags, create_missing=True)
            desired_ids = {t.pk for t in resolved_desired}
            current_tags = list(contact.tags.all())
            current_ids = {t.pk for t in current_tags}
            to_add = [t for t in resolved_desired if t.pk not in current_ids]
            to_remove = [t for t in current_tags if t.pk not in desired_ids]
            if to_add:
                ContactService.add_tags(contact=contact, tags=to_add, actor=actor)
            if to_remove:
                ContactService.remove_tags(contact=contact, tags=to_remove, actor=actor)

        return Contact.objects.select_related(
            "lead_status", "assigned_to", "assigned_to__user", "tenant"
        ).prefetch_related("tags").get(pk=contact.pk)

    @staticmethod
    @transaction.atomic
    def add_tags(
        *,
        contact: Contact,
        tags: Iterable[Any],
        actor: User | None = None,
        create_missing: bool = False,
    ) -> list[Tag]:
        tenant_id = require_tenant_id()
        resolved_tags = _resolve_tags(tags, create_missing=create_missing)
        if not resolved_tags:
            return []

        existing_tag_ids = set(
            ContactTag.objects.filter(
                contact=contact,
                tag__in=resolved_tags,
            ).values_list("tag_id", flat=True)
        )

        newly_added: list[Tag] = []
        to_create: list[ContactTag] = []
        for tag in resolved_tags:
            if tag.pk not in existing_tag_ids:
                newly_added.append(tag)
                to_create.append(
                    ContactTag(
                        tenant_id=tenant_id,
                        contact=contact,
                        tag=tag,
                    )
                )

        if not to_create:
            return []

        ContactTag.objects.bulk_create(to_create)
        for tag in newly_added:
            log_contact_activity(
                contact=contact,
                actor=actor,
                activity_type=Activity.ActivityType.TAG_CHANGE,
                description=f"Tag '{tag.name}' added.",
                metadata={"action": "added", "tag_id": str(tag.id), "tag_name": tag.name},
            )
            publish(
                EventName.TAG_ADDED,
                tenant_id=tenant_id,
                contact_id=contact.id,
                payload={"tag_id": str(tag.id), "tag_name": tag.name},
            )
        return newly_added

    @staticmethod
    @transaction.atomic
    def remove_tags(
        *,
        contact: Contact,
        tags: Iterable[Any],
        actor: User | None = None,
    ) -> list[Tag]:
        tenant_id = require_tenant_id()
        resolved_tags = _resolve_tags(tags, create_missing=False)
        if not resolved_tags:
            return []

        existing_links = list(
            ContactTag.objects.select_related("tag").filter(
                contact=contact,
                tag__in=resolved_tags,
            )
        )
        if not existing_links:
            return []

        removed_tags = [link.tag for link in existing_links]
        ContactTag.objects.filter(pk__in=[link.pk for link in existing_links]).delete()

        for tag in removed_tags:
            log_contact_activity(
                contact=contact,
                actor=actor,
                activity_type=Activity.ActivityType.TAG_CHANGE,
                description=f"Tag '{tag.name}' removed.",
                metadata={"action": "removed", "tag_id": str(tag.id), "tag_name": tag.name},
            )
            publish(
                EventName.TAG_REMOVED,
                tenant_id=tenant_id,
                contact_id=contact.id,
                payload={"tag_id": str(tag.id), "tag_name": tag.name},
            )
        return removed_tags

    @staticmethod
    @transaction.atomic
    def bulk_add_tags(
        *,
        contacts: Iterable[Contact],
        tags: Iterable[Any],
        actor: User | None = None,
        create_missing: bool = False,
    ) -> int:
        resolved_tags = _resolve_tags(tags, create_missing=create_missing)
        if not resolved_tags:
            return 0
        updated_contacts = 0
        for contact in contacts:
            added = ContactService.add_tags(contact=contact, tags=resolved_tags, actor=actor)
            if added:
                updated_contacts += 1
        return updated_contacts

    @staticmethod
    @transaction.atomic
    def bulk_remove_tags(
        *,
        contacts: Iterable[Contact],
        tags: Iterable[Any],
        actor: User | None = None,
    ) -> int:
        resolved_tags = _resolve_tags(tags, create_missing=False)
        if not resolved_tags:
            return 0
        updated_contacts = 0
        for contact in contacts:
            removed = ContactService.remove_tags(contact=contact, tags=resolved_tags, actor=actor)
            if removed:
                updated_contacts += 1
        return updated_contacts

    @staticmethod
    @transaction.atomic
    def change_status(
        *,
        contact: Contact,
        new_status: LeadStatus,
        actor: User | None = None,
    ) -> Contact:
        tenant_id = require_tenant_id()
        old_status = contact.lead_status
        if old_status is not None and old_status.pk == new_status.pk:
            return contact

        now = timezone.now()
        contact.lead_status = new_status
        update_fields = ["lead_status", "last_activity_at", "inactive_notified_at", "updated_at"]
        contact.last_activity_at = now
        contact.inactive_notified_at = None

        became_customer = (
            new_status.name.strip().lower() == "customer" and contact.converted_at is None
        )
        if became_customer:
            contact.converted_at = now
            update_fields.append("converted_at")

        contact.save(update_fields=update_fields)

        old_name = old_status.name if old_status else None
        log_contact_activity(
            contact=contact,
            actor=actor,
            activity_type=Activity.ActivityType.STATUS_CHANGE,
            description=f"Lead status changed from '{old_name or 'None'}' to '{new_status.name}'.",
            metadata={
                "old_status_id": str(old_status.id) if old_status else None,
                "old_status_name": old_name,
                "new_status_id": str(new_status.id),
                "new_status_name": new_status.name,
            },
            touch_activity=False,
        )

        publish(
            EventName.LEAD_STATUS_CHANGED,
            tenant_id=tenant_id,
            contact_id=contact.id,
            payload={
                "old_status_id": str(old_status.id) if old_status else None,
                "old_status_name": old_name,
                "new_status_id": str(new_status.id),
                "new_status_name": new_status.name,
            },
        )

        if became_customer:
            publish(
                EventName.CONVERSION,
                tenant_id=tenant_id,
                contact_id=contact.id,
                payload={
                    "converted_at": now.isoformat(),
                    "status_id": str(new_status.id),
                    "status_name": new_status.name,
                },
            )

        return contact

    @staticmethod
    @transaction.atomic
    def assign(
        *,
        contact: Contact,
        assignee: Membership | None,
        actor: User | None = None,
        conversation_id: uuid.UUID | str | None = None,
    ) -> Contact:
        from accounts.services import AssignmentService

        updated_contact, _, _ = AssignmentService.assign_contact_and_conversation(
            contact=contact,
            assignee=assignee,
            actor=actor,
            notify_assignee=True,
            log_activity=True,
            publish_event=True,
        )
        return updated_contact

    @staticmethod
    @transaction.atomic
    def bulk_assign(
        *,
        contacts: Iterable[Contact],
        assignee: Membership | None,
        actor: User | None = None,
    ) -> int:
        count = 0
        for contact in contacts:
            ContactService.assign(contact=contact, assignee=assignee, actor=actor)
            count += 1
        return count

    @staticmethod
    @transaction.atomic
    def update_subscriptions(
        *,
        contact: Contact,
        email_subscription: str | None = None,
        sms_subscription: str | None = None,
        whatsapp_subscription: str | None = None,
        actor: User | None = None,
    ) -> Contact:
        changes: dict[str, str] = {}
        update_fields = ["last_activity_at", "inactive_notified_at", "updated_at"]
        if email_subscription is not None and email_subscription != contact.email_subscription:
            contact.email_subscription = email_subscription
            update_fields.append("email_subscription")
            changes["email_subscription"] = email_subscription
        if sms_subscription is not None and sms_subscription != contact.sms_subscription:
            contact.sms_subscription = sms_subscription
            update_fields.append("sms_subscription")
            changes["sms_subscription"] = sms_subscription
        if whatsapp_subscription is not None and whatsapp_subscription != contact.whatsapp_subscription:
            contact.whatsapp_subscription = whatsapp_subscription
            update_fields.append("whatsapp_subscription")
            changes["whatsapp_subscription"] = whatsapp_subscription

        if not changes:
            return contact

        contact.last_activity_at = timezone.now()
        contact.inactive_notified_at = None
        contact.save(update_fields=update_fields)

        log_contact_activity(
            contact=contact,
            actor=actor,
            activity_type=Activity.ActivityType.EVENT,
            description="Updated communication subscription preferences.",
            metadata=changes,
            touch_activity=False,
        )
        return contact

    @staticmethod
    @transaction.atomic
    def add_note(
        *,
        contact: Contact,
        body: str,
        actor: User | None = None,
    ) -> Activity:
        return log_contact_activity(
            contact=contact,
            actor=actor,
            activity_type=Activity.ActivityType.NOTE,
            description=body.strip(),
            metadata={"author_id": str(actor.id) if actor else None},
        )


@transaction.atomic
def create_lead_status(
    *,
    name: str,
    order: int = 0,
    is_default: bool = False,
) -> LeadStatus:
    cleaned_name = name.strip()
    if LeadStatus.objects.filter(name__iexact=cleaned_name).exists():
        raise ApplicationError(
            detail="A lead status with this name already exists.",
            code="duplicate_lead_status",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if is_default:
        LeadStatus.objects.filter(is_default=True).update(is_default=False)
    return LeadStatus.objects.create(
        name=cleaned_name,
        order=order,
        is_default=is_default,
    )


@transaction.atomic
def update_lead_status(
    *,
    lead_status: LeadStatus,
    name: str | None = None,
    order: int | None = None,
    is_default: bool | None = None,
) -> LeadStatus:
    if name is not None:
        cleaned_name = name.strip()
        if LeadStatus.objects.filter(name__iexact=cleaned_name).exclude(pk=lead_status.pk).exists():
            raise ApplicationError(
                detail="A lead status with this name already exists.",
                code="duplicate_lead_status",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        lead_status.name = cleaned_name
    if order is not None:
        lead_status.order = order
    if is_default is not None:
        if is_default:
            LeadStatus.objects.filter(is_default=True).exclude(pk=lead_status.pk).update(is_default=False)
        lead_status.is_default = is_default
    lead_status.save()
    return lead_status


@transaction.atomic
def delete_lead_status(*, lead_status: LeadStatus) -> None:
    if Contact.objects.filter(lead_status=lead_status).exists():
        raise ApplicationError(
            detail="Cannot delete a lead status that is currently assigned to contacts.",
            code="lead_status_in_use",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    lead_status.delete()


@transaction.atomic
def create_tag(*, name: str, color: str = "#3B82F6") -> Tag:
    cleaned_name = name.strip()
    if Tag.objects.filter(name__iexact=cleaned_name).exists():
        raise ApplicationError(
            detail="A tag with this name already exists.",
            code="duplicate_tag",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return Tag.objects.create(name=cleaned_name, color=color or "#3B82F6")


@transaction.atomic
def update_tag(*, tag: Tag, name: str | None = None, color: str | None = None) -> Tag:
    if name is not None:
        cleaned_name = name.strip()
        if Tag.objects.filter(name__iexact=cleaned_name).exclude(pk=tag.pk).exists():
            raise ApplicationError(
                detail="A tag with this name already exists.",
                code="duplicate_tag",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        tag.name = cleaned_name
    if color is not None:
        tag.color = color
    tag.save()
    return tag


@transaction.atomic
def create_custom_field_definition(
    *,
    key: str,
    label: str,
    field_type: str = CustomFieldDefinition.FieldType.TEXT,
    options: list[str] | None = None,
    is_required: bool = False,
) -> CustomFieldDefinition:
    cleaned_key = key.strip()
    if CustomFieldDefinition.objects.filter(key__iexact=cleaned_key).exists():
        raise ApplicationError(
            detail="A custom field with this key already exists.",
            code="duplicate_custom_field_key",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if field_type == CustomFieldDefinition.FieldType.SELECT and not options:
        raise ApplicationError(
            detail="Select custom fields require a non-empty options list.",
            code="select_options_required",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return CustomFieldDefinition.objects.create(
        key=cleaned_key,
        label=label.strip(),
        field_type=field_type,
        options=list(options or []),
        is_required=is_required,
    )


@transaction.atomic
def update_custom_field_definition(
    *,
    definition: CustomFieldDefinition,
    label: str | None = None,
    field_type: str | None = None,
    options: list[str] | None = None,
    is_required: bool | None = None,
) -> CustomFieldDefinition:
    if label is not None:
        definition.label = label.strip()
    if field_type is not None:
        definition.field_type = field_type
    if options is not None:
        definition.options = list(options)
    if is_required is not None:
        definition.is_required = is_required
    if definition.field_type == CustomFieldDefinition.FieldType.SELECT and not definition.options:
        raise ApplicationError(
            detail="Select custom fields require a non-empty options list.",
            code="select_options_required",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    definition.save()
    return definition


@transaction.atomic
def book_appointment(
    *,
    contact: Contact,
    starts_at: Any,
    external_ref: str = "",
    location: str = "",
    notes: str = "",
    actor: User | None = None,
) -> Appointment:
    tenant_id = require_tenant_id()
    appointment = Appointment.objects.create(
        contact=contact,
        starts_at=starts_at,
        status=Appointment.Status.BOOKED,
        external_ref=external_ref.strip(),
        location=location.strip(),
        notes=notes.strip(),
    )
    log_contact_activity(
        contact=contact,
        actor=actor,
        activity_type=Activity.ActivityType.APPOINTMENT,
        description=f"Appointment booked for {appointment.starts_at.isoformat()}.",
        metadata={
            "appointment_id": str(appointment.id),
            "status": appointment.status,
            "starts_at": appointment.starts_at.isoformat(),
            "location": appointment.location,
            "external_ref": appointment.external_ref,
        },
    )
    publish(
        EventName.APPOINTMENT_BOOKED,
        tenant_id=tenant_id,
        contact_id=contact.id,
        payload={
            "appointment_id": str(appointment.id),
            "starts_at": appointment.starts_at.isoformat(),
            "location": appointment.location,
            "external_ref": appointment.external_ref,
        },
    )
    return appointment


@transaction.atomic
def cancel_appointment(
    *,
    appointment: Appointment,
    actor: User | None = None,
) -> Appointment:
    tenant_id = require_tenant_id()
    if appointment.status == Appointment.Status.CANCELLED:
        return appointment
    appointment.status = Appointment.Status.CANCELLED
    appointment.save(update_fields=["status", "updated_at"])

    log_contact_activity(
        contact=appointment.contact,
        actor=actor,
        activity_type=Activity.ActivityType.APPOINTMENT,
        description=f"Appointment for {appointment.starts_at.isoformat()} was cancelled.",
        metadata={
            "appointment_id": str(appointment.id),
            "status": appointment.status,
            "starts_at": appointment.starts_at.isoformat(),
        },
    )
    publish(
        EventName.APPOINTMENT_CANCELLED,
        tenant_id=tenant_id,
        contact_id=appointment.contact_id,
        payload={
            "appointment_id": str(appointment.id),
            "starts_at": appointment.starts_at.isoformat(),
            "location": appointment.location,
        },
    )
    return appointment


@transaction.atomic
def reschedule_appointment(
    *,
    appointment: Appointment,
    starts_at: Any,
    location: str | None = None,
    notes: str | None = None,
    actor: User | None = None,
) -> Appointment:
    tenant_id = require_tenant_id()
    old_starts_at = appointment.starts_at
    appointment.starts_at = starts_at
    appointment.status = Appointment.Status.RESCHEDULED
    if location is not None:
        appointment.location = location.strip()
    if notes is not None:
        appointment.notes = notes.strip()
    appointment.save(update_fields=["starts_at", "status", "location", "notes", "updated_at"])

    log_contact_activity(
        contact=appointment.contact,
        actor=actor,
        activity_type=Activity.ActivityType.APPOINTMENT,
        description=f"Appointment rescheduled from {old_starts_at.isoformat()} to {appointment.starts_at.isoformat()}.",
        metadata={
            "appointment_id": str(appointment.id),
            "status": appointment.status,
            "old_starts_at": old_starts_at.isoformat(),
            "starts_at": appointment.starts_at.isoformat(),
            "location": appointment.location,
        },
    )
    publish(
        EventName.APPOINTMENT_BOOKED,
        tenant_id=tenant_id,
        contact_id=appointment.contact_id,
        payload={
            "appointment_id": str(appointment.id),
            "starts_at": appointment.starts_at.isoformat(),
            "old_starts_at": old_starts_at.isoformat(),
            "location": appointment.location,
            "rescheduled": True,
        },
    )
    return appointment


@transaction.atomic
def complete_appointment(
    *,
    appointment: Appointment,
    actor: User | None = None,
) -> Appointment:
    appointment.status = Appointment.Status.COMPLETED
    appointment.save(update_fields=["status", "updated_at"])
    log_contact_activity(
        contact=appointment.contact,
        actor=actor,
        activity_type=Activity.ActivityType.APPOINTMENT,
        description=f"Appointment for {appointment.starts_at.isoformat()} marked completed.",
        metadata={
            "appointment_id": str(appointment.id),
            "status": appointment.status,
        },
    )
    return appointment


@transaction.atomic
def update_appointment(
    *,
    appointment: Appointment,
    actor: User | None = None,
    **changes: Any,
) -> Appointment:
    new_status = changes.get("status")
    new_starts_at = changes.get("starts_at")

    if new_status == Appointment.Status.CANCELLED and appointment.status != Appointment.Status.CANCELLED:
        return cancel_appointment(appointment=appointment, actor=actor)
    if new_status == Appointment.Status.COMPLETED and appointment.status != Appointment.Status.COMPLETED:
        return complete_appointment(appointment=appointment, actor=actor)
    if new_starts_at is not None and new_starts_at != appointment.starts_at:
        return reschedule_appointment(
            appointment=appointment,
            starts_at=new_starts_at,
            location=changes.get("location"),
            notes=changes.get("notes"),
            actor=actor,
        )

    for field in ("external_ref", "location", "notes", "status"):
        if field in changes and changes[field] is not None:
            setattr(appointment, field, changes[field])
    appointment.save()
    return appointment


@transaction.atomic
def create_task(
    *,
    title: str,
    description: str = "",
    contact: Contact | None = None,
    assignee: Membership | None = None,
    due_at: Any = None,
    created_by: User | None = None,
    from_workflow: bool = False,
) -> Task:
    tenant_id = require_tenant_id()
    task = Task.objects.create(
        contact=contact,
        assignee=assignee,
        title=title.strip(),
        description=description.strip(),
        due_at=due_at,
        status=Task.Status.OPEN,
        created_by=created_by,
        from_workflow=from_workflow,
    )
    if contact is not None:
        log_contact_activity(
            contact=contact,
            actor=created_by,
            activity_type=Activity.ActivityType.WORKFLOW_ACTION if from_workflow else Activity.ActivityType.EVENT,
            description=f"Task created: {task.title}",
            metadata={
                "task_id": str(task.id),
                "assignee_id": str(assignee.id) if assignee else None,
                "due_at": task.due_at.isoformat() if task.due_at else None,
                "from_workflow": from_workflow,
            },
        )
    if assignee is not None:
        notify(
            tenant=tenant_id,
            users=[assignee.user],
            type=NotificationType.INTERNAL_TASK,
            payload={
                "title": f"New Task Assigned: {task.title}",
                "body": task.description or task.title,
                "task_id": str(task.id),
                "contact_id": str(contact.id) if contact else None,
                "due_at": task.due_at.isoformat() if task.due_at else None,
            },
            link=f"/contacts/{contact.id}" if contact else "/tasks",
        )
    return task


@transaction.atomic
def update_task(
    *,
    task: Task,
    actor: User | None = None,
    **changes: Any,
) -> Task:
    if changes.get("status") == Task.Status.COMPLETED and task.status != Task.Status.COMPLETED:
        return complete_task(task=task, actor=actor)

    previous_assignee_id = task.assignee_id
    for field in ("title", "description", "due_at", "status", "assignee", "contact"):
        if field in changes:
            setattr(task, field, changes[field])
    if "due_at" in changes:
        task.due_soon_notified_at = None
    task.save()

    if task.assignee is not None and task.assignee_id != previous_assignee_id:
        notify(
            tenant=task.tenant_id,
            users=[task.assignee.user],
            type=NotificationType.INTERNAL_TASK,
            payload={
                "title": f"Task Assigned: {task.title}",
                "body": task.description or task.title,
                "task_id": str(task.id),
                "contact_id": str(task.contact_id) if task.contact_id else None,
            },
            link=f"/contacts/{task.contact_id}" if task.contact_id else "/tasks",
        )
    return task


@transaction.atomic
def complete_task(
    *,
    task: Task,
    actor: User | None = None,
) -> Task:
    if task.status == Task.Status.COMPLETED:
        return task
    now = timezone.now()
    task.status = Task.Status.COMPLETED
    task.completed_at = now
    task.save(update_fields=["status", "completed_at", "updated_at"])

    if task.contact is not None:
        log_contact_activity(
            contact=task.contact,
            actor=actor,
            activity_type=Activity.ActivityType.EVENT,
            description=f"Task completed: {task.title}",
            metadata={"task_id": str(task.id)},
        )
    return task
