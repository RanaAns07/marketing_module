import uuid
from core.context import clear_current_tenant, set_current_tenant


class TenantContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        clear_current_tenant()
        request.tenant = None
        request.membership = None
        raw_tenant_id = request.headers.get("X-Tenant") or request.META.get("HTTP_X_TENANT")
        request.tenant_header = raw_tenant_id

        if raw_tenant_id and hasattr(request, "user") and request.user.is_authenticated:
            try:
                tenant_uuid = uuid.UUID(str(raw_tenant_id))
                from accounts.models import Membership
                membership = (
                    Membership.unscoped_objects.select_related("tenant", "user")
                    .filter(
                        tenant_id=tenant_uuid,
                        user=request.user,
                        is_active=True,
                        tenant__is_active=True,
                    )
                    .first()
                )
                if membership is not None:
                    request.tenant = membership.tenant
                    request.membership = membership
                    set_current_tenant(membership.tenant)
            except (ValueError, TypeError):
                pass

        try:
            return self.get_response(request)
        finally:
            clear_current_tenant()
