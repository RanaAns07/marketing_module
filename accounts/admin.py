from django.contrib import admin
from accounts.models import Membership, TenantAPIKey, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("email", "first_name", "last_name", "is_active", "is_staff", "created_at")
    search_fields = ("email", "first_name", "last_name")
    list_filter = ("is_active", "is_staff")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "tenant", "role", "is_active", "is_available", "created_at")
    list_filter = ("role", "is_active", "is_available")
    search_fields = ("user__email", "tenant__name")

    def get_queryset(self, request):
        return Membership.unscoped_objects.select_related("user", "tenant")


@admin.register(TenantAPIKey)
class TenantAPIKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "key_id", "tenant", "scopes", "is_active", "last_used_at", "revoked_at", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "key_id", "tenant__name")

    def get_queryset(self, request):
        return TenantAPIKey.unscoped_objects.select_related("tenant")
