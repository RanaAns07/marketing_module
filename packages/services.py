from typing import Any
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status as status_codes
from core.context import require_tenant_id
from core.exceptions import ApplicationError
from packages.models import Package


@transaction.atomic
def upsert_package_from_external(
    *,
    external_id: str,
    name: str,
    description: str = "",
    price: Any = 0.00,
    currency: str = "USD",
    billing_interval: str = "one_time",
    status: str = "active",
    external_url: str = "",
    features: list[Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[Package, bool]:
    clean_ext_id = str(external_id).strip()
    if not clean_ext_id:
        raise ApplicationError(
            detail="Package external_id is required.",
            code="missing_package_external_id",
            status_code=status_codes.HTTP_400_BAD_REQUEST,
        )

    clean_name = str(name).strip()
    if not clean_name:
        raise ApplicationError(
            detail="Package name is required.",
            code="missing_package_name",
            status_code=status_codes.HTTP_400_BAD_REQUEST,
        )

    now = timezone.now()
    created = False
    try:
        with transaction.atomic():
            package = (
                Package.objects.select_for_update()
                .filter(external_id=clean_ext_id)
                .first()
            )
            if package is None:
                package = Package(
                    external_id=clean_ext_id,
                    name=clean_name,
                    description=description or "",
                    price=price or 0.00,
                    currency=(currency or "USD").upper(),
                    billing_interval=billing_interval or Package.BillingInterval.ONE_TIME,
                    status=status or Package.Status.ACTIVE,
                    external_url=external_url or "",
                    features=features or [],
                    metadata=metadata or {},
                    synced_at=now,
                )
                package.save()
                created = True
    except IntegrityError:
        package = (
            Package.objects.select_for_update()
            .filter(external_id=clean_ext_id)
            .first()
        )
        created = False

    if not created and package is not None:
        package.name = clean_name
        if description is not None:
            package.description = description
        if price is not None:
            package.price = price
        if currency:
            package.currency = currency.upper()
        if billing_interval:
            package.billing_interval = billing_interval
        if status:
            package.status = status
        if external_url is not None:
            package.external_url = external_url
        if features is not None:
            package.features = features
        if metadata is not None:
            package.metadata = metadata
        package.synced_at = now
        package.save(
            update_fields=[
                "name",
                "description",
                "price",
                "currency",
                "billing_interval",
                "status",
                "external_url",
                "features",
                "metadata",
                "synced_at",
                "updated_at",
            ]
        )

    return package, created


@transaction.atomic
def bulk_sync_packages(items: list[dict[str, Any]]) -> dict[str, Any]:
    created_count = 0
    updated_count = 0
    synced_packages: list[Package] = []
    for item in items:
        pkg, created = upsert_package_from_external(**item)
        if created:
            created_count += 1
        else:
            updated_count += 1
        synced_packages.append(pkg)
    return {
        "total": len(items),
        "created": created_count,
        "updated": updated_count,
        "packages": synced_packages,
    }
