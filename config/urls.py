from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from core.views import HealthCheckView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", HealthCheckView.as_view(), name="health-root"),
    path("api/v1/health/", HealthCheckView.as_view(), name="health"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="v1-schema"),
    path("api/v1/docs/", SpectacularSwaggerView.as_view(url_name="v1-schema"), name="v1-swagger-ui"),
    path("api/v1/", include("accounts.urls")),
    path("api/v1/", include("contacts.urls")),
    path("api/v1/notifications/", include("notifications.urls")),
    path("api/v1/integrations/", include("integrations.urls")),
    path("api/v1/messaging/", include("messaging.urls")),
    path("api/v1/automation/", include("automation.urls")),
    path("api/v1/inbox/", include("inbox.urls")),
    path("api/v1/usage/", include("usage.urls")),
    path("api/v1/reports/", include("reports.urls")),
    path("api/v1/webhooks/", include("webhooks.urls")),
    path("api/v1/external/", include("webhooks.external_urls")),
    path("webhooks/", include(("webhooks.urls", "webhooks"), namespace="public-webhooks")),
    path("api/v1/leads/", include("leads.urls")),
]
