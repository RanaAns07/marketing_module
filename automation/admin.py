from django.contrib import admin
from automation.models import StepExecution, Workflow, WorkflowEnrollment, WorkflowStep


@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "name",
        "status",
        "trigger_type",
        "re_entry",
        "stop_on_reply",
        "is_promotional",
        "version",
        "created_at",
    )
    list_filter = ("status", "trigger_type", "re_entry", "stop_on_reply", "is_promotional")
    search_fields = ("name", "description", "tenant__name", "tenant__slug")
    readonly_fields = ("id", "version", "created_at", "updated_at")

    def get_queryset(self, request):
        return Workflow.unscoped_objects.select_related("tenant")


@admin.register(WorkflowStep)
class WorkflowStepAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "workflow",
        "key",
        "type",
        "subtype",
        "next_key",
        "true_key",
        "false_key",
        "created_at",
    )
    list_filter = ("type", "subtype", "created_at")
    search_fields = ("key", "workflow__name", "tenant__name")
    readonly_fields = ("id", "created_at", "updated_at")

    def get_queryset(self, request):
        return WorkflowStep.unscoped_objects.select_related("tenant", "workflow")


@admin.register(WorkflowEnrollment)
class WorkflowEnrollmentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "workflow",
        "contact",
        "status",
        "current_step_key",
        "next_run_at",
        "iteration",
        "paused_reason",
        "started_at",
        "finished_at",
    )
    list_filter = ("status", "started_at")
    search_fields = ("workflow__name", "contact__full_name", "contact__email", "paused_reason")
    readonly_fields = ("id", "created_at", "updated_at", "started_at", "finished_at")

    def get_queryset(self, request):
        return WorkflowEnrollment.unscoped_objects.select_related("tenant", "workflow", "contact")


@admin.register(StepExecution)
class StepExecutionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "enrollment",
        "step_key",
        "iteration",
        "status",
        "attempts",
        "executed_at",
    )
    list_filter = ("status", "executed_at")
    search_fields = ("step_key", "error", "enrollment__workflow__name")
    readonly_fields = ("id", "created_at", "updated_at", "executed_at")

    def get_queryset(self, request):
        return StepExecution.unscoped_objects.select_related("tenant", "enrollment")
