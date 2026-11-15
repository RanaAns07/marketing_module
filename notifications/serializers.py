from rest_framework import serializers
from notifications.models import Notification, NotificationType


class NotificationSerializer(serializers.ModelSerializer):
    notification_type_display = serializers.CharField(
        source="get_notification_type_display",
        read_only=True,
    )

    class Meta:
        model = Notification
        fields = [
            "id",
            "tenant_id",
            "recipient_id",
            "notification_type",
            "notification_type_display",
            "title",
            "body",
            "payload",
            "link",
            "is_read",
            "read_at",
            "email_sent",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class NotificationPreferenceSerializer(serializers.Serializer):
    email_notifications_enabled = serializers.BooleanField(required=False)
    notification_preferences = serializers.DictField(required=False)

    def validate_notification_preferences(self, value):
        valid_types = set(NotificationType.values)
        invalid = set(value.keys()) - valid_types
        if invalid:
            raise serializers.ValidationError(
                f"Invalid notification types: {', '.join(sorted(invalid))}"
            )
        return value
