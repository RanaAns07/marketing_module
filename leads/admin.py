from django.contrib import admin
from core.admin import UnscopedTenantModelAdmin
from leads.models import MetaConnection, MetaLeadEvent, MetaLeadForm, MetaPage


@admin.register(MetaConnection)
class MetaConnectionAdmin(UnscopedTenantModelAdmin):
    list_display = (
        "id",
        "tenant",
        "meta_user_id",
        "meta_user_name",
        "status",
        "token_expires_at",
        "last_checked_at",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = ("meta_user_id", "meta_user_name", "tenant__name", "tenant__slug")
    readonly_fields = ("id", "created_at", "updated_at", "last_checked_at")

    def get_queryset(self, request):
        return MetaConnection.unscoped_objects.select_related("tenant")


@admin.register(MetaPage)
class MetaPageAdmin(UnscopedTenantModelAdmin):
    list_display = (
        "id",
        "tenant",
        "page_id",
        "name",
        "selected",
        "subscribed",
        "created_at",
    )
    list_filter = ("selected", "subscribed", "created_at")
    search_fields = ("page_id", "name", "tenant__name", "tenant__slug")
    readonly_fields = ("id", "created_at", "updated_at")

    def get_queryset(self, request):
        return MetaPage.unscoped_objects.select_related("tenant", "connection")


@admin.register(MetaLeadForm)
class MetaLeadFormAdmin(UnscopedTenantModelAdmin):
    list_display = (
        "id",
        "tenant",
        "page",
        "form_id",
        "name",
        "platform",
        "status",
        "selected",
        "last_synced_at",
    )
    list_filter = ("platform", "selected", "status", "created_at")
    search_fields = ("form_id", "name", "page__name", "tenant__name")
    readonly_fields = ("id", "created_at", "updated_at", "last_synced_at")

    def get_queryset(self, request):
        return MetaLeadForm.unscoped_objects.select_related("tenant", "page")


@admin.register(MetaLeadEvent)
class MetaLeadEventAdmin(UnscopedTenantModelAdmin):
    list_display = (
        "id",
        "tenant",
        "leadgen_id",
        "page",
        "form",
        "contact",
        "status",
        "attempts",
        "processed_at",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = ("leadgen_id", "error", "tenant__name")
    readonly_fields = ("id", "created_at", "updated_at", "processed_at")

    def get_queryset(self, request):
        return MetaLeadEvent.unscoped_objects.select_related("tenant", "page", "form", "contact")
