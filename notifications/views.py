from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from accounts.permissions import IsTenantMember
from notifications.models import Notification
from notifications.selectors import (
    get_unread_notifications_count,
    get_user_notification_by_id,
    get_user_notifications_queryset,
)
from notifications.serializers import (
    NotificationPreferenceSerializer,
    NotificationSerializer,
)
from notifications.services import (
    mark_all_notifications_read,
    mark_notification_read,
    update_notification_preferences,
)


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated, IsTenantMember]
    lookup_value_regex = "[0-9a-f-]{36}"
    filterset_fields = ["is_read", "notification_type"]
    ordering_fields = ["created_at", "read_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.unscoped_objects.none()
        return get_user_notifications_queryset(self.request.user)

    def get_object(self):
        notification = get_user_notification_by_id(self.request.user, self.kwargs["pk"])
        if notification is None:
            raise NotFound("Notification not found.")
        return notification

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = get_unread_notifications_count(request.user)
        return Response({"unread_count": count}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        updated = mark_notification_read(notification=notification)
        return Response(NotificationSerializer(updated).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        updated_count = mark_all_notifications_read(user=request.user)
        return Response({"updated_count": updated_count}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get", "patch"], url_path="preferences")
    def preferences(self, request):
        membership = request.membership
        if request.method == "GET":
            return Response(
                {
                    "email_notifications_enabled": membership.email_notifications_enabled,
                    "notification_preferences": membership.notification_preferences or {},
                }
            )
        serializer = NotificationPreferenceSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = update_notification_preferences(
            membership=membership,
            email_notifications_enabled=serializer.validated_data.get("email_notifications_enabled"),
            notification_preferences=serializer.validated_data.get("notification_preferences"),
        )
        return Response(
            {
                "email_notifications_enabled": updated.email_notifications_enabled,
                "notification_preferences": updated.notification_preferences or {},
            }
        )
