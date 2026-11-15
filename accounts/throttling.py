from django.core.cache import cache
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle, SimpleRateThrottle


def get_client_ip(request) -> str:
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or "unknown"


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
