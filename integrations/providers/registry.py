from typing import Any
import uuid
from rest_framework import status
from core.context import get_current_tenant_id, tenant_context
from core.exceptions import ApplicationError
from integrations.providers.base import (
    EmailProvider,
    NormalizedInboundMessage,
    NormalizedStatusEvent,
    NormalizedTemplate,
    ProviderDispatchError,
    ProviderSendResult,
    SMSProvider,
    WhatsAppProvider,
)
from integrations.providers.sendgrid import SendGridEmailProvider
from integrations.providers.twilio import (
    TwilioSMSProvider,
    TwilioWhatsAppProvider,
    strip_whatsapp_prefix,
)

PROVIDER_CLASSES: dict[tuple[str, str], type[EmailProvider | SMSProvider | WhatsAppProvider]] = {
    ("email", "sendgrid"): SendGridEmailProvider,
    ("sms", "twilio"): TwilioSMSProvider,
    ("whatsapp", "twilio"): TwilioWhatsAppProvider,
}

DEFAULT_PROVIDER_FOR_CHANNEL: dict[str, str] = {
    "email": "sendgrid",
    "sms": "twilio",
    "whatsapp": "twilio",
}


def get_provider_class(
    channel: str,
    provider_name: str = "",
) -> type[EmailProvider | SMSProvider | WhatsAppProvider]:
    norm_channel = (channel or "").strip().lower()
    norm_provider = (
        (provider_name or "").strip().lower()
        or DEFAULT_PROVIDER_FOR_CHANNEL.get(norm_channel, "")
    )
    provider_cls = PROVIDER_CLASSES.get((norm_channel, norm_provider))
    if provider_cls is None:
        raise ApplicationError(
            detail=f"Unsupported provider '{norm_provider}' for channel '{norm_channel}'.",
            code="unsupported_channel_provider",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return provider_cls


def instantiate_provider(
    *,
    channel: str,
    provider_name: str,
    credentials: dict[str, Any],
    sender_identity: str = "",
    sender_name: str = "",
) -> EmailProvider | SMSProvider | WhatsAppProvider:
    provider_cls = get_provider_class(channel, provider_name)
    if issubclass(provider_cls, EmailProvider):
        return provider_cls(
            credentials=credentials,
            sender_identity=sender_identity,
            sender_name=sender_name,
        )
    return provider_cls(
        credentials=credentials,
        sender_identity=sender_identity,
    )


def build_provider_for_connection(
    connection: Any,
) -> EmailProvider | SMSProvider | WhatsAppProvider:
    return instantiate_provider(
        channel=connection.channel,
        provider_name=connection.provider,
        credentials=connection.credentials or {},
        sender_identity=connection.sender_identity,
        sender_name=getattr(connection, "sender_name", ""),
    )


def resolve_tenant_provider(
    channel: str,
    tenant_or_id: Any = None,
    require_active: bool = True,
) -> tuple[Any, EmailProvider | SMSProvider | WhatsAppProvider]:
    from integrations.models import ChannelConnection

    target_tenant_id = (
        (tenant_or_id.pk if hasattr(tenant_or_id, "pk") else tenant_or_id)
        if tenant_or_id is not None
        else get_current_tenant_id()
    )
    if target_tenant_id is None:
        raise ApplicationError(
            detail="Active tenant context is required to resolve channel provider.",
            code="tenant_context_required",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    with tenant_context(target_tenant_id):
        qs = ChannelConnection.objects.filter(channel=channel.strip().lower())
        if require_active:
            qs = qs.filter(status=ChannelConnection.Status.ACTIVE)
        connection = qs.first()

    if connection is None:
        raise ApplicationError(
            detail=f"No active {channel} channel connection is configured for this tenant.",
            code="channel_not_connected",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    provider = build_provider_for_connection(connection)
    return connection, provider


def resolve_connection_by_webhook_token(webhook_token: str) -> Any | None:
    from integrations.models import ChannelConnection

    token = (webhook_token or "").strip()
    if not token:
        return None
    return (
        ChannelConnection.unscoped_objects.select_related("tenant")
        .filter(
            channel=ChannelConnection.Channel.EMAIL,
            webhook_token=token,
            tenant__is_active=True,
        )
        .first()
    )


def resolve_twilio_connection_by_number(
    channel: str,
    *candidate_numbers: str,
) -> Any | None:
    from contacts.services import normalize_phone_e164
    from integrations.models import ChannelConnection

    normalized_candidates: list[str] = []
    for raw in candidate_numbers:
        cleaned = strip_whatsapp_prefix(raw)
        if not cleaned:
            continue
        try:
            norm = normalize_phone_e164(cleaned)
        except Exception:
            norm = cleaned
        if norm and norm not in normalized_candidates:
            normalized_candidates.append(norm)
        if cleaned not in normalized_candidates:
            normalized_candidates.append(cleaned)

    if not normalized_candidates:
        return None

    for candidate in normalized_candidates:
        conn = (
            ChannelConnection.unscoped_objects.select_related("tenant")
            .filter(
                channel=channel,
                sender_identity=candidate,
                tenant__is_active=True,
            )
            .first()
        )
        if conn is not None:
            return conn
    return None


__all__ = [
    "DEFAULT_PROVIDER_FOR_CHANNEL",
    "EmailProvider",
    "NormalizedInboundMessage",
    "NormalizedStatusEvent",
    "NormalizedTemplate",
    "PROVIDER_CLASSES",
    "ProviderDispatchError",
    "ProviderSendResult",
    "SMSProvider",
    "WhatsAppProvider",
    "build_provider_for_connection",
    "get_provider_class",
    "instantiate_provider",
    "resolve_connection_by_webhook_token",
    "resolve_tenant_provider",
    "resolve_twilio_connection_by_number",
    "strip_whatsapp_prefix",
]
