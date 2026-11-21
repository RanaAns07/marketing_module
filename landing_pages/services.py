from typing import Any
import uuid
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import status as status_codes
from accounts.models import Membership
from automation.services import EnrollmentService
from contacts.models import Contact
from contacts.services import ContactService, normalize_email, normalize_phone_e164
from core.context import require_tenant, require_tenant_id, tenant_context
from core.events import EventName, publish
from core.exceptions import ApplicationError
from landing_pages.models import (
    FormSubmission,
    LandingPage,
    LandingPageForm,
    LandingPageSection,
    LandingPageTemplate,
    generate_preview_token,
)
from packages.models import Package


def _is_valid_email(val: Any) -> bool:
    if not val or not str(val).strip():
        return False
    try:
        validate_email(str(val).strip())
        return True
    except DjangoValidationError:
        return False


def _generate_unique_page_slug(title: str, tenant_id: uuid.UUID, exclude_id: uuid.UUID | None = None) -> str:
    base_slug = slugify(title) or "page"
    candidate = base_slug
    counter = 1
    qs = LandingPage.unscoped_objects.filter(tenant_id=tenant_id)
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    while qs.filter(slug=candidate).exists():
        candidate = f"{base_slug}-{counter}"
        counter += 1
    return candidate


@transaction.atomic
def create_landing_page(
    *,
    title: str,
    slug: str | None = None,
    template: LandingPageTemplate | None = None,
    seo_title: str = "",
    seo_description: str = "",
    og_image_url: str = "",
    favicon_url: str = "",
    theme_config: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    package_ids: list[str | uuid.UUID] | None = None,
    target_workflow_id: str | uuid.UUID | None = None,
    sections_data: list[dict[str, Any]] | None = None,
    form_data: dict[str, Any] | None = None,
) -> LandingPage:
    tenant = require_tenant()
    tenant_id = tenant.id

    clean_title = str(title).strip()
    if not clean_title:
        raise ApplicationError(
            detail="Landing page title is required.",
            code="missing_page_title",
            status_code=status_codes.HTTP_400_BAD_REQUEST,
        )

    if slug and str(slug).strip():
        final_slug = slugify(str(slug).strip())
        if LandingPage.unscoped_objects.filter(tenant_id=tenant_id, slug=final_slug).exists():
            raise ApplicationError(
                detail=f"A landing page with slug '{final_slug}' already exists in this tenant.",
                code="duplicate_page_slug",
                status_code=status_codes.HTTP_400_BAD_REQUEST,
            )
    else:
        final_slug = _generate_unique_page_slug(clean_title, tenant_id)

    target_wf = None
    if target_workflow_id:
        from automation.models import Workflow
        target_wf = Workflow.objects.filter(pk=target_workflow_id).first()

    page = LandingPage(
        tenant=tenant,
        title=clean_title,
        slug=final_slug,
        status=LandingPage.Status.DRAFT,
        template=template,
        seo_title=seo_title or clean_title,
        seo_description=seo_description or "",
        og_image_url=og_image_url or "",
        favicon_url=favicon_url or "",
        theme_config=theme_config or (template.default_theme if template else {}),
        settings=settings or {},
        target_workflow=target_wf,
        preview_token=generate_preview_token(),
    )
    page.save()

    if package_ids:
        pkgs = Package.objects.filter(pk__in=package_ids)
        page.packages.set(pkgs)

    if sections_data:
        for idx, sec in enumerate(sections_data):
            LandingPageSection.objects.create(
                tenant=tenant,
                page=page,
                section_type=sec.get("section_type", LandingPageSection.SectionType.HERO),
                order=sec.get("order", idx),
                title=sec.get("title", ""),
                subtitle=sec.get("subtitle", ""),
                content=sec.get("content", {}),
                is_visible=sec.get("is_visible", True),
            )
    elif template and template.default_sections:
        for idx, sec in enumerate(template.default_sections):
            LandingPageSection.objects.create(
                tenant=tenant,
                page=page,
                section_type=sec.get("section_type", LandingPageSection.SectionType.HERO),
                order=sec.get("order", idx),
                title=sec.get("title", ""),
                subtitle=sec.get("subtitle", ""),
                content=sec.get("content", {}),
                is_visible=sec.get("is_visible", True),
            )

    default_fields = [
        {"name": "full_name", "label": "Full Name", "type": "text", "required": True, "placeholder": "Your Name"},
        {"name": "email", "label": "Email Address", "type": "email", "required": True, "placeholder": "you@example.com"},
        {"name": "phone", "label": "Phone Number", "type": "phone", "required": False, "placeholder": "+1 234 567 8900"},
    ]

    f_data = form_data or {}
    form = LandingPageForm(
        tenant=tenant,
        page=page,
        title=f_data.get("title", "Get Started"),
        subtitle=f_data.get("subtitle", ""),
        submit_button_text=f_data.get("submit_button_text", "Submit"),
        fields=f_data.get("fields") or default_fields,
        success_action=f_data.get("success_action", LandingPageForm.SuccessAction.MESSAGE),
        success_message=f_data.get("success_message", "Thank you! We will be in touch shortly."),
        redirect_url=f_data.get("redirect_url", ""),
    )
    form.save()

    if f_data.get("default_tags"):
        from contacts.models import Tag
        tags = Tag.objects.filter(pk__in=f_data["default_tags"])
        form.default_tags.set(tags)

    return page


