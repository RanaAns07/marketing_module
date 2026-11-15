import hmac
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from core.context import tenant_context
from integrations.models import ChannelConnection, InboundWebhookEvent
from integrations.providers.registry import (
    build_provider_for_connection,
    get_meta_provider,
    resolve_connection_by_webhook_token,
    resolve_twilio_connection_by_number,
)
from integrations.tasks import process_webhook_event_task
from leads.services import ingest_meta_webhook_changes


class WebhookAckSerializer(serializers.Serializer):
    status = serializers.CharField()
    webhook_event_id = serializers.UUIDField(required=False)
    events_created = serializers.IntegerField(required=False)


def _resolve_public_webhook_url(request) -> str:
    request_url = request.build_absolute_uri()
    public_base = str(getattr(settings, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if not public_base:
        return request_url
    try:
        parsed_base = urlsplit(public_base)
        parsed_req = urlsplit(request_url)
        if parsed_base.scheme and parsed_base.netloc:
            base_path = parsed_base.path.rstrip("/")
            combined_path = f"{base_path}{parsed_req.path}" if base_path else parsed_req.path
            return urlunsplit(
                (
                    parsed_base.scheme,
                    parsed_base.netloc,
                    combined_path,
                    parsed_req.query,
                    "",
                )
            )
    except Exception:
        pass
    return request_url


def _extract_safe_headers(request) -> dict[str, str]:
    interesting = {
        "x-twilio-signature",
        "x-twilio-email-event-webhook-signature",
        "x-twilio-email-event-webhook-timestamp",
        "x-hub-signature-256",
        "content-type",
        "user-agent",
    }
    out: dict[str, str] = {}
    for k, v in request.headers.items():
        if k.lower() in interesting:
            out[k] = str(v)
    return out


def _enqueue_webhook_event(tenant_id: str, event_id: str) -> None:
    try:
        process_webhook_event_task.apply_async(
            args=[tenant_id, event_id],
            queue="webhooks",
        )
    except Exception:
        process_webhook_event_task(tenant_id, event_id)


def _handle_twilio_webhook(
    request,
    *,
    channel: str,
    forced_event_type: str | None = None,
) -> Response:
    if hasattr(request.POST, "dict") and request.POST:
        payload_dict: dict[str, Any] = request.POST.dict()
    elif isinstance(request.data, dict):
        payload_dict = {str(k): v for k, v in request.data.items()}
    else:
        payload_dict = {}

    has_body = (
        payload_dict.get("Body") is not None
        or bool(payload_dict.get("ButtonPayload"))
        or bool(payload_dict.get("ButtonText"))
    )
    has_status = bool(payload_dict.get("MessageStatus") or payload_dict.get("SmsStatus"))

    if forced_event_type:
        event_type = forced_event_type
    elif has_status and not has_body:
        event_type = InboundWebhookEvent.EventType.STATUS
    else:
        event_type = InboundWebhookEvent.EventType.INBOUND

    to_num = str(payload_dict.get("To") or "")
    from_num = str(payload_dict.get("From") or "")

    if event_type == InboundWebhookEvent.EventType.STATUS:
        connection = resolve_twilio_connection_by_number(channel, from_num, to_num)
    else:
        connection = resolve_twilio_connection_by_number(channel, to_num, from_num)

    if connection is None:
        return Response(
            {"detail": "Unable to resolve tenant for Twilio webhook."},
            status=status.HTTP_403_FORBIDDEN,
        )

    provider = build_provider_for_connection(connection)
    full_url = _resolve_public_webhook_url(request)
    if not provider.verify_signature(
        payload=payload_dict,
        headers=request.headers,
        url=full_url,
    ):
        return Response(
            {"detail": "Invalid Twilio webhook signature."},
            status=status.HTTP_403_FORBIDDEN,
        )

    tenant_id_str = str(connection.tenant_id)
    with tenant_context(connection.tenant_id):
        with transaction.atomic():
            webhook_event = InboundWebhookEvent.objects.create(
                connection=connection,
                provider=ChannelConnection.Provider.TWILIO,
                channel=channel,
                event_type=event_type,
                raw_payload=payload_dict,
                headers=_extract_safe_headers(request),
            )
            event_id_str = str(webhook_event.id)
            transaction.on_commit(
                lambda: _enqueue_webhook_event(tenant_id_str, event_id_str)
            )

    return Response(
        {"status": "accepted", "webhook_event_id": event_id_str},
        status=status.HTTP_200_OK,
    )


class TwilioSMSWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [FormParser, MultiPartParser, JSONParser]

    @extend_schema(request=None, responses={200: WebhookAckSerializer})
    def post(self, request, event_type: str | None = None):
        return _handle_twilio_webhook(
            request,
            channel=ChannelConnection.Channel.SMS,
            forced_event_type=event_type,
        )


class TwilioWhatsAppWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [FormParser, MultiPartParser, JSONParser]

    @extend_schema(request=None, responses={200: WebhookAckSerializer})
    def post(self, request, event_type: str | None = None):
        return _handle_twilio_webhook(
            request,
            channel=ChannelConnection.Channel.WHATSAPP,
            forced_event_type=event_type,
        )


class SendGridWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    @extend_schema(request=None, responses={200: WebhookAckSerializer})
    def post(self, request, webhook_token: str, mode: str | None = None):
        connection = resolve_connection_by_webhook_token(webhook_token)
        if connection is None:
            return Response(
                {"detail": "Invalid SendGrid webhook token."},
                status=status.HTTP_403_FORBIDDEN,
            )

        provider = build_provider_for_connection(connection)
        raw_bytes = request.body
        if not provider.verify_signature(
            payload=raw_bytes,
            headers=request.headers,
            url=_resolve_public_webhook_url(request),
        ):
            return Response(
                {"detail": "Invalid SendGrid webhook signature."},
                status=status.HTTP_403_FORBIDDEN,
            )

        parsed_data = request.data
        is_inbound = mode == "inbound" or (
            isinstance(parsed_data, dict)
            and "events" not in parsed_data
            and "event" not in parsed_data
            and ("from" in parsed_data or "text" in parsed_data or "html" in parsed_data)
        )

        if is_inbound:
            event_type = InboundWebhookEvent.EventType.INBOUND
            if hasattr(request.POST, "dict") and request.POST:
                raw_payload: dict[str, Any] = request.POST.dict()
            elif isinstance(parsed_data, dict):
                raw_payload = {
                    str(k): (v if isinstance(v, (str, int, float, bool, list, dict)) else str(v))
                    for k, v in parsed_data.items()
                }
            else:
                raw_payload = {}
        else:
            event_type = InboundWebhookEvent.EventType.EVENTS
            if isinstance(parsed_data, list):
                raw_payload = {"events": parsed_data}
            elif isinstance(parsed_data, dict):
                raw_payload = dict(parsed_data)
            else:
                raw_payload = {"events": []}

        tenant_id_str = str(connection.tenant_id)
        with tenant_context(connection.tenant_id):
            with transaction.atomic():
                webhook_event = InboundWebhookEvent.objects.create(
                    connection=connection,
                    provider=ChannelConnection.Provider.SENDGRID,
                    channel=ChannelConnection.Channel.EMAIL,
                    event_type=event_type,
                    raw_payload=raw_payload,
                    headers=_extract_safe_headers(request),
                )
                event_id_str = str(webhook_event.id)
                transaction.on_commit(
                    lambda: _enqueue_webhook_event(tenant_id_str, event_id_str)
                )

        return Response(
            {"status": "accepted", "webhook_event_id": event_id_str},
            status=status.HTTP_200_OK,
        )


class MetaWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]

    @extend_schema(
        parameters=[
            OpenApiParameter(name="hub.mode", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="hub.verify_token", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="hub.challenge", type=str, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={200: str},
    )
    def get(self, request):
        mode = str(request.query_params.get("hub.mode") or "")
        token = str(request.query_params.get("hub.verify_token") or "")
        challenge = str(request.query_params.get("hub.challenge") or "")
        expected_token = str(getattr(settings, "META_WEBHOOK_VERIFY_TOKEN", "") or "")

        if (
            mode == "subscribe"
            and expected_token
            and hmac.compare_digest(token.encode("utf-8"), expected_token.encode("utf-8"))
        ):
            return HttpResponse(challenge, content_type="text/plain", status=status.HTTP_200_OK)

        return Response(
            {"detail": "Invalid Meta webhook verification token."},
            status=status.HTTP_403_FORBIDDEN,
        )

    @extend_schema(
        request=None,
        responses={200: WebhookAckSerializer},
    )
    def post(self, request):
        provider = get_meta_provider()
        raw_bytes = request.body
        if not provider.verify_signature(
            payload=raw_bytes,
            headers=request.headers,
            url=_resolve_public_webhook_url(request),
        ):
            return Response(
                {"detail": "Invalid Meta webhook signature."},
                status=status.HTTP_403_FORBIDDEN,
            )

        changes = provider.parse_webhook_changes(request.data)
        created_events = ingest_meta_webhook_changes(changes)
        return Response(
            {"status": "accepted", "events_created": len(created_events)},
            status=status.HTTP_200_OK,
        )
