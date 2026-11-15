from django.contrib import admin
from integrations.models import ChannelConnection, InboundWebhookEvent


@admin.register(ChannelConnection)
class ChannelConnectionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "channel",
        "provider",
        "sender_identity",
        "status",
        "last_checked_at",
        "created_at",
    )
    list_filter = ("channel", "provider", "status", "tenant")
    search_fields = ("sender_identity", "sender_name", "webhook_token")
    readonly_fields = ("id", "webhook_token", "created_at", "updated_at")

    def get_queryset(self, request):
        return ChannelConnection.unscoped_objects.select_related("tenant")


@admin.register(InboundWebhookEvent)
class InboundWebhookEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "provider",
        "channel",
        "event_type",
        "processed",
        "processed_at",
        "created_at",
    )
    list_filter = ("provider", "channel", "event_type", "processed", "tenant")
    readonly_fields = ("id", "created_at", "updated_at")

    def get_queryset(self, request):
        return InboundWebhookEvent.unscoped_objects.select_related("tenant", "connection")
