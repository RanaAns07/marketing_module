from django.contrib import admin
from core.models import Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "timezone", "is_active", "assignment_mode", "created_at")
    search_fields = ("name", "slug")
    list_filter = ("is_active", "assignment_mode")
