from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from landing_pages.models import LandingPage, LandingPageForm
from landing_pages.selectors import (
    get_public_landing_page_by_slug_or_id,
    secrets_match,
)
from landing_pages.serializers import (
    PublicFormSubmitResponseSerializer,
    PublicFormSubmitSerializer,
    PublicLandingPageSerializer,
)
from landing_pages.services import (
    increment_page_view_count,
    process_public_form_submission,
)
from landing_pages.throttling import LandingPageFormSubmissionThrottle


def _append_utm_to_url(base_url: str, utm_params: dict[str, str]) -> str:
    clean_url = str(base_url).strip()
    if not clean_url:
        return ""
    try:
        parsed = urlparse(clean_url)
        query_dict = dict(parse_qsl(parsed.query))
        for k, v in utm_params.items():
            if v and str(v).strip():
                query_dict[k] = str(v).strip()
        new_query = urlencode(query_dict)
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query,
                parsed.fragment,
            )
        )
    except Exception:
        return clean_url


def _get_client_ip(request) -> str | None:
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        parts = [p.strip() for p in x_forwarded.split(",")]
        if parts:
            return parts[0]
    return request.META.get("REMOTE_ADDR")


class PublicLandingPageView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]

    @extend_schema(
        operation_id="public_landing_page_retrieve",
        summary="Retrieve published landing page by slug or UUID",
        parameters=[
            OpenApiParameter(
                name="tenant",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Tenant slug or UUID for scoped lookup",
            ),
            OpenApiParameter(
                name="preview",
                type=bool,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Set true to view drafts with valid preview_token",
            ),
            OpenApiParameter(
                name="token",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Preview token required when viewing drafts",
            ),
            OpenApiParameter(
                name="utm_source",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
            ),
            OpenApiParameter(
                name="utm_medium",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
            ),
            OpenApiParameter(
                name="utm_campaign",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
            ),
        ],
        responses={200: PublicLandingPageSerializer},
    )
    def get(self, request, slug_or_id: str, tenant_slug_or_id: str | None = None):
        is_preview = str(request.query_params.get("preview") or "").lower() in ("true", "1")
        preview_token = request.query_params.get("token") if is_preview else None
        tenant_ident = tenant_slug_or_id or request.query_params.get("tenant") or request.headers.get("X-Tenant")

        page = get_public_landing_page_by_slug_or_id(
            slug_or_id,
            tenant_identifier=tenant_ident,
            preview_token=preview_token,
        )
        if page is None:
            raise NotFound("Landing page not found.")

        if page.status == LandingPage.Status.PUBLISHED and not is_preview:
            try:
                increment_page_view_count(page.id)
            except Exception:
                pass

        utm_params = {
            "utm_source": str(request.query_params.get("utm_source") or ""),
            "utm_medium": str(request.query_params.get("utm_medium") or ""),
            "utm_campaign": str(request.query_params.get("utm_campaign") or ""),
            "utm_content": str(request.query_params.get("utm_content") or ""),
            "utm_term": str(request.query_params.get("utm_term") or ""),
        }

        packages_data = []
        for pkg in page.packages.all():
            if pkg.status != "active":
                continue
            purchase_url = _append_utm_to_url(pkg.external_url, utm_params)
            packages_data.append(
                {
                    "id": pkg.id,
                    "external_id": pkg.external_id,
                    "name": pkg.name,
                    "description": pkg.description,
                    "price": pkg.price,
                    "currency": pkg.currency,
                    "billing_interval": pkg.billing_interval,
                    "features": pkg.features or [],
                    "external_purchase_url": purchase_url,
                }
            )

        sections_data = []
        visible_sections = sorted(
            [sec for sec in page.sections.all() if sec.is_visible],
            key=lambda s: s.order,
        )
        for sec in visible_sections:
            sec_content = dict(sec.content or {})
            if sec.section_type == "packages" and not sec_content.get("packages"):
                sec_content["packages"] = packages_data
            sections_data.append(
                {
                    "id": sec.id,
                    "section_type": sec.section_type,
                    "order": sec.order,
                    "title": sec.title,
                    "subtitle": sec.subtitle,
                    "content": sec_content,
                }
            )

        form_data = None
        if hasattr(page, "form") and page.form:
            f = page.form
            form_data = {
                "title": f.title,
                "subtitle": f.subtitle,
                "submit_button_text": f.submit_button_text,
                "fields": f.fields or [],
                "success_action": f.success_action,
                "success_message": f.success_message,
                "redirect_url": f.redirect_url,
            }

        response_payload = {
            "id": page.id,
            "title": page.title,
            "slug": page.slug,
            "seo_title": page.seo_title,
            "seo_description": page.seo_description,
            "og_image_url": page.og_image_url,
            "favicon_url": page.favicon_url,
            "theme_config": page.theme_config or {},
            "settings": page.settings or {},
            "sections": sections_data,
            "form": form_data,
            "packages": packages_data,
        }

        return Response(response_payload, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="public_landing_page_direct_submit",
        summary="Submit lead capture form on a landing page",
        request=PublicFormSubmitSerializer,
        responses={200: PublicFormSubmitResponseSerializer},
    )
    def post(self, request, slug_or_id: str, tenant_slug_or_id: str | None = None):
        submit_view = PublicFormSubmitView()
        return submit_view.post(
            request,
            slug_or_id=slug_or_id,
            tenant_slug_or_id=tenant_slug_or_id,
        )


