from django.core.cache import cache
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle, SimpleRateThrottle


def get_client_ip(request) -> str:
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or "unknown"


def get_auth_throttle_ident(request) -> str:
    ip = get_client_ip(request)
    email = ""
    if hasattr(request, "data") and isinstance(request.data, dict):
        email = str(request.data.get("email") or "").strip().lower()
    if not email and hasattr(request, "query_params"):
        email = str(request.query_params.get("email") or "").strip().lower()
    return f"{ip}:{email}" if email else ip


LOGIN_LOCKOUT_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_DURATION = 900


def get_login_fail_cache_key(email: str, ip: str) -> str:
    norm = (email or "").strip().lower()
    return f"login_fails:{norm}:{ip}"


def is_account_locked(email: str, ip: str) -> bool:
    key = get_login_fail_cache_key(email, ip)
    try:
        fails = int(cache.get(key, 0) or 0)
        return fails >= LOGIN_LOCKOUT_MAX_ATTEMPTS
    except Exception:
        return False


def record_login_failure(email: str, ip: str) -> int:
    key = get_login_fail_cache_key(email, ip)
    try:
        current = int(cache.get(key, 0) or 0)
        new_val = current + 1
        cache.set(key, new_val, timeout=LOGIN_LOCKOUT_DURATION)
        return new_val
    except Exception:
        return 0


def clear_login_failures(email: str, ip: str) -> None:
    key = get_login_fail_cache_key(email, ip)
    try:
        cache.delete(key)
    except Exception:
        pass


class WebhookSignatureAbuseThrottle(BaseThrottle):
    max_failures = 50
    duration = 60

    def allow_request(self, request, view):
        ident = get_client_ip(request)
        try:
            fail_count = int(cache.get(f"wh_sig_fail:{ident}", 0) or 0)
        except Exception:
            fail_count = 0
        return fail_count < self.max_failures

    def wait(self):
        return self.duration


def record_webhook_signature_failure(
    request,
    detail: str = "Invalid webhook signature.",
    status_code: int = status.HTTP_403_FORBIDDEN,
) -> Response:
    ident = get_client_ip(request)
    cache_key = f"wh_sig_fail:{ident}"
    try:
        current = int(cache.get(cache_key, 0) or 0)
        new_count = current + 1
        cache.set(cache_key, new_count, timeout=60)
        if new_count >= 50:
            return Response(
                {"detail": "Too many failed webhook verification attempts."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
    except Exception:
        pass
    return Response({"detail": detail}, status=status_code)


class RegisterRateThrottle(SimpleRateThrottle):
    scope = "auth_register"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": get_auth_throttle_ident(request),
        }

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            return True


class LoginRateThrottle(SimpleRateThrottle):
    scope = "auth_login"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": get_auth_throttle_ident(request),
        }

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            return True


class ForgotPasswordRateThrottle(SimpleRateThrottle):
    scope = "auth_forgot"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": get_auth_throttle_ident(request),
        }

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            return True


class ResendVerificationRateThrottle(SimpleRateThrottle):
    scope = "auth_resend"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": get_auth_throttle_ident(request),
        }

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            return True


class AcceptInvitationRateThrottle(SimpleRateThrottle):
    scope = "auth_accept"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": get_auth_throttle_ident(request),
        }

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            return True


class APIKeyRateThrottle(SimpleRateThrottle):
    scope = "api_key"

    def get_cache_key(self, request, view):
        api_key = getattr(request, "api_key", None)
        if api_key is None:
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": str(api_key.id),
        }

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            return True


class TenantRateThrottle(SimpleRateThrottle):
    scope = "tenant"

    def get_cache_key(self, request, view):
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            api_key = getattr(request, "api_key", None)
            if api_key is not None:
                tenant = api_key.tenant
        if tenant is None:
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": str(tenant.id),
        }

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            return True


class WebhookRateThrottle(SimpleRateThrottle):
    scope = "webhook"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            return True
