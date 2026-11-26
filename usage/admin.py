from django.contrib import admin
from core.admin import UnscopedTenantModelAdmin
from usage.models import RateCard, UsageRecord


@admin.register(UsageRecord)
class UsageRecordAdmin(UnscopedTenantModelAdmin):
    list_display = (
        "id",
        "tenant",
        "channel",
        "quantity",
        "destination_country",
        "whatsapp_category",
        "unit_cost",
        "total_cost",
        "currency",
        "status",
        "occurred_at",
    )
    list_filter = ("channel", "status", "destination_country")
    search_fields = ("message__id", "tenant__name")
    raw_id_fields = ("tenant", "message")
    readonly_fields = ("id", "created_at", "updated_at")

    def get_queryset(self, request):
        return UsageRecord.unscoped_objects.select_related("tenant", "message")


@admin.register(RateCard)
class RateCardAdmin(UnscopedTenantModelAdmin):
    list_display = (
        "id",
        "tenant",
        "channel",
        "destination_country",
        "whatsapp_category",
        "unit_cost",
        "currency",
        "is_active",
        "updated_at",
    )
    list_filter = ("channel", "is_active", "destination_country")
    search_fields = ("tenant__name", "channel", "destination_country", "whatsapp_category")
    raw_id_fields = ("tenant",)
    readonly_fields = ("id", "created_at", "updated_at")
