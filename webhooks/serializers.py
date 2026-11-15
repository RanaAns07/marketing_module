from drf_spectacular.utils import OpenApiExample, extend_schema_serializer
from rest_framework import serializers
from contacts.models import Appointment
from core.serializers import TenantScopedSerializer
from webhooks.models import ExternalEvent, WebhookDelivery, WebhookEndpoint


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Fitverx Contact Upsert Example",
            value={
                "external_id": "fitverx_usr_9081",
                "first_name": "Sara",
                "last_name": "Malik",
                "email": "sara.malik@example.com",
                "phone": "+14155550199",
                "city": "San Francisco",
                "country": "US",
                "timezone": "America/Los_Angeles",
                "tags": ["Fitverx Trial", "Fitness"],
                "custom_fields": {
                    "fitness_goal": "Weight Loss",
                    "plan_tier": "Pro",
                },
            },
            request_only=True,
        )
    ]
)
class ExternalContactUpsertSerializer(serializers.Serializer):
    external_id = serializers.CharField(max_length=255, required=False, allow_blank=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    full_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    city = serializers.CharField(max_length=120, required=False, allow_blank=True)
    country = serializers.CharField(max_length=120, required=False, allow_blank=True)
    timezone = serializers.CharField(max_length=64, required=False, default="UTC")
    campaign = serializers.CharField(max_length=255, required=False, allow_blank=True)
    ad_set = serializers.CharField(max_length=255, required=False, allow_blank=True)
    ad = serializers.CharField(max_length=255, required=False, allow_blank=True)
    tags = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        default=list,
    )
    custom_fields = serializers.DictField(required=False, default=dict)


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "External Contact Tags Example",
            value={
                "tags": ["VIP", "Onboarding Complete"],
                "action": "add",
            },
            request_only=True,
        )
    ]
)
class ExternalContactTagsSerializer(serializers.Serializer):
    tags = serializers.ListField(
        child=serializers.CharField(max_length=100),
        allow_empty=False,
    )
    action = serializers.ChoiceField(
        choices=[("add", "Add"), ("remove", "Remove")],
        required=False,
        default="add",
    )


class ExternalAppointmentContactLookupSerializer(serializers.Serializer):
    external_id = serializers.CharField(max_length=255, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    full_name = serializers.CharField(max_length=255, required=False, allow_blank=True)


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Create External Appointment Example",
            value={
                "contact_external_id": "fitverx_usr_9081",
                "starts_at": "2026-12-01T15:00:00Z",
                "external_ref": "appt_fx_301",
                "location": "Zoom Consultation Room",
                "notes": "Initial fitness assessment call",
            },
            request_only=True,
        )
    ]
)
class ExternalAppointmentCreateSerializer(serializers.Serializer):
    contact_id = serializers.UUIDField(required=False)
    contact_external_id = serializers.CharField(max_length=255, required=False, allow_blank=True)
    contact = ExternalAppointmentContactLookupSerializer(required=False)
    starts_at = serializers.DateTimeField()
    external_ref = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    location = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Update External Appointment Status Example",
            value={
                "status": "rescheduled",
                "starts_at": "2026-12-02T17:30:00Z",
                "location": "Studio B",
                "notes": "Moved by client request",
            },
            request_only=True,
        )
    ]
)
class ExternalAppointmentUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=Appointment.Status.choices,
        required=True,
    )
    starts_at = serializers.DateTimeField(required=False)
    location = serializers.CharField(max_length=255, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class ExternalEventContactPayloadSerializer(serializers.Serializer):
    external_id = serializers.CharField(max_length=255, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    full_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    city = serializers.CharField(max_length=120, required=False, allow_blank=True)
    country = serializers.CharField(max_length=120, required=False, allow_blank=True)
    timezone = serializers.CharField(max_length=64, required=False, allow_blank=True)
    tags = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
    )
    custom_fields = serializers.DictField(required=False)

    def validate(self, attrs):
        ext_id = str(attrs.get("external_id") or "").strip()
        email = str(attrs.get("email") or "").strip()
        phone = str(attrs.get("phone") or "").strip()
        if not ext_id and not email and not phone:
            raise serializers.ValidationError(
                "At least one contact identifier (external_id, email, or phone) is required."
            )
        return attrs


class ExternalAutomationDirectiveSerializer(serializers.Serializer):
    action = serializers.ChoiceField(
        choices=[
            ("trigger", "Trigger"),
            ("pause", "Pause"),
            ("stop", "Stop"),
            ("complete", "Complete"),
            ("update", "Update"),
        ]
    )
    workflow = serializers.CharField(max_length=255, required=False, allow_blank=True)
    workflow_id = serializers.UUIDField(required=False)
    step_key = serializers.CharField(max_length=100, required=False, allow_blank=True)
    delay_minutes = serializers.IntegerField(required=False, min_value=0)
    contact = serializers.DictField(required=False)
    custom_fields = serializers.DictField(required=False)
    tags = serializers.ListField(child=serializers.CharField(), required=False)


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Package Purchased Event with Automation Directives",
            value={
                "event_id": "evt_fx_20261115_001",
                "type": "package.purchased",
                "occurred_at": "2026-11-15T12:00:00Z",
                "contact": {
                    "external_id": "fitverx_usr_9081",
                    "email": "sara.malik@example.com",
                    "phone": "+14155550199",
                    "first_name": "Sara",
                    "last_name": "Malik",
                },
                "data": {
                    "package": "12-Week Transformation",
                    "plan": "Annual Pro",
                    "amount": 499.00,
                    "currency": "USD",
                    "automation": [
                        {"action": "stop", "workflow": "Lead Follow-Up Sequence"},
                        {"action": "trigger", "workflow": "Post-Purchase Onboarding"},
                    ],
                },
            },
            request_only=True,
        )
    ]
)
class ExternalEventEnvelopeSerializer(serializers.Serializer):
    event_id = serializers.CharField(max_length=255)
    type = serializers.CharField(max_length=100)
    occurred_at = serializers.DateTimeField()
    contact = ExternalEventContactPayloadSerializer()
    data = serializers.DictField(required=False, default=dict)
    automation = serializers.ListField(
        child=ExternalAutomationDirectiveSerializer(),
        required=False,
    )


class ExternalEventIngestResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    idempotent_duplicate = serializers.BooleanField()
    external_event_id = serializers.UUIDField()
    event_id = serializers.CharField()
    type = serializers.CharField()
    event_status = serializers.ChoiceField(choices=ExternalEvent.Status.choices)
    result = serializers.DictField()


class ExternalEventSerializer(TenantScopedSerializer):
    api_key_name = serializers.CharField(source="api_key.name", read_only=True, default=None)
    contact_email = serializers.EmailField(source="contact.email", read_only=True, default=None)
    contact_external_id = serializers.CharField(source="contact.external_id", read_only=True, default=None)

    class Meta:
        model = ExternalEvent
        fields = [
            "id",
            "tenant_id",
            "api_key",
            "api_key_name",
            "event_id",
            "type",
            "occurred_at",
            "payload",
            "status",
            "contact",
            "contact_email",
            "contact_external_id",
            "result",
            "error",
            "attempts",
            "processed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class WebhookEndpointSerializer(TenantScopedSerializer):
    class Meta:
        model = WebhookEndpoint
        fields = [
            "id",
            "tenant_id",
            "name",
            "url",
            "subscribed_events",
            "active",
            "failure_count",
            "disabled_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "tenant_id",
            "failure_count",
            "disabled_at",
            "created_at",
            "updated_at",
        ]


class WebhookEndpointSecretResponseSerializer(WebhookEndpointSerializer):
    signing_secret = serializers.CharField(read_only=True)

    class Meta(WebhookEndpointSerializer.Meta):
        fields = WebhookEndpointSerializer.Meta.fields + ["signing_secret"]
        read_only_fields = WebhookEndpointSerializer.Meta.read_only_fields + ["signing_secret"]


class WebhookDeliverySerializer(TenantScopedSerializer):
    endpoint_name = serializers.CharField(source="endpoint.name", read_only=True)
    endpoint_url = serializers.CharField(source="endpoint.url", read_only=True)

    class Meta:
        model = WebhookDelivery
        fields = [
            "id",
            "tenant_id",
            "endpoint",
            "endpoint_name",
            "endpoint_url",
            "event_name",
            "event_id",
            "payload",
            "status",
            "attempts",
            "response_status",
            "response_excerpt",
            "next_retry_at",
            "delivered_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
