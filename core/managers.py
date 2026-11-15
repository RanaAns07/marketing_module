from django.db import models
from core.context import require_tenant_id
from core.exceptions import CrossTenantAccessError


class TenantScopedQuerySet(models.QuerySet):
    def create(self, **kwargs):
        active_tenant_id = require_tenant_id()
        tenant = kwargs.get("tenant")
        tenant_id = kwargs.get("tenant_id")
        if tenant is not None and tenant.pk != active_tenant_id:
            raise CrossTenantAccessError()
        if tenant_id is not None and tenant_id != active_tenant_id:
            raise CrossTenantAccessError()
        if tenant is None and tenant_id is None:
            kwargs["tenant_id"] = active_tenant_id
        return super().create(**kwargs)

    def bulk_create(self, objs, **kwargs):
        active_tenant_id = require_tenant_id()
        for obj in objs:
            if obj.tenant_id is not None and obj.tenant_id != active_tenant_id:
                raise CrossTenantAccessError()
            obj.tenant_id = active_tenant_id
        return super().bulk_create(objs, **kwargs)


class TenantScopedManager(models.Manager):
    def get_queryset(self):
        tenant_id = require_tenant_id()
        return TenantScopedQuerySet(self.model, using=self._db).filter(tenant_id=tenant_id)

    def none(self):
        return TenantScopedQuerySet(self.model, using=self._db).none()
