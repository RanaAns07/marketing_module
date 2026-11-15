from typing import Any
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from automation.models import StepExecution, Workflow, WorkflowEnrollment, WorkflowStep
from automation.validators import validate_step_config, validate_trigger_config
from contacts.models import Contact
from core.serializers import TenantScopedPrimaryKeyRelatedField, TenantScopedSerializer


@extend_schema_field(OpenApiTypes.OBJECT)
class PositionField(serializers.Field):
    def to_representation(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, int):
            return {"order": value}
        return {}

    def to_internal_value(self, data: Any) -> dict[str, Any]:
        if data is None:
            return {}
        if isinstance(data, int):
            return {"order": data}
        if isinstance(data, dict):
            return dict(data)
        raise serializers.ValidationError("position must be an integer or a JSON object.")


class WorkflowStepInputSerializer(serializers.Serializer):
    key = serializers.CharField(max_length=100)
    type = serializers.ChoiceField(choices=WorkflowStep.StepType.choices)
    subtype = serializers.ChoiceField(choices=WorkflowStep.Subtype.choices)
    config = serializers.DictField(required=False, default=dict)
    next_key = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    true_key = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    false_key = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    position = PositionField(required=False, default=dict)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        errors = validate_step_config(
            step_key=attrs.get("key", ""),
            step_type=attrs.get("type", ""),
            subtype=attrs.get("subtype", ""),
            config=attrs.get("config", {}),
        )
        if errors:
            raise serializers.ValidationError({err["field"]: err["message"] for err in errors})
        return attrs


class WorkflowStepSerializer(TenantScopedSerializer):
    class Meta:
        model = WorkflowStep
        fields = [
            "id",
            "key",
            "type",
            "subtype",
            "config",
            "next_key",
            "true_key",
            "false_key",
            "position",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class WorkflowSerializer(TenantScopedSerializer):
    steps = WorkflowStepInputSerializer(many=True, required=False)

    class Meta:
        model = Workflow
        fields = [
            "id",
            "name",
            "description",
            "status",
            "trigger_type",
            "trigger_config",
            "re_entry",
            "stop_on_reply",
            "is_promotional",
            "exit_events",
            "entry_step_key",
            "version",
            "steps",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "version", "created_at", "updated_at"]

    def validate_trigger_type(self, value: str) -> str:
        norm = (value or "").strip().lower()
        if norm == "lead_created":
            return Workflow.TriggerType.NEW_LEAD_CREATED
        if norm == "email_received":
            return Workflow.TriggerType.EMAIL_REPLIED
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = super().validate(attrs)
        trig_type = attrs.get(
            "trigger_type",
            self.instance.trigger_type if self.instance else Workflow.TriggerType.NEW_LEAD_CREATED,
        )
        trig_cfg = attrs.get(
            "trigger_config",
            self.instance.trigger_config if self.instance else {},
        )
        trig_errors = validate_trigger_config(trig_type, trig_cfg)
        if trig_errors:
            raise serializers.ValidationError(
                {err["field"]: err["message"] for err in trig_errors}
            )
        return attrs

    def to_representation(self, instance: Workflow) -> dict[str, Any]:
        data = super().to_representation(instance)
        steps_qs = instance.steps.all().order_by("created_at", "key")
        data["steps"] = WorkflowStepSerializer(steps_qs, many=True).data
        return data


class WorkflowDryRunValidateSerializer(serializers.Serializer):
    trigger_type = serializers.ChoiceField(
        choices=Workflow.TriggerType.choices,
        required=False,
        default=Workflow.TriggerType.NEW_LEAD_CREATED,
    )
    trigger_config = serializers.DictField(required=False, default=dict)
    entry_step_key = serializers.CharField(required=False, allow_blank=True, default="")
    strict_activation = serializers.BooleanField(required=False, default=True)
    steps = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        default=list,
    )


class WorkflowValidationResultSerializer(serializers.Serializer):
    valid = serializers.BooleanField()
    errors = serializers.ListField(child=serializers.DictField())


class WorkflowDuplicateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class ManualEnrollSerializer(serializers.Serializer):
    workflow_id = TenantScopedPrimaryKeyRelatedField(
        queryset=Workflow.unscoped_objects.all(),
        source="workflow",
        required=False,
    )
    contact_id = TenantScopedPrimaryKeyRelatedField(
        queryset=Contact.unscoped_objects.all(),
        source="contact",
        required=False,
    )
    contact_ids = TenantScopedPrimaryKeyRelatedField(
        queryset=Contact.unscoped_objects.all(),
        many=True,
        source="contacts",
        required=False,
    )
    allow_inactive = serializers.BooleanField(required=False, default=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs.get("contact") and not attrs.get("contacts"):
            raise serializers.ValidationError("contact_id or contact_ids is required.")
        return attrs


class ManualUnenrollSerializer(serializers.Serializer):
    contact_id = TenantScopedPrimaryKeyRelatedField(
        queryset=Contact.unscoped_objects.all(),
        source="contact",
        required=False,
    )
    enrollment_id = TenantScopedPrimaryKeyRelatedField(
        queryset=WorkflowEnrollment.unscoped_objects.all(),
        source="enrollment",
        required=False,
    )
    reason = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        default="manual_unenroll",
    )


class ContactPauseResumeSerializer(serializers.Serializer):
    contact_id = TenantScopedPrimaryKeyRelatedField(
        queryset=Contact.unscoped_objects.all(),
        source="contact",
        required=False,
    )
    reason = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        default="manual_pause_all",
    )


class WorkflowEnrollmentSerializer(TenantScopedSerializer):
    workflow_name = serializers.CharField(source="workflow.name", read_only=True)
    contact_name = serializers.CharField(source="contact.full_name", read_only=True)
    contact_email = serializers.EmailField(source="contact.email", read_only=True)
    contact_phone = serializers.CharField(source="contact.phone", read_only=True)

    class Meta:
        model = WorkflowEnrollment
        fields = [
            "id",
            "workflow",
            "workflow_name",
            "contact",
            "contact_name",
            "contact_email",
            "contact_phone",
            "status",
            "current_step_key",
            "next_run_at",
            "paused_reason",
            "waiting_for_event",
            "started_at",
            "finished_at",
            "iteration",
            "locked_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class StepExecutionSerializer(TenantScopedSerializer):
    workflow_id = serializers.UUIDField(source="enrollment.workflow_id", read_only=True)
    workflow_name = serializers.CharField(source="enrollment.workflow.name", read_only=True)
    contact_id = serializers.UUIDField(source="enrollment.contact_id", read_only=True)
    contact_name = serializers.CharField(source="enrollment.contact.full_name", read_only=True)

    class Meta:
        model = StepExecution
        fields = [
            "id",
            "enrollment",
            "workflow_id",
            "workflow_name",
            "contact_id",
            "contact_name",
            "step_key",
            "iteration",
            "status",
            "attempts",
            "error",
            "result",
            "executed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
