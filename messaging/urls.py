from django.urls import include, path
from rest_framework.routers import DefaultRouter
from messaging.views import (
    BroadcastViewSet,
    EmailUnsubscribeConfirmView,
    MessageTemplateViewSet,
    MessageViewSet,
    SMSSegmentCountView,
    WhatsAppWindowStatusView,
)

router = DefaultRouter()
router.register("templates", MessageTemplateViewSet, basename="message-templates")
router.register("broadcasts", BroadcastViewSet, basename="broadcasts")
router.register("messages", MessageViewSet, basename="messages")

urlpatterns = [
    path("sms/segment-count/", SMSSegmentCountView.as_view(), name="sms-segment-count"),
    path("segment-count/", SMSSegmentCountView.as_view(), name="segment-count"),
    path(
        "whatsapp-window/<uuid:contact_id>/",
        WhatsAppWindowStatusView.as_view(),
        name="whatsapp-window-status",
    ),
    path(
        "unsubscribe/confirm/",
        EmailUnsubscribeConfirmView.as_view(),
        name="email-unsubscribe-confirm",
    ),
    path(
        "unsubscribe/<str:token>/",
        EmailUnsubscribeConfirmView.as_view(),
        name="email-unsubscribe-token",
    ),
    path("", include(router.urls)),
]
