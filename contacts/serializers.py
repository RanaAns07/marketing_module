from rest_framework import serializers
from accounts.models import Membership
from contacts.models import (
    Activity,
    Appointment,
    Contact,
    CustomFieldDefinition,
    LeadStatus,
    Tag,
    Task,
)
from core.serializers import TenantScopedPrimaryKeyRelatedField, TenantScopedSerializer


class LeadStatusSerializer(TenantScopedSerializer):
    class Meta:
        model = LeadStatus
        fields = [
            "id",
            "tenant_id",
            "name",
            "order",
            "is_default",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "tenant_id", "created_at", "updated_at"]


class TagSerializer(TenantScopedSerializer):
    class Meta:
        model = Tag
        fields = [
            "id",
            "tenant_id",
            "name",
            "color",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "tenant_id", "created_at", "updated_at"]


class CustomFieldDefinitionSerializer(TenantScopedSerializer):
    class Meta:
        model = CustomFieldDefinition
        fields = [
            "id",
            "tenant_id",
            "key",
            "label",
            "field_type",
            "options",
            "is_required",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "tenant_id", "created_at", "updated_at"]


class ContactAssigneeSummarySerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user_id", "email", "full_name", "role"]
        read_only_fields = fields


class ContactSerializer(TenantScopedSerializer):
    lead_status = TenantScopedPrimaryKeyRelatedField(
        queryset=LeadStatus.unscoped_objects.all(),
        required=False,
        allow_null=True,
    )
    lead_status_detail = LeadStatusSerializer(source="lead_status", read_only=True)
    assigned_to = TenantScopedPrimaryKeyRelatedField(
        queryset=Membership.unscoped_objects.all(),
        required=False,
        allow_null=True,
    )
    assigned_to_detail = ContactAssigneeSummarySerializer(source="assigned_to", read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    tag_ids = serializers.ListField(
        child=serializers.CharField(),
        write_only=True,
        required=False,
    )

    class Meta:
        model = Contact
        fields = [
            "id",
            "tenant_id",
            "first_name",
            "last_name",
            "full_name",
            "email",
            "phone",
            "city",
            "country",
            "timezone",
            "lead_status",
            "lead_status_detail",
            "assigned_to",
            "assigned_to_detail",
            "source_channel",
            "source_page",
            "source_form",
            "campaign",
            "ad_set",
            "ad",
            "acquired_at",
            "converted_at",
            "external_id",
            "last_activity_at",
            "email_subscription",
            "sms_subscription",
            "whatsapp_subscription",
            "custom_fields",
            "tags",
            "tag_ids",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "tenant_id",
            "acquired_at",
            "converted_at",
            "last_activity_at",
            "lead_status_detail",
            "assigned_to_detail",
            "tags",
            "created_at",
            "updated_at",
        ]


class TagListActionSerializer(serializers.Serializer):
    tags = serializers.ListField(
        child=serializers.CharField(),
        allow_empty=False,
    )


class BulkTagActionSerializer(serializers.Serializer):
    contact_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
    )
    tags = serializers.ListField(
        child=serializers.CharField(),
        allow_empty=False,
    )


class BulkAssignActionSerializer(TenantScopedSerializer):
    contact_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
    )
    assigned_to = TenantScopedPrimaryKeyRelatedField(
        queryset=Membership.unscoped_objects.all(),
        allow_null=True,
    )

    class Meta:
        model = Contact
        fields = ["contact_ids", "assigned_to"]


class ChangeLeadStatusSerializer(TenantScopedSerializer):
    lead_status = TenantScopedPrimaryKeyRelatedField(
        queryset=LeadStatus.unscoped_objects.all(),
        allow_null=False,
    )

    class Meta:
        model = Contact
        fields = ["lead_status"]


class AssignContactSerializer(TenantScopedSerializer):
    assigned_to = TenantScopedPrimaryKeyRelatedField(
        queryset=Membership.unscoped_objects.all(),
        allow_null=True,
    )

    class Meta:
        model = Contact
        fields = ["assigned_to"]


class SubscriptionUpdateSerializer(serializers.Serializer):
    email_subscription = serializers.ChoiceField(
        choices=Contact.SubscriptionStatus.choices,
        required=False,
    )
    sms_subscription = serializers.ChoiceField(
        choices=Contact.SubscriptionStatus.choices,
        required=False,
    )
    whatsapp_subscription = serializers.ChoiceField(
        choices=Contact.SubscriptionStatus.choices,
        required=False,
    )


class AddNoteSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=5000)


class ActivitySerializer(TenantScopedSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True, default=None)
    actor_name = serializers.CharField(source="actor.full_name", read_only=True, default=None)

    class Meta:
        model = Activity
        fields = [
            "id",
            "tenant_id",
            "contact_id",
            "actor_id",
            "actor_email",
            "actor_name",
            "activity_type",
            "description",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields


class AppointmentSerializer(TenantScopedSerializer):
    contact = TenantScopedPrimaryKeyRelatedField(
        queryset=Contact.unscoped_objects.all(),
        required=False,
    )

    class Meta:
        model = Appointment
        fields = [
            "id",
            "tenant_id",
            "contact",
            "starts_at",
            "status",
            "external_ref",
            "location",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "tenant_id", "created_at", "updated_at"]


class AppointmentRescheduleSerializer(serializers.Serializer):
    starts_at = serializers.DateTimeField()
    location = serializers.CharField(max_length=255, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class TaskSerializer(TenantScopedSerializer):
    contact = TenantScopedPrimaryKeyRelatedField(
        queryset=Contact.unscoped_objects.all(),
        required=False,
        allow_null=True,
    )
    assignee = TenantScopedPrimaryKeyRelatedField(
        queryset=Membership.unscoped_objects.all(),
        required=False,
        allow_null=True,
    )
    assignee_detail = ContactAssigneeSummarySerializer(source="assignee", read_only=True)

    class Meta:
        model = Task
        fields = [
            "id",
            "tenant_id",
            "contact",
            "assignee",
            "assignee_detail",
            "title",
            "description",
            "due_at",
            "status",
            "created_by_id",
            "from_workflow",
            "completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "tenant_id",
            "assignee_detail",
            "created_by_id",
            "completed_at",
            "created_at",
            "updated_at",
        ]
