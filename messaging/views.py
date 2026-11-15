from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.permissions import (
    CanAccessContactObject,
    IsOwnerOrAdmin,
    IsOwnerOrAdminOrReadOnly,
    IsTenantMember,
)
from accounts.throttling import (
    WebhookSignatureAbuseThrottle,
    record_webhook_signature_failure,
)
from contacts.selectors import get_contact_by_id
from core.exceptions import ApplicationError
from core.pagination import KeysetCursorPagination
from core.views import TenantScopedViewSet
from integrations.services import sync_whatsapp_templates
from messaging.models import Broadcast, Message, MessageTemplate
from messaging.selectors import (
    get_broadcasts_queryset,
    get_last_inbound_whatsapp_at,
    get_message_templates_queryset,
    get_messages_queryset,
    is_whatsapp_window_open,
)
from messaging.serializers import (
    BroadcastProgressSerializer,
    BroadcastScheduleSerializer,
    BroadcastSerializer,
    EmailUnsubscribeConfirmSerializer,
    EmailUnsubscribeResponseSerializer,
    MessageSerializer,
    MessageTemplateSerializer,
    SMSSegmentCountRequestSerializer,
    SMSSegmentCountResponseSerializer,
    SendMessageSerializer,
    TemplatePreviewRequestSerializer,
    TemplatePreviewResponseSerializer,
    WhatsAppWindowStatusSerializer,
)
from messaging.services import (
    MessagingService,
    calculate_sms_segments,
    cancel_broadcast,
    confirm_email_unsubscribe,
    create_broadcast,
    create_message_template,
    delete_message_template,
    get_broadcast_progress,
    preview_template_for_contact,
    schedule_broadcast,
    update_broadcast,
    update_message_template,
)


