from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from contacts.models import Contact
from core.serializers import TenantScopedPrimaryKeyRelatedField, TenantScopedSerializer
from messaging.models import Broadcast, Channel, Message, MessageTemplate


class MessageTemplateSerializer(TenantScopedSerializer):
    is_read_only = serializers.SerializerMethodField()

    class Meta:
        model = MessageTemplate
        fields = [
            "id",
            "channel",
            "name",
            "subject",
            "body",
            "variables",
            "whatsapp_content_sid",
            "whatsapp_category",
            "approval_status",
            "active",
            "is_read_only",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "whatsapp_content_sid",
            "whatsapp_category",
            "approval_status",
            "is_read_only",
            "created_at",
            "updated_at",
        ]

    @extend_schema_field(serializers.BooleanField())
    def get_is_read_only(self, obj: MessageTemplate) -> bool:
        return obj.channel == Channel.WHATSAPP


class TemplatePreviewRequestSerializer(serializers.Serializer):
    contact_id = TenantScopedPrimaryKeyRelatedField(
        queryset=Contact.unscoped_objects.all(),
        source="contact",
    )
    template_id = TenantScopedPrimaryKeyRelatedField(
        queryset=MessageTemplate.unscoped_objects.all(),
        source="template",
        required=False,
        allow_null=True,
    )
    channel = serializers.ChoiceField(
        choices=Channel.choices,
        required=False,
        default=Channel.EMAIL,
    )
    subject = serializers.CharField(required=False, allow_blank=True, default="")
    body = serializers.CharField(required=False, allow_blank=True, default="")
    extra_context = serializers.DictField(required=False, default=dict)


class SMSSegmentCountRequestSerializer(serializers.Serializer):
    body = serializers.CharField(allow_blank=True, default="")


class SMSSegmentCountResponseSerializer(serializers.Serializer):
    encoding = serializers.CharField()
    char_count = serializers.IntegerField()
    effective_length = serializers.IntegerField()
    segments = serializers.IntegerField()
    per_segment_limit = serializers.IntegerField()
    remaining_in_segment = serializers.IntegerField()
    has_special_characters = serializers.BooleanField()


class TemplatePreviewResponseSerializer(serializers.Serializer):
    channel = serializers.CharField()
    subject = serializers.CharField()
    body = serializers.CharField()
    variables = serializers.ListField(child=serializers.CharField())
    sms_segments = SMSSegmentCountResponseSerializer(required=False)


class MessageSerializer(TenantScopedSerializer):
    class Meta:
        model = Message
        fields = [
            "id",
            "contact",
            "channel",
            "direction",
            "status",
            "body",
            "subject",
            "provider",
            "provider_message_id",
            "segments",
            "destination_country",
            "whatsapp_category",
            "source",
            "template",
            "broadcast",
            "sent_by",
            "error",
            "idempotency_key",
            "sent_at",
            "delivered_at",
            "read_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SendMessageSerializer(serializers.Serializer):
    contact = TenantScopedPrimaryKeyRelatedField(
        queryset=Contact.unscoped_objects.all(),
    )
    channel = serializers.ChoiceField(choices=Channel.choices)
    body = serializers.CharField(required=False, allow_blank=True, default="")
    subject = serializers.CharField(required=False, allow_blank=True, default="")
    template = TenantScopedPrimaryKeyRelatedField(
        queryset=MessageTemplate.unscoped_objects.all(),
        required=False,
        allow_null=True,
    )
    source = serializers.ChoiceField(
        choices=Message.Source.choices,
        required=False,
        default=Message.Source.MANUAL,
    )
    idempotency_key = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=255,
    )
    extra_context = serializers.DictField(required=False, default=dict)


class BroadcastSerializer(TenantScopedSerializer):
    channel = serializers.CharField(source="template.channel", read_only=True)
    template_name = serializers.CharField(source="template.name", read_only=True)

    class Meta:
        model = Broadcast
        fields = [
            "id",
            "name",
            "template",
            "template_name",
            "channel",
            "audience_filter",
            "scheduled_at",
            "started_at",
            "completed_at",
            "status",
            "total_recipients",
            "sent_count",
            "delivered_count",
            "failed_count",
            "skipped_count",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "template_name",
            "channel",
            "started_at",
            "completed_at",
            "status",
            "total_recipients",
            "sent_count",
            "delivered_count",
            "failed_count",
            "skipped_count",
            "created_by",
            "created_at",
            "updated_at",
        ]


class BroadcastScheduleSerializer(serializers.Serializer):
    scheduled_at = serializers.DateTimeField(required=False, allow_null=True)


class BroadcastProgressSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    channel = serializers.CharField()
    status = serializers.CharField()
    scheduled_at = serializers.DateTimeField(allow_null=True)
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    total_recipients = serializers.IntegerField()
    sent_count = serializers.IntegerField()
    delivered_count = serializers.IntegerField()
    failed_count = serializers.IntegerField()
    skipped_count = serializers.IntegerField()
    progress_percentage = serializers.FloatField()


class WhatsAppWindowStatusSerializer(serializers.Serializer):
    contact_id = serializers.UUIDField()
    window_open = serializers.BooleanField()
    last_inbound_at = serializers.DateTimeField(allow_null=True)
    template_required = serializers.BooleanField()


class EmailUnsubscribeConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()


class EmailUnsubscribeResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    contact_id = serializers.UUIDField()
    email = serializers.EmailField(allow_null=True)
