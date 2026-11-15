from django.core.exceptions import ImproperlyConfigured
from config.settings import base as _base
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

for _k, _v in vars(_base).items():
    if not _k.startswith("_"):
        globals()[_k] = _v

env = _base.env
DATABASES = _base.DATABASES
REDIS_URL = _base.REDIS_URL
SECRET_KEY = _base.SECRET_KEY

DEBUG = False
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

if not SECRET_KEY or SECRET_KEY == "insecure-dev-secret-key-for-local-setup-only-32chars":
    raise ImproperlyConfigured("SECRET_KEY must be configured in production.")

FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")
if not FIELD_ENCRYPTION_KEY:
    raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY must be set in production.")

if env.bool("ALLOW_LOCAL_WEBHOOKS", default=False):
    raise ImproperlyConfigured("ALLOW_LOCAL_WEBHOOKS cannot be enabled in production.")
ALLOW_LOCAL_WEBHOOKS = False

if env.bool("ENABLE_TEST_DOUBLES", default=False):
    raise ImproperlyConfigured("ENABLE_TEST_DOUBLES cannot be enabled in production.")
ENABLE_TEST_DOUBLES = False

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_ALL_ORIGINS = env.bool("CORS_ALLOW_ALL_ORIGINS", default=False)

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = env.bool("USE_X_FORWARDED_HOST", default=True)
USE_X_FORWARDED_PROTO = env.bool("USE_X_FORWARDED_PROTO", default=True)

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

CONN_MAX_AGE = env.int("CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_MAX_AGE"] = CONN_MAX_AGE
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        traces_sample_rate=0.2,
        send_default_pii=False,
    )

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "core.logging.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}
