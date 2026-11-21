import secrets
import uuid
from django.db import models
from core.models import TenantOwnedModel


def generate_preview_token() -> str:
    return secrets.token_urlsafe(24)


class LandingPageTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="custom_landing_templates",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=150)
    description = models.TextField(blank=True)
    thumbnail_url = models.URLField(max_length=500, blank=True)
    category = models.CharField(max_length=100, default="fitness")
    default_sections = models.JSONField(default=list, blank=True)
    default_theme = models.JSONField(default=dict, blank=True)
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_system", "name", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "slug"],
                name="uniq_landing_template_tenant_slug",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class LandingPage(TenantOwnedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=150, db_index=True)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    template = models.ForeignKey(
        "landing_pages.LandingPageTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="landing_pages",
    )
    seo_title = models.CharField(max_length=255, blank=True)
    seo_description = models.TextField(blank=True)
    og_image_url = models.URLField(max_length=500, blank=True)
    favicon_url = models.URLField(max_length=500, blank=True)
    theme_config = models.JSONField(default=dict, blank=True)
    packages = models.ManyToManyField(
        "packages.Package",
        blank=True,
        related_name="landing_pages",
    )
    target_workflow = models.ForeignKey(
        "automation.Workflow",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="landing_pages",
    )
    settings = models.JSONField(default=dict, blank=True)
    preview_token = models.CharField(
        max_length=64,
        default=generate_preview_token,
        blank=True,
    )
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        "accounts.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_landing_pages",
    )
    view_count = models.PositiveIntegerField(default=0)
    submission_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "slug"],
                name="uniq_landingpage_tenant_slug",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "status"],
                name="idx_landingpage_t_status",
            ),
            models.Index(
                fields=["slug"],
                name="idx_landingpage_slug",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.slug})"


class LandingPageSection(TenantOwnedModel):
    class SectionType(models.TextChoices):
        HERO = "hero", "Hero"
        FEATURES = "features", "Features"
        PACKAGES = "packages", "Packages"
        FORM = "form", "Form"
        TESTIMONIALS = "testimonials", "Testimonials"
        CTA = "cta", "Call to Action"
        FAQ = "faq", "FAQ"
        STATS = "stats", "Statistics"
        VIDEO = "video", "Video"
        CONTENT = "content", "Content / Rich Text"
        FOOTER = "footer", "Footer"
        CUSTOM = "custom", "Custom"

    page = models.ForeignKey(
        "landing_pages.LandingPage",
        on_delete=models.CASCADE,
        related_name="sections",
    )
    section_type = models.CharField(
        max_length=50,
        choices=SectionType.choices,
        default=SectionType.HERO,
    )
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=255, blank=True)
    subtitle = models.CharField(max_length=255, blank=True)
    content = models.JSONField(default=dict, blank=True)
    is_visible = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "created_at"]
        indexes = [
            models.Index(
                fields=["tenant", "page", "order"],
                name="idx_lpsec_t_p_order",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.page.title} - {self.section_type} ({self.order})"


class LandingPageForm(TenantOwnedModel):
    class SuccessAction(models.TextChoices):
        MESSAGE = "message", "Display Message"
        REDIRECT = "redirect", "Redirect to URL"

    page = models.OneToOneField(
        "landing_pages.LandingPage",
        on_delete=models.CASCADE,
        related_name="form",
    )
    title = models.CharField(max_length=255, default="Sign Up")
    subtitle = models.CharField(max_length=255, blank=True)
    submit_button_text = models.CharField(max_length=100, default="Submit")
    fields = models.JSONField(default=list)
    success_action = models.CharField(
        max_length=32,
        choices=SuccessAction.choices,
        default=SuccessAction.MESSAGE,
    )
    success_message = models.TextField(
        default="Thank you for submitting! We have received your request."
    )
    redirect_url = models.URLField(max_length=500, blank=True)
    default_tags = models.ManyToManyField(
        "contacts.Tag",
        blank=True,
        related_name="landing_page_forms",
    )
    assignee = models.ForeignKey(
        "accounts.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_landing_forms",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Form for {self.page.title}"


class FormSubmission(TenantOwnedModel):
    page = models.ForeignKey(
        "landing_pages.LandingPage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submissions",
    )
    form = models.ForeignKey(
        "landing_pages.LandingPageForm",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submissions",
    )
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="landing_page_submissions",
    )
    form_data = models.JSONField(default=dict)
    utm_source = models.CharField(max_length=255, blank=True)
    utm_medium = models.CharField(max_length=255, blank=True)
    utm_campaign = models.CharField(max_length=255, blank=True)
    utm_content = models.CharField(max_length=255, blank=True)
    utm_term = models.CharField(max_length=255, blank=True)
    referrer_url = models.URLField(max_length=1000, blank=True)
    landing_page_url = models.URLField(max_length=1000, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["tenant", "-created_at"],
                name="idx_formsub_t_created",
            ),
            models.Index(
                fields=["page", "-created_at"],
                name="idx_formsub_p_created",
            ),
        ]

    def __str__(self) -> str:
        return f"Submission on {self.page_id} at {self.created_at}"
