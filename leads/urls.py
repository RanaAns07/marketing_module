from django.urls import include, path
from rest_framework.routers import DefaultRouter
from leads.views import (
    MetaConnectionStatusView,
    MetaDisconnectView,
    MetaLeadEventViewSet,
    MetaLeadFormViewSet,
    MetaOAuthCallbackView,
    MetaOAuthConnectURLView,
    MetaPageViewSet,
    MetaReconnectView,
)

router = DefaultRouter()
router.register("pages", MetaPageViewSet, basename="meta-page")
router.register("forms", MetaLeadFormViewSet, basename="meta-form")
router.register("events", MetaLeadEventViewSet, basename="meta-lead-event")

urlpatterns = [
    path("connect-url/", MetaOAuthConnectURLView.as_view(), name="leads-meta-connect-url"),
    path("callback/", MetaOAuthCallbackView.as_view(), name="leads-meta-callback"),
    path("connection/", MetaConnectionStatusView.as_view(), name="leads-meta-connection-status"),
    path("reconnect/", MetaReconnectView.as_view(), name="leads-meta-reconnect"),
    path("disconnect/", MetaDisconnectView.as_view(), name="leads-meta-disconnect"),
    path("", include(router.urls)),
]
