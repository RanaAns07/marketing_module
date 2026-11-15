import hashlib
import hmac
import uuid
from django.utils import timezone
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication
from accounts.models import Membership, TenantAPIKey
from core.context import set_current_tenant


def resolve_request_tenant_membership(request, user):
    raw_tenant_id = request.headers.get("X-Tenant") or request.META.get("HTTP_X_TENANT")
    if not raw_tenant_id:
        return None
    try:
        tenant_uuid = uuid.UUID(str(raw_tenant_id))
    except (ValueError, TypeError) as exc:
        raise PermissionDenied("Invalid X-Tenant header UUID.") from exc

    membership = (
        Membership.unscoped_objects.select_related("tenant", "user")
        .filter(
            tenant_id=tenant_uuid,
            user=user,
            is_active=True,
            tenant__is_active=True,
        )
        .first()
    )
    if membership is None:
        raise PermissionDenied("User does not have active membership in the specified tenant.")

    request.tenant = membership.tenant
    request.membership = membership
    set_current_tenant(membership.tenant)
    return membership


class TenantJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, validated_token = result
        if not user.is_active:
            raise AuthenticationFailed("User account is disabled.")
        resolve_request_tenant_membership(request, user)
        return user, validated_token


class APIKeyHMACAuthentication(BaseAuthentication):
    def authenticate(self, request):
        key_id = request.headers.get("X-API-Key") or request.META.get("HTTP_X_API_KEY")
        signature = request.headers.get("X-Signature") or request.META.get("HTTP_X_SIGNATURE")
        if not key_id or not signature:
            return None

        api_key_obj = (
            TenantAPIKey.unscoped_objects.select_related("tenant")
            .filter(key_id=key_id, is_active=True, tenant__is_active=True)
            .first()
        )
        if api_key_obj is None:
            raise AuthenticationFailed("Invalid or inactive API key.")

        raw_tenant_id = request.headers.get("X-Tenant") or request.META.get("HTTP_X_TENANT")
        if raw_tenant_id and str(api_key_obj.tenant_id) != str(raw_tenant_id):
            raise PermissionDenied("API key does not match X-Tenant header.")

        timestamp = request.headers.get("X-Timestamp") or request.META.get("HTTP_X_TIMESTAMP") or ""
        body_bytes = request.body or b""
        if timestamp:
            signing_payload = f"{timestamp}.".encode("utf-8") + body_bytes
        else:
            signing_payload = body_bytes

        expected_sig = hmac.new(
            api_key_obj.secret_encrypted.encode("utf-8"),
            signing_payload,
            hashlib.sha256,
        ).hexdigest()

        provided_sig = signature.removeprefix("sha256=")
        if not hmac.compare_digest(expected_sig, provided_sig):
            raise AuthenticationFailed("Invalid HMAC signature.")

        owner_membership = (
            Membership.unscoped_objects.select_related("tenant", "user")
            .filter(
                tenant_id=api_key_obj.tenant_id,
                is_active=True,
                role__in=[Membership.Role.OWNER, Membership.Role.ADMIN],
            )
            .order_by("created_at")
            .first()
        )
        if owner_membership is None:
            raise AuthenticationFailed("Tenant has no active owner or administrator.")

        TenantAPIKey.unscoped_objects.filter(pk=api_key_obj.pk).update(last_used_at=timezone.now())
        request.tenant = api_key_obj.tenant
        request.membership = owner_membership
        request.api_key = api_key_obj
        set_current_tenant(api_key_obj.tenant)
        return owner_membership.user, api_key_obj


class TenantJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "accounts.authentication.TenantJWTAuthentication"
    name = "BearerAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }


class APIKeyHMACAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "accounts.authentication.APIKeyHMACAuthentication"
    name = "APIKeyHMACAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
        }
