import uuid
from django.contrib import admin
from django.contrib.admin.utils import unquote
from core.context import get_current_tenant_id, tenant_context
from core.models import Tenant


class UnscopedTenantModelAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        if hasattr(self.model, "unscoped_objects"):
            return self.model.unscoped_objects.all()
        return super().get_queryset(request)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        remote_model = db_field.remote_field.model
        if hasattr(remote_model, "unscoped_objects"):
            if "queryset" not in kwargs:
                kwargs["queryset"] = remote_model.unscoped_objects.all()
            if get_current_tenant_id() is None:
                with tenant_context(uuid.UUID(int=0)):
                    return super().formfield_for_foreignkey(db_field, request, **kwargs)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        remote_model = db_field.remote_field.model
        if hasattr(remote_model, "unscoped_objects"):
            if "queryset" not in kwargs:
                kwargs["queryset"] = remote_model.unscoped_objects.all()
            if get_current_tenant_id() is None:
                with tenant_context(uuid.UUID(int=0)):
                    return super().formfield_for_manytomany(db_field, request, **kwargs)
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def _resolve_admin_tenant_id(self, request, object_id=None):
        if object_id is not None and hasattr(self.model, "unscoped_objects"):
            obj = self.get_object(request, unquote(object_id))
            if obj is not None and getattr(obj, "tenant_id", None):
                return obj.tenant_id
        if request.method == "POST":
            raw_tenant = request.POST.get("tenant")
            if raw_tenant:
                try:
                    return uuid.UUID(str(raw_tenant).strip())
                except (ValueError, TypeError):
                    return None
        return None

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        tenant_id = self._resolve_admin_tenant_id(request, object_id)
        if tenant_id is not None:
            with tenant_context(tenant_id):
                return super().changeform_view(request, object_id, form_url, extra_context)
        return super().changeform_view(request, object_id, form_url, extra_context)

    def delete_view(self, request, object_id, extra_context=None):
        tenant_id = self._resolve_admin_tenant_id(request, object_id)
        if tenant_id is not None:
            with tenant_context(tenant_id):
                return super().delete_view(request, object_id, extra_context)
        return super().delete_view(request, object_id, extra_context)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "timezone", "is_active", "assignment_mode", "created_at")
    search_fields = ("name", "slug")
    list_filter = ("is_active", "assignment_mode")
