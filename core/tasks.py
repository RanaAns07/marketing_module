from importlib import import_module
from typing import Any
from celery import current_app, shared_task
from core.context import TenantAwareTask
from core.events import SUBSCRIBER_TASKS


@shared_task(base=TenantAwareTask, bind=True, name="core.tasks.fanout_domain_event")
def fanout_domain_event(self, tenant_id: str, event: dict[str, Any]) -> dict[str, Any]:
    dispatched = []
    for subscriber_name, task_name, queue_name in SUBSCRIBER_TASKS:
        try:
            if task_name not in current_app.tasks:
                module_path = task_name.rsplit(".", 1)[0]
                import_module(module_path)
            task = current_app.tasks.get(task_name)
            if task is not None:
                task.apply_async(args=[tenant_id, event], queue=queue_name)
            else:
                current_app.send_task(
                    task_name,
                    args=[tenant_id, event],
                    queue=queue_name,
                )
            dispatched.append(subscriber_name)
        except Exception:
            pass
    return {"event_id": event.get("event_id"), "dispatched": dispatched}
