import hmac
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.authentication import APIKeyHMACAuthentication
from accounts.models import TenantAPIKey
from accounts.permissions import HasAPIKeyScope
from accounts.throttling import APIKeyRateThrottle, TenantRateThrottle, WebhookRateThrottle
from contacts.models import Appointment, Contact
from contacts.serializers import AppointmentSerializer, ContactSerializer
from contacts.services import (
    ContactService,
    book_appointment,
    cancel_appointment,
    complete_appointment,
    reschedule_appointment,
)
from core.context import tenant_context
from core.exceptions import ApplicationError
from core.pagination import KeysetCursorPagination
from core.views import TenantScopedViewSet
from integrations.models import ChannelConnection, InboundWebhookEvent
from integrations.providers.registry import (
    build_provider_for_connection,
    get_meta_provider,
    resolve_connection_by_webhook_token,
    resolve_twilio_connection_by_number,
)
from integrations.tasks import process_webhook_event_task
from leads.services import ingest_meta_webhook_changes
from webhooks.models import ExternalEvent
from webhooks.selectors import (
    get_appointment_by_external_ref_or_id,
    get_contact_by_external_id_or_id,
    get_external_events_queryset,
    get_webhook_deliveries_queryset,
    get_webhook_endpoints_queryset,
)
from webhooks.serializers import (
    ExternalAppointmentCreateSerializer,
    ExternalAppointmentUpdateSerializer,
    ExternalContactTagsSerializer,
    ExternalContactUpsertSerializer,
    ExternalEventEnvelopeSerializer,
    ExternalEventIngestResponseSerializer,
    ExternalEventSerializer,
    WebhookDeliverySerializer,
    WebhookEndpointSecretResponseSerializer,
    WebhookEndpointSerializer,
)
from webhooks.services import (
    SUPPORTED_EXTERNAL_EVENT_TYPES,
    create_webhook_endpoint,
    delete_webhook_endpoint,
    ingest_external_event,
    ping_webhook_endpoint,
    redeliver_webhook_delivery,
    replay_external_event,
    rotate_webhook_endpoint_secret,
    update_webhook_endpoint,
)


class WebhookAckSerializer(serializers.Serializer):
    status = serializers.CharField()
    webhook_event_id = serializers.UUIDField(required=False)
    events_created = serializers.IntegerField(required=False)


MAX_WEBHOOK_PAYLOAD_BYTES = 5 * 1024 * 1024


