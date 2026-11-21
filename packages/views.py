from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from core.views import TenantScopedViewSet
from packages.models import Package
from packages.selectors import get_packages_queryset
from packages.serializers import PackageSerializer


class PackageViewSet(TenantScopedViewSet):
    serializer_class = PackageSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Package.unscoped_objects.none()
        return get_packages_queryset(self.request.query_params)

    @extend_schema(summary="List tenant packages")
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(summary="Create a package manually")
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @extend_schema(summary="Retrieve package details")
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(summary="Update package details")
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @extend_schema(summary="Partial update package details")
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @extend_schema(summary="Delete/archive a package")
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)