class MessageTemplateViewSet(TenantScopedViewSet):
    serializer_class = MessageTemplateSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdminOrReadOnly]
    queryset = MessageTemplate.unscoped_objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return MessageTemplate.unscoped_objects.none()
        channel = self.request.query_params.get("channel")
        active_param = self.request.query_params.get("active")
        approval = self.request.query_params.get("approval_status")
        active = None
        if active_param is not None:
            active = active_param.strip().lower() in ("true", "1", "yes")
        return get_message_templates_queryset(
            channel=channel,
            active=active,
            approval_status=approval,
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        template = create_message_template(
            channel=data["channel"],
            name=data["name"],
            subject=data.get("subject", ""),
            body=data["body"],
            variables=data.get("variables"),
            active=data.get("active", True),
        )
        return Response(
            MessageTemplateSerializer(template, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        updated = update_message_template(
            template=instance,
            name=data.get("name"),
            subject=data.get("subject"),
            body=data.get("body"),
            variables=data.get("variables"),
            active=data.get("active"),
        )
        return Response(
            MessageTemplateSerializer(updated, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        delete_message_template(template=instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        request=None,
        responses={200: MessageTemplateSerializer(many=True)},
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="sync",
        permission_classes=[IsAuthenticated, IsOwnerOrAdmin],
    )
    def sync_whatsapp(self, request):
        templates = sync_whatsapp_templates()
        return Response(
            MessageTemplateSerializer(templates, many=True).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="messaging_template_preview_detail",
        request=TemplatePreviewRequestSerializer,
        responses={200: TemplatePreviewResponseSerializer},
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="preview",
        permission_classes=[IsAuthenticated, IsTenantMember],
    )
    def preview_detail(self, request, pk=None):
        template = self.get_object()
        serializer = TemplatePreviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        contact = data["contact"]
        if not CanAccessContactObject().has_object_permission(request, self, contact):
            self.permission_denied(request)
        result = preview_template_for_contact(
            contact=contact,
            template=template,
            extra_context=data.get("extra_context"),
        )
        return Response(result, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="messaging_template_preview_adhoc",
        request=TemplatePreviewRequestSerializer,
        responses={200: TemplatePreviewResponseSerializer},
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="preview",
        permission_classes=[IsAuthenticated, IsTenantMember],
    )
    def preview_adhoc(self, request):
        serializer = TemplatePreviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        contact = data["contact"]
        if not CanAccessContactObject().has_object_permission(request, self, contact):
            self.permission_denied(request)
        result = preview_template_for_contact(
            contact=contact,
            template=data.get("template"),
            channel=data.get("channel", ""),
            subject=data.get("subject", ""),
            body=data.get("body", ""),
            extra_context=data.get("extra_context"),
        )
        return Response(result, status=status.HTTP_200_OK)


class BroadcastViewSet(TenantScopedViewSet):
    serializer_class = BroadcastSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    queryset = Broadcast.unscoped_objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Broadcast.unscoped_objects.none()
        status_filter = self.request.query_params.get("status")
        return get_broadcasts_queryset(status_filter=status_filter)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        broadcast = create_broadcast(
            name=data.get("name", ""),
            template=data["template"],
            audience_filter=data.get("audience_filter"),
            scheduled_at=data.get("scheduled_at"),
            created_by=request.user,
        )
        return Response(
            BroadcastSerializer(broadcast, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        updated = update_broadcast(
            broadcast=instance,
            name=data.get("name"),
            template=data.get("template"),
            audience_filter=data.get("audience_filter"),
            scheduled_at=data.get("scheduled_at"),
        )
        return Response(
            BroadcastSerializer(updated, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status not in (Broadcast.Status.DRAFT, Broadcast.Status.CANCELLED):
            raise ApplicationError(
                detail="Only draft or cancelled broadcasts can be deleted.",
                code="broadcast_not_deletable",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        request=BroadcastScheduleSerializer,
        responses={200: BroadcastSerializer},
    )
    @action(detail=True, methods=["post"], url_path="schedule")
    def schedule(self, request, pk=None):
        instance = self.get_object()
        serializer = BroadcastScheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        scheduled = schedule_broadcast(
            broadcast=instance,
            scheduled_at=serializer.validated_data.get("scheduled_at"),
        )
        return Response(
            BroadcastSerializer(scheduled, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=None,
        responses={200: BroadcastSerializer},
    )
    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        instance = self.get_object()
        cancelled = cancel_broadcast(broadcast=instance)
        return Response(
            BroadcastSerializer(cancelled, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=None,
        responses={200: BroadcastProgressSerializer},
    )
    @action(detail=True, methods=["get"], url_path="progress")
    def progress(self, request, pk=None):
        instance = self.get_object()
        return Response(get_broadcast_progress(instance), status=status.HTTP_200_OK)


class MessageViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated, IsTenantMember]
    pagination_class = KeysetCursorPagination
    queryset = Message.unscoped_objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Message.unscoped_objects.none()
        params = self.request.query_params
        return get_messages_queryset(
            membership=getattr(self.request, "membership", None),
            contact_id=params.get("contact") or params.get("contact_id"),
            channel=params.get("channel"),
            direction=params.get("direction"),
            status_filter=params.get("status"),
            broadcast_id=params.get("broadcast") or params.get("broadcast_id"),
        )

    @extend_schema(
        request=SendMessageSerializer,
        responses={201: MessageSerializer},
    )
    @action(detail=False, methods=["post"], url_path="send")
    def send_message(self, request):
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        contact = data["contact"]
        if not CanAccessContactObject().has_object_permission(request, self, contact):
            self.permission_denied(request)
        message = MessagingService.send(
            contact=contact,
            channel=data["channel"],
            body=data.get("body", ""),
            subject=data.get("subject", ""),
            template=data.get("template"),
            source=data.get("source", Message.Source.MANUAL),
            sent_by=request.user,
            idempotency_key=data.get("idempotency_key"),
            extra_context=data.get("extra_context"),
        )
        return Response(
            MessageSerializer(message, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )


class SMSSegmentCountView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    @extend_schema(
        request=SMSSegmentCountRequestSerializer,
        responses={200: SMSSegmentCountResponseSerializer},
    )
    def post(self, request):
        serializer = SMSSegmentCountRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = calculate_sms_segments(serializer.validated_data.get("body", ""))
        return Response(result, status=status.HTTP_200_OK)


class WhatsAppWindowStatusView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    @extend_schema(responses={200: WhatsAppWindowStatusSerializer})
    def get(self, request, contact_id):
        contact = get_contact_by_id(
            contact_id,
            membership=getattr(request, "membership", None),
        )
        if contact is None:
            raise ApplicationError(
                detail="Contact not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        window_open = is_whatsapp_window_open(contact)
        last_inbound = get_last_inbound_whatsapp_at(contact)
        return Response(
            {
                "contact_id": str(contact.id),
                "window_open": window_open,
                "last_inbound_at": last_inbound,
                "template_required": not window_open,
            },
            status=status.HTTP_200_OK,
        )


class EmailUnsubscribeConfirmView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [WebhookSignatureAbuseThrottle]

    @extend_schema(responses={200: EmailUnsubscribeResponseSerializer})
    def get(self, request, token: str | None = None):
        content_length = request.META.get("CONTENT_LENGTH")
        if content_length:
            try:
                if int(content_length) > 5 * 1024 * 1024:
                    return Response(
                        {"detail": "Request payload exceeds size limit."},
                        status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    )
            except (ValueError, TypeError):
                pass
        effective_token = token or request.query_params.get("token", "")
        try:
            contact = confirm_email_unsubscribe(effective_token)
        except ApplicationError as exc:
            if exc.code == "invalid_unsubscribe_token":
                return record_webhook_signature_failure(
                    request,
                    detail=exc.detail,
                    status_code=exc.status_code,
                )
            raise exc
        return Response(
            {
                "status": contact.email_subscription,
                "contact_id": str(contact.id),
                "email": contact.email,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=EmailUnsubscribeConfirmSerializer,
        responses={200: EmailUnsubscribeResponseSerializer},
    )
    def post(self, request, token: str | None = None):
        content_length = request.META.get("CONTENT_LENGTH")
        if content_length:
            try:
                if int(content_length) > 5 * 1024 * 1024:
                    return Response(
                        {"detail": "Request payload exceeds size limit."},
                        status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    )
            except (ValueError, TypeError):
                pass
        try:
            body = getattr(request, "_body", None) or getattr(request, "body", None)
            if body and len(body) > 5 * 1024 * 1024:
                return Response(
                    {"detail": "Request payload exceeds size limit."},
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )
        except Exception:
            pass
        effective_token = token
        if not effective_token:
            serializer = EmailUnsubscribeConfirmSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            effective_token = serializer.validated_data["token"]
        try:
            contact = confirm_email_unsubscribe(effective_token)
        except ApplicationError as exc:
            if exc.code == "invalid_unsubscribe_token":
                return record_webhook_signature_failure(
                    request,
                    detail=exc.detail,
                    status_code=exc.status_code,
                )
            raise exc
        return Response(
            {
                "status": contact.email_subscription,
                "contact_id": str(contact.id),
                "email": contact.email,
            },
            status=status.HTTP_200_OK,
        )