def _check_request_size(request) -> Response | None:
    content_length = request.META.get("CONTENT_LENGTH")
    if content_length:
        try:
            if int(content_length) > MAX_WEBHOOK_PAYLOAD_BYTES:
                return Response(
                    {"detail": "Request payload exceeds size limit."},
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )
        except (ValueError, TypeError):
            pass
    try:
        body = getattr(request, "_body", None) or getattr(request, "body", None)
        if body and len(body) > MAX_WEBHOOK_PAYLOAD_BYTES:
            return Response(
                {"detail": "Request payload exceeds size limit."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
    except Exception:
        pass
    return None


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
    size_err = _check_request_size(request)
    if size_err is not None:
        return size_err

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
    throttle_classes = [WebhookRateThrottle]
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
    throttle_classes = [WebhookRateThrottle]
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
    throttle_classes = [WebhookRateThrottle]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    @extend_schema(request=None, responses={200: WebhookAckSerializer})
    def post(self, request, webhook_token: str, mode: str | None = None):
        size_err = _check_request_size(request)
        if size_err is not None:
            return size_err

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
    throttle_classes = [WebhookRateThrottle]
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
        size_err = _check_request_size(request)
        if size_err is not None:
            return size_err

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


class BaseExternalAPIView(APIView):
    authentication_classes = [APIKeyHMACAuthentication]
    permission_classes = [HasAPIKeyScope]
    throttle_classes = [APIKeyRateThrottle, TenantRateThrottle]
    parser_classes = [JSONParser]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        size_err = _check_request_size(request)
        if size_err is not None:
            raise ApplicationError(
                detail="Request payload exceeds size limit.",
                code="payload_too_large",
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )


class ExternalContactUpsertView(BaseExternalAPIView):
    required_scope = TenantAPIKey.Scope.CONTACTS_WRITE

    @extend_schema(
        summary="Upsert contact from external platform",
        request=ExternalContactUpsertSerializer,
        responses={200: ContactSerializer, 201: ContactSerializer},
    )
    def post(self, request):
        serializer = ExternalContactUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        api_key: TenantAPIKey = request.api_key
        source_channel = api_key.name if api_key and api_key.name else "external_api"

        contact, created = ContactService.upsert_from_source(
            first_name=v.get("first_name", ""),
            last_name=v.get("last_name", ""),
            full_name=v.get("full_name", ""),
            email=v.get("email", ""),
            phone=v.get("phone", ""),
            city=v.get("city", ""),
            country=v.get("country", ""),
            timezone_name=v.get("timezone", "UTC") or "UTC",
            source_channel=source_channel,
            campaign=v.get("campaign", ""),
            ad_set=v.get("ad_set", ""),
            ad=v.get("ad", ""),
            external_id=v.get("external_id", ""),
            custom_fields=v.get("custom_fields") or None,
            tags=v.get("tags") or None,
        )
        refreshed = get_contact_by_external_id_or_id(str(contact.id)) or contact
        return Response(
            ContactSerializer(refreshed, context={"request": request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ExternalContactDetailView(BaseExternalAPIView):
    required_scope = TenantAPIKey.Scope.CONTACTS_READ

    @extend_schema(
        summary="Get contact by external_id or UUID",
        responses={200: ContactSerializer},
    )
    def get(self, request, external_id_or_id: str):
        contact = get_contact_by_external_id_or_id(external_id_or_id)
        if contact is None:
            raise NotFound("Contact not found.")
        return Response(
            ContactSerializer(contact, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class ExternalContactTagsView(BaseExternalAPIView):
    required_scope = TenantAPIKey.Scope.CONTACTS_WRITE

    @extend_schema(
        summary="Add or remove tags on a contact",
        request=ExternalContactTagsSerializer,
        responses={200: ContactSerializer},
    )
    def post(self, request, id: str):
        contact = get_contact_by_external_id_or_id(id)
        if contact is None:
            raise NotFound("Contact not found.")

        serializer = ExternalContactTagsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tags = serializer.validated_data["tags"]
        tag_action = serializer.validated_data.get("action", "add")

        if tag_action == "remove":
            ContactService.remove_tags(contact=contact, tags=tags)
        else:
            ContactService.add_tags(contact=contact, tags=tags, create_missing=True)

        refreshed = get_contact_by_external_id_or_id(str(contact.id)) or contact
        return Response(
            ContactSerializer(refreshed, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class ExternalAppointmentCreateView(BaseExternalAPIView):
    required_scope = TenantAPIKey.Scope.APPOINTMENTS_WRITE

    @extend_schema(
        summary="Book an appointment for a contact",
        request=ExternalAppointmentCreateSerializer,
        responses={201: AppointmentSerializer},
    )
    def post(self, request):
        serializer = ExternalAppointmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        api_key: TenantAPIKey = request.api_key
        source_channel = api_key.name if api_key and api_key.name else "external_api"

        contact: Contact | None = None
        if v.get("contact_id"):
            contact = get_contact_by_external_id_or_id(str(v["contact_id"]))
        if contact is None and v.get("contact_external_id"):
            contact = get_contact_by_external_id_or_id(str(v["contact_external_id"]))
        if contact is None and isinstance(v.get("contact"), dict):
            c_info = v["contact"]
            contact, _ = ContactService.upsert_from_source(
                first_name=c_info.get("first_name", ""),
                last_name=c_info.get("last_name", ""),
                full_name=c_info.get("full_name", ""),
                email=c_info.get("email", ""),
                phone=c_info.get("phone", ""),
                source_channel=source_channel,
                external_id=c_info.get("external_id", ""),
            )
        if contact is None:
            raise ApplicationError(
                detail="Valid contact_id, contact_external_id, or contact object is required.",
                code="contact_required",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        appt = book_appointment(
            contact=contact,
            starts_at=v["starts_at"],
            external_ref=v.get("external_ref", ""),
            location=v.get("location", ""),
            notes=v.get("notes", ""),
        )
        return Response(
            AppointmentSerializer(appt, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class ExternalAppointmentUpdateView(BaseExternalAPIView):
    required_scope = TenantAPIKey.Scope.APPOINTMENTS_WRITE

    @extend_schema(
        summary="Update appointment status (booked, cancelled, completed, rescheduled)",
        request=ExternalAppointmentUpdateSerializer,
        responses={200: AppointmentSerializer},
    )
    def patch(self, request, id: str):
        appt = get_appointment_by_external_ref_or_id(id)
        if appt is None:
            raise NotFound("Appointment not found.")

        serializer = ExternalAppointmentUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        target_status = v["status"]

        if target_status == Appointment.Status.CANCELLED:
            appt = cancel_appointment(appointment=appt)
        elif target_status == Appointment.Status.COMPLETED:
            appt = complete_appointment(appointment=appt)
        elif target_status == Appointment.Status.RESCHEDULED:
            new_starts_at = v.get("starts_at") or appt.starts_at
            appt = reschedule_appointment(
                appointment=appt,
                starts_at=new_starts_at,
                location=v.get("location") if "location" in v else None,
                notes=v.get("notes") if "notes" in v else None,
            )
        elif target_status == Appointment.Status.BOOKED:
            update_fields = ["status", "updated_at"]
            appt.status = Appointment.Status.BOOKED
            if "starts_at" in v and v["starts_at"] is not None:
                appt.starts_at = v["starts_at"]
                update_fields.append("starts_at")
            if "location" in v and v["location"] is not None:
                appt.location = v["location"]
                update_fields.append("location")
            if "notes" in v and v["notes"] is not None:
                appt.notes = v["notes"]
                update_fields.append("notes")
            appt.save(update_fields=update_fields)

        return Response(
            AppointmentSerializer(appt, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class ExternalEventIngestView(BaseExternalAPIView):
    required_scope = TenantAPIKey.Scope.EVENTS_WRITE

    @extend_schema(
        summary="Ingest signed external business event (purchases, subscriptions, payments, appointments)",
        request=ExternalEventEnvelopeSerializer,
        responses={
            200: ExternalEventIngestResponseSerializer,
            202: ExternalEventIngestResponseSerializer,
        },
    )
    def post(self, request):
        raw_type = request.data.get("type") if isinstance(request.data, dict) else None
        if raw_type is not None and str(raw_type).strip() not in SUPPORTED_EXTERNAL_EVENT_TYPES:
            raise ApplicationError(
                detail=f"Unsupported external event type '{raw_type}'.",
                code="unsupported_event_type",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        serializer = ExternalEventEnvelopeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data

        ext_event, is_duplicate = ingest_external_event(
            api_key=request.api_key,
            event_id=v["event_id"],
            event_type=v["type"],
            occurred_at=v["occurred_at"],
            contact_payload=v["contact"],
            data=v.get("data") or {},
            automation=v.get("automation"),
        )
        ext_event.refresh_from_db()

        response_payload = {
            "status": "duplicate" if is_duplicate else "accepted",
            "idempotent_duplicate": is_duplicate,
            "external_event_id": str(ext_event.id),
            "event_id": ext_event.event_id,
            "type": ext_event.type,
            "event_status": ext_event.status,
            "result": ext_event.result or {},
        }
        return Response(
            response_payload,
            status=status.HTTP_200_OK if is_duplicate else status.HTTP_202_ACCEPTED,
        )


class WebhookEndpointViewSet(TenantScopedViewSet):
    serializer_class = WebhookEndpointSerializer
    permission_classes = [IsAuthenticated, HasAPIKeyScope]
    throttle_classes = [APIKeyRateThrottle, TenantRateThrottle]
    allow_jwt_owner_admin = True
    required_scope = TenantAPIKey.Scope.WEBHOOKS_MANAGE

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.serializer_class.Meta.model.unscoped_objects.none()
        return get_webhook_endpoints_queryset()

    @extend_schema(
        request=WebhookEndpointSerializer,
        responses={201: WebhookEndpointSecretResponseSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        endpoint, raw_secret = create_webhook_endpoint(
            name=v["name"],
            url=v["url"],
            subscribed_events=v.get("subscribed_events"),
            active=v.get("active", True),
        )
        out = WebhookEndpointSerializer(endpoint, context=self.get_serializer_context()).data
        out["signing_secret"] = raw_secret
        return Response(out, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        update_webhook_endpoint(
            endpoint=self.get_object(),
            **serializer.validated_data,
        )

    def perform_destroy(self, instance):
        delete_webhook_endpoint(endpoint=instance)

    @extend_schema(
        request=None,
        responses={200: WebhookEndpointSecretResponseSerializer},
    )
    @action(detail=True, methods=["post"], url_path="rotate-secret")
    def rotate_secret(self, request, pk=None):
        endpoint = self.get_object()
        updated, new_secret = rotate_webhook_endpoint_secret(endpoint=endpoint)
        out = WebhookEndpointSerializer(updated, context=self.get_serializer_context()).data
        out["signing_secret"] = new_secret
        return Response(out, status=status.HTTP_200_OK)

    @extend_schema(
        request=None,
        responses={200: WebhookDeliverySerializer},
    )
    @action(detail=True, methods=["post"], url_path="ping")
    def ping(self, request, pk=None):
        endpoint = self.get_object()
        delivery = ping_webhook_endpoint(endpoint=endpoint)
        return Response(
            WebhookDeliverySerializer(delivery, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )


class WebhookDeliveryViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = WebhookDeliverySerializer
    permission_classes = [IsAuthenticated, HasAPIKeyScope]
    throttle_classes = [APIKeyRateThrottle, TenantRateThrottle]
    pagination_class = KeysetCursorPagination
    allow_jwt_owner_admin = True
    required_scope = TenantAPIKey.Scope.WEBHOOKS_MANAGE

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.serializer_class.Meta.model.unscoped_objects.none()
        return get_webhook_deliveries_queryset(self.request.query_params)

    @extend_schema(
        request=None,
        responses={200: WebhookDeliverySerializer},
    )
    @action(detail=True, methods=["post"], url_path="redeliver")
    def redeliver(self, request, pk=None):
        delivery = self.get_object()
        updated = redeliver_webhook_delivery(delivery=delivery)
        return Response(
            WebhookDeliverySerializer(updated, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )


class ExternalEventViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = ExternalEventSerializer
    permission_classes = [IsAuthenticated, HasAPIKeyScope]
    throttle_classes = [APIKeyRateThrottle, TenantRateThrottle]
    pagination_class = KeysetCursorPagination
    allow_jwt_owner_admin = True
    required_scope = TenantAPIKey.Scope.WEBHOOKS_MANAGE

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ExternalEvent.unscoped_objects.none()
        return get_external_events_queryset(self.request.query_params)

    @extend_schema(
        request=None,
        responses={200: ExternalEventSerializer},
    )
    @action(detail=True, methods=["post"], url_path="replay")
    def replay(self, request, pk=None):
        ext_event = self.get_object()
        replayed = replay_external_event(external_event=ext_event)
        return Response(
            ExternalEventSerializer(replayed, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )
