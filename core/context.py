from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Optional, Union
import uuid
from celery import Task
from core.exceptions import TenantContextRequired

_current_tenant_id: ContextVar[Optional[uuid.UUID]] = ContextVar("current_tenant_id", default=None)
_current_tenant_obj: ContextVar[Optional[object]] = ContextVar("current_tenant_obj", default=None)


def _normalize_uuid(value: Union[uuid.UUID, str]) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def set_current_tenant(tenant_or_id: Union[object, uuid.UUID, str, None]) -> None:
    if tenant_or_id is None:
        _current_tenant_id.set(None)
        _current_tenant_obj.set(None)
        return
    if hasattr(tenant_or_id, "pk"):
        _current_tenant_id.set(_normalize_uuid(tenant_or_id.pk))
        _current_tenant_obj.set(tenant_or_id)
    else:
        _current_tenant_id.set(_normalize_uuid(tenant_or_id))
        _current_tenant_obj.set(None)


def clear_current_tenant() -> None:
    _current_tenant_id.set(None)
    _current_tenant_obj.set(None)


def get_current_tenant_id() -> Optional[uuid.UUID]:
    return _current_tenant_id.get()


def get_current_tenant():
    tenant = _current_tenant_obj.get()
    if tenant is not None:
        return tenant
    tenant_id = _current_tenant_id.get()
    if tenant_id is None:
        return None
    from core.models import Tenant
    tenant = Tenant.objects.filter(pk=tenant_id).first()
    _current_tenant_obj.set(tenant)
    return tenant


def require_tenant_id() -> uuid.UUID:
    tenant_id = _current_tenant_id.get()
    if tenant_id is None:
        raise TenantContextRequired()
    return tenant_id


def require_tenant():
    tenant = get_current_tenant()
    if tenant is None:
        raise TenantContextRequired()
    return tenant


@contextmanager
def tenant_context(tenant_or_id: Union[object, uuid.UUID, str]):
    prev_id = _current_tenant_id.get()
    prev_obj = _current_tenant_obj.get()
    set_current_tenant(tenant_or_id)
    try:
        yield get_current_tenant_id()
    finally:
        _current_tenant_id.set(prev_id)
        _current_tenant_obj.set(prev_obj)


class TenantAwareTask(Task):
    abstract = True

    def __call__(self, *args, **kwargs):
        tenant_id = kwargs.get("tenant_id")
        if tenant_id is None and args:
            first_arg = args[0]
            if isinstance(first_arg, (str, uuid.UUID)):
                try:
                    uuid.UUID(str(first_arg))
                    tenant_id = first_arg
                except ValueError:
                    tenant_id = None
            elif isinstance(first_arg, dict) and "tenant_id" in first_arg:
                tenant_id = first_arg["tenant_id"]

        if tenant_id is not None:
            with tenant_context(tenant_id):
                return super().__call__(*args, **kwargs)
        return super().__call__(*args, **kwargs)


def with_tenant_context(func):
    @wraps(func)
    def wrapper(tenant_id, *args, **kwargs):
        with tenant_context(tenant_id):
            return func(tenant_id, *args, **kwargs)
    return wrapper