@transaction.atomic
def update_landing_page(
    *,
    page: LandingPage,
    title: str | None = None,
    slug: str | None = None,
    seo_title: str | None = None,
    seo_description: str | None = None,
    og_image_url: str | None = None,
    favicon_url: str | None = None,
    theme_config: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    package_ids: list[str | uuid.UUID] | None = None,
    target_workflow_id: str | uuid.UUID | None = None,
    form_data: dict[str, Any] | None = None,
) -> LandingPage:
    update_fields = ["updated_at"]

    if title is not None:
        clean_title = str(title).strip()
        if not clean_title:
            raise ApplicationError(
                detail="Landing page title cannot be blank.",
                code="invalid_page_title",
                status_code=status_codes.HTTP_400_BAD_REQUEST,
            )
        page.title = clean_title
        update_fields.append("title")

    if slug is not None:
        clean_slug = slugify(str(slug).strip())
        if not clean_slug:
            raise ApplicationError(
                detail="Landing page slug cannot be blank.",
                code="invalid_page_slug",
                status_code=status_codes.HTTP_400_BAD_REQUEST,
            )
        if LandingPage.unscoped_objects.filter(tenant_id=page.tenant_id, slug=clean_slug).exclude(pk=page.pk).exists():
            raise ApplicationError(
                detail=f"A landing page with slug '{clean_slug}' already exists in this tenant.",
                code="duplicate_page_slug",
                status_code=status_codes.HTTP_400_BAD_REQUEST,
            )
        page.slug = clean_slug
        update_fields.append("slug")

    if seo_title is not None:
        page.seo_title = seo_title
        update_fields.append("seo_title")

    if seo_description is not None:
        page.seo_description = seo_description
        update_fields.append("seo_description")

    if og_image_url is not None:
        page.og_image_url = og_image_url
        update_fields.append("og_image_url")

    if favicon_url is not None:
        page.favicon_url = favicon_url
        update_fields.append("favicon_url")

    if theme_config is not None:
        page.theme_config = theme_config
        update_fields.append("theme_config")

    if settings is not None:
        page.settings = settings
        update_fields.append("settings")

    if target_workflow_id is not None:
        if target_workflow_id == "":
            page.target_workflow = None
        else:
            from automation.models import Workflow
            page.target_workflow = Workflow.objects.filter(pk=target_workflow_id).first()
        update_fields.append("target_workflow")

    page.save(update_fields=update_fields)

    if package_ids is not None:
        pkgs = Package.objects.filter(pk__in=package_ids)
        page.packages.set(pkgs)

    if form_data is not None:
        form, _ = LandingPageForm.objects.get_or_create(page=page, defaults={"tenant_id": page.tenant_id})
        f_updates = ["updated_at"]
        if "title" in form_data:
            form.title = form_data["title"]
            f_updates.append("title")
        if "subtitle" in form_data:
            form.subtitle = form_data["subtitle"]
            f_updates.append("subtitle")
        if "submit_button_text" in form_data:
            form.submit_button_text = form_data["submit_button_text"]
            f_updates.append("submit_button_text")
        if "fields" in form_data:
            form.fields = form_data["fields"]
            f_updates.append("fields")
        if "success_action" in form_data:
            form.success_action = form_data["success_action"]
            f_updates.append("success_action")
        if "success_message" in form_data:
            form.success_message = form_data["success_message"]
            f_updates.append("success_message")
        if "redirect_url" in form_data:
            form.redirect_url = form_data["redirect_url"]
            f_updates.append("redirect_url")
        form.save(update_fields=f_updates)

        if "default_tags" in form_data:
            from contacts.models import Tag
            tags = Tag.objects.filter(pk__in=form_data["default_tags"])
            form.default_tags.set(tags)

    return page


