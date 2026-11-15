from django.contrib import admin
from inbox.models import AssignmentRule, Conversation, ConversationNote


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "contact",
        "status",
        "assigned_to",
        "last_channel",
        "unread_count",
        "last_message_at",
        "resolved_at",
    )
    list_filter = ("status", "last_channel")
    search_fields = (
        "contact__full_name",
        "contact__email",
        "contact__phone",
        "last_message_preview",
    )
    raw_id_fields = ("tenant", "contact", "assigned_to")

    def get_queryset(self, request):
        return Conversation.unscoped_objects.select_related(
            "tenant",
            "contact",
            "assigned_to",
            "assigned_to__user",
        )


@admin.register(ConversationNote)
class ConversationNoteAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "conversation",
        "author",
        "created_at",
    )
    search_fields = ("body", "author__email")
    raw_id_fields = ("tenant", "conversation", "author")

    def get_queryset(self, request):
        return ConversationNote.unscoped_objects.select_related(
            "tenant",
            "conversation",
            "author",
        )


@admin.register(AssignmentRule)
class AssignmentRuleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "priority",
        "name",
        "match_page",
        "match_form",
        "match_tag",
        "match_tag_name",
        "assignee",
        "is_active",
    )
    list_filter = ("is_active",)
    search_fields = ("name", "match_page", "match_form", "match_tag_name")
    raw_id_fields = ("tenant", "match_tag", "assignee")

    def get_queryset(self, request):
        return AssignmentRule.unscoped_objects.select_related(
            "tenant",
            "match_tag",
            "assignee",
            "assignee__user",
        )
