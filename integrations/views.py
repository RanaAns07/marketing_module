from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from accounts.permissions import IsOwnerOrAdmin
from core.views import TenantScopedViewSet
from integrations.models import ChannelConnection
from integrations.selectors import get_channel_connections_queryset
from integrations.serializers import (
    ChannelConnectionSerializer,
    ChannelConnectionTestResultSerializer,
)
from integrations.services import (
    create_channel_connection,
    sync_whatsapp_templates,
    test_channel_connection,
    update_channel_connection,
)
from messaging.serializers import MessageTemplateSerializer


class ChannelConnectionViewSet(TenantScopedViewSet):
    serializer_class = ChannelConnectionSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    queryset = ChannelConnection.unscoped_objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ChannelConnection.unscoped_objects.none()
        return get_channel_connections_queryset()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        connection = create_channel_connection(
            channel=data["channel"],
            provider=data.get("provider", ""),
            credentials=data.get("credentials"),
            sender_identity=data["sender_identity"],
            sender_name=data.get("sender_name", ""),
            status_value=data.get("status", ChannelConnection.Status.ACTIVE),
        )
        return Response(
            ChannelConnectionSerializer(connection, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        updated = update_channel_connection(
            connection=instance,
            provider=data.get("provider"),
            credentials=data.get("credentials"),
            sender_identity=data.get("sender_identity"),
            sender_name=data.get("sender_name"),
            status_value=data.get("status"),
        )
        return Response(
            ChannelConnectionSerializer(updated, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(request=None, responses={200: ChannelConnectionTestResultSerializer})
    @action(detail=True, methods=["post"], url_path="test")
    def test(self, request, pk=None):
        connection = self.get_object()
        result = test_channel_connection(connection=connection)
        return Response(result, status=status.HTTP_200_OK)

    @extend_schema(request=None, responses={200: ChannelConnectionTestResultSerializer})
    @action(detail=True, methods=["post"], url_path="test-connection")
    def test_connection_action(self, request, pk=None):
        connection = self.get_object()
        result = test_channel_connection(connection=connection)
        return Response(result, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="integrations_connection_sync_whatsapp_templates",
        request=None,
        responses={200: MessageTemplateSerializer(many=True)},
    )
    @action(detail=True, methods=["post"], url_path="sync-whatsapp-templates")
    def sync_whatsapp(self, request, pk=None):
        connection = self.get_object()
        templates = sync_whatsapp_templates(connection=connection)
        return Response(
            MessageTemplateSerializer(templates, many=True).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="integrations_sync_whatsapp_templates",
        request=None,
        responses={200: MessageTemplateSerializer(many=True)},
    )
    @action(detail=False, methods=["post"], url_path="sync-whatsapp-templates")
    def sync_whatsapp_collection(self, request):
        templates = sync_whatsapp_templates()
        return Response(
            MessageTemplateSerializer(templates, many=True).data,
            status=status.HTTP_200_OK,
        )
