from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from accounts.permissions import IsTenantMember
from core.context import require_tenant
from core.views import TenantScopedViewSet
from landing_pages.models import (
    FormSubmission,
    LandingPage,
    LandingPageSection,
    LandingPageTemplate,
)
from landing_pages.selectors import (
    get_form_submissions_queryset,
    get_landing_page_by_id,
    get_landing_pages_queryset,
    get_landing_templates_queryset,
)
from landing_pages.serializers import (
    FormSubmissionSerializer,
    LandingPageCreateSerializer,
    LandingPageDetailSerializer,
    LandingPageListSerializer,
    LandingPageSectionSerializer,
    LandingPageTemplateSerializer,
    LandingPageUpdateSerializer,
    SectionReorderSerializer,
)
from landing_pages.services import (
    create_landing_page,
    duplicate_landing_page,
    generate_new_preview_token,
    publish_landing_page,
    reorder_sections,
    unpublish_landing_page,
    update_landing_page,
)


class LandingPageTemplateViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsTenantMember]
    serializer_class = LandingPageTemplateSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return LandingPageTemplate.objects.none()
        tenant = getattr(self.request, "tenant", None)
        return get_landing_templates_queryset(tenant.id if tenant else None)

    def perform_create(self, serializer):
        tenant = require_tenant()
        serializer.save(tenant=tenant, is_system=False)


class LandingPageViewSet(TenantScopedViewSet):
    def get_serializer_class(self):
        if self.action == "list":
            return LandingPageListSerializer
        if self.action == "create":
            return LandingPageCreateSerializer
        if self.action in ("update", "partial_update"):
            return LandingPageUpdateSerializer
        return LandingPageDetailSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return LandingPage.unscoped_objects.none()
        return get_landing_pages_queryset(self.request.query_params)

    @extend_schema(summary="List all landing pages")
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        summary="Create a new landing page",
        request=LandingPageCreateSerializer,
        responses={201: LandingPageDetailSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = LandingPageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data

        tmpl = None
        if v.get("template_id"):
            tenant = require_tenant()
            from django.db.models import Q
            from core.exceptions import ApplicationError
            tmpl = LandingPageTemplate.objects.filter(
                Q(tenant=tenant) | Q(is_system=True),
                pk=v["template_id"],
                is_active=True,
            ).first()
            if tmpl is None:
                raise ApplicationError(
                    detail="Template not found or access denied.",
                    code="template_not_found",
                    status_code=status.HTTP_404_NOT_FOUND,
                )

        page = create_landing_page(
            title=v["title"],
            slug=v.get("slug") or None,
            template=tmpl,
            seo_title=v.get("seo_title", ""),
            seo_description=v.get("seo_description", ""),
            og_image_url=v.get("og_image_url", ""),
            favicon_url=v.get("favicon_url", ""),
            theme_config=v.get("theme_config"),
            settings=v.get("settings"),
            package_ids=v.get("package_ids"),
            target_workflow_id=v.get("target_workflow_id"),
            sections_data=v.get("sections"),
            form_data=v.get("form"),
        )
        return Response(
            LandingPageDetailSerializer(page, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(summary="Retrieve landing page details", responses={200: LandingPageDetailSerializer})
    def retrieve(self, request, *args, **kwargs):
        page = self.get_object()
        return Response(
            LandingPageDetailSerializer(page, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        summary="Update landing page details",
        request=LandingPageUpdateSerializer,
        responses={200: LandingPageDetailSerializer},
    )
    def update(self, request, *args, **kwargs):
        page = self.get_object()
        serializer = LandingPageUpdateSerializer(data=request.data, partial=kwargs.get("partial", False))
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data

        updated = update_landing_page(
            page=page,
            title=v.get("title"),
            slug=v.get("slug"),
            seo_title=v.get("seo_title"),
            seo_description=v.get("seo_description"),
            og_image_url=v.get("og_image_url"),
            favicon_url=v.get("favicon_url"),
            theme_config=v.get("theme_config"),
            settings=v.get("settings"),
            package_ids=v.get("package_ids"),
            target_workflow_id=v.get("target_workflow_id"),
            form_data=v.get("form"),
        )
        return Response(
            LandingPageDetailSerializer(updated, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        summary="Publish a landing page",
        request=None,
        responses={200: LandingPageDetailSerializer},
    )
    @action(detail=True, methods=["post"], url_path="publish")
    def publish(self, request, pk=None):
        page = self.get_object()
        membership = getattr(request, "membership", None)
        published = publish_landing_page(page=page, user_membership=membership)
        return Response(
            LandingPageDetailSerializer(published, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        summary="Unpublish a landing page",
        request=None,
        responses={200: LandingPageDetailSerializer},
    )
    @action(detail=True, methods=["post"], url_path="unpublish")
    def unpublish(self, request, pk=None):
        page = self.get_object()
        unpublished = unpublish_landing_page(page=page)
        return Response(
            LandingPageDetailSerializer(unpublished, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        summary="Duplicate a landing page",
        request=None,
        responses={201: LandingPageDetailSerializer},
    )
    @action(detail=True, methods=["post"], url_path="duplicate")
    def duplicate(self, request, pk=None):
        page = self.get_object()
        membership = getattr(request, "membership", None)
        duplicated = duplicate_landing_page(page=page, user_membership=membership)
        return Response(
            LandingPageDetailSerializer(duplicated, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="Rotate preview token for draft page",
        request=None,
        responses={200: LandingPageDetailSerializer},
    )
    @action(detail=True, methods=["post"], url_path="preview-token")
    def preview_token(self, request, pk=None):
        page = self.get_object()
        generate_new_preview_token(page=page)
        return Response(
            LandingPageDetailSerializer(page, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        summary="Reorder page sections",
        request=SectionReorderSerializer,
        responses={200: LandingPageDetailSerializer},
    )
    @action(detail=True, methods=["post"], url_path="reorder-sections")
    def reorder_sections_action(self, request, pk=None):
        page = self.get_object()
        serializer = SectionReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reorder_sections(page=page, section_order=serializer.validated_data["sections"])
        refreshed = get_landing_page_by_id(page.id) or page
        return Response(
            LandingPageDetailSerializer(refreshed, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class LandingPageSectionViewSet(TenantScopedViewSet):
    serializer_class = LandingPageSectionSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return LandingPageSection.unscoped_objects.none()
        qs = LandingPageSection.objects.all()
        page_id = self.request.query_params.get("page")
        if page_id:
            qs = qs.filter(page_id=page_id)
        return qs


class FormSubmissionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated, IsTenantMember]
    serializer_class = FormSubmissionSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return FormSubmission.unscoped_objects.none()
        page_id = self.request.query_params.get("page")
        return get_form_submissions_queryset(page_id=page_id)