@transaction.atomic
def publish_landing_page(*, page: LandingPage, user_membership: Membership | None = None) -> LandingPage:
    page.status = LandingPage.Status.PUBLISHED
    page.published_at = timezone.now()
    if user_membership:
        page.published_by = user_membership
    page.save(update_fields=["status", "published_at", "published_by", "updated_at"])
    return page


@transaction.atomic
def unpublish_landing_page(*, page: LandingPage) -> LandingPage:
    page.status = LandingPage.Status.DRAFT
    page.save(update_fields=["status", "updated_at"])
    return page


@transaction.atomic
def duplicate_landing_page(*, page: LandingPage, user_membership: Membership | None = None) -> LandingPage:
    tenant = page.tenant
    new_title = f"{page.title} (Copy)"
    new_slug = _generate_unique_page_slug(new_title, tenant.id)

    new_page = LandingPage.objects.create(
        tenant=tenant,
        title=new_title,
        slug=new_slug,
        status=LandingPage.Status.DRAFT,
        template=page.template,
        seo_title=page.seo_title,
        seo_description=page.seo_description,
        og_image_url=page.og_image_url,
        favicon_url=page.favicon_url,
        theme_config=dict(page.theme_config or {}),
        settings=dict(page.settings or {}),
        target_workflow=page.target_workflow,
        preview_token=generate_preview_token(),
    )

    new_page.packages.set(page.packages.all())

    for sec in page.sections.all():
        LandingPageSection.objects.create(
            tenant=tenant,
            page=new_page,
            section_type=sec.section_type,
            order=sec.order,
            title=sec.title,
            subtitle=sec.subtitle,
            content=dict(sec.content or {}),
            is_visible=sec.is_visible,
        )

    if hasattr(page, "form") and page.form:
        old_form = page.form
        new_form = LandingPageForm.objects.create(
            tenant=tenant,
            page=new_page,
            title=old_form.title,
            subtitle=old_form.subtitle,
            submit_button_text=old_form.submit_button_text,
            fields=list(old_form.fields or []),
            success_action=old_form.success_action,
            success_message=old_form.success_message,
            redirect_url=old_form.redirect_url,
            assignee=old_form.assignee,
        )
        new_form.default_tags.set(old_form.default_tags.all())

    return new_page


@transaction.atomic
def generate_new_preview_token(*, page: LandingPage) -> str:
    token = generate_preview_token()
    page.preview_token = token
    page.save(update_fields=["preview_token", "updated_at"])
    return token


@transaction.atomic
def reorder_sections(*, page: LandingPage, section_order: list[dict[str, Any]]) -> None:
    for item in section_order:
        sec_id = item.get("id")
        new_order = item.get("order")
        if sec_id and new_order is not None:
            LandingPageSection.objects.filter(page=page, pk=sec_id).update(order=new_order)


def increment_page_view_count(page_id: uuid.UUID) -> None:
    LandingPage.unscoped_objects.filter(pk=page_id).update(view_count=F("view_count") + 1)


