import uuid
from drf_spectacular.openapi import AutoSchema
from core.context import get_current_tenant_id, tenant_context

_SCHEMA_SENTINEL_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


class TenantAwareAutoSchema(AutoSchema):
    def get_operation(self, path, path_regex, path_prefix, method, registry):
        if get_current_tenant_id() is None:
            with tenant_context(_SCHEMA_SENTINEL_TENANT_ID):
                return super().get_operation(path, path_regex, path_prefix, method, registry)
        return super().get_operation(path, path_regex, path_prefix, method, registry)
