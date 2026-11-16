from collections.abc import Iterable
from datetime import timedelta
import secrets
from typing import Any
import uuid
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import status
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from accounts.models import Membership, TenantAPIKey, TenantInvitation, User
from accounts.throttling import (
    clear_login_failures,
    is_account_locked,
    record_login_failure,
)
from core.context import require_tenant, require_tenant_id, tenant_context
from core.events import EventName, publish
from core.exceptions import ApplicationError
from core.models import Tenant
from notifications.models import NotificationType
from notifications.services import notify

_EMAIL_VERIFY_SALT = "platform-email-verification"
_PASSWORD_RESET_SALT = "platform-password-reset"
_EMAIL_CHANGE_SALT = "platform-email-change"
_INVITE_SALT = "platform-tenant-invitation"


def _get_platform_base_url() -> str:
    base = (
        getattr(settings, "FRONTEND_BASE_URL", "")
        or getattr(settings, "PUBLIC_BASE_URL", "")
        or "https://marketing.fitverx.com"
    )
    return str(base).rstrip("/")


def send_platform_email(
    *,
    to_email: str,
    subject: str,
    body: str,
    html_content: str | None = None,
) -> bool:
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@ads.fitverx.com")
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=from_email,
            recipient_list=[to_email],
            html_message=html_content,
            fail_silently=True,
        )
        return True
    except Exception:
        return False


def build_email_verification_token(user: User) -> str:
    return signing.dumps(
        {"user_id": str(user.id), "email": user.email},
        salt=_EMAIL_VERIFY_SALT,
    )


def send_email_verification_link(user: User) -> None:
    token = build_email_verification_token(user)
    base_url = _get_platform_base_url()
    verification_url = f"{base_url}/verify-email?token={token}"
    subject = "Verify your email address"
    body = (
        f"Hi {user.first_name or 'there'},\n\n"
        f"Please verify your email address by clicking the link below:\n\n"
        f"{verification_url}\n\n"
        f"This link expires in 24 hours.\n\n"
        f"If you did not create an account, you can ignore this email."
    )
    send_platform_email(to_email=user.email, subject=subject, body=body)


