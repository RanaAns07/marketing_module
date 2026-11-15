import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
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
    name = models.CharField(max_length=120)
    key_id = models.CharField(max_length=64, unique=True, db_index=True)
    secret_encrypted = EncryptedTextField()
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "name"],
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
