from typing import Any
import uuid
from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from accounts.models import Membership
from contacts.models import Contact, LeadStatus, Tag
from core.context import require_tenant_id
from core.models import Tenant
from core.serializers import TenantScopedPrimaryKeyRelatedField, TenantScopedSerializer
from inbox.models import AssignmentRule, Conversation, ConversationNote
from inbox.selectors import get_contact_active_enrollments, get_whatsapp_window_state
from messaging.models import Channel, MessageTemplate


class InboxAssigneeSummarySerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id",
            "user_id",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "role",
            "is_available",
        ]
        read_only_fields = fields


class InboxLeadStatusSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = LeadStatus
        fields = ["id", "name", "order", "is_default"]
        read_only_fields = fields


class InboxTagSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name", "color"]
        read_only_fields = fields


class InboxContactSummarySerializer(serializers.ModelSerializer):
    lead_status = InboxLeadStatusSummarySerializer(read_only=True)
    tags = InboxTagSummarySerializer(many=True, read_only=True)
    assigned_to = InboxAssigneeSummarySerializer(read_only=True)
    active_enrollments = serializers.SerializerMethodField()
    whether_whatsapp_window_open = serializers.SerializerMethodField()
    whatsapp_window_open = serializers.SerializerMethodField()
    window_expires_at = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = [
            "id",
            "first_name",
            "last_name",
            "full_name",
            "email",
            "phone",
            "city",
            "country",
            "timezone",
            "lead_status",
            "tags",
            "source_channel",
            "source_page",
            "source_form",
            "campaign",
            "ad_set",
            "ad",
            "acquired_at",
            "converted_at",
            "last_activity_at",
            "assigned_to",
            "email_subscription",
            "sms_subscription",
            "whatsapp_subscription",
            "custom_fields",
            "active_enrollments",
            "whether_whatsapp_window_open",
            "whatsapp_window_open",
            "window_expires_at",
        ]
        read_only_fields = fields

    def _get_window_cache(self, obj: Contact) -> dict[str, Any]:
        cache_attr = f"_wa_win_{obj.pk}"
        cached = getattr(self, cache_attr, None)
        if cached is None:
            cached = get_whatsapp_window_state(obj)
            setattr(self, cache_attr, cached)
        return cached

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_active_enrollments(self, obj: Contact) -> list[dict[str, Any]]:
        return get_contact_active_enrollments(obj)

    def get_whether_whatsapp_window_open(self, obj: Contact) -> bool:
        return bool(self._get_window_cache(obj)["whether_whatsapp_window_open"])

    def get_whatsapp_window_open(self, obj: Contact) -> bool:
        return bool(self._get_window_cache(obj)["whatsapp_window_open"])

    def get_window_expires_at(self, obj: Contact) -> str | None:
        return self._get_window_cache(obj)["window_expires_at"]


class ConversationSerializer(TenantScopedSerializer):
    contact = InboxContactSummarySerializer(read_only=True)
    contact_id = serializers.UUIDField(read_only=True)
    assigned_to = InboxAssigneeSummarySerializer(read_only=True)
    assigned_to_id = serializers.UUIDField(read_only=True, allow_null=True)
    whether_whatsapp_window_open = serializers.SerializerMethodField()
    whatsapp_window_open = serializers.SerializerMethodField()
    window_expires_at = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "tenant_id",
            "contact",
            "contact_id",
            "status",
            "assigned_to",
            "assigned_to_id",
            "last_message_at",
            "last_inbound_at",
            "last_channel",
            "last_message_preview",
            "unread_count",
            "resolved_at",
            "whether_whatsapp_window_open",
            "whatsapp_window_open",
            "window_expires_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def _get_window_cache(self, obj: Conversation) -> dict[str, Any]:
        cache_attr = f"_conv_wa_win_{obj.contact_id}"
        cached = getattr(self, cache_attr, None)
        if cached is None:
            cached = get_whatsapp_window_state(obj.contact)
            setattr(self, cache_attr, cached)
        return cached

    def get_whether_whatsapp_window_open(self, obj: Conversation) -> bool:
        return bool(self._get_window_cache(obj)["whether_whatsapp_window_open"])

    def get_whatsapp_window_open(self, obj: Conversation) -> bool:
        return bool(self._get_window_cache(obj)["whatsapp_window_open"])

    def get_window_expires_at(self, obj: Conversation) -> str | None:
        return self._get_window_cache(obj)["window_expires_at"]


class ConversationCreateSerializer(serializers.Serializer):
    contact = TenantScopedPrimaryKeyRelatedField(
        queryset=Contact.unscoped_objects.all(),
        required=False,
    )
    contact_id = TenantScopedPrimaryKeyRelatedField(
        queryset=Contact.unscoped_objects.all(),
        source="contact",
        required=False,
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs.get("contact"):
            raise serializers.ValidationError({"contact_id": "contact or contact_id is required."})
        return attrs


class ConversationNoteSerializer(TenantScopedSerializer):
    conversation_id = serializers.UUIDField(read_only=True)
    author = serializers.SerializerMethodField()

    class Meta:
        model = ConversationNote
        fields = [
            "id",
            "tenant_id",
            "conversation_id",
            "author",
            "body",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "tenant_id", "conversation_id", "author", "created_at", "updated_at"]

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_author(self, obj: ConversationNote) -> dict[str, Any] | None:
        if obj.author is None:
            return None
        return {
            "id": str(obj.author.id),
            "email": obj.author.email,
            "first_name": obj.author.first_name,
            "last_name": obj.author.last_name,
            "full_name": obj.author.full_name,
        }


class ConversationNoteCreateSerializer(serializers.Serializer):
    body = serializers.CharField()


class ConversationReplySerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=Channel.choices)
    body = serializers.CharField(required=False, allow_blank=True, default="")
    subject = serializers.CharField(required=False, allow_blank=True, default="")
    template = TenantScopedPrimaryKeyRelatedField(
        queryset=MessageTemplate.unscoped_objects.all(),
        required=False,
        allow_null=True,
    )
    template_id = TenantScopedPrimaryKeyRelatedField(
        queryset=MessageTemplate.unscoped_objects.all(),
        source="template",
        required=False,
        allow_null=True,
    )
    status = serializers.ChoiceField(
        choices=Conversation.Status.choices,
        required=False,
        allow_null=True,
    )
    idempotency_key = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        default="",
    )
    extra_context = serializers.DictField(required=False, default=dict)


class ConversationStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Conversation.Status.choices)


class ConversationAssignSerializer(serializers.Serializer):
    assigned_to = serializers.UUIDField(required=False, allow_null=True)
    assignee = serializers.UUIDField(required=False, allow_null=True)
    membership_id = serializers.UUIDField(required=False, allow_null=True)
    user_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        tenant_id = require_tenant_id()
        raw_id = (
            attrs.get("assigned_to")
            or attrs.get("assignee")
            or attrs.get("membership_id")
            or attrs.get("user_id")
        )
        if raw_id is None:
            attrs["resolved_membership"] = None
            return attrs

        cross_tenant_member = (
            Membership.unscoped_objects.filter(pk=raw_id)
            .exclude(tenant_id=tenant_id)
            .exists()
        )
        if cross_tenant_member:
            raise serializers.ValidationError(
                {"assigned_to": "Cross-tenant foreign key reference is forbidden."}
            )

        member = (
            Membership.objects.select_related("user", "tenant")
            .filter(
                Q(pk=raw_id) | Q(user_id=raw_id),
                is_active=True,
                user__is_active=True,
            )
            .first()
        )
        if member is None:
            raise serializers.ValidationError(
                {"assigned_to": "Active team member not found in this tenant."}
            )
        attrs["resolved_membership"] = member
        return attrs


class ConversationContactStatusSerializer(serializers.Serializer):
    lead_status = TenantScopedPrimaryKeyRelatedField(
        queryset=LeadStatus.unscoped_objects.all(),
        required=False,
    )
    lead_status_id = TenantScopedPrimaryKeyRelatedField(
        queryset=LeadStatus.unscoped_objects.all(),
        source="lead_status",
        required=False,
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs.get("lead_status"):
            raise serializers.ValidationError(
                {"lead_status": "lead_status or lead_status_id is required."}
            )
        return attrs


class ConversationContactTagsSerializer(serializers.Serializer):
    add_tags = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )
    remove_tags = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )
    tags = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )


class AssignmentRuleSerializer(TenantScopedSerializer):
    assignee = TenantScopedPrimaryKeyRelatedField(
        queryset=Membership.unscoped_objects.all()
    )
    assignee_detail = InboxAssigneeSummarySerializer(source="assignee", read_only=True)
    match_tag = TenantScopedPrimaryKeyRelatedField(
        queryset=Tag.unscoped_objects.all(),
        required=False,
        allow_null=True,
    )
    match_tag_detail = InboxTagSummarySerializer(source="match_tag", read_only=True)

    class Meta:
        model = AssignmentRule
        fields = [
            "id",
            "tenant_id",
            "name",
            "priority",
            "match_page",
            "match_form",
            "match_tag",
            "match_tag_name",
            "match_tag_detail",
            "assignee",
            "assignee_detail",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "tenant_id",
            "assignee_detail",
            "match_tag_detail",
            "created_at",
            "updated_at",
        ]
        validators = []

    def to_internal_value(self, data: Any) -> dict[str, Any]:
        if isinstance(data, dict):
            mutable = dict(data)
            if "page" in mutable and "match_page" not in mutable:
                mutable["match_page"] = mutable.pop("page")
            if "form" in mutable and "match_form" not in mutable:
                mutable["match_form"] = mutable.pop("form")
            if "tag" in mutable and "match_tag" not in mutable and "match_tag_name" not in mutable:
                tag_val = mutable.pop("tag")
                if tag_val:
                    try:
                        uuid.UUID(str(tag_val).strip())
                        mutable["match_tag"] = tag_val
                    except ValueError:
                        mutable["match_tag_name"] = str(tag_val).strip()
            elif "match_tag" in mutable and mutable["match_tag"] is not None:
                raw_mt = str(mutable["match_tag"]).strip()
                if raw_mt:
                    try:
                        uuid.UUID(raw_mt)
                    except ValueError:
                        mutable["match_tag_name"] = raw_mt
                        mutable["match_tag"] = None
            data = mutable
        return super().to_internal_value(data)


class TenantAssignmentSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = [
            "assignment_strategy",
            "handoff_add_interested_tag",
            "marketer_can_view_all_contacts",
            "notify_admins_on_new_lead",
            "assignment_mode",
        ]
        read_only_fields = ["assignment_mode"]


class InboxUnreadCountsSerializer(serializers.Serializer):
    total_conversations = serializers.IntegerField()
    unread_conversations = serializers.IntegerField()
    unread_messages = serializers.IntegerField()
    unassigned = serializers.DictField()
    by_status = serializers.DictField()
    by_assignee = serializers.ListField(child=serializers.DictField())


class TeamWorkloadSerializer(serializers.Serializer):
    members = serializers.ListField(child=serializers.DictField())
    unassigned = serializers.DictField()
