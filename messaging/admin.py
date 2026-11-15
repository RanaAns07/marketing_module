from django.contrib import admin
from messaging.models import Broadcast, Message, MessageTemplate


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "channel",
        "name",
        "approval_status",
        "whatsapp_content_sid",
        "active",
        "created_at",
    )
    list_filter = ("channel", "approval_status", "active", "tenant")
    search_fields = ("name", "subject", "whatsapp_content_sid")
    readonly_fields = ("id", "created_at", "updated_at")

    def get_queryset(self, request):
        return MessageTemplate.unscoped_objects.select_related("tenant")


@admin.register(Broadcast)
class BroadcastAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "name",
        "template",
        "status",
        "scheduled_at",
        "total_recipients",
        "sent_count",
        "delivered_count",
        "failed_count",
        "created_at",
    )
    list_filter = ("status", "tenant")
    search_fields = ("name",)
    readonly_fields = ("id", "created_at", "updated_at")

    def get_queryset(self, request):
        return Broadcast.unscoped_objects.select_related("tenant", "template", "created_by")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "contact",
        "channel",
        "direction",
        "status",
        "provider",
        "provider_message_id",
        "segments",
        "destination_country",
        "source",
        "created_at",
    )
    list_filter = ("channel", "direction", "status", "source", "tenant")
    search_fields = ("provider_message_id", "subject", "body", "idempotency_key")
    readonly_fields = ("id", "created_at", "updated_at")

    def get_queryset(self, request):
        return Message.unscoped_objects.select_related("tenant", "contact", "template", "broadcast")
