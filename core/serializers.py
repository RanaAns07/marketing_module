from rest_framework import serializers
from core.context import require_tenant, require_tenant_id
from core.models import TenantOwnedModel


class TenantScopedPrimaryKeyRelatedField(serializers.PrimaryKeyRelatedField):
    def get_queryset(self):
        queryset = self.queryset
        if isinstance(queryset, ( models_manager := getattr(queryset, "model", None) and queryset )):
            pass
        model_cls = getattr(queryset, "model", None)
        if model_cls is not None and issubclass(model_cls, TenantOwnedModel):
            return model_cls.objects.all()
        return super().get_queryset()

    def to_internal_value(self, data):
        tenant_id = require_tenant_id()
        queryset = self.queryset
        model_cls = getattr(queryset, "model", None)
        if model_cls is not None and issubclass(model_cls, TenantOwnedModel):
            try:
                instance = model_cls.unscoped_objects.filter(pk=data).first()
            except Exception:
                instance = None
            if instance is not None and instance.tenant_id != tenant_id:
                raise serializers.ValidationError("Cross-tenant foreign key reference is forbidden.")
        return super().to_internal_value(data)


class TenantScopedSerializer(serializers.ModelSerializer):
    serializer_related_field = TenantScopedPrimaryKeyRelatedField

    def build_relational_field(self, field_name, relation_info):
        field_class, field_kwargs = super().build_relational_field(field_name, relation_info)
        related_model = relation_info.related_model
        if related_model is not None and issubclass(related_model, TenantOwnedModel):
            field_kwargs["queryset"] = related_model.unscoped_objects.all()
        return field_class, field_kwargs

    def validate(self, attrs):
        attrs = super().validate(attrs)
        tenant_id = require_tenant_id()
        for field_name, value in attrs.items():
            if isinstance(value, TenantOwnedModel) and value.tenant_id != tenant_id:
                raise serializers.ValidationError(
                    {field_name: "Cross-tenant foreign key reference is forbidden."}
                )
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    if isinstance(item, TenantOwnedModel) and item.tenant_id != tenant_id:
                        raise serializers.ValidationError(
                            {field_name: "Cross-tenant foreign key reference is forbidden."}
                        )
        return attrs

    def create(self, validated_data):
        if "tenant" not in validated_data and "tenant_id" not in validated_data:
            validated_data["tenant"] = require_tenant()
        return super().create(validated_data)
