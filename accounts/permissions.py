from collections.abc import Mapping
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import SAFE_METHODS, BasePermission
from accounts.authentication import resolve_request_tenant_membership
from accounts.models import Membership, TenantAPIKey
from core.context import set_current_tenant
from core.exceptions import ApplicationError, TenantContextRequired


class IsEmailVerified(BasePermission):
    message = "Email verification is required to perform this action."

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated or not user.is_active:
            return False
        if not getattr(user, "email_verified", False):
            raise ApplicationError(
                detail="Email verification is required to perform this action.",
                code="email_unverified",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return True


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

        if membership.tenant.deactivated_at is not None:
            if membership.tenant.grace_period_ends_at and timezone.now() > membership.tenant.grace_period_ends_at:
                return False
            if request.method not in SAFE_METHODS and getattr(view, "allow_during_deactivation", False) is not True:
                raise ApplicationError(
                    detail="Tenant is deactivated and in grace period. Writes and sends are disabled.",
                    code="tenant_deactivated",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

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


def _reject_tenant_identifiers_in_api_key_request(request) -> None:
    raw_header = request.headers.get("X-Tenant") or request.META.get("HTTP_X_TENANT")
    if raw_header:
        raise PermissionDenied("X-Tenant header is not permitted on API key requests.")

    query_params = getattr(request, "query_params", None) or {}
    if "tenant" in query_params or "tenant_id" in query_params:
        raise PermissionDenied("Tenant identifiers are not permitted in query parameters for API key requests.")

    if request.method not in SAFE_METHODS:
        data = getattr(request, "data", None)
        if isinstance(data, Mapping) and ("tenant" in data or "tenant_id" in data):
            raise PermissionDenied("Tenant identifiers are not permitted in the request payload for API key requests.")


class HasAPIKeyScope(BasePermission):
    message = "Valid API key with the required scope is required."

    def has_permission(self, request, view) -> bool:
        api_key = getattr(request, "api_key", None)
        if api_key is None and isinstance(getattr(request, "auth", None), TenantAPIKey):
            api_key = request.auth
            request.api_key = api_key

        if api_key is not None:
            if not api_key.is_currently_valid() or not api_key.tenant.is_active:
                return False

            _reject_tenant_identifiers_in_api_key_request(request)
            request.tenant = api_key.tenant
            set_current_tenant(api_key.tenant)

            scopes_by_method = getattr(view, "required_scopes_by_method", None)
            required_scope = (
                scopes_by_method.get(request.method)
                if isinstance(scopes_by_method, Mapping)
                else None
            ) or getattr(view, "required_scope", None)

            if required_scope and not api_key.has_scope(required_scope):
                raise PermissionDenied(f"API key lacks required scope '{required_scope}'.")

            return True

        if getattr(view, "allow_jwt_owner_admin", False):
            return IsOwnerOrAdmin().has_permission(request, view)

        return False
