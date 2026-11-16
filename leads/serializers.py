from rest_framework import serializers
from contacts.models import Tag
from contacts.serializers import TagSerializer
from core.serializers import TenantScopedPrimaryKeyRelatedField, TenantScopedSerializer
from leads.models import MetaConnection, MetaLeadEvent, MetaLeadForm, MetaPage
from leads.services import validate_form_field_mapping


class MetaOAuthConnectQuerySerializer(serializers.Serializer):
    redirect_uri = serializers.URLField(required=False, allow_blank=True, default="")


class MetaOAuthConnectResponseSerializer(serializers.Serializer):
    connect_url = serializers.URLField()
    state = serializers.CharField()


class MetaOAuthCallbackSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=2048)
    state = serializers.CharField(max_length=2048)
    redirect_uri = serializers.URLField(required=False, allow_blank=True, default="")


class MetaConnectionSerializer(TenantScopedSerializer):
    is_token_configured = serializers.SerializerMethodField()

    class Meta:
        model = MetaConnection
        fields = [
            "id",
            "meta_user_id",
            "meta_user_name",
            "status",
            "token_expires_at",
            "is_token_configured",
            "last_checked_at",
            "last_error",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_is_token_configured(self, obj: MetaConnection) -> bool:
        return bool(obj.user_access_token)


class MetaPageSerializer(TenantScopedSerializer):
    class Meta:
        model = MetaPage
        fields = [
            "id",
            "connection",
            "page_id",
            "name",
            "instagram_business_account_id",
            "selected",
            "subscribed",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class MetaPageSelectSerializer(serializers.Serializer):
    page_ids = serializers.ListField(
        child=serializers.CharField(max_length=128),
        allow_empty=True,
        required=False,
    )
    page_id = serializers.CharField(max_length=128, required=False)

    def validate(self, attrs):
        if "page_ids" not in attrs and "page_id" in attrs:
            attrs["page_ids"] = [attrs["page_id"]]
        if "page_ids" not in attrs:
            attrs["page_ids"] = []
        return attrs


class MetaLeadFormSerializer(TenantScopedSerializer):
    page_name = serializers.CharField(source="page.name", read_only=True)
    meta_page_id = serializers.CharField(source="page.page_id", read_only=True)
    default_tags = TagSerializer(many=True, read_only=True)

    class Meta:
        model = MetaLeadForm
        fields = [
            "id",
            "page",
            "page_name",
            "meta_page_id",
            "form_id",
            "name",
            "platform",
            "status",
            "selected",
            "default_tags",
            "questions",
            "field_mapping",
            "last_synced_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class MetaFormSelectSerializer(serializers.Serializer):
    form_ids = serializers.ListField(
        child=serializers.CharField(max_length=128),
        allow_empty=True,
    )
    page_id = serializers.CharField(
        max_length=128,
        required=False,
        allow_blank=True,
        default="",
    )


class MetaFormQuestionsResponseSerializer(serializers.Serializer):
    form_id = serializers.CharField()
    meta_form_id = serializers.CharField()
    name = serializers.CharField()
    questions = serializers.ListField(child=serializers.DictField())
    field_mapping = serializers.DictField(child=serializers.CharField())
    suggested_mapping = serializers.DictField(child=serializers.CharField())


class MetaFormUpdateSerializer(serializers.Serializer):
    field_mapping = serializers.DictField(
        child=serializers.CharField(allow_blank=True),
        required=False,
    )
    default_tags = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        allow_empty=True,
    )
    default_tag_ids = TenantScopedPrimaryKeyRelatedField(
        queryset=Tag.unscoped_objects.all(),
        many=True,
        required=False,
    )
    selected = serializers.BooleanField(required=False)
    platform = serializers.ChoiceField(
        choices=MetaLeadForm.Platform.choices,
        required=False,
    )

    def validate_field_mapping(self, value: dict) -> dict[str, str]:
        return validate_form_field_mapping(value)


class MetaLeadEventSerializer(TenantScopedSerializer):
    page_name = serializers.CharField(source="page.name", read_only=True, default="")
    form_name = serializers.CharField(source="form.name", read_only=True, default="")

    class Meta:
        model = MetaLeadEvent
        fields = [
            "id",
            "leadgen_id",
            "page",
            "page_name",
            "form",
            "form_name",
            "contact",
            "raw_payload",
            "status",
            "attempts",
            "error",
            "processed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
