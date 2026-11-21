from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.authentication import APIKeyHMACAuthentication
from accounts.models import TenantAPIKey
from accounts.permissions import HasAPIKeyScope
from accounts.throttling import APIKeyRateThrottle, TenantRateThrottle
from core.exceptions import ApplicationError
from packages.selectors import get_package_by_external_id_or_id
from packages.serializers import (
    BulkPackageSyncSerializer,
    PackageSerializer,
    PackageSyncItemSerializer,
    PackageSyncResponseSerializer,
)
from packages.services import bulk_sync_packages, upsert_package_from_external

MAX_PACKAGE_PAYLOAD_BYTES = 5 * 1024 * 1024


class BaseExternalPackageAPIView(APIView):
    authentication_classes = [APIKeyHMACAuthentication]
    permission_classes = [HasAPIKeyScope]
    throttle_classes = [APIKeyRateThrottle, TenantRateThrottle]
    parser_classes = [JSONParser]

    def initial(self, request, *args, **kwargs):
        content_length = request.META.get("CONTENT_LENGTH")
        if content_length:
            try:
                if int(content_length) > MAX_PACKAGE_PAYLOAD_BYTES:
                    raise ApplicationError(
                        detail="Request payload exceeds size limit.",
                        code="payload_too_large",
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    )
            except (ValueError, TypeError):
                pass
        try:
            body = getattr(request, "_body", None) or getattr(request, "body", None)
            if body and len(body) > MAX_PACKAGE_PAYLOAD_BYTES:
                raise ApplicationError(
                    detail="Request payload exceeds size limit.",
                    code="payload_too_large",
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )
        except ApplicationError:
            raise
        except Exception:
            pass
        super().initial(request, *args, **kwargs)


class ExternalPackageUpsertView(BaseExternalPackageAPIView):
    required_scope = TenantAPIKey.Scope.PACKAGES_WRITE

    @extend_schema(
        summary="Upsert single package from external platform",
        request=PackageSyncItemSerializer,
        responses={200: PackageSerializer, 201: PackageSerializer},
    )
    def post(self, request):
        serializer = PackageSyncItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pkg, created = upsert_package_from_external(**serializer.validated_data)
        return Response(
            PackageSerializer(pkg, context={"request": request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ExternalPackageBulkSyncView(BaseExternalPackageAPIView):
    required_scope = TenantAPIKey.Scope.PACKAGES_WRITE

    @extend_schema(
        summary="Bulk synchronize package catalog from external platform",
        request=BulkPackageSyncSerializer,
        responses={200: PackageSyncResponseSerializer},
    )
    def post(self, request):
        serializer = BulkPackageSyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = bulk_sync_packages(serializer.validated_data["packages"])
        return Response(
            {
                "status": "success",
                "total": result["total"],
                "created": result["created"],
                "updated": result["updated"],
                "packages": PackageSerializer(
                    result["packages"],
                    many=True,
                    context={"request": request},
                ).data,
            },
            status=status.HTTP_200_OK,
        )


class ExternalPackageDetailView(BaseExternalPackageAPIView):
    required_scope = TenantAPIKey.Scope.PACKAGES_READ

    @extend_schema(
        summary="Retrieve package by external_id or UUID from external platform",
        responses={200: PackageSerializer},
    )
    def get(self, request, external_id_or_id: str):
        pkg = get_package_by_external_id_or_id(external_id_or_id)
        if pkg is None:
            raise NotFound("Package not found.")
        return Response(
            PackageSerializer(pkg, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )
