from rest_framework import serializers
from core.constants import COST_ESTIMATE_DISCLAIMER
from core.serializers import TenantScopedSerializer
from usage.models import RateCard, UsageRecord


class UsageRecordSerializer(TenantScopedSerializer):
    message_id = serializers.UUIDField(source="message.id", read_only=True)
    is_estimate = serializers.BooleanField(default=True, read_only=True)
    estimate = serializers.BooleanField(default=True, read_only=True)
    disclaimer = serializers.CharField(default=COST_ESTIMATE_DISCLAIMER, read_only=True)

    class Meta:
        model = UsageRecord
        fields = [
            "id",
            "tenant_id",
            "message_id",
            "channel",
            "direction",
            "quantity",
            "destination_country",
            "whatsapp_category",
            "unit_cost",
            "total_cost",
            "currency",
            "status",
            "is_estimate",
            "estimate",
            "disclaimer",
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
            "direction",
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
    outbound_count = serializers.IntegerField(required=False)
    inbound_count = serializers.IntegerField(required=False)
    sms_segments = serializers.IntegerField()
    outbound_segments = serializers.IntegerField(required=False)
    inbound_segments = serializers.IntegerField(required=False)
    delivered_count = serializers.IntegerField()
    failed_count = serializers.IntegerField()
    bounced_count = serializers.IntegerField()
    estimated_cost = serializers.FloatField()
    outbound_cost = serializers.FloatField(required=False)
    inbound_cost = serializers.FloatField(required=False)
    currency = serializers.CharField()
    is_estimate = serializers.BooleanField(default=True, required=False)
    estimate = serializers.BooleanField(default=True, required=False)
    disclaimer = serializers.CharField(default=COST_ESTIMATE_DISCLAIMER, required=False)


class UsageSummarySerializer(serializers.Serializer):
    period = serializers.CharField()
    is_estimate = serializers.BooleanField(default=True)
    estimate = serializers.BooleanField(default=True)
    disclaimer = serializers.CharField()
    currency = serializers.CharField()
    total_messages = serializers.IntegerField()
    total_outbound_messages = serializers.IntegerField(required=False)
    total_inbound_messages = serializers.IntegerField(required=False)
    total_sms_segments = serializers.IntegerField()
    total_outbound_sms_segments = serializers.IntegerField(required=False)
    total_inbound_sms_segments = serializers.IntegerField(required=False)
    total_delivered = serializers.IntegerField()
    total_failed = serializers.IntegerField()
    total_bounced = serializers.IntegerField()
    total_estimated_cost = serializers.FloatField()
    total_outbound_cost = serializers.FloatField(required=False)
    total_inbound_cost = serializers.FloatField(required=False)
    by_channel = serializers.DictField(child=ChannelUsageSummarySerializer())
    delivery_outcomes = serializers.DictField(child=serializers.IntegerField())
