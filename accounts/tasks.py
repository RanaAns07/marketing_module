from typing import Any
from celery import shared_task
from django.utils import timezone
from accounts.models import TenantAPIKey
from core.context import TenantAwareTask


@shared_task(base=TenantAwareTask, bind=True, name="accounts.tasks.revoke_api_key_task")
def revoke_api_key_task(self, tenant_id: str, api_key_id: str) -> dict[str, Any]:
    now = timezone.now()
    api_key = TenantAPIKey.objects.filter(pk=api_key_id).first()
    if api_key is None:
        return {"api_key_id": api_key_id, "revoked": False}
    if api_key.revoked_at is not None and api_key.revoked_at > now:
        return {"api_key_id": api_key_id, "revoked": False, "scheduled_for": api_key.revoked_at.isoformat()}
    api_key.is_active = False
    if api_key.revoked_at is None:
        api_key.revoked_at = now
    api_key.save(update_fields=["is_active", "revoked_at", "updated_at"])
    return {"api_key_id": str(api_key.id), "revoked": True}
