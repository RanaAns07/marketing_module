from django.core.cache import cache
from django.db import connection
from rest_framework import serializers, status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.permissions import IsTenantMember
from core.context import require_tenant


class TenantScopedViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            model = getattr(self.serializer_class, "Meta", None)
            if model is not None and hasattr(model, "model"):
                return model.model.unscoped_objects.none()
        return super().get_queryset()

    def perform_create(self, serializer):
        serializer.save(tenant=require_tenant())


class HealthStatusSerializer(serializers.Serializer):
    status = serializers.CharField()
    database = serializers.CharField()
    cache = serializers.CharField()


class HealthCheckView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = HealthStatusSerializer

    def get(self, request):
        db_status = "ok"
        cache_status = "ok"
        http_status = status.HTTP_200_OK

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:
            db_status = "error"
            http_status = status.HTTP_503_SERVICE_UNAVAILABLE

        try:
            cache.set("healthcheck_ping", "pong", timeout=5)
            if cache.get("healthcheck_ping") != "pong":
                cache_status = "error"
        except Exception:
            cache_status = "degraded"

        return Response(
            {
                "status": "ok" if http_status == status.HTTP_200_OK else "error",
                "database": db_status,
                "cache": cache_status,
            },
            status=http_status,
        )
