from rest_framework import serializers
from accounts.models import Membership, TenantAPIKey, User
from core.models import Tenant


class TenantSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = [
            "id",
            "name",
            "slug",
            "timezone",
            "marketer_can_view_all_contacts",
            "notify_admins_on_new_lead",
            "assignment_mode",
            "settings",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "slug", "created_at", "updated_at"]


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
            "email_notifications_enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "email", "full_name", "is_active", "created_at", "updated_at"]


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
            "is_active",
            "last_used_at",
            "created_at",
        ]
        read_only_fields = ["id", "key_id", "last_used_at", "created_at"]
