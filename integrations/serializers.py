from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from core.serializers import TenantScopedSerializer
from integrations.models import ChannelConnection


class ChannelConnectionSerializer(TenantScopedSerializer):
    credentials = serializers.DictField(
        write_only=True,
        required=False,
        default=dict,
    )
    has_credentials = serializers.SerializerMethodField()
    configured_credential_keys = serializers.SerializerMethodField()
    webhook_path = serializers.SerializerMethodField()

    class Meta:
        model = ChannelConnection
        fields = [
            "id",
            "channel",
            "provider",
            "credentials",
            "has_credentials",
            "configured_credential_keys",
            "sender_identity",
            "sender_name",
            "webhook_token",
            "webhook_path",
            "status",
            "last_checked_at",
            "last_error",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "has_credentials",
            "configured_credential_keys",
            "webhook_token",
            "webhook_path",
            "last_checked_at",
            "last_error",
            "created_at",
            "updated_at",
        ]

    @extend_schema_field(serializers.BooleanField())
    def get_has_credentials(self, obj: ChannelConnection) -> bool:
        return bool(obj.credentials)

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_configured_credential_keys(self, obj: ChannelConnection) -> list[str]:
        if isinstance(obj.credentials, dict):
            return sorted(str(k) for k in obj.credentials.keys())
        return []

    @extend_schema_field(serializers.CharField())
    def get_webhook_path(self, obj: ChannelConnection) -> str:
        if obj.channel == ChannelConnection.Channel.EMAIL:
            return f"/webhooks/sendgrid/{obj.webhook_token}/"
        return f"/webhooks/twilio/{obj.channel}/"


class ChannelConnectionTestResultSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    status = serializers.CharField()
    message = serializers.CharField()
    last_checked_at = serializers.DateTimeField()
