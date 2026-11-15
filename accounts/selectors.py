import uuid
from django.db.models import F, QuerySet
from accounts.models import Membership, TenantAPIKey, User


def get_user_memberships(user: User) -> QuerySet[Membership]:
    return (
        Membership.unscoped_objects.select_related("tenant", "user")
        .filter(user=user, is_active=True, tenant__is_active=True)
        .order_by("created_at")
    )


def get_team_members_queryset() -> QuerySet[Membership]:
    return Membership.objects.select_related("user", "tenant").order_by("created_at")


def get_team_member_by_id(membership_id: uuid.UUID | str) -> Membership | None:
    return (
        Membership.objects.select_related("user", "tenant")
        .filter(pk=membership_id)
        .first()
    )


def get_available_round_robin_memberships() -> QuerySet[Membership]:
    return (
        Membership.objects.select_related("user", "tenant")
        .filter(
            is_active=True,
            is_available=True,
            user__is_active=True,
            role__in=[Membership.Role.MARKETER, Membership.Role.ADMIN],
        )
        .order_by(F("last_assigned_at").asc(nulls_first=True), "created_at")
    )


def get_tenant_api_keys_queryset() -> QuerySet[TenantAPIKey]:
    return TenantAPIKey.objects.select_related("tenant").order_by("-created_at")
