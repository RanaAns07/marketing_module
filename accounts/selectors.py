from typing import Any
import uuid
from django.db.models import Count, F, QuerySet
from accounts.models import Membership, TenantAPIKey, TenantInvitation, User


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


def get_team_workload() -> list[dict[str, Any]]:
    from contacts.models import Contact
    from inbox.models import Conversation

    memberships = (
        Membership.objects.select_related("user")
        .filter(is_active=True, user__is_active=True)
        .order_by("user__first_name", "user__last_name", "created_at")
    )
    m_list = list(memberships)
    if not m_list:
        return []

    leads_counts = {
        row["assigned_to"]: row["count"]
        for row in Contact.objects.filter(assigned_to__in=m_list)
        .values("assigned_to")
        .annotate(count=Count("id"))
    }
    open_convs_counts = {
        row["assigned_to"]: row["count"]
        for row in Conversation.objects.filter(
            assigned_to__in=m_list, status=Conversation.Status.OPEN
        )
        .values("assigned_to")
        .annotate(count=Count("id"))
    }
    resolved_convs_counts = {
        row["assigned_to"]: row["count"]
        for row in Conversation.objects.filter(
            assigned_to__in=m_list, status=Conversation.Status.RESOLVED
        )
        .values("assigned_to")
        .annotate(count=Count("id"))
    }

    workload = []
    for m in m_list:
        workload.append(
            {
                "membership_id": str(m.id),
                "user_id": str(m.user_id),
                "name": m.user.full_name or m.user.email,
                "email": m.user.email,
                "role": m.role,
                "is_available": m.is_available,
                "assigned_leads_count": leads_counts.get(m.id, 0),
                "open_conversations_count": open_convs_counts.get(m.id, 0),
                "resolved_conversations_count": resolved_convs_counts.get(m.id, 0),
            }
        )
    return workload


def get_tenant_invitations_queryset() -> QuerySet[TenantInvitation]:
    return TenantInvitation.objects.select_related("tenant", "invited_by").order_by("-created_at")


def get_tenant_invitation_by_id(invitation_id: uuid.UUID | str) -> TenantInvitation | None:
    return (
        TenantInvitation.objects.select_related("tenant", "invited_by")
        .filter(pk=invitation_id)
        .first()
    )

