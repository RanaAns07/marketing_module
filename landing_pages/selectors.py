from typing import Any
import uuid
from django.db.models import Count, Q, QuerySet
from rest_framework import status as status_codes
from core.exceptions import ApplicationError
from landing_pages.models import FormSubmission, LandingPage, LandingPageTemplate


def get_landing_pages_queryset(filters: dict[str, Any] | None = None) -> QuerySet[LandingPage]:
    qs = (
        LandingPage.objects.select_related("template", "published_by__user", "target_workflow")
        .annotate(
            annotated_packages_count=Count("packages", distinct=True),
            annotated_sections_count=Count("sections", distinct=True),
        )
        .prefetch_related("packages", "sections")
        .all()
    )
    if not filters:
        return qs
    status_filter = filters.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)
    search = filters.get("search")
    if search:
        qs = qs.filter(Q(title__icontains=search) | Q(slug__icontains=search))
    return qs


def get_landing_page_by_id(page_id: str | uuid.UUID) -> LandingPage | None:
    try:
        parsed = uuid.UUID(str(page_id).strip())
    except (ValueError, TypeError):
        return None
    return (
        LandingPage.objects.select_related("template", "published_by__user", "target_workflow")
        .prefetch_related("packages", "sections", "form__default_tags")
        .filter(pk=parsed)
        .first()
    )


def get_landing_page_by_slug(slug: str) -> LandingPage | None:
    clean_slug = str(slug).strip()
    if not clean_slug:
        return None
    return (
        LandingPage.objects.select_related("template", "published_by__user", "target_workflow")
        .prefetch_related("packages", "sections", "form__default_tags")
        .filter(slug=clean_slug)
        .first()
    )


def get_public_landing_page_by_slug_or_id(
    val: str,
    tenant_identifier: str | None = None,
    preview_token: str | None = None,
) -> LandingPage | None:
    from django.db.models import Prefetch
    from contacts.models import Tag
    from packages.models import Package
    from landing_pages.models import LandingPageSection

    clean_val = str(val).strip()
    if not clean_val:
        return None

    qs = (
        LandingPage.unscoped_objects.select_related("tenant", "template", "target_workflow", "form")
        .prefetch_related(
            Prefetch("packages", queryset=Package.unscoped_objects.all()),
            Prefetch("sections", queryset=LandingPageSection.unscoped_objects.all()),
            Prefetch("form__default_tags", queryset=Tag.unscoped_objects.all()),
        )
    )

    t_filter = None
    if tenant_identifier and str(tenant_identifier).strip():
        clean_tenant = str(tenant_identifier).strip()
        try:
            t_uuid = uuid.UUID(clean_tenant)
            t_filter = Q(tenant_id=t_uuid)
        except (ValueError, TypeError):
            t_filter = Q(tenant__slug__iexact=clean_tenant)

    try:
        parsed = uuid.UUID(clean_val)
        page_qs = qs.filter(pk=parsed)
        if t_filter:
            page_qs = page_qs.filter(t_filter)
        page = page_qs.first()
    except (ValueError, TypeError):
        if t_filter:
            page = qs.filter(t_filter, slug=clean_val).first()
        else:
            matching = list(qs.filter(slug=clean_val)[:2])
            if len(matching) > 1:
                raise ApplicationError(
                    detail=f"Multiple landing pages found with slug '{clean_val}'. Please specify the tenant in the URL path or tenant parameter.",
                    code="ambiguous_slug_tenant_required",
                    status_code=status_codes.HTTP_400_BAD_REQUEST,
                )
            page = matching[0] if matching else None

    if page is None:
        return None

    if page.status == LandingPage.Status.PUBLISHED:
        return page

    if preview_token and page.preview_token and secrets_match(preview_token, page.preview_token):
        return page

    return None


def secrets_match(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(str(a).strip().encode("utf-8"), str(b).strip().encode("utf-8"))


def get_landing_templates_queryset(tenant_id: uuid.UUID | None = None) -> QuerySet[LandingPageTemplate]:
    if tenant_id:
        return LandingPageTemplate.objects.filter(Q(tenant_id=tenant_id) | Q(is_system=True), is_active=True)
    return LandingPageTemplate.objects.filter(is_active=True)


def get_form_submissions_queryset(page_id: str | uuid.UUID | None = None) -> QuerySet[FormSubmission]:
    qs = FormSubmission.objects.select_related("page", "form", "contact").all()
    if page_id:
        try:
            parsed = uuid.UUID(str(page_id).strip())
            qs = qs.filter(page_id=parsed)
        except (ValueError, TypeError):
            pass
    return qs
