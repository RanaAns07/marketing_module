from django.contrib import admin
from core.admin import UnscopedTenantModelAdmin
from contacts.models import (
    Activity,
    Appointment,
    Contact,
    ContactTag,
    CustomFieldDefinition,
    LeadStatus,
    Tag,
    Task,
)


@admin.register(LeadStatus)
class LeadStatusAdmin(UnscopedTenantModelAdmin):
    list_display = ("name", "tenant", "order", "is_default", "created_at")
    list_filter = ("is_default",)
    search_fields = ("name", "tenant__name")

    def get_queryset(self, request):
        return LeadStatus.unscoped_objects.select_related("tenant")


@admin.register(CustomFieldDefinition)
class CustomFieldDefinitionAdmin(UnscopedTenantModelAdmin):
    list_display = ("label", "key", "field_type", "is_required", "tenant", "created_at")
    list_filter = ("field_type", "is_required")
    search_fields = ("label", "key", "tenant__name")

    def get_queryset(self, request):
        return CustomFieldDefinition.unscoped_objects.select_related("tenant")


@admin.register(Tag)
class TagAdmin(UnscopedTenantModelAdmin):
    list_display = ("name", "color", "tenant", "created_at")
    search_fields = ("name", "tenant__name")

    def get_queryset(self, request):
        return Tag.unscoped_objects.select_related("tenant")


@admin.register(Contact)
class ContactAdmin(UnscopedTenantModelAdmin):
    list_display = (
        "full_name",
        "email",
        "phone",
        "lead_status",
        "assigned_to",
        "source_channel",
        "tenant",
        "last_activity_at",
    )
    list_filter = ("email_subscription", "sms_subscription", "whatsapp_subscription")
    search_fields = ("full_name", "email", "phone", "campaign", "tenant__name")

    def get_queryset(self, request):
        return Contact.unscoped_objects.select_related("tenant", "lead_status", "assigned_to")


@admin.register(ContactTag)
class ContactTagAdmin(UnscopedTenantModelAdmin):
    list_display = ("contact", "tag", "tenant", "created_at")
    search_fields = ("contact__full_name", "tag__name", "tenant__name")

    def get_queryset(self, request):
        return ContactTag.unscoped_objects.select_related("contact", "tag", "tenant")


@admin.register(Activity)
class ActivityAdmin(UnscopedTenantModelAdmin):
    list_display = ("activity_type", "contact", "actor", "tenant", "created_at")
    list_filter = ("activity_type",)
    search_fields = ("description", "contact__full_name", "tenant__name")

    def get_queryset(self, request):
        return Activity.unscoped_objects.select_related("contact", "actor", "tenant")


@admin.register(Appointment)
class AppointmentAdmin(UnscopedTenantModelAdmin):
    list_display = ("contact", "starts_at", "status", "location", "tenant", "created_at")
    list_filter = ("status",)
    search_fields = ("contact__full_name", "location", "external_ref", "tenant__name")

    def get_queryset(self, request):
        return Appointment.unscoped_objects.select_related("contact", "tenant")


@admin.register(Task)
class TaskAdmin(UnscopedTenantModelAdmin):
    list_display = ("title", "contact", "assignee", "status", "due_at", "from_workflow", "tenant")
    list_filter = ("status", "from_workflow")
    search_fields = ("title", "description", "tenant__name")

    def get_queryset(self, request):
        return Task.unscoped_objects.select_related("contact", "assignee", "tenant")
