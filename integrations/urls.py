from django.urls import include, path
from rest_framework.routers import DefaultRouter
from integrations.views import ChannelConnectionViewSet

router = DefaultRouter()
router.register("connections", ChannelConnectionViewSet, basename="channel-connections")

urlpatterns = [
    path("", include(router.urls)),
]
