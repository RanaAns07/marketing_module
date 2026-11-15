from django.db import models
from core.fields import EncryptedTextField
from core.models import TenantOwnedModel


class MetaConnection(TenantOwnedModel):
    class Status(models.TextChoices):
        CONNECTED = "connected", "Connected"
        EXPIRED = "expired", "Expired"
        ERROR = "error", "Error"
        DISCONNECTED = "disconnected", "Disconnected"

    user_access_token = EncryptedTextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    meta_user_id = models.CharField(max_length=128, blank=True)
    meta_user_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CONNECTED,
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    alert_notified_status = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant"],
                name="uniq_metaconn_tenant",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "status"],
                name="idx_metaconn_tenant_status",
            ),
        ]

    def __str__(self) -> str:
        return f"MetaConnection({self.tenant_id}: {self.status})"


class MetaPage(TenantOwnedModel):
    connection = models.ForeignKey(
        "leads.MetaConnection",
        on_delete=models.CASCADE,
        related_name="pages",
    )
    page_id = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=255)
    instagram_business_account_id = models.CharField(max_length=128, blank=True)
    page_access_token = EncryptedTextField(blank=True)
    selected = models.BooleanField(default=False)
    subscribed = models.BooleanField(default=False)

    class Meta:
        ordering = ["name", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "page_id"],
                name="uniq_metapage_tenant_page_id",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "selected"],
                name="idx_metapage_tenant_selected",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.page_id})"


class MetaLeadForm(TenantOwnedModel):
    class Platform(models.TextChoices):
        FACEBOOK = "facebook", "Facebook"
        INSTAGRAM = "instagram", "Instagram"

    page = models.ForeignKey(
        "leads.MetaPage",
        on_delete=models.CASCADE,
        related_name="forms",
    )
    form_id = models.CharField(max_length=128)
    name = models.CharField(max_length=255)
    platform = models.CharField(
        max_length=20,
        choices=Platform.choices,
        default=Platform.FACEBOOK,
    )
    status = models.CharField(max_length=32, default="ACTIVE", blank=True)
    selected = models.BooleanField(default=False)
    default_tags = models.ManyToManyField(
        "contacts.Tag",
        blank=True,
        related_name="meta_lead_forms",
    )
    questions = models.JSONField(default=list, blank=True)
    field_mapping = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "form_id"],
                name="uniq_metaleadform_tenant_formid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "page", "selected"],
                name="idx_metaleadform_t_pg_sel",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.form_id})"


class MetaLeadEvent(TenantOwnedModel):
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        PROCESSED = "processed", "Processed"
        FAILED = "failed", "Failed"
        IGNORED = "ignored", "Ignored"

    leadgen_id = models.CharField(max_length=128)
    page = models.ForeignKey(
        "leads.MetaPage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lead_events",
    )
    form = models.ForeignKey(
        "leads.MetaLeadForm",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lead_events",
    )
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="meta_lead_events",
    )
    raw_payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
    )
    attempts = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "leadgen_id"],
                name="uniq_metaleadevent_tenant_leadid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "status", "-created_at"],
                name="idx_metaleadevent_t_st_created",
            ),
        ]

    def __str__(self) -> str:
        return f"MetaLeadEvent({self.leadgen_id}: {self.status})"
