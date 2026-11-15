from django.contrib import admin
from notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("notification_type", "recipient", "tenant", "title", "is_read", "email_sent", "created_at")
    list_filter = ("notification_type", "is_read", "email_sent")
    search_fields = ("title", "recipient__email", "tenant__name")

    def get_queryset(self, request):
        return Notification.unscoped_objects.select_related("recipient", "tenant")
