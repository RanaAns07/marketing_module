from typing import Any
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from accounts.models import Membership
from contacts.services import normalize_email, normalize_phone_e164
from core.context import require_tenant_id
from core.exceptions import ApplicationError
from integrations.models import ChannelConnection
from integrations.providers.registry import (
    DEFAULT_PROVIDER_FOR_CHANNEL,
    ProviderDispatchError,
    WhatsAppProvider,
    build_provider_for_connection,
    get_provider_class,
    resolve_tenant_provider,
    strip_whatsapp_prefix,
)
from messaging.models import Channel, MessageTemplate, WhatsAppCategory
from notifications.models import NotificationType
from notifications.services import notify


def _normalize_sender_identity(channel: str, raw_identity: str) -> str:
    norm_channel = (channel or "").strip().lower()
    if norm_channel == ChannelConnection.Channel.EMAIL:
        norm_email = normalize_email(raw_identity)
        if not norm_email or "@" not in norm_email:
            raise ApplicationError(
                detail="A valid sender email address is required for email channel connections.",
                code="invalid_sender_email",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        return norm_email
    cleaned = strip_whatsapp_prefix(raw_identity)
    norm_phone = normalize_phone_e164(cleaned)
    if not norm_phone:
        raise ApplicationError(
            detail="A valid E.164 sender phone number is required.",
            code="invalid_sender_phone",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return norm_phone


def _validate_unique_sender_number(
    channel: str,
    sender_identity: str,
    exclude_id: Any = None,
) -> None:
    if channel not in (ChannelConnection.Channel.SMS, ChannelConnection.Channel.WHATSAPP):
        return
    qs = ChannelConnection.unscoped_objects.filter(
        channel__in=[ChannelConnection.Channel.SMS, ChannelConnection.Channel.WHATSAPP],
        sender_identity=sender_identity,
    )
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    if qs.exists():
        raise ApplicationError(
            detail="This sender phone number is already registered to a channel connection.",
            code="duplicate_sender_number",
            status_code=status.HTTP_400_BAD_REQUEST,
        )


@transaction.atomic
def create_channel_connection(
    *,
    channel: str,
    provider: str = "",
    credentials: dict[str, Any] | None = None,
    sender_identity: str,
    sender_name: str = "",
    status_value: str = ChannelConnection.Status.ACTIVE,
) -> ChannelConnection:
    norm_channel = (channel or "").strip().lower()
    norm_provider = (
        (provider or "").strip().lower()
        or DEFAULT_PROVIDER_FOR_CHANNEL.get(norm_channel, "")
    )
    get_provider_class(norm_channel, norm_provider)

    if ChannelConnection.objects.filter(channel=norm_channel).exists():
        raise ApplicationError(
            detail=f"A {norm_channel} channel connection already exists for this tenant.",
            code="duplicate_channel_connection",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    norm_sender = _normalize_sender_identity(norm_channel, sender_identity)
    _validate_unique_sender_number(norm_channel, norm_sender)

    return ChannelConnection.objects.create(
        channel=norm_channel,
        provider=norm_provider,
        credentials=dict(credentials or {}),
        sender_identity=norm_sender,
        sender_name=(sender_name or "").strip(),
        status=status_value or ChannelConnection.Status.ACTIVE,
    )


@transaction.atomic
def update_channel_connection(
    *,
    connection: ChannelConnection,
    provider: str | None = None,
    credentials: dict[str, Any] | None = None,
    sender_identity: str | None = None,
    sender_name: str | None = None,
    status_value: str | None = None,
) -> ChannelConnection:
    if provider is not None:
        norm_provider = provider.strip().lower()
        get_provider_class(connection.channel, norm_provider)
        connection.provider = norm_provider

    if sender_identity is not None:
        norm_sender = _normalize_sender_identity(connection.channel, sender_identity)
        _validate_unique_sender_number(
            connection.channel,
            norm_sender,
            exclude_id=connection.pk,
        )
        connection.sender_identity = norm_sender

    if sender_name is not None:
        connection.sender_name = sender_name.strip()

    if credentials is not None:
        merged = dict(connection.credentials or {})
        merged.update(credentials)
        connection.credentials = merged

    if status_value is not None:
        connection.status = status_value

    connection.save()
    return connection


@transaction.atomic
def test_channel_connection(*, connection: ChannelConnection) -> dict[str, Any]:
    tenant_id = require_tenant_id()
    provider_instance = build_provider_for_connection(connection)
    ok, message = provider_instance.test_connection()
    now = timezone.now()
    connection.last_checked_at = now
    if ok:
        connection.status = ChannelConnection.Status.ACTIVE
        connection.last_error = ""
    else:
        connection.status = ChannelConnection.Status.ERROR
        connection.last_error = message

    connection.save(update_fields=["status", "last_checked_at", "last_error", "updated_at"])

    if not ok:
        admin_user_ids = list(
            Membership.objects.filter(
                role__in=[Membership.Role.OWNER, Membership.Role.ADMIN],
                is_active=True,
            ).values_list("user_id", flat=True)
        )
        if admin_user_ids:
            notify(
                tenant=tenant_id,
                users=admin_user_ids,
                type=NotificationType.WORKFLOW_OR_INTEGRATION_ALERT,
                payload={
                    "title": f"Integration Alert: {connection.get_channel_display()} Disconnected",
                    "body": message,
                    "channel": connection.channel,
                    "connection_id": str(connection.id),
                },
                link="/settings/integrations",
            )

    return {
        "ok": ok,
        "status": connection.status,
        "message": message,
        "last_checked_at": now,
    }


@transaction.atomic
def sync_whatsapp_templates(
    *,
    connection: ChannelConnection | None = None,
) -> list[MessageTemplate]:
    tenant_id = require_tenant_id()
    if connection is None:
        connection, provider = resolve_tenant_provider(
            Channel.WHATSAPP,
            tenant_or_id=tenant_id,
            require_active=False,
        )
    else:
        if connection.channel != ChannelConnection.Channel.WHATSAPP:
            raise ApplicationError(
                detail="Template sync is only supported for WhatsApp channel connections.",
                code="invalid_channel_for_template_sync",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        provider = build_provider_for_connection(connection)

    if not isinstance(provider, WhatsAppProvider):
        raise ApplicationError(
            detail="Configured provider does not support WhatsApp template sync.",
            code="unsupported_whatsapp_provider",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        remote_templates = provider.fetch_templates()
    except ProviderDispatchError as exc:
        raise ApplicationError(
            detail=exc.message,
            code="whatsapp_template_sync_failed",
            status_code=status.HTTP_502_BAD_GATEWAY,
        ) from exc

    synced: list[MessageTemplate] = []
    valid_categories = {c[0] for c in WhatsAppCategory.choices}
    valid_approvals = {c[0] for c in MessageTemplate.ApprovalStatus.choices}

    for item in remote_templates:
        category = item.category if item.category in valid_categories else WhatsAppCategory.UTILITY
        approval = (
            item.approval_status
            if item.approval_status in valid_approvals
            else MessageTemplate.ApprovalStatus.PENDING
        )
        existing = MessageTemplate.objects.filter(
            whatsapp_content_sid=item.content_sid,
        ).first()
        if existing is None:
            existing = MessageTemplate.objects.filter(
                channel=Channel.WHATSAPP,
                name=item.name,
            ).first()

        is_active = approval == MessageTemplate.ApprovalStatus.APPROVED
        if existing is not None:
            existing.name = item.name
            existing.body = item.body
            existing.variables = list(item.variables or [])
            existing.whatsapp_content_sid = item.content_sid
            existing.whatsapp_category = category
            existing.approval_status = approval
            existing.active = is_active
            existing.save()
            synced.append(existing)
        else:
            created = MessageTemplate.objects.create(
                channel=Channel.WHATSAPP,
                name=item.name,
                subject="",
                body=item.body,
                variables=list(item.variables or []),
                whatsapp_content_sid=item.content_sid,
                whatsapp_category=category,
                approval_status=approval,
                active=is_active,
            )
            synced.append(created)

    return synced
