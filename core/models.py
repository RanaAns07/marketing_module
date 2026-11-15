import uuid
from django.db import models
from core.context import get_current_tenant_id, require_tenant_id
from core.exceptions import CrossTenantAccessError
from core.managers import TenantScopedManager


class Tenant(models.Model):
    class AssignmentMode(models.TextChoices):
        MANUAL = "manual", "Manual / Shared Queue"
        ROUND_ROBIN = "round_robin", "Round-Robin"
        BY_SOURCE = "by_source", "Dedicated Owner by Source or Form"

    class AssignmentStrategy(models.TextChoices):
        SHARED_QUEUE = "shared_queue", "Manual Assignment / Shared Queue"
        DEDICATED_OWNER = "dedicated_owner", "Dedicated Owner by Source or Form"
        ROUND_ROBIN = "round_robin", "Even Team Distribution (Round-Robin)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    timezone = models.CharField(max_length=64, default="UTC")
    is_active = models.BooleanField(default=True)
    marketer_can_view_all_contacts = models.BooleanField(default=True)
    notify_admins_on_new_lead = models.BooleanField(default=True)
    assignment_mode = models.CharField(
        max_length=32,
        choices=AssignmentMode.choices,
        default=AssignmentMode.MANUAL,
    )
    assignment_strategy = models.CharField(
        max_length=32,
        choices=AssignmentStrategy.choices,
        default=AssignmentStrategy.SHARED_QUEUE,
    )
    handoff_add_interested_tag = models.BooleanField(default=True)
    settings = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["is_active", "-created_at"]),
        ]

    def __str__(self) -> str:
        return self.name


class TenantOwnedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_set",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()
    unscoped_objects = models.Manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        active_tenant_id = get_current_tenant_id()
        if self.tenant_id is None:
            self.tenant_id = require_tenant_id()
        elif active_tenant_id is not None and self.tenant_id != active_tenant_id:
            raise CrossTenantAccessError()
        return super().save(*args, **kwargs)
