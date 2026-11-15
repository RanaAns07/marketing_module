from collections.abc import Iterable
import secrets
from typing import Any
import uuid
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from accounts.models import Membership, TenantAPIKey, User
from core.context import require_tenant, require_tenant_id, tenant_context
from core.events import EventName, publish
from core.exceptions import ApplicationError
from core.models import Tenant
from notifications.models import NotificationType
from notifications.services import notify


def _generate_unique_tenant_slug(name: str) -> str:
    base_slug = slugify(name) or "workspace"
    slug = base_slug[:220]
    counter = 1
    while Tenant.objects.filter(slug=slug).exists():
        counter += 1
        slug = f"{base_slug[:210]}-{counter}"
    return slug


def issue_jwt_tokens_for_user(user: User) -> dict[str, str]:
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }


@transaction.atomic
def register_tenant_and_owner(
    *,
    tenant_name: str,
    email: str,
    password: str,
    first_name: str = "",
    last_name: str = "",
    phone: str = "",
    timezone_name: str = "UTC",
) -> dict[str, Any]:
    normalized_email = email.lower().strip()
    if User.objects.filter(email=normalized_email).exists():
        raise ApplicationError(
            detail="A user with this email address already exists.",
            code="email_already_registered",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = User(
        email=normalized_email,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        phone=phone.strip(),
        is_active=True,
    )
    validate_password(password, user=user)
    user.set_password(password)
    user.save()

    slug = _generate_unique_tenant_slug(tenant_name)
    tenant = Tenant.objects.create(
        name=tenant_name.strip(),
        slug=slug,
        timezone=timezone_name or "UTC",
        is_active=True,
    )

    with tenant_context(tenant):
        membership = Membership.objects.create(
            tenant=tenant,
            user=user,
            role=Membership.Role.OWNER,
            is_active=True,
            is_available=True,
        )
        from contacts.services import seed_default_lead_statuses
        seed_default_lead_statuses(tenant)

    tokens = issue_jwt_tokens_for_user(user)
    return {
        "user": user,
        "tenant": tenant,
        "membership": membership,
        "tokens": tokens,
    }


def login_user(*, email: str, password: str) -> dict[str, Any]:
    normalized_email = email.lower().strip()
    user = authenticate(username=normalized_email, password=password)
    if user is None:
        raise ApplicationError(
            detail="Invalid email or password.",
            code="invalid_credentials",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if not user.is_active:
        raise ApplicationError(
            detail="User account is deactivated.",
            code="account_deactivated",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    tokens = issue_jwt_tokens_for_user(user)
    return {
        "user": user,
        "tokens": tokens,
    }


@transaction.atomic
def change_user_password(*, user: User, old_password: str, new_password: str) -> User:
    if not user.check_password(old_password):
        raise ApplicationError(
            detail="Current password is incorrect.",
            code="invalid_old_password",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    validate_password(new_password, user=user)
    user.set_password(new_password)
    user.save(update_fields=["password", "updated_at"])
    return user


@transaction.atomic
def update_user_profile(
    *,
    user: User,
    first_name: str | None = None,
    last_name: str | None = None,
    phone: str | None = None,
    email_notifications_enabled: bool | None = None,
) -> User:
    update_fields = ["updated_at"]
    if first_name is not None:
        user.first_name = first_name.strip()
        update_fields.append("first_name")
    if last_name is not None:
        user.last_name = last_name.strip()
        update_fields.append("last_name")
    if phone is not None:
        user.phone = phone.strip()
        update_fields.append("phone")
    if email_notifications_enabled is not None:
        user.email_notifications_enabled = email_notifications_enabled
        update_fields.append("email_notifications_enabled")
    user.save(update_fields=update_fields)
    return user


def _ensure_not_last_active_owner(membership: Membership) -> None:
    if membership.role != Membership.Role.OWNER or not membership.is_active:
        return
    other_owners_count = (
        Membership.objects.filter(
            role=Membership.Role.OWNER,
            is_active=True,
        )
        .exclude(pk=membership.pk)
        .count()
    )
    if other_owners_count == 0:
        raise ApplicationError(
            detail="Cannot remove or downgrade the last active Business Owner of the tenant.",
            code="last_owner_protected",
            status_code=status.HTTP_400_BAD_REQUEST,
        )


@transaction.atomic
def add_team_member(
    *,
    actor_membership: Membership,
    email: str,
    role: str = Membership.Role.MARKETER,
    first_name: str = "",
    last_name: str = "",
    phone: str = "",
    password: str | None = None,
    is_available: bool = True,
    email_notifications_enabled: bool = True,
) -> Membership:
    if actor_membership.role not in (Membership.Role.OWNER, Membership.Role.ADMIN):
        raise ApplicationError(
            detail="Only Business Owners or Administrators can add team members.",
            code="permission_denied",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if role == Membership.Role.OWNER and actor_membership.role != Membership.Role.OWNER:
        raise ApplicationError(
            detail="Only a Business Owner can assign the Business Owner role.",
            code="owner_role_restricted",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    tenant = require_tenant()
    normalized_email = email.lower().strip()
    user = User.objects.filter(email=normalized_email).first()
    if user is None:
        user = User(
            email=normalized_email,
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            phone=phone.strip(),
            is_active=True,
            email_notifications_enabled=email_notifications_enabled,
        )
        if password:
            validate_password(password, user=user)
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
    else:
        updated_fields = []
        if first_name and not user.first_name:
            user.first_name = first_name.strip()
            updated_fields.append("first_name")
        if last_name and not user.last_name:
            user.last_name = last_name.strip()
            updated_fields.append("last_name")
        if phone and not user.phone:
            user.phone = phone.strip()
            updated_fields.append("phone")
        if updated_fields:
            updated_fields.append("updated_at")
            user.save(update_fields=updated_fields)

    existing = Membership.objects.select_related("user", "tenant").filter(user=user).first()
    if existing is not None:
        if existing.is_active:
            raise ApplicationError(
                detail="This user is already an active member of this tenant.",
                code="member_already_exists",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        existing.role = role
        existing.is_active = True
        existing.is_available = is_available
        existing.email_notifications_enabled = email_notifications_enabled
        existing.save(
            update_fields=[
                "role",
                "is_active",
                "is_available",
                "email_notifications_enabled",
                "updated_at",
            ]
        )
        return existing

    membership = Membership.objects.create(
        tenant=tenant,
        user=user,
        role=role,
        is_active=True,
        is_available=is_available,
        email_notifications_enabled=email_notifications_enabled,
    )
    return Membership.objects.select_related("user", "tenant").get(pk=membership.pk)


@transaction.atomic
def change_team_member_role(
    *,
    actor_membership: Membership,
    target_membership: Membership,
    new_role: str,
) -> Membership:
    if actor_membership.role not in (Membership.Role.OWNER, Membership.Role.ADMIN):
        raise ApplicationError(
            detail="Only Business Owners or Administrators can change roles.",
            code="permission_denied",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if (
        new_role == Membership.Role.OWNER or target_membership.role == Membership.Role.OWNER
    ) and actor_membership.role != Membership.Role.OWNER:
        raise ApplicationError(
            detail="Only a Business Owner can grant or revoke the Business Owner role.",
            code="owner_role_restricted",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if target_membership.role == Membership.Role.OWNER and new_role != Membership.Role.OWNER:
        _ensure_not_last_active_owner(target_membership)

    target_membership.role = new_role
    target_membership.save(update_fields=["role", "updated_at"])
    return target_membership


@transaction.atomic
def deactivate_team_member(
    *,
    actor_membership: Membership,
    target_membership: Membership,
) -> Membership:
    if actor_membership.role not in (Membership.Role.OWNER, Membership.Role.ADMIN):
        raise ApplicationError(
            detail="Only Business Owners or Administrators can deactivate team members.",
            code="permission_denied",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if target_membership.role == Membership.Role.OWNER and actor_membership.role != Membership.Role.OWNER:
        raise ApplicationError(
            detail="Only a Business Owner can deactivate another Business Owner.",
            code="owner_role_restricted",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    _ensure_not_last_active_owner(target_membership)
    target_membership.is_active = False
    target_membership.is_available = False
    target_membership.save(update_fields=["is_active", "is_available", "updated_at"])
    return target_membership


@transaction.atomic
def activate_team_member(
    *,
    actor_membership: Membership,
    target_membership: Membership,
) -> Membership:
    if actor_membership.role not in (Membership.Role.OWNER, Membership.Role.ADMIN):
        raise ApplicationError(
            detail="Only Business Owners or Administrators can activate team members.",
            code="permission_denied",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    target_membership.is_active = True
    target_membership.save(update_fields=["is_active", "updated_at"])
    return target_membership


@transaction.atomic
def set_member_availability(
    *,
    actor_membership: Membership,
    target_membership: Membership,
    is_available: bool,
) -> Membership:
    if (
        actor_membership.pk != target_membership.pk
        and actor_membership.role not in (Membership.Role.OWNER, Membership.Role.ADMIN)
    ):
        raise ApplicationError(
            detail="You do not have permission to change this member's availability.",
            code="permission_denied",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    target_membership.is_available = is_available
    target_membership.save(update_fields=["is_available", "updated_at"])
    return target_membership


@transaction.atomic
def update_team_member(
    *,
    actor_membership: Membership,
    target_membership: Membership,
    role: str | None = None,
    is_active: bool | None = None,
    is_available: bool | None = None,
    email_notifications_enabled: bool | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    phone: str | None = None,
) -> Membership:
    is_self = actor_membership.pk == target_membership.pk
    is_manager = actor_membership.role in (Membership.Role.OWNER, Membership.Role.ADMIN)

    if not is_self and not is_manager:
        raise ApplicationError(
            detail="You do not have permission to update this team member.",
            code="permission_denied",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    if role is not None and role != target_membership.role:
        change_team_member_role(
            actor_membership=actor_membership,
            target_membership=target_membership,
            new_role=role,
        )

    if is_active is not None and is_active != target_membership.is_active:
        if is_active:
            activate_team_member(actor_membership=actor_membership, target_membership=target_membership)
        else:
            deactivate_team_member(actor_membership=actor_membership, target_membership=target_membership)

    if is_available is not None and is_available != target_membership.is_available:
        set_member_availability(
            actor_membership=actor_membership,
            target_membership=target_membership,
            is_available=is_available,
        )

    if email_notifications_enabled is not None:
        target_membership.email_notifications_enabled = email_notifications_enabled
        target_membership.save(update_fields=["email_notifications_enabled", "updated_at"])

    if any(v is not None for v in (first_name, last_name, phone)):
        update_user_profile(
            user=target_membership.user,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
        )

    return Membership.objects.select_related("user", "tenant").get(pk=target_membership.pk)


def _resolve_effective_assignment_strategy(tenant: Tenant) -> str:
    strategy = (tenant.assignment_strategy or "").strip().lower()
    mode = (tenant.assignment_mode or "").strip().lower()

    if strategy in (
        Tenant.AssignmentStrategy.DEDICATED_OWNER,
        Tenant.AssignmentStrategy.ROUND_ROBIN,
    ):
        return strategy

    if mode == Tenant.AssignmentMode.ROUND_ROBIN:
        return Tenant.AssignmentStrategy.ROUND_ROBIN
    if mode == Tenant.AssignmentMode.BY_SOURCE:
        return Tenant.AssignmentStrategy.DEDICATED_OWNER

    return Tenant.AssignmentStrategy.SHARED_QUEUE


@transaction.atomic
def select_next_round_robin_member(marketers_only: bool = False) -> Membership | None:
    qs = Membership.objects.select_for_update().select_related("user", "tenant").filter(
        is_active=True,
        is_available=True,
        user__is_active=True,
        role__in=[Membership.Role.MARKETER, Membership.Role.ADMIN],
    )
    if marketers_only:
        marketer_qs = qs.filter(role=Membership.Role.MARKETER)
        if marketer_qs.exists():
            qs = marketer_qs
    candidate = qs.order_by(F("last_assigned_at").asc(nulls_first=True), "created_at").first()
    if candidate is None:
        return None
    candidate.last_assigned_at = timezone.now()
    candidate.save(update_fields=["last_assigned_at", "updated_at"])
    return candidate


def _match_dedicated_owner_rule(
    *,
    contact: Any = None,
    source_page: str = "",
    source_form: str = "",
    tag_names: Iterable[str] | None = None,
    tag_ids: Iterable[Any] | None = None,
) -> Membership | None:
    tenant = require_tenant()
    raw_pages = [source_page, getattr(contact, "source_page", "") or ""]
    raw_forms = [source_form, getattr(contact, "source_form", "") or ""]

    candidate_pages = {s.strip().lower() for s in raw_pages if s and s.strip()}
    candidate_forms = {s.strip().lower() for s in raw_forms if s and s.strip()}

    if candidate_pages:
        try:
            from leads.models import MetaPage
            for mp in MetaPage.objects.all():
                mp_vals = {
                    (mp.name or "").strip().lower(),
                    (mp.page_id or "").strip().lower(),
                    str(mp.id).lower(),
                }
                if candidate_pages & mp_vals:
                    candidate_pages.update(v for v in mp_vals if v)
        except Exception:
            pass

    if candidate_forms:
        try:
            from leads.models import MetaLeadForm
            for mf in MetaLeadForm.objects.all():
                mf_vals = {
                    (mf.name or "").strip().lower(),
                    (mf.form_id or "").strip().lower(),
                    str(mf.id).lower(),
                }
                if candidate_forms & mf_vals:
                    candidate_forms.update(v for v in mf_vals if v)
        except Exception:
            pass

    candidate_tag_names = {str(t).strip().lower() for t in (tag_names or []) if str(t).strip()}
    candidate_tag_ids = {str(tid).strip().lower() for tid in (tag_ids or []) if str(tid).strip()}
    if contact is not None and getattr(contact, "pk", None) is not None:
        for t in contact.tags.all():
            if t.name:
                candidate_tag_names.add(t.name.strip().lower())
            candidate_tag_ids.add(str(t.id).lower())

    from inbox.models import AssignmentRule

    rules = list(
        AssignmentRule.objects.select_related("assignee", "assignee__user", "match_tag")
        .filter(
            is_active=True,
            assignee__is_active=True,
            assignee__user__is_active=True,
        )
        .order_by("priority", "created_at")
    )

    for rule in rules:
        r_page = (rule.match_page or "").strip().lower()
        r_form = (rule.match_form or "").strip().lower()
        r_tag_id = str(rule.match_tag_id).lower() if rule.match_tag_id else ""
        r_tag_name = (
            rule.match_tag.name
            if rule.match_tag is not None
            else (rule.match_tag_name or "")
        ).strip().lower()

        if not (r_page or r_form or r_tag_id or r_tag_name):
            continue

        if r_page and r_page not in candidate_pages:
            continue
        if r_form and r_form not in candidate_forms:
            continue
        if r_tag_id or r_tag_name:
            tag_matched = (
                (bool(r_tag_id) and r_tag_id in candidate_tag_ids)
                or (bool(r_tag_name) and r_tag_name in candidate_tag_names)
                or (bool(r_tag_name) and r_tag_name in candidate_tag_ids)
            )
            if not tag_matched:
                continue

        return rule.assignee

    legacy_rules = (tenant.settings or {}).get("assignment_rules") or {}
    if isinstance(legacy_rules, dict):
        target_id = (
            (legacy_rules.get("forms") or {}).get(source_form)
            or (legacy_rules.get("pages") or {}).get(source_page)
        )
        if not target_id and candidate_tag_names:
            tag_map = legacy_rules.get("tags") or {}
            for k, v in tag_map.items():
                if str(k).strip().lower() in candidate_tag_names:
                    target_id = v
                    break
        if target_id:
            member = (
                Membership.objects.select_related("user", "tenant")
                .filter(pk=target_id, is_active=True, user__is_active=True)
                .first()
            )
            if member is not None:
                return member

    return None


@transaction.atomic
def resolve_tenant_lead_assignee(
    *,
    contact: Any = None,
    source_page: str = "",
    source_form: str = "",
    tag_names: Iterable[str] | None = None,
    tag_ids: Iterable[Any] | None = None,
) -> Membership | None:
    tenant = require_tenant()
    strategy = _resolve_effective_assignment_strategy(tenant)

    if strategy == Tenant.AssignmentStrategy.SHARED_QUEUE:
        return None

    if strategy == Tenant.AssignmentStrategy.DEDICATED_OWNER:
        return _match_dedicated_owner_rule(
            contact=contact,
            source_page=source_page,
            source_form=source_form,
            tag_names=tag_names,
            tag_ids=tag_ids,
        )

    if strategy == Tenant.AssignmentStrategy.ROUND_ROBIN:
        return select_next_round_robin_member(marketers_only=False)

    return None


class AssignmentService:
    @staticmethod
    @transaction.atomic
    def resolve_for_lead(
        *,
        contact: Any = None,
        source_page: str = "",
        source_form: str = "",
        tag_names: Iterable[str] | None = None,
        tag_ids: Iterable[Any] | None = None,
    ) -> Membership | None:
        return resolve_tenant_lead_assignee(
            contact=contact,
            source_page=source_page,
            source_form=source_form,
            tag_names=tag_names,
            tag_ids=tag_ids,
        )

    @staticmethod
    def match_dedicated_owner_rule(
        *,
        contact: Any = None,
        source_page: str = "",
        source_form: str = "",
        tag_names: Iterable[str] | None = None,
        tag_ids: Iterable[Any] | None = None,
    ) -> Membership | None:
        return _match_dedicated_owner_rule(
            contact=contact,
            source_page=source_page,
            source_form=source_form,
            tag_names=tag_names,
            tag_ids=tag_ids,
        )

    @staticmethod
    @transaction.atomic
    def assign_contact_and_conversation(
        *,
        contact: Any,
        assignee: Membership | None,
        conversation: Any = None,
        actor: User | None = None,
        notify_assignee: bool = True,
        log_activity: bool = True,
        publish_event: bool = True,
    ) -> tuple[Any, Any, bool]:
        from contacts.models import Activity, Contact
        from contacts.services import log_contact_activity
        from inbox.models import Conversation

        tenant_id = require_tenant_id()
        locked_contact = (
            Contact.objects.select_for_update(of=("self",))
            .select_related("assigned_to", "assigned_to__user", "lead_status", "tenant")
            .get(pk=contact.pk)
        )
        locked_conv = (
            Conversation.objects.select_for_update(of=("self",))
            .select_related("assigned_to", "assigned_to__user")
            .filter(contact=locked_contact)
            .first()
        )

        previous_assignee = (
            locked_conv.assigned_to
            if (locked_conv is not None and locked_conv.assigned_to_id is not None)
            else locked_contact.assigned_to
        )

        target_id = assignee.pk if assignee is not None else None
        prev_id = previous_assignee.pk if previous_assignee is not None else None
        changed = prev_id != target_id

        now = timezone.now()
        if locked_contact.assigned_to_id != target_id:
            locked_contact.assigned_to = assignee
            locked_contact.last_activity_at = now
            locked_contact.inactive_notified_at = None
            locked_contact.save(
                update_fields=["assigned_to", "last_activity_at", "inactive_notified_at", "updated_at"]
            )

        if locked_conv is not None and locked_conv.assigned_to_id != target_id:
            locked_conv.assigned_to = assignee
            locked_conv.save(update_fields=["assigned_to", "updated_at"])

        if not changed:
            return locked_contact, locked_conv, False

        if assignee is None:
            desc = "Unassigned."
        elif previous_assignee is not None:
            desc = f"Reassigned from {previous_assignee.user.full_name} to {assignee.user.full_name}."
        else:
            desc = f"Assigned to {assignee.user.full_name}."

        conv_id_str = str(locked_conv.id) if locked_conv is not None else None
        payload = {
            "assigned_membership_id": str(assignee.id) if assignee is not None else None,
            "assigned_user_id": str(assignee.user_id) if assignee is not None else None,
            "previous_assigned_membership_id": (
                str(previous_assignee.id) if previous_assignee is not None else None
            ),
            "previous_assigned_user_id": (
                str(previous_assignee.user_id) if previous_assignee is not None else None
            ),
            "conversation_id": conv_id_str,
            "notified": True,
        }

        if log_activity:
            log_contact_activity(
                contact=locked_contact,
                actor=actor,
                activity_type=Activity.ActivityType.ASSIGNMENT,
                description=desc,
                metadata=payload,
                touch_activity=False,
            )

        if (
            notify_assignee
            and assignee is not None
            and (actor is None or assignee.user_id != actor.id)
        ):
            notif_type = (
                NotificationType.CONVERSATION_REASSIGNED
                if previous_assignee is not None
                else NotificationType.CONVERSATION_ASSIGNED
            )
            contact_label = (
                locked_contact.full_name
                or locked_contact.email
                or locked_contact.phone
                or "Contact"
            )
            title = (
                f"Conversation Transferred to You: {contact_label}"
                if previous_assignee is not None
                else f"Conversation Assigned to You: {contact_label}"
            )
            link = (
                f"/inbox/{conv_id_str}"
                if conv_id_str
                else f"/contacts/{locked_contact.id}"
            )
            notify(
                tenant=tenant_id,
                users=[assignee.user],
                type=notif_type,
                payload={
                    **payload,
                    "title": title,
                    "body": desc,
                    "contact_id": str(locked_contact.id),
                },
                link=link,
            )

        if publish_event:
            publish(
                EventName.ASSIGNMENT_CHANGED,
                tenant_id=tenant_id,
                contact_id=locked_contact.id,
                payload=payload,
            )

        return locked_contact, locked_conv, True

    @staticmethod
    @transaction.atomic
    def auto_assign_on_inbound(
        *,
        contact: Any,
        conversation: Any = None,
        notify_assignee: bool = False,
    ) -> tuple[Membership | None, bool]:
        from contacts.models import Contact
        from inbox.models import Conversation

        locked_contact = (
            Contact.objects.select_for_update(of=("self",))
            .select_related("assigned_to", "assigned_to__user", "tenant")
            .prefetch_related("tags")
            .get(pk=contact.pk)
        )
        locked_conv = (
            Conversation.objects.select_for_update(of=("self",))
            .select_related("assigned_to", "assigned_to__user")
            .filter(contact=locked_contact)
            .first()
        )

        if locked_conv is not None and locked_conv.assigned_to_id is not None:
            if locked_contact.assigned_to_id is None:
                locked_contact.assigned_to = locked_conv.assigned_to
                locked_contact.save(update_fields=["assigned_to", "updated_at"])
            return locked_conv.assigned_to, False

        if locked_contact.assigned_to_id is not None:
            if locked_conv is not None and locked_conv.assigned_to_id is None:
                locked_conv.assigned_to = locked_contact.assigned_to
                locked_conv.save(update_fields=["assigned_to", "updated_at"])
            return locked_contact.assigned_to, False

        tag_names = [t.name for t in locked_contact.tags.all()]
        tag_ids = [t.id for t in locked_contact.tags.all()]
        assignee = resolve_tenant_lead_assignee(
            contact=locked_contact,
            source_page=locked_contact.source_page,
            source_form=locked_contact.source_form,
            tag_names=tag_names,
            tag_ids=tag_ids,
        )
        if assignee is None:
            return None, False

        _, _, changed = AssignmentService.assign_contact_and_conversation(
            contact=locked_contact,
            conversation=locked_conv,
            assignee=assignee,
            actor=None,
            notify_assignee=notify_assignee,
            log_activity=True,
            publish_event=True,
        )
        return assignee, changed

    @staticmethod
    @transaction.atomic
    def assign_from_workflow(
        *,
        contact: Any,
        mode: str = "round_robin",
        member_ref: Any = None,
        marketers_only: bool = False,
    ) -> Membership | None:
        from contacts.models import Contact

        locked_contact = (
            Contact.objects.select_for_update(of=("self",))
            .select_related("assigned_to", "assigned_to__user", "tenant")
            .prefetch_related("tags")
            .get(pk=contact.pk)
        )
        norm_mode = (mode or "round_robin").strip().lower()
        assignee: Membership | None = None

        if norm_mode == "specific":
            if member_ref:
                try:
                    parsed_uuid = uuid.UUID(str(member_ref).strip())
                    assignee = (
                        Membership.objects.select_related("user", "tenant")
                        .filter(Q(pk=parsed_uuid) | Q(user_id=parsed_uuid), is_active=True, user__is_active=True)
                        .first()
                    )
                except ValueError:
                    assignee = None
        elif norm_mode in ("by_rule", "rule", "dedicated_owner"):
            assignee = _match_dedicated_owner_rule(
                contact=locked_contact,
                source_page=locked_contact.source_page,
                source_form=locked_contact.source_form,
                tag_names=[t.name for t in locked_contact.tags.all()],
                tag_ids=[t.id for t in locked_contact.tags.all()],
            )
            if assignee is None:
                assignee = resolve_tenant_lead_assignee(
                    contact=locked_contact,
                    source_page=locked_contact.source_page,
                    source_form=locked_contact.source_form,
                    tag_names=[t.name for t in locked_contact.tags.all()],
                    tag_ids=[t.id for t in locked_contact.tags.all()],
                )
        else:
            assignee = select_next_round_robin_member(marketers_only=marketers_only)

        if assignee is not None:
            AssignmentService.assign_contact_and_conversation(
                contact=locked_contact,
                assignee=assignee,
                actor=None,
                notify_assignee=True,
            )
        return assignee


@transaction.atomic
def update_tenant_settings(*, tenant: Tenant, **changes) -> Tenant:
    allowed_fields = {
        "name",
        "timezone",
        "marketer_can_view_all_contacts",
        "notify_admins_on_new_lead",
        "assignment_mode",
        "assignment_strategy",
        "handoff_add_interested_tag",
        "settings",
    }
    update_fields = ["updated_at"]
    for key, value in changes.items():
        if key in allowed_fields and value is not None:
            setattr(tenant, key, value)
            if key not in update_fields:
                update_fields.append(key)

    if "assignment_strategy" in changes and changes["assignment_strategy"] is not None:
        strat = changes["assignment_strategy"]
        mode_map = {
            Tenant.AssignmentStrategy.SHARED_QUEUE: Tenant.AssignmentMode.MANUAL,
            Tenant.AssignmentStrategy.DEDICATED_OWNER: Tenant.AssignmentMode.BY_SOURCE,
            Tenant.AssignmentStrategy.ROUND_ROBIN: Tenant.AssignmentMode.ROUND_ROBIN,
        }
        mapped_mode = mode_map.get(strat)
        if mapped_mode and tenant.assignment_mode != mapped_mode:
            tenant.assignment_mode = mapped_mode
            if "assignment_mode" not in update_fields:
                update_fields.append("assignment_mode")
    elif "assignment_mode" in changes and changes["assignment_mode"] is not None:
        mode = changes["assignment_mode"]
        strat_map = {
            Tenant.AssignmentMode.MANUAL: Tenant.AssignmentStrategy.SHARED_QUEUE,
            Tenant.AssignmentMode.BY_SOURCE: Tenant.AssignmentStrategy.DEDICATED_OWNER,
            Tenant.AssignmentMode.ROUND_ROBIN: Tenant.AssignmentStrategy.ROUND_ROBIN,
        }
        mapped_strat = strat_map.get(mode)
        if mapped_strat and tenant.assignment_strategy != mapped_strat:
            tenant.assignment_strategy = mapped_strat
            if "assignment_strategy" not in update_fields:
                update_fields.append("assignment_strategy")

    tenant.save(update_fields=update_fields)
    return tenant


@transaction.atomic
def create_tenant_api_key(*, name: str) -> tuple[TenantAPIKey, str]:
    tenant = require_tenant()
    key_id = f"crm_{secrets.token_hex(16)}"
    raw_secret = secrets.token_urlsafe(32)
    api_key = TenantAPIKey.objects.create(
        tenant=tenant,
        name=name.strip(),
        key_id=key_id,
        secret_encrypted=raw_secret,
        is_active=True,
    )
    return api_key, raw_secret
