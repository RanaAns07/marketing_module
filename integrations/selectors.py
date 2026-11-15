import uuid
from django.db.models import QuerySet
from integrations.models import ChannelConnection, InboundWebhookEvent


def get_channel_connections_queryset() -> QuerySet[ChannelConnection]:
    return ChannelConnection.objects.select_related("tenant").order_by("channel", "-created_at")


def get_channel_connection_by_id(connection_id: uuid.UUID | str) -> ChannelConnection | None:
    return get_channel_connections_queryset().filter(pk=connection_id).first()


def get_channel_connection_for_channel(channel: str) -> ChannelConnection | None:
    return get_channel_connections_queryset().filter(channel=channel.strip().lower()).first()


def get_inbound_webhook_events_queryset() -> QuerySet[InboundWebhookEvent]:
    return InboundWebhookEvent.objects.select_related("tenant", "connection").order_by("-created_at")