class PublicFormSubmitView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [LandingPageFormSubmissionThrottle]
    parser_classes = [JSONParser]

    @extend_schema(
        operation_id="public_form_submit",
        summary="Submit lead capture form on a landing page",
        request=PublicFormSubmitSerializer,
        responses={200: PublicFormSubmitResponseSerializer},
    )
    def post(
        self,
        request,
        slug_or_id: str | None = None,
        tenant_slug_or_id: str | None = None,
        form_id: str | None = None,
    ):
        is_preview = str(
            request.query_params.get("preview")
            or (request.data.get("preview") if isinstance(request.data, dict) else "")
            or ""
        ).lower() in ("true", "1")
        preview_token = (
            request.query_params.get("token")
            or (request.data.get("token") if isinstance(request.data, dict) else "")
            or None
        )

        page = None
        if slug_or_id:
            tenant_ident = (
                tenant_slug_or_id
                or request.query_params.get("tenant")
                or request.headers.get("X-Tenant")
                or (request.data.get("tenant") if isinstance(request.data, dict) else None)
            )
            page = get_public_landing_page_by_slug_or_id(
                slug_or_id,
                tenant_identifier=tenant_ident,
                preview_token=preview_token,
            )
        elif form_id:
            form = (
                LandingPageForm.unscoped_objects.select_related("page", "tenant")
                .filter(pk=form_id)
                .first()
            )
            if form:
                page = form.page

        if page is None:
            raise NotFound("Landing page or form not found.")

        is_valid_preview = bool(
            is_preview
            and preview_token
            and page.preview_token
            and secrets_match(preview_token, page.preview_token)
        )
        if page.status != LandingPage.Status.PUBLISHED and not is_valid_preview:
            raise NotFound("Landing page or form is not active or accessible.")

        serializer = PublicFormSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data

        client_ip = _get_client_ip(request)
        user_agent = str(request.META.get("HTTP_USER_AGENT") or "")

        attribution = {
            "utm_source": v.get("utm_source", ""),
            "utm_medium": v.get("utm_medium", ""),
            "utm_campaign": v.get("utm_campaign", ""),
            "utm_content": v.get("utm_content", ""),
            "utm_term": v.get("utm_term", ""),
            "referrer_url": v.get("referrer_url", ""),
            "landing_page_url": v.get("landing_page_url", ""),
        }

        outcome = process_public_form_submission(
            page=page,
            form_data=v["form_data"],
            attribution_data=attribution,
            ip_address=client_ip,
            user_agent=user_agent,
            honeypot=v.get("_hp", ""),
        )

        return Response(outcome, status=status.HTTP_200_OK)


class TenantPublicLandingPageView(PublicLandingPageView):
    @extend_schema(
        operation_id="public_tenant_landing_page_retrieve",
        summary="Retrieve published landing page by tenant and slug or UUID",
        parameters=[
            OpenApiParameter(
                name="preview",
                type=bool,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Set true to view drafts with valid preview_token",
            ),
            OpenApiParameter(
                name="token",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Preview token required when viewing drafts",
            ),
            OpenApiParameter(
                name="utm_source",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
            ),
            OpenApiParameter(
                name="utm_medium",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
            ),
            OpenApiParameter(
                name="utm_campaign",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
            ),
        ],
        responses={200: PublicLandingPageSerializer},
    )
    def get(self, request, slug_or_id: str, tenant_slug_or_id: str | None = None):
        return super().get(request, slug_or_id=slug_or_id, tenant_slug_or_id=tenant_slug_or_id)

    @extend_schema(
        operation_id="public_tenant_landing_page_direct_submit",
        summary="Submit lead capture form directly on the tenant landing page URL",
        request=PublicFormSubmitSerializer,
        responses={200: PublicFormSubmitResponseSerializer},
    )
    def post(self, request, slug_or_id: str, tenant_slug_or_id: str | None = None):
        return super().post(request, slug_or_id=slug_or_id, tenant_slug_or_id=tenant_slug_or_id)


class TenantPublicFormSubmitView(PublicFormSubmitView):
    @extend_schema(
        operation_id="public_tenant_form_submit",
        summary="Submit lead capture form on a tenant landing page",
        request=PublicFormSubmitSerializer,
        responses={200: PublicFormSubmitResponseSerializer},
    )
    def post(
        self,
        request,
        slug_or_id: str | None = None,
        tenant_slug_or_id: str | None = None,
        form_id: str | None = None,
    ):
        return super().post(
            request,
            slug_or_id=slug_or_id,
            tenant_slug_or_id=tenant_slug_or_id,
            form_id=form_id,
        )


class DirectFormSubmitView(PublicFormSubmitView):
    @extend_schema(
        operation_id="public_direct_form_submit",
        summary="Submit lead capture form directly by form UUID",
        request=PublicFormSubmitSerializer,
        responses={200: PublicFormSubmitResponseSerializer},
    )
    def post(
        self,
        request,
        slug_or_id: str | None = None,
        tenant_slug_or_id: str | None = None,
        form_id: str | None = None,
    ):
        return super().post(
            request,
            slug_or_id=slug_or_id,
            tenant_slug_or_id=tenant_slug_or_id,
            form_id=form_id,
        )
