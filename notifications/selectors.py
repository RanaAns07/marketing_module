import uuid
from django.db.models import QuerySet
from accounts.models import User
from notifications.models import Notification


def get_user_notifications_queryset(user: User) -> QuerySet[Notification]:
    return (
        Notification.objects.select_related("recipient", "tenant")
        .filter(recipient=user)
        .order_by("-created_at", "-id")
    )


def get_user_notification_by_id(user: User, notification_id: uuid.UUID | str) -> Notification | None:
    return (
        Notification.objects.select_related("recipient", "tenant")
        .filter(recipient=user, pk=notification_id)
        .first()
    )


def get_unread_notifications_count(user: User) -> int:
    return Notification.objects.filter(recipient=user, is_read=False).count()
