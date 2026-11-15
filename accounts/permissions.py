from rest_framework.permissions import SAFE_METHODS, BasePermission
from accounts.authentication import resolve_request_tenant_membership
from accounts.models import Membership
from core.context import set_current_tenant
from core.exceptions import TenantContextRequired


class IsTenantMember(BasePermission):
    message = "Active membership in the specified tenant is required."

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated or not user.is_active:
            return False

        membership = getattr(request, "membership", None)
        if membership is None:
            membership = resolve_request_tenant_membership(request, user)

        if membership is None:
            raise TenantContextRequired("X-Tenant header is required.")

        if not membership.is_active or not membership.tenant.is_active:
            return False

        set_current_tenant(membership.tenant)
        return True


class IsOwner(IsTenantMember):
    message = "Business Owner role is required."

    def has_permission(self, request, view) -> bool:
        if not super().has_permission(request, view):
            return False
        return request.membership.role == Membership.Role.OWNER


class IsOwnerOrAdmin(IsTenantMember):
    message = "Business Owner or Business Administrator role is required."

    def has_permission(self, request, view) -> bool:
        if not super().has_permission(request, view):
            return False
        return request.membership.role in (Membership.Role.OWNER, Membership.Role.ADMIN)


class IsOwnerOrAdminOrReadOnly(IsTenantMember):
    message = "Write access requires Business Owner or Business Administrator role."

    def has_permission(self, request, view) -> bool:
        if not super().has_permission(request, view):
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.membership.role in (Membership.Role.OWNER, Membership.Role.ADMIN)


class CanAccessContactObject(IsTenantMember):
    message = "You do not have permission to access this contact."

    def has_object_permission(self, request, view, obj) -> bool:
        membership = request.membership
        if membership.role in (Membership.Role.OWNER, Membership.Role.ADMIN):
            return True
        if membership.tenant.marketer_can_view_all_contacts:
            return True
        assigned_id = getattr(obj, "assigned_to_id", None)
        return assigned_id in (membership.id, membership.user_id)
