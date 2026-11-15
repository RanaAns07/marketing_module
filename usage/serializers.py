from rest_framework import serializers
from core.serializers import TenantScopedSerializer
from usage.models import RateCard, UsageRecord


class UsageRecordSerializer(TenantScopedSerializer):
    message_id = serializers.UUIDField(source="message.id", read_only=True)

    class Meta:
        model = UsageRecord
        fields = [
            "id",
            "tenant_id",
            "message_id",
            "channel",
            "quantity",
            "destination_country",
            "whatsapp_category",
            "unit_cost",
            "total_cost",
            "currency",
            "status",
            "occurred_at",
            "created_at",
        ]
        read_only_fields = fields


class RateCardSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True, default=None)

    class Meta:
        model = RateCard
        fields = [
            "id",
            "tenant_id",
            "channel",
            "destination_country",
            "whatsapp_category",
            "unit_cost",
            "currency",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "tenant_id", "created_at", "updated_at"]


class ChannelUsageSummarySerializer(serializers.Serializer):
    message_count = serializers.IntegerField()
    sms_segments = serializers.IntegerField()
    delivered_count = serializers.IntegerField()
    failed_count = serializers.IntegerField()
    bounced_count = serializers.IntegerField()
    estimated_cost = serializers.FloatField()
    currency = serializers.CharField()


class UsageSummarySerializer(serializers.Serializer):
    period = serializers.CharField()
    is_estimate = serializers.BooleanField(default=True)
    estimate = serializers.BooleanField(default=True)
    disclaimer = serializers.CharField()
    currency = serializers.CharField()
    total_messages = serializers.IntegerField()
    total_sms_segments = serializers.IntegerField()
    total_delivered = serializers.IntegerField()
    total_failed = serializers.IntegerField()
    total_bounced = serializers.IntegerField()
    total_estimated_cost = serializers.FloatField()
    by_channel = serializers.DictField(child=ChannelUsageSummarySerializer())
    delivery_outcomes = serializers.DictField(child=serializers.IntegerField())