@transaction.atomic
def process_public_form_submission(
    *,
    page: LandingPage,
    form_data: dict[str, Any],
    attribution_data: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str = "",
    honeypot: str = "",
) -> dict[str, Any]:
    if honeypot and str(honeypot).strip():
        return {
            "status": "success",
            "submission_id": str(uuid.uuid4()),
            "contact_id": None,
            "action": "message",
            "message": "Thank you! Your submission has been received.",
            "redirect_url": "",
        }

    with tenant_context(page.tenant_id):
        form = getattr(page, "form", None)
        if form is None:
            raise ApplicationError(
                detail="Landing page does not have an active form.",
                code="no_active_form",
                status_code=status_codes.HTTP_400_BAD_REQUEST,
            )

        form_fields = form.fields or []
        for fld in form_fields:
            fld_name = fld.get("name")
            fld_label = fld.get("label") or fld_name
            is_required = bool(fld.get("required"))
            fld_type = str(fld.get("type") or "").strip().lower()
            val = form_data.get(fld_name)

            if is_required and (val is None or str(val).strip() == ""):
                raise ApplicationError(
                    detail=f"Field '{fld_label}' is required.",
                    code="missing_required_field",
                    status_code=status_codes.HTTP_400_BAD_REQUEST,
                )

            if val is not None and str(val).strip():
                if fld_type == "email" or fld_name in ("email", "email_address"):
                    if not _is_valid_email(val):
                        raise ApplicationError(
                            detail=f"Field '{fld_label}' must be a valid email address.",
                            code="invalid_email_format",
                            status_code=status_codes.HTTP_400_BAD_REQUEST,
                        )
                elif fld_type == "phone" or fld_name in ("phone", "phone_number"):
                    try:
                        normalize_phone_e164(val)
                    except Exception:
                        raise ApplicationError(
                            detail=f"Field '{fld_label}' must be a valid phone number in E.164 format.",
                            code="invalid_phone_format",
                            status_code=status_codes.HTTP_400_BAD_REQUEST,
                        )

        email_raw = form_data.get("email") or form_data.get("email_address")
        phone_raw = form_data.get("phone") or form_data.get("phone_number")
        norm_email = str(email_raw).strip().lower() if (email_raw and _is_valid_email(email_raw)) else ""
        norm_phone = ""
        if phone_raw:
            try:
                norm_phone = normalize_phone_e164(phone_raw) or ""
            except Exception:
                norm_phone = ""

        if not norm_email and not norm_phone:
            raise ApplicationError(
                detail="A valid email address or phone number is required to submit this form.",
                code="missing_contact_identifier",
                status_code=status_codes.HTTP_400_BAD_REQUEST,
            )

        first_name = str(form_data.get("first_name") or "").strip()
        last_name = str(form_data.get("last_name") or "").strip()
        full_name = str(form_data.get("full_name") or form_data.get("name") or "").strip()

        if not full_name and (first_name or last_name):
            full_name = f"{first_name} {last_name}".strip()
        elif full_name and not first_name:
            parts = full_name.split(" ", 1)
            first_name = parts[0]
            if len(parts) > 1:
                last_name = parts[1]

        known_keys = {"email", "email_address", "phone", "phone_number", "first_name", "last_name", "full_name", "name"}
        custom_fields = {k: v for k, v in form_data.items() if k not in known_keys}

        attr = attribution_data or {}
        utm_source = str(attr.get("utm_source") or "").strip()
        utm_medium = str(attr.get("utm_medium") or "").strip()
        utm_campaign = str(attr.get("utm_campaign") or "").strip()
        utm_content = str(attr.get("utm_content") or "").strip()
        utm_term = str(attr.get("utm_term") or "").strip()
        referrer_url = str(attr.get("referrer_url") or attr.get("referrer") or "").strip()
        landing_page_url = str(attr.get("landing_page_url") or "").strip()

        tag_names = list(form.default_tags.values_list("name", flat=True))
        contact, _ = ContactService.upsert_from_source(
            first_name=first_name,
            last_name=last_name,
            full_name=full_name,
            email=norm_email,
            phone=norm_phone,
            source_channel="landing_page",
            source_page=page.title,
            source_form=form.title,
            campaign=utm_campaign,
            ad_set=utm_medium,
            ad=utm_content,
            custom_fields=custom_fields or None,
            tags=tag_names or None,
        )

        if form.assignee and not contact.assigned_to:
            ContactService.assign_contact(contact=contact, assignee=form.assignee)

        submission = FormSubmission.objects.create(
            tenant=page.tenant,
            page=page,
            form=form,
            contact=contact,
            form_data=form_data,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
            utm_content=utm_content,
            utm_term=utm_term,
            referrer_url=referrer_url,
            landing_page_url=landing_page_url,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        LandingPage.unscoped_objects.filter(pk=page.pk).update(submission_count=F("submission_count") + 1)

        publish(
            EventName.FORM_SUBMITTED,
            tenant_id=page.tenant_id,
            contact_id=contact.id,
            payload={
                "page_id": str(page.id),
                "landing_page_id": str(page.id),
                "landing_page_slug": page.slug,
                "source_page": page.title,
                "landing_page_title": page.title,
                "form_id": str(form.id),
                "source_form": form.title,
                "source_channel": "landing_page",
                "submission_id": str(submission.id),
                "utm_source": utm_source,
                "utm_medium": utm_medium,
                "utm_campaign": utm_campaign,
                "utm_content": utm_content,
                "utm_term": utm_term,
                "form_data": form_data,
            },
        )

        if page.target_workflow:
            EnrollmentService.enroll(workflow=page.target_workflow, contact=contact)

        return {
            "status": "success",
            "submission_id": str(submission.id),
            "contact_id": str(contact.id),
            "action": form.success_action,
            "message": form.success_message,
            "redirect_url": form.redirect_url,
        }
