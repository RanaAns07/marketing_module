from django.urls import include, path
from rest_framework.routers import DefaultRouter
from integrations.models import InboundWebhookEvent
from messaging.views import EmailUnsubscribeConfirmView
from webhooks.views import (
    ExternalEventViewSet,
    MetaWebhookView,
    SendGridWebhookView,
    TwilioSMSWebhookView,
    TwilioWhatsAppWebhookView,
    WebhookDeliveryViewSet,
    WebhookEndpointViewSet,
)

router = DefaultRouter()
router.register("endpoints", WebhookEndpointViewSet, basename="webhook-endpoint")
router.register("deliveries", WebhookDeliveryViewSet, basename="webhook-delivery")
router.register("external-events", ExternalEventViewSet, basename="external-event")

urlpatterns = [
    path(
        "meta/",
        MetaWebhookView.as_view(),
        name="webhook-meta",
    ),
    path(
        "twilio/sms/",
        TwilioSMSWebhookView.as_view(),
        name="webhook-twilio-sms",
    ),
    path(
        "twilio/sms/inbound/",
        TwilioSMSWebhookView.as_view(),
        {"event_type": InboundWebhookEvent.EventType.INBOUND},
        name="webhook-twilio-sms-inbound",
    ),
    path(
        "twilio/sms/status/",
        TwilioSMSWebhookView.as_view(),
        {"event_type": InboundWebhookEvent.EventType.STATUS},
        name="webhook-twilio-sms-status",
    ),
    path(
        "twilio/whatsapp/",
        TwilioWhatsAppWebhookView.as_view(),
        name="webhook-twilio-whatsapp",
    ),
    path(
        "twilio/whatsapp/inbound/",
        TwilioWhatsAppWebhookView.as_view(),
        {"event_type": InboundWebhookEvent.EventType.INBOUND},
        name="webhook-twilio-whatsapp-inbound",
    ),
    path(
        "twilio/whatsapp/status/",
        TwilioWhatsAppWebhookView.as_view(),
        {"event_type": InboundWebhookEvent.EventType.STATUS},
        name="webhook-twilio-whatsapp-status",
    ),
    path(
        "sendgrid/<str:webhook_token>/",
        SendGridWebhookView.as_view(),
        name="webhook-sendgrid-events",
    ),
    path(
        "sendgrid/<str:webhook_token>/inbound/",
        SendGridWebhookView.as_view(),
        {"mode": "inbound"},
        name="webhook-sendgrid-inbound",
    ),
    path(
        "unsubscribe/confirm/",
        EmailUnsubscribeConfirmView.as_view(),
        name="webhook-email-unsubscribe-confirm",
    ),
    path(
        "unsubscribe/<str:token>/",
        EmailUnsubscribeConfirmView.as_view(),
        name="webhook-email-unsubscribe-token",
    ),
    path("", include(router.urls)),
]
