from typing import Any
import uuid
from django.db.models import QuerySet
from packages.models import Package


def get_packages_queryset(filters: dict[str, Any] | None = None) -> QuerySet[Package]:
    qs = Package.objects.all()
    if not filters:
        return qs
    status_filter = filters.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)
    search = filters.get("search")
    if search:
        qs = qs.filter(name__icontains=search)
    return qs


def get_package_by_external_id_or_id(val: str) -> Package | None:
    clean_val = str(val).strip()
    if not clean_val:
        return None
    try:
        parsed_uuid = uuid.UUID(clean_val)
        by_id = Package.objects.filter(pk=parsed_uuid).first()
        if by_id is not None:
            return by_id
    except (ValueError, TypeError):
        pass
    return Package.objects.filter(external_id=clean_val).first()
