from django.contrib import admin
from webhooks.models import ExternalEvent, WebhookDelivery, WebhookEndpoint


@admin.register(ExternalEvent)
class ExternalEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "event_id",
        "type",
        "status",
        "api_key",
        "contact",
        "attempts",
        "occurred_at",
        "processed_at",
        "created_at",
    )
    list_filter = ("status", "type", "created_at")
    search_fields = ("event_id", "type", "error", "contact__email", "contact__external_id")
    raw_id_fields = ("tenant", "api_key", "contact")
    readonly_fields = ("id", "created_at", "updated_at", "processed_at")

    def get_queryset(self, request):
        return ExternalEvent.unscoped_objects.select_related("tenant", "api_key", "contact")


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "name",
        "url",
        "active",
        "failure_count",
        "disabled_at",
        "created_at",
    )
    list_filter = ("active", "created_at")
    search_fields = ("name", "url", "tenant__name")
    raw_id_fields = ("tenant",)
    readonly_fields = ("id", "created_at", "updated_at", "disabled_at")
    exclude = ("signing_secret",)

    def get_queryset(self, request):
        return WebhookEndpoint.unscoped_objects.select_related("tenant")


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "endpoint",
        "event_name",
        "event_id",
        "status",
        "attempts",
        "response_status",
        "next_retry_at",
        "delivered_at",
        "created_at",
    )
    list_filter = ("status", "event_name", "created_at")
    search_fields = ("event_name", "event_id", "endpoint__name", "endpoint__url", "response_excerpt")
    raw_id_fields = ("tenant", "endpoint")
    readonly_fields = ("id", "created_at", "updated_at", "delivered_at")

    def get_queryset(self, request):
        return WebhookDelivery.unscoped_objects.select_related("tenant", "endpoint")
