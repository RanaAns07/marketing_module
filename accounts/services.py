import secrets
from typing import Any
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from accounts.models import Membership, TenantAPIKey, User
from core.context import require_tenant, tenant_context
from core.exceptions import ApplicationError
from core.models import Tenant


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


@transaction.atomic
def select_next_round_robin_member(marketers_only: bool = True) -> Membership | None:
    qs = Membership.objects.select_for_update().select_related("user", "tenant").filter(
        is_active=True,
        is_available=True,
        user__is_active=True,
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


@transaction.atomic
def resolve_tenant_lead_assignee(
    *,
    source_page: str = "",
    source_form: str = "",
    tag_names: list[str] | None = None,
) -> Membership | None:
    tenant = require_tenant()
    if tenant.assignment_mode == Tenant.AssignmentMode.ROUND_ROBIN:
        return select_next_round_robin_member(marketers_only=True)

    if tenant.assignment_mode == Tenant.AssignmentMode.BY_SOURCE:
        rules = (tenant.settings or {}).get("assignment_rules") or {}
        if isinstance(rules, dict):
            target_id = (
                (rules.get("forms") or {}).get(source_form)
                or (rules.get("pages") or {}).get(source_page)
            )
            if not target_id and tag_names:
                tag_rules = rules.get("tags") or {}
                for tname in tag_names:
                    if tname in tag_rules:
                        target_id = tag_rules[tname]
                        break
            if target_id:
                member = (
                    Membership.objects.select_related("user", "tenant")
                    .filter(pk=target_id, is_active=True)
                    .first()
                )
                if member is not None:
                    return member

    return None


@transaction.atomic
def update_tenant_settings(*, tenant: Tenant, **changes) -> Tenant:
    allowed_fields = {
        "name",
        "timezone",
        "marketer_can_view_all_contacts",
        "notify_admins_on_new_lead",
        "assignment_mode",
        "settings",
    }
    update_fields = ["updated_at"]
    for key, value in changes.items():
        if key in allowed_fields and value is not None:
            setattr(tenant, key, value)
            update_fields.append(key)
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
