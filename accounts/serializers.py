from rest_framework import serializers
from accounts.models import Membership, TenantAPIKey, TenantInvitation, User
from core.models import Tenant


class TenantSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = [
            "id",
            "name",
            "slug",
            "timezone",
            "is_active",
            "deactivated_at",
            "grace_period_ends_at",
            "deactivation_reason",
            "marketer_can_view_all_contacts",
            "notify_admins_on_new_lead",
            "assignment_mode",
            "assignment_strategy",
            "handoff_add_interested_tag",
            "settings",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "slug", "created_at", "updated_at", "deactivated_at", "grace_period_ends_at", "deactivation_reason"]


class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "phone",
            "is_active",
            "email_verified",
            "email_verified_at",
            "email_notifications_enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "email", "full_name", "is_active", "email_verified", "email_verified_at", "created_at", "updated_at"]


class MembershipSummarySerializer(serializers.ModelSerializer):
    tenant = TenantSummarySerializer(read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id",
            "tenant",
            "role",
            "is_active",
            "is_available",
            "email_notifications_enabled",
            "last_assigned_at",
            "created_at",
        ]
        read_only_fields = fields


class TeamMemberSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    phone = serializers.CharField(source="user.phone", read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id",
            "tenant_id",
            "user",
            "email",
            "first_name",
            "last_name",
            "phone",
            "role",
            "is_active",
            "is_available",
            "email_notifications_enabled",
            "notification_preferences",
            "last_assigned_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "tenant_id",
            "user",
            "email",
            "first_name",
            "last_name",
            "phone",
            "last_assigned_at",
            "created_at",
            "updated_at",
        ]


class RegisterTenantOwnerSerializer(serializers.Serializer):
    tenant_name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    timezone = serializers.CharField(max_length=64, required=False, default="UTC")


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)


class AddTeamMemberSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=Membership.Role.choices, default=Membership.Role.MARKETER)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    password = serializers.CharField(write_only=True, required=False, allow_blank=False, min_length=8)
    is_available = serializers.BooleanField(required=False, default=True)
    email_notifications_enabled = serializers.BooleanField(required=False, default=True)


class UpdateTeamMemberSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Membership.Role.choices, required=False)
    is_active = serializers.BooleanField(required=False)
    is_available = serializers.BooleanField(required=False)
    email_notifications_enabled = serializers.BooleanField(required=False)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)


class RoleChangeSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Membership.Role.choices)


class AvailabilityToggleSerializer(serializers.Serializer):
    is_available = serializers.BooleanField()


class TenantAPIKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = TenantAPIKey
        fields = [
            "id",
            "name",
            "key_id",
            "scopes",
            "is_active",
            "last_used_at",
            "revoked_at",
            "created_at",
        ]
        read_only_fields = ["id", "key_id", "last_used_at", "revoked_at", "created_at"]


class CreateTenantAPIKeySerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    scopes = serializers.ListField(
        child=serializers.ChoiceField(choices=TenantAPIKey.Scope.choices),
        required=False,
        allow_empty=False,
    )


class RotateTenantAPIKeySerializer(serializers.Serializer):
    grace_period_seconds = serializers.IntegerField(min_value=0, max_value=604800, required=False, default=0)
    grace_period_minutes = serializers.IntegerField(min_value=0, max_value=10080, required=False, default=0)
    name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    scopes = serializers.ListField(
        child=serializers.ChoiceField(choices=TenantAPIKey.Scope.choices),
        required=False,
        allow_empty=False,
    )


class VerifyEmailSerializer(serializers.Serializer):
    token = serializers.CharField()


class ResendVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False, allow_blank=True)


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=8)


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class TenantInvitationSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    invited_by_email = serializers.EmailField(source="invited_by.email", read_only=True, default=None)

    class Meta:
        model = TenantInvitation
        fields = [
            "id",
            "tenant_id",
            "email",
            "role",
            "status",
            "expires_at",
            "accepted_at",
            "invited_by_email",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class CreateTenantInvitationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=Membership.Role.choices, default=Membership.Role.MARKETER)


class AcceptInvitationSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, required=False, allow_blank=False, min_length=8)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")


class OnboardingItemSerializer(serializers.Serializer):
    key = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    status = serializers.CharField()
    completed = serializers.BooleanField()
    frontend_route = serializers.CharField()
    details = serializers.DictField(required=False)


class OnboardingStatusSerializer(serializers.Serializer):
    progress_percentage = serializers.IntegerField()
    completed_count = serializers.IntegerField()
    total_count = serializers.IntegerField()
    items = OnboardingItemSerializer(many=True)


class ChangeEmailRequestSerializer(serializers.Serializer):
    new_email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class VerifyChangeEmailSerializer(serializers.Serializer):
    token = serializers.CharField()


class SwitchTenantSerializer(serializers.Serializer):
    tenant_id = serializers.UUIDField()


class UserTenantMembershipSerializer(serializers.Serializer):
    tenant_id = serializers.UUIDField()
    tenant_name = serializers.CharField()
    slug = serializers.CharField()
    role = serializers.CharField()
    is_active = serializers.BooleanField()
    is_available = serializers.BooleanField()
    deactivated_at = serializers.DateTimeField(allow_null=True)
    grace_period_ends_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


class TransferOwnershipSerializer(serializers.Serializer):
    target_membership_id = serializers.UUIDField()


class DeactivateTenantSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    grace_period_days = serializers.IntegerField(default=14, min_value=1, max_value=60)
