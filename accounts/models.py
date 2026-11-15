import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models import Q
from django.utils import timezone
from core.fields import EncryptedTextField
from core.models import TenantOwnedModel


class UserManager(BaseUserManager):
    def create_user(self, email: str, password: str | None = None, **extra_fields):
        if not email:
            raise ValueError("Email address is required.")
        normalized_email = self.normalize_email(email).lower().strip()
        user = self.model(email=normalized_email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        return self.create_user(email=email, password=password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, db_index=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    email_notifications_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["is_active", "-created_at"]),
        ]

    @property
    def full_name(self) -> str:
        full = f"{self.first_name} {self.last_name}".strip()
        return full or self.email

    def __str__(self) -> str:
        return self.email


class Membership(TenantOwnedModel):
    class Role(models.TextChoices):
        OWNER = "owner", "Business Owner"
        ADMIN = "admin", "Business Administrator"
        MARKETER = "marketer", "Marketer / Sales Representative"

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.MARKETER,
    )
    is_active = models.BooleanField(default=True)
    is_available = models.BooleanField(default=True)
    email_notifications_enabled = models.BooleanField(default=True)
    notification_preferences = models.JSONField(default=dict, blank=True)
    last_assigned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "user"],
                name="uniq_membership_tenant_user",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "role", "is_active"],
                name="idx_member_tenant_role_act",
            ),
            models.Index(
                fields=["tenant", "is_active", "is_available", "last_assigned_at"],
                name="idx_member_tenant_rr_pool",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user.email} ({self.role}) @ {self.tenant_id}"


class TenantAPIKey(TenantOwnedModel):
    class Scope(models.TextChoices):
        EVENTS_WRITE = "events:write", "Events Write"
        CONTACTS_READ = "contacts:read", "Contacts Read"
        CONTACTS_WRITE = "contacts:write", "Contacts Write"
        APPOINTMENTS_WRITE = "appointments:write", "Appointments Write"
        WEBHOOKS_MANAGE = "webhooks:manage", "Webhooks Manage"

    name = models.CharField(max_length=120)
    key_id = models.CharField(max_length=64, unique=True, db_index=True)
    secret_encrypted = EncryptedTextField()
    scopes = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "name"],
                condition=Q(is_active=True, revoked_at__isnull=True),
                name="uniq_apikey_tenant_name",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "is_active"],
                name="idx_apikey_tenant_active",
            ),
            models.Index(
                fields=["tenant", "key_id"],
                name="idx_apikey_tenant_keyid",
            ),
        ]

    def is_currently_valid(self, now=None) -> bool:
        if not self.is_active:
            return False
        if self.revoked_at is not None:
            ref_now = now or timezone.now()
            if self.revoked_at <= ref_now:
                return False
        return True

    def has_scope(self, scope: str) -> bool:
        current_scopes = self.scopes or []
        return "*" in current_scopes or scope in current_scopes

    def __str__(self) -> str:
        return f"{self.name} ({self.key_id})"


class TenantInvitation(TenantOwnedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    email = models.EmailField(db_index=True)
    role = models.CharField(
        max_length=20,
        choices=Membership.Role.choices,
        default=Membership.Role.MARKETER,
    )
    invited_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_invitations",
    )
    token = models.CharField(max_length=255, unique=True, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "email"],
                condition=Q(status="pending"),
                name="uniq_pending_invite_tenant_email",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status"], name="idx_invite_tenant_status"),
            models.Index(fields=["token"], name="idx_invite_token"),
        ]

    def is_valid(self, now=None) -> bool:
        ref_now = now or timezone.now()
        return self.status == self.Status.PENDING and self.expires_at > ref_now

    def __str__(self) -> str:
        return f"Invitation({self.email}, {self.role}, {self.status}) @ {self.tenant_id}"