def verify_email_token(token: str) -> User:
    try:
        data = signing.loads(token, salt=_EMAIL_VERIFY_SALT, max_age=86400)
        user_id = data.get("user_id")
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            raise ApplicationError(
                detail="User not found for this verification token.",
                code="user_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if not user.email_verified:
            user.email_verified = True
            user.email_verified_at = timezone.now()
            user.save(update_fields=["email_verified", "email_verified_at", "updated_at"])
        return user
    except ApplicationError:
        raise
    except signing.SignatureExpired:
        raise ApplicationError(
            detail="Email verification link has expired. Please request a new one.",
            code="token_expired",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:
        raise ApplicationError(
            detail="Invalid email verification token.",
            code="invalid_token",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc


def resend_verification_email(*, user: User | None = None, email: str = "") -> None:
    target_user = user
    if target_user is None and email:
        target_user = User.objects.filter(email=email.strip().lower()).first()
    if target_user is None or target_user.email_verified:
        return
    send_email_verification_link(target_user)


def request_password_reset(email: str) -> None:
    norm_email = (email or "").strip().lower()
    user = User.objects.filter(email=norm_email, is_active=True).first()
    if user is None:
        return
    token = signing.dumps(
        {"user_id": str(user.id), "hash": user.password[:15]},
        salt=_PASSWORD_RESET_SALT,
    )
    base_url = _get_platform_base_url()
    reset_url = f"{base_url}/reset-password?token={token}"
    subject = "Reset your password"
    body = (
        f"Hi {user.first_name or 'there'},\n\n"
        f"You requested to reset your password. Click the link below to set a new password:\n\n"
        f"{reset_url}\n\n"
        f"This link expires in 1 hour and can only be used once.\n\n"
        f"If you did not request this, please ignore this email."
    )
    send_platform_email(to_email=user.email, subject=subject, body=body)


@transaction.atomic
def reset_password_with_token(*, token: str, new_password: str) -> User:
    try:
        data = signing.loads(token, salt=_PASSWORD_RESET_SALT, max_age=3600)
    except signing.SignatureExpired:
        raise ApplicationError(
            detail="Password reset link has expired. Please request a new one.",
            code="reset_token_expired",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:
        raise ApplicationError(
            detail="Invalid password reset token.",
            code="invalid_reset_token",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc

    token_key = f"pr_used:{token}"
    if cache.get(token_key):
        raise ApplicationError(
            detail="Password reset token has already been used.",
            code="reset_token_already_used",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user_id = data.get("user_id")
    user = User.objects.select_for_update().filter(pk=user_id, is_active=True).first()
    if user is None:
        raise ApplicationError(
            detail="User account not found or is deactivated.",
            code="user_not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if user.password[:15] != data.get("hash"):
        raise ApplicationError(
            detail="Password reset token is invalid or has already been used.",
            code="reset_token_invalidated",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    validate_password(new_password, user=user)
    user.set_password(new_password)
    user.save(update_fields=["password", "updated_at"])
    cache.set(token_key, True, timeout=86400)
    logout_all_user_sessions(user)
    return user


def logout_user(refresh_token_str: str) -> None:
    try:
        token = RefreshToken(refresh_token_str)
        token.blacklist()
    except Exception:
        pass


def logout_all_user_sessions(user: User) -> None:
    try:
        outstanding = OutstandingToken.objects.filter(user=user)
        for ot in outstanding:
            try:
                BlacklistedToken.objects.get_or_create(token=ot)
            except Exception:
                pass
    except Exception:
        pass


@transaction.atomic
def create_tenant_invitation(
    *,
    tenant: Tenant,
    email: str,
    role: str,
    invited_by: User,
) -> TenantInvitation:
    norm_email = email.lower().strip()
    if not norm_email:
        raise ApplicationError(
            detail="Email address is required.",
            code="email_required",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    existing_member = (
        Membership.objects.filter(tenant=tenant, user__email=norm_email, is_active=True).first()
    )
    if existing_member is not None:
        raise ApplicationError(
            detail="A user with this email address is already an active member of this workspace.",
            code="already_member",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    token = signing.dumps(
        {
            "tenant_id": str(tenant.id),
            "email": norm_email,
            "role": role,
            "nonce": secrets.token_hex(8),
        },
        salt=_INVITE_SALT,
    )
    expires_at = timezone.now() + timedelta(days=7)

    pending = (
        TenantInvitation.objects.select_for_update()
        .filter(tenant=tenant, email=norm_email, status=TenantInvitation.Status.PENDING)
        .first()
    )
    if pending is not None:
        pending.token = token
        pending.role = role
        pending.expires_at = expires_at
        pending.invited_by = invited_by
        pending.save(update_fields=["token", "role", "expires_at", "invited_by", "updated_at"])
        invitation = pending
    else:
        invitation = TenantInvitation.objects.create(
            tenant=tenant,
            email=norm_email,
            role=role,
            invited_by=invited_by,
            token=token,
            expires_at=expires_at,
            status=TenantInvitation.Status.PENDING,
        )

    base_url = _get_platform_base_url()
    accept_url = f"{base_url}/invite/accept?token={token}"
    subject = f"You have been invited to join {tenant.name}"
    body = (
        f"Hi there,\n\n"
        f"{invited_by.full_name} has invited you to join '{tenant.name}' as a {role.capitalize()}.\n\n"
        f"Click the link below to accept the invitation and join the workspace:\n\n"
        f"{accept_url}\n\n"
        f"This invitation link expires in 7 days."
    )
    send_platform_email(to_email=norm_email, subject=subject, body=body)
    return invitation


@transaction.atomic
def resend_tenant_invitation(*, invitation: TenantInvitation) -> TenantInvitation:
    if invitation.status != TenantInvitation.Status.PENDING:
        raise ApplicationError(
            detail="Cannot resend an invitation that is not pending.",
            code="invitation_not_pending",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    invitation.expires_at = timezone.now() + timedelta(days=7)
    invitation.save(update_fields=["expires_at", "updated_at"])

    base_url = _get_platform_base_url()
    accept_url = f"{base_url}/invite/accept?token={invitation.token}"
    subject = f"Invitation reminder: Join {invitation.tenant.name}"
    body = (
        f"Hi there,\n\n"
        f"This is a reminder that you have been invited to join '{invitation.tenant.name}' as a {invitation.role.capitalize()}.\n\n"
        f"Click the link below to accept:\n\n"
        f"{accept_url}\n\n"
        f"This link expires in 7 days."
    )
    send_platform_email(to_email=invitation.email, subject=subject, body=body)
    return invitation


@transaction.atomic
def revoke_tenant_invitation(*, invitation: TenantInvitation) -> TenantInvitation:
    if invitation.status != TenantInvitation.Status.PENDING:
        raise ApplicationError(
            detail="Cannot revoke an invitation that is not pending.",
            code="invitation_not_pending",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    invitation.status = TenantInvitation.Status.REVOKED
    invitation.save(update_fields=["status", "updated_at"])
    return invitation


@transaction.atomic
def accept_tenant_invitation(
    *,
    token: str,
    password: str | None = None,
    first_name: str = "",
    last_name: str = "",
) -> dict[str, Any]:
    invitation = (
        TenantInvitation.objects.select_for_update()
        .select_related("tenant", "invited_by")
        .filter(token=token)
        .first()
    )
    if invitation is None or not invitation.is_valid():
        raise ApplicationError(
            detail="Invitation link is invalid, expired, or has already been accepted.",
            code="invalid_invitation",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    tenant = invitation.tenant
    email = invitation.email.lower().strip()
    user = User.objects.filter(email=email).first()

    if user is None:
        if not password:
            raise ApplicationError(
                detail="Password is required to set up your account.",
                code="password_required",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        user = User(
            email=email,
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            is_active=True,
            email_verified=True,
            email_verified_at=timezone.now(),
        )
        validate_password(password, user=user)
        user.set_password(password)
        user.save()
    else:
        if not user.email_verified:
            user.email_verified = True
            user.email_verified_at = timezone.now()
            user.save(update_fields=["email_verified", "email_verified_at", "updated_at"])

    membership = (
        Membership.objects.select_for_update()
        .filter(tenant=tenant, user=user)
        .first()
    )
    if membership is None:
        membership = Membership.objects.create(
            tenant=tenant,
            user=user,
            role=invitation.role,
            is_active=True,
            is_available=True,
        )
    else:
        membership.role = invitation.role
        membership.is_active = True
        membership.is_available = True
        membership.save(update_fields=["role", "is_active", "is_available", "updated_at"])

    invitation.status = TenantInvitation.Status.ACCEPTED
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=["status", "accepted_at", "updated_at"])

    tokens = issue_jwt_tokens_for_user(user)
    return {
        "user": user,
        "tenant": tenant,
        "membership": membership,
        "tokens": tokens,
    }


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
        email_verified=False,
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

    transaction.on_commit(lambda: send_email_verification_link(user))

    tokens = issue_jwt_tokens_for_user(user)
    return {
        "user": user,
        "tenant": tenant,
        "membership": membership,
        "tokens": tokens,
    }


def login_user(*, email: str, password: str, client_ip: str = "unknown") -> dict[str, Any]:
    normalized_email = email.lower().strip()
    if is_account_locked(normalized_email, client_ip):
        raise ApplicationError(
            detail="Too many failed login attempts. Your account has been temporarily locked. Please try again in 15 minutes.",
            code="account_locked",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    user = authenticate(username=normalized_email, password=password)
    if user is None:
        record_login_failure(normalized_email, client_ip)
        raise ApplicationError(
            detail="Invalid email or password.",
            code="invalid_credentials",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    clear_login_failures(normalized_email, client_ip)

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
    logout_all_user_sessions(user)
    return user


@transaction.atomic
def request_change_email(*, user: User, current_password: str, new_email: str) -> None:
    if not user.check_password(current_password):
        raise ApplicationError(
            detail="Current password is incorrect.",
            code="invalid_password",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    norm_new = (new_email or "").strip().lower()
    if not norm_new:
        raise ApplicationError(
            detail="New email address is required.",
            code="email_required",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if norm_new == user.email:
        raise ApplicationError(
            detail="New email address must be different from current email.",
            code="same_email",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if User.objects.filter(email=norm_new).exists():
        raise ApplicationError(
            detail="A user with this email address already exists.",
            code="email_already_registered",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    token = signing.dumps(
        {"user_id": str(user.id), "new_email": norm_new},
        salt=_EMAIL_CHANGE_SALT,
    )
    base_url = _get_platform_base_url()
    confirm_url = f"{base_url}/verify-email-change?token={token}"
    subject = "Confirm your new email address"
    body = (
        f"Hi {user.first_name or 'there'},\n\n"
        f"You requested to change your email address to {norm_new}. Click the link below to confirm:\n\n"
        f"{confirm_url}\n\n"
        f"This link expires in 24 hours.\n\n"
        f"If you did not request this, please change your password immediately."
    )
    send_platform_email(to_email=norm_new, subject=subject, body=body)


@transaction.atomic
def verify_change_email(*, token: str) -> User:
    try:
        data = signing.loads(token, salt=_EMAIL_CHANGE_SALT, max_age=86400)
    except signing.SignatureExpired:
        raise ApplicationError(
            detail="Email change confirmation link has expired.",
            code="token_expired",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:
        raise ApplicationError(
            detail="Invalid email change token.",
            code="invalid_token",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc

    user_id = data.get("user_id")
    new_email = data.get("new_email", "").strip().lower()
    user = User.objects.select_for_update().filter(pk=user_id, is_active=True).first()
    if user is None:
        raise ApplicationError(
            detail="User account not found.",
            code="user_not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if User.objects.filter(email=new_email).exclude(pk=user.pk).exists():
        raise ApplicationError(
            detail="This email address is already in use by another account.",
            code="email_in_use",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user.email = new_email
    user.email_verified = True
    user.email_verified_at = timezone.now()
    user.save(update_fields=["email", "email_verified", "email_verified_at", "updated_at"])
    logout_all_user_sessions(user)
    return user


def get_user_tenants(user: User) -> list[dict[str, Any]]:
    memberships = (
        Membership.objects.select_related("tenant")
        .filter(user=user, is_active=True)
        .order_by("created_at")
    )
    results = []
    for m in memberships:
        t = m.tenant
        results.append(
            {
                "tenant_id": str(t.id),
                "tenant_name": t.name,
                "slug": t.slug,
                "role": m.role,
                "is_active": t.is_active,
                "is_available": m.is_available,
                "deactivated_at": t.deactivated_at,
                "grace_period_ends_at": t.grace_period_ends_at,
                "created_at": m.created_at,
            }
        )
    return results


def switch_user_tenant(*, user: User, tenant_id: str) -> dict[str, Any]:
    membership = (
        Membership.objects.select_related("tenant", "user")
        .filter(user=user, tenant_id=tenant_id, is_active=True)
        .first()
    )
    if membership is None or not membership.tenant.is_active:
        raise ApplicationError(
            detail="You do not have active access to the requested workspace.",
            code="workspace_access_denied",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    tokens = issue_jwt_tokens_for_user(user)
    return {
        "user": user,
        "tenant": membership.tenant,
        "membership": membership,
        "tokens": tokens,
    }


@transaction.atomic
def leave_tenant(*, user: User, tenant: Tenant) -> None:
    membership = (
        Membership.objects.select_for_update()
        .filter(user=user, tenant=tenant, is_active=True)
        .first()
    )
    if membership is None:
        raise ApplicationError(
            detail="You are not an active member of this workspace.",
            code="not_a_member",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if membership.role == Membership.Role.OWNER:
        other_owners = (
            Membership.objects.filter(tenant=tenant, role=Membership.Role.OWNER, is_active=True)
            .exclude(pk=membership.pk)
            .count()
        )
        if other_owners == 0:
            raise ApplicationError(
                detail="The last Business Owner cannot leave the workspace. Transfer ownership first.",
                code="cannot_leave_as_last_owner",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    membership.is_active = False
    membership.save(update_fields=["is_active", "updated_at"])
    logout_all_user_sessions(user)


@transaction.atomic
def transfer_tenant_ownership(
    *,
    tenant: Tenant,
    current_owner_user: User,
    target_membership_id: str,
) -> dict[str, Any]:
    current_membership = (
        Membership.objects.select_for_update()
        .filter(tenant=tenant, user=current_owner_user, is_active=True, role=Membership.Role.OWNER)
        .first()
    )
    if current_membership is None:
        raise ApplicationError(
            detail="Only a Business Owner can transfer ownership.",
            code="owner_required",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    target_membership = (
        Membership.objects.select_for_update()
        .select_related("user")
        .filter(tenant=tenant, pk=target_membership_id, is_active=True)
        .first()
    )
    if target_membership is None:
        raise ApplicationError(
            detail="Target member not found or is deactivated in this workspace.",
            code="target_member_not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if target_membership.pk == current_membership.pk:
        raise ApplicationError(
            detail="You are already the Business Owner.",
            code="already_owner",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    target_membership.role = Membership.Role.OWNER
    target_membership.save(update_fields=["role", "updated_at"])

    current_membership.role = Membership.Role.ADMIN
    current_membership.save(update_fields=["role", "updated_at"])

    logout_all_user_sessions(current_owner_user)
    logout_all_user_sessions(target_membership.user)

    return {
        "new_owner": target_membership,
        "previous_owner": current_membership,
    }


@transaction.atomic
def deactivate_tenant(
    *,
    tenant: Tenant,
    owner_user: User,
    reason: str = "",
    grace_period_days: int = 14,
) -> Tenant:
    owner_membership = (
        Membership.objects.filter(tenant=tenant, user=owner_user, is_active=True, role=Membership.Role.OWNER)
        .first()
    )
    if owner_membership is None:
        raise ApplicationError(
            detail="Only a Business Owner can deactivate the workspace.",
            code="owner_required",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    days = max(1, min(60, int(grace_period_days or 14)))
    now = timezone.now()
    tenant.deactivated_at = now
    tenant.grace_period_ends_at = now + timedelta(days=days)
    tenant.deactivation_reason = reason.strip()
    tenant.save(
        update_fields=[
            "deactivated_at",
            "grace_period_ends_at",
            "deactivation_reason",
            "updated_at",
        ]
    )
    return tenant


@transaction.atomic
def reactivate_tenant(*, tenant: Tenant, owner_user: User) -> Tenant:
    owner_membership = (
        Membership.objects.filter(tenant=tenant, user=owner_user, is_active=True, role=Membership.Role.OWNER)
        .first()
    )
    if owner_membership is None:
        raise ApplicationError(
            detail="Only a Business Owner can reactivate the workspace.",
            code="owner_required",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    tenant.deactivated_at = None
    tenant.grace_period_ends_at = None
    tenant.deactivation_reason = ""
    tenant.save(
        update_fields=[
            "deactivated_at",
            "grace_period_ends_at",
            "deactivation_reason",
            "updated_at",
        ]
    )
    return tenant


def get_tenant_onboarding_status(*, tenant: Tenant, user: User) -> dict[str, Any]:
    from automation.models import Workflow
    from integrations.models import ChannelConnection
    from leads.models import MetaConnection, MetaLeadForm, MetaPage
    from messaging.models import Channel, MessageTemplate

    email_verified = bool(user.email_verified)

    meta_conn = MetaConnection.objects.filter(tenant=tenant, status=MetaConnection.Status.CONNECTED).first()
    has_page = MetaPage.objects.filter(tenant=tenant, selected=True).exists() if meta_conn else False
    has_form = MetaLeadForm.objects.filter(tenant=tenant, selected=True).exists() if meta_conn else False
    meta_connected = bool(meta_conn and has_page and has_form)

    active_connections = list(
        ChannelConnection.objects.filter(tenant=tenant, status=ChannelConnection.Status.CONNECTED).values_list("channel", flat=True)
    )
    email_channel = Channel.EMAIL in active_connections
    sms_channel = Channel.SMS in active_connections
    whatsapp_channel = Channel.WHATSAPP in active_connections
    channels_all = email_channel and sms_channel and whatsapp_channel
    channels_any = email_channel or sms_channel or whatsapp_channel

    template_exists = MessageTemplate.objects.filter(tenant=tenant).exists()
    workflow_active = Workflow.objects.filter(tenant=tenant, status=Workflow.Status.ACTIVE).exists()
    team_member_added = (
        Membership.objects.filter(tenant=tenant).count() > 1
        or TenantInvitation.objects.filter(tenant=tenant).exists()
    )

    items = [
        {
            "key": "email_verified",
            "title": "Verify your email",
            "description": "Confirm your business email address to unlock messaging and invitations.",
            "status": "completed" if email_verified else "pending",
            "completed": email_verified,
            "frontend_route": "/settings/profile",
        },
        {
            "key": "meta_connected",
            "title": "Connect Meta Lead Ads",
            "description": "Connect your Facebook Page and select lead generation forms to capture leads automatically.",
            "status": "completed" if meta_connected else "pending",
            "completed": meta_connected,
            "frontend_route": "/integrations/meta",
        },
        {
            "key": "channels_connected",
            "title": "Connect communication channels",
            "description": "Connect SendGrid Email and Twilio SMS/WhatsApp channel integrations.",
            "status": "completed" if channels_all else ("in_progress" if channels_any else "pending"),
            "completed": channels_all,
            "frontend_route": "/integrations/channels",
            "details": {
                "email": email_channel,
                "sms": sms_channel,
                "whatsapp": whatsapp_channel,
            },
        },
        {
            "key": "template_exists",
            "title": "Create a message template",
            "description": "Create an email or WhatsApp message template for your outreach campaigns.",
            "status": "completed" if template_exists else "pending",
            "completed": template_exists,
            "frontend_route": "/templates",
        },
        {
            "key": "workflow_active",
            "title": "Activate an automation workflow",
            "description": "Create and activate an automated customer journey workflow.",
            "status": "completed" if workflow_active else "pending",
            "completed": workflow_active,
            "frontend_route": "/automations",
        },
        {
            "key": "team_member_added",
            "title": "Invite a team member",
            "description": "Invite agents or collaborators to your workspace.",
            "status": "completed" if team_member_added else "pending",
            "completed": team_member_added,
            "frontend_route": "/team",
        },
    ]

    completed_count = sum(1 for it in items if it["completed"])
    total_count = len(items)
    progress_pct = int((completed_count / total_count) * 100) if total_count else 0

    return {
        "progress_percentage": progress_pct,
        "completed_count": completed_count,
        "total_count": total_count,
        "items": items,
    }


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
    if actor_membership.role != Membership.Role.OWNER:
        raise ApplicationError(
            detail="Direct member creation is restricted to Business Owners. Please use the invitation flow.",
            code="invitation_required",
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
            email_verified=False,
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
    logout_all_user_sessions(target_membership.user)
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
    target_membership.save(update_fields=["is_active", "updated_at"])
    logout_all_user_sessions(target_membership.user)
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
    membership: Membership,
    is_available: bool,
) -> Membership:
    membership.is_available = is_available
    membership.save(update_fields=["is_available", "updated_at"])
    return membership


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
    if role is not None and role != target_membership.role:
        change_team_member_role(
            actor_membership=actor_membership,
            target_membership=target_membership,
            new_role=role,
        )
    if is_active is not None and is_active != target_membership.is_active:
        if is_active:
            activate_team_member(
                actor_membership=actor_membership,
                target_membership=target_membership,
            )
        else:
            deactivate_team_member(
                actor_membership=actor_membership,
                target_membership=target_membership,
            )

    update_fields = ["updated_at"]
    if is_available is not None:
        target_membership.is_available = is_available
        update_fields.append("is_available")
    if email_notifications_enabled is not None:
        target_membership.email_notifications_enabled = email_notifications_enabled
        update_fields.append("email_notifications_enabled")
    if len(update_fields) > 1:
        target_membership.save(update_fields=update_fields)

    user = target_membership.user
    user_updated_fields = []
    if first_name is not None:
        user.first_name = first_name.strip()
        user_updated_fields.append("first_name")
    if last_name is not None:
        user.last_name = last_name.strip()
        user_updated_fields.append("last_name")
    if phone is not None:
        user.phone = phone.strip()
        user_updated_fields.append("phone")
    if user_updated_fields:
        user_updated_fields.append("updated_at")
        user.save(update_fields=user_updated_fields)

    return target_membership



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



def update_tenant_settings(*, tenant: Tenant, **changes: Any) -> Tenant:
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


def _validate_api_key_scopes(scopes: Iterable[str] | None) -> list[str]:
    if scopes is None:
        return list(TenantAPIKey.Scope.values)
    valid_set = set(TenantAPIKey.Scope.values)
    normalized: list[str] = []
    for item in scopes:
        clean = str(item).strip()
        if not clean:
            continue
        if clean not in valid_set:
            raise ApplicationError(
                detail=f"Invalid API key scope '{clean}'. Allowed scopes: {', '.join(TenantAPIKey.Scope.values)}.",
                code="invalid_api_key_scope",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if clean not in normalized:
            normalized.append(clean)
    if not normalized:
        raise ApplicationError(
            detail="At least one valid scope must be provided for the API key.",
            code="empty_api_key_scopes",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return normalized


@transaction.atomic
def create_tenant_api_key(
    *,
    name: str,
    scopes: Iterable[str] | None = None,
) -> tuple[TenantAPIKey, str]:
    tenant = require_tenant()
    clean_name = (name or "").strip()
    if not clean_name:
        raise ApplicationError(
            detail="API key name is required.",
            code="api_key_name_required",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if TenantAPIKey.objects.filter(
        name=clean_name,
        is_active=True,
        revoked_at__isnull=True,
    ).exists():
        raise ApplicationError(
            detail="An active API key with this name already exists for this tenant.",
            code="duplicate_api_key_name",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    validated_scopes = _validate_api_key_scopes(scopes)
    key_id = f"crm_{secrets.token_hex(16)}"
    raw_secret = secrets.token_urlsafe(32)
    api_key = TenantAPIKey.objects.create(
        tenant=tenant,
        name=clean_name,
        key_id=key_id,
        secret_encrypted=raw_secret,
        scopes=validated_scopes,
        is_active=True,
    )
    return api_key, raw_secret


@transaction.atomic
def revoke_tenant_api_key(*, api_key: TenantAPIKey) -> TenantAPIKey:
    locked = TenantAPIKey.objects.select_for_update().get(pk=api_key.pk)
    now = timezone.now()
    locked.is_active = False
    locked.revoked_at = now
    locked.save(update_fields=["is_active", "revoked_at", "updated_at"])
    return locked


@transaction.atomic
def rotate_tenant_api_key(
    *,
    api_key: TenantAPIKey,
    grace_period_seconds: int = 0,
    name: str | None = None,
    scopes: Iterable[str] | None = None,
) -> tuple[TenantAPIKey, str, TenantAPIKey]:
    from accounts.tasks import revoke_api_key_task

    locked_old = TenantAPIKey.objects.select_for_update().get(pk=api_key.pk)
    if not locked_old.is_currently_valid():
        raise ApplicationError(
            detail="Cannot rotate an API key that has already been revoked.",
            code="api_key_already_revoked",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    now = timezone.now()
    grace_sec = max(0, int(grace_period_seconds or 0))
    if grace_sec > 0:
        locked_old.revoked_at = now + timedelta(seconds=grace_sec)
        locked_old.save(update_fields=["revoked_at", "updated_at"])
        tid_str = str(locked_old.tenant_id)
        kid_str = str(locked_old.id)
        transaction.on_commit(
            lambda: revoke_api_key_task.apply_async(
                args=[tid_str, kid_str],
                countdown=grace_sec,
            )
        )
    else:
        locked_old.is_active = False
        locked_old.revoked_at = now
        locked_old.save(update_fields=["is_active", "revoked_at", "updated_at"])

    new_name = (name.strip() if name and name.strip() else locked_old.name)
    new_scopes = (
        _validate_api_key_scopes(scopes)
        if scopes is not None
        else list(locked_old.scopes or TenantAPIKey.Scope.values)
    )
    key_id = f"crm_{secrets.token_hex(16)}"
    raw_secret = secrets.token_urlsafe(32)
    new_key = TenantAPIKey.objects.create(
        tenant=locked_old.tenant,
        name=new_name,
        key_id=key_id,
        secret_encrypted=raw_secret,
        scopes=new_scopes,
        is_active=True,
    )
    return new_key, raw_secret, locked_old
