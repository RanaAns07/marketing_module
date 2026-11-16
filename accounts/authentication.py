from datetime import timezone as dt_timezone
import hashlib
import hmac
import uuid
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication
from accounts.models import Membership, TenantAPIKey
from core.context import set_current_tenant

HMAC_TIMESTAMP_TOLERANCE_SECONDS = 300


def _parse_hmac_timestamp(raw_timestamp: str) -> float:
    cleaned = (raw_timestamp or "").strip()
    if not cleaned:
        raise AuthenticationFailed("X-Timestamp header is required for API key authentication.")
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        dt = parse_datetime(cleaned)
        if dt is None:
            raise AuthenticationFailed("Invalid X-Timestamp header format.")
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, dt_timezone.utc)
        return dt.timestamp()


def resolve_request_tenant_membership(request, user):
    raw_tenant_id = request.headers.get("X-Tenant") or request.META.get("HTTP_X_TENANT")
    if not raw_tenant_id:
        return None
    cleaned = str(raw_tenant_id).strip()
    try:
        tenant_uuid = uuid.UUID(cleaned)
        tenant_filter = Q(tenant_id=tenant_uuid)
    except (ValueError, TypeError):
        tenant_filter = Q(tenant__slug__iexact=cleaned)

    membership = (
        Membership.unscoped_objects.select_related("tenant", "user")
        .filter(
            tenant_filter,
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
        if not key_id and not signature:
            return None
        if not key_id or not signature:
            raise AuthenticationFailed("Both X-API-Key and X-Signature headers are required.")

        raw_tenant_id = request.headers.get("X-Tenant") or request.META.get("HTTP_X_TENANT")
        if raw_tenant_id:
            raise PermissionDenied("X-Tenant header is not permitted with API key authentication.")

        now = timezone.now()
        api_key_obj = (
            TenantAPIKey.unscoped_objects.select_related("tenant")
            .filter(key_id=key_id, is_active=True, tenant__is_active=True)
            .first()
        )
        if api_key_obj is None:
            raise AuthenticationFailed("Invalid or inactive API key.")

        if not api_key_obj.is_currently_valid(now=now):
            TenantAPIKey.unscoped_objects.filter(pk=api_key_obj.pk).update(is_active=False)
            raise AuthenticationFailed("API key has been revoked.")

        timestamp = (
            request.headers.get("X-Timestamp")
            or request.META.get("HTTP_X_TIMESTAMP")
            or ""
        ).strip()
        ts_epoch = _parse_hmac_timestamp(timestamp)
        if abs(now.timestamp() - ts_epoch) > HMAC_TIMESTAMP_TOLERANCE_SECONDS:
            raise AuthenticationFailed("Request timestamp is outside the 5-minute tolerance window.")

        body_bytes = request.body or b""
        secret_bytes = api_key_obj.secret_encrypted.encode("utf-8")
        expected_sig_dot = hmac.new(
            secret_bytes,
            f"{timestamp}.".encode("utf-8") + body_bytes,
            hashlib.sha256,
        ).hexdigest()
        expected_sig_concat = hmac.new(
            secret_bytes,
            timestamp.encode("utf-8") + body_bytes,
            hashlib.sha256,
        ).hexdigest()

        provided_sig = signature.removeprefix("sha256=").strip().lower()
        if not (
            hmac.compare_digest(expected_sig_dot, provided_sig)
            or hmac.compare_digest(expected_sig_concat, provided_sig)
        ):
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

        TenantAPIKey.unscoped_objects.filter(pk=api_key_obj.pk).update(last_used_at=now)
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
