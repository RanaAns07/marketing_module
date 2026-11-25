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
def bulk_sync_packages(
    items: list[dict[str, Any]],
    *,
    branding: dict[str, Any] | None = None,
    brand_colors: dict[str, Any] | None = None,
    logo_url: str | None = None,
    icon_url: str | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tenant_id = require_tenant_id()
    if any([branding, brand_colors, logo_url, icon_url, settings]):
        from core.models import Tenant
        tenant = Tenant.objects.filter(pk=tenant_id).first()
        if tenant:
            t_settings = dict(tenant.settings or {})
            if isinstance(settings, dict):
                t_settings.update(settings)
            if isinstance(brand_colors, dict):
                t_settings["brand_colors"] = brand_colors
            elif isinstance(branding, dict):
                t_settings["branding"] = branding
                if "brand_colors" not in t_settings and ("primary" in branding or "primary_color" in branding):
                    t_settings["brand_colors"] = branding
            if logo_url:
                t_settings["logo_url"] = str(logo_url).strip()
            if icon_url:
                t_settings["icon_url"] = str(icon_url).strip()
            tenant.settings = t_settings
            tenant.save(update_fields=["settings", "updated_at"])

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
