from rest_framework import serializers
from core.serializers import TenantScopedSerializer
from landing_pages.models import (
    FormSubmission,
    LandingPage,
    LandingPageForm,
    LandingPageSection,
    LandingPageTemplate,
)
from packages.serializers import PackageSerializer


class LandingPageTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LandingPageTemplate
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "thumbnail_url",
            "category",
            "default_sections",
            "default_theme",
            "is_system",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "is_system", "created_at", "updated_at"]


class LandingPageSectionSerializer(TenantScopedSerializer):
    class Meta:
        model = LandingPageSection
        fields = [
            "id",
            "page",
            "section_type",
            "order",
            "title",
            "subtitle",
            "content",
            "is_visible",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class LandingPageFormSerializer(TenantScopedSerializer):
    class Meta:
        model = LandingPageForm
        fields = [
            "id",
            "page",
            "title",
            "subtitle",
            "submit_button_text",
            "fields",
            "success_action",
            "success_message",
            "redirect_url",
            "default_tags",
            "assignee",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class LandingPageListSerializer(TenantScopedSerializer):
    template_name = serializers.CharField(source="template.name", read_only=True, default=None)
    published_by_name = serializers.CharField(source="published_by.user.full_name", read_only=True, default=None)
    packages_count = serializers.IntegerField(source="annotated_packages_count", read_only=True, default=0)
    sections_count = serializers.IntegerField(source="annotated_sections_count", read_only=True, default=0)

    class Meta:
        model = LandingPage
        fields = [
            "id",
            "title",
            "slug",
            "status",
            "template",
            "template_name",
            "seo_title",
            "published_at",
            "published_by_name",
            "view_count",
            "submission_count",
            "packages_count",
            "sections_count",
            "created_at",
            "updated_at",
        ]


class LandingPageDetailSerializer(TenantScopedSerializer):
    template = LandingPageTemplateSerializer(read_only=True)
    sections = LandingPageSectionSerializer(many=True, read_only=True)
    form = LandingPageFormSerializer(read_only=True)
    packages = PackageSerializer(many=True, read_only=True)
    published_by_name = serializers.CharField(source="published_by.user.full_name", read_only=True, default=None)
    target_workflow_name = serializers.CharField(source="target_workflow.name", read_only=True, default=None)

    class Meta:
        model = LandingPage
        fields = [
            "id",
            "title",
            "slug",
            "status",
            "template",
            "seo_title",
            "seo_description",
            "og_image_url",
            "favicon_url",
            "theme_config",
            "packages",
            "target_workflow",
            "target_workflow_name",
            "settings",
            "preview_token",
            "published_at",
            "published_by_name",
            "view_count",
            "submission_count",
            "sections",
            "form",
            "created_at",
            "updated_at",
        ]


class LandingPageCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    slug = serializers.SlugField(max_length=150, required=False, allow_blank=True)
    template_id = serializers.UUIDField(required=False, allow_null=True)
    seo_title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    seo_description = serializers.CharField(required=False, allow_blank=True)
    og_image_url = serializers.URLField(max_length=500, required=False, allow_blank=True)
    favicon_url = serializers.URLField(max_length=500, required=False, allow_blank=True)
    theme_config = serializers.DictField(required=False, default=dict)
    settings = serializers.DictField(required=False, default=dict)
    package_ids = serializers.ListField(child=serializers.UUIDField(), required=False, default=list)
    target_workflow_id = serializers.UUIDField(required=False, allow_null=True)
    sections = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    form = serializers.DictField(required=False, default=dict)


class LandingPageUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    slug = serializers.SlugField(max_length=150, required=False)
    seo_title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    seo_description = serializers.CharField(required=False, allow_blank=True)
    og_image_url = serializers.URLField(max_length=500, required=False, allow_blank=True)
    favicon_url = serializers.URLField(max_length=500, required=False, allow_blank=True)
    theme_config = serializers.DictField(required=False)
    settings = serializers.DictField(required=False)
    package_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    target_workflow_id = serializers.UUIDField(required=False, allow_null=True)
    form = serializers.DictField(required=False)


class SectionReorderItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    order = serializers.IntegerField(min_value=0)


class SectionReorderSerializer(serializers.Serializer):
    sections = serializers.ListField(child=SectionReorderItemSerializer(), allow_empty=False)


class PublicPackageItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    external_id = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    price = serializers.DecimalField(max_digits=10, decimal_places=2)
    currency = serializers.CharField()
    billing_interval = serializers.CharField()
    features = serializers.ListField(child=serializers.CharField())
    external_purchase_url = serializers.URLField(allow_blank=True)


class PublicSectionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    section_type = serializers.CharField()
    order = serializers.IntegerField()
    title = serializers.CharField(allow_blank=True)
    subtitle = serializers.CharField(allow_blank=True)
    content = serializers.DictField()


class PublicFormSerializer(serializers.Serializer):
    title = serializers.CharField()
    subtitle = serializers.CharField(allow_blank=True)
    submit_button_text = serializers.CharField()
    fields = serializers.ListField(child=serializers.DictField())
    success_action = serializers.CharField()
    success_message = serializers.CharField()
    redirect_url = serializers.CharField(allow_blank=True)


class PublicLandingPageSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    slug = serializers.CharField()
    seo_title = serializers.CharField()
    seo_description = serializers.CharField()
    og_image_url = serializers.URLField(allow_blank=True)
    favicon_url = serializers.URLField(allow_blank=True)
    logo_url = serializers.CharField(allow_blank=True, required=False, default="")
    icon_url = serializers.CharField(allow_blank=True, required=False, default="")
    theme_config = serializers.DictField()
    settings = serializers.DictField()
    sections = PublicSectionSerializer(many=True)
    form = PublicFormSerializer(allow_null=True)
    packages = PublicPackageItemSerializer(many=True)


class PublicFormSubmitSerializer(serializers.Serializer):
    form_data = serializers.DictField()
    utm_source = serializers.CharField(required=False, allow_blank=True, default="")
    utm_medium = serializers.CharField(required=False, allow_blank=True, default="")
    utm_campaign = serializers.CharField(required=False, allow_blank=True, default="")
    utm_content = serializers.CharField(required=False, allow_blank=True, default="")
    utm_term = serializers.CharField(required=False, allow_blank=True, default="")
    referrer_url = serializers.CharField(required=False, allow_blank=True, default="")
    landing_page_url = serializers.CharField(required=False, allow_blank=True, default="")
    _hp = serializers.CharField(required=False, allow_blank=True, default="")


class PublicFormSubmitResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    submission_id = serializers.UUIDField(allow_null=True)
    contact_id = serializers.UUIDField(allow_null=True)
    action = serializers.CharField()
    message = serializers.CharField()
    redirect_url = serializers.CharField(allow_blank=True)


class FormSubmissionSerializer(TenantScopedSerializer):
    page_title = serializers.CharField(source="page.title", read_only=True, default=None)
    contact_email = serializers.CharField(source="contact.email", read_only=True, default=None)
    contact_name = serializers.CharField(source="contact.full_name", read_only=True, default=None)

    class Meta:
        model = FormSubmission
        fields = [
            "id",
            "page",
            "page_title",
            "form",
            "contact",
            "contact_email",
            "contact_name",
            "form_data",
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_content",
            "utm_term",
            "referrer_url",
            "landing_page_url",
            "created_at",
        ]
        read_only_fields = fields
