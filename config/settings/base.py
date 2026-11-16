from datetime import timedelta
import os
from pathlib import Path
import environ
from kombu import Exchange, Queue

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    SECRET_KEY=(str, "insecure-dev-secret-key-for-local-setup-only-32chars"),
    FIELD_ENCRYPTION_KEY=(str, ""),
    ALLOW_LOCAL_WEBHOOKS=(bool, False),
    ENABLE_TEST_DOUBLES=(bool, False),
    DATABASE_URL=(str, "postgres://crm_user:crm_password@localhost:5433/crm_db"),
    REDIS_URL=(str, "redis://localhost:6380/0"),
    CELERY_BROKER_URL=(str, "redis://localhost:6380/0"),
    CELERY_RESULT_BACKEND=(str, "redis://localhost:6380/0"),
)

env_file = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_file):
    environ.Env.read_env(env_file)

DEBUG = env("DEBUG")
SECRET_KEY = env("SECRET_KEY")
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["*"])

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = env.bool("USE_X_FORWARDED_HOST", default=True)
USE_X_FORWARDED_PROTO = env.bool("USE_X_FORWARDED_PROTO", default=True)
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", default="")
FRONTEND_BASE_URL = env("FRONTEND_BASE_URL", default="https://marketing.fitverx.com")
ALLOW_LOCAL_WEBHOOKS = env.bool("ALLOW_LOCAL_WEBHOOKS", default=False)
ENABLE_TEST_DOUBLES = env.bool("ENABLE_TEST_DOUBLES", default=False)

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "django_filters",
    "corsheaders",
    "django_celery_beat",
]

LOCAL_APPS = [
    "core.apps.CoreConfig",
    "accounts.apps.AccountsConfig",
    "contacts.apps.ContactsConfig",
    "integrations.apps.IntegrationsConfig",
    "messaging.apps.MessagingConfig",
    "automation.apps.AutomationConfig",
    "inbox.apps.InboxConfig",
    "notifications.apps.NotificationsConfig",
    "usage.apps.UsageConfig",
    "reports.apps.ReportsConfig",
    "webhooks.apps.WebhooksConfig",
    "leads.apps.LeadsConfig",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.TenantContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL"),
}

REDIS_URL = env("REDIS_URL")

CACHES = {
    "default": {
        "BACKEND": env(
            "CACHE_BACKEND",
            default="django.core.cache.backends.redis.RedisCache",
        )
        or "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
MEDIA_URL = "/media/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "accounts.authentication.TenantJWTAuthentication",
        "accounts.authentication.APIKeyHMACAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "core.pagination.StandardPageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "core.schema.TenantAwareAutoSchema",
    "EXCEPTION_HANDLER": "core.exceptions.custom_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "accounts.throttling.APIKeyRateThrottle",
        "accounts.throttling.TenantRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "api_key": env("THROTTLE_RATE_API_KEY", default="600/minute"),
        "tenant": env("THROTTLE_RATE_TENANT", default="1200/minute"),
        "webhook": env("THROTTLE_RATE_WEBHOOK", default="600/minute"),
        "auth_register": env("THROTTLE_RATE_AUTH_REGISTER", default="10/minute"),
        "auth_login": env("THROTTLE_RATE_AUTH_LOGIN", default="15/minute"),
        "auth_forgot": env("THROTTLE_RATE_AUTH_FORGOT", default="10/minute"),
        "auth_resend": env("THROTTLE_RATE_AUTH_RESEND", default="5/minute"),
        "auth_accept": env("THROTTLE_RATE_AUTH_ACCEPT", default="15/minute"),
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=12),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "CRM & Marketing Automation Platform API",
    "DESCRIPTION": "Multi-tenant CRM, Meta Lead Ads acquisition, Email/SMS/WhatsApp automation, and Unified Inbox API.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "APPEND_COMPONENTS": {
        "parameters": {
            "XTenantHeader": {
                "name": "X-Tenant",
                "in": "header",
                "required": False,
                "description": "UUID of the active tenant workspace.",
                "schema": {"type": "string", "format": "uuid"},
            }
        }
    },
    "ENUM_NAME_OVERRIDES": {
        "SubscriptionStatusEnum": "contacts.models.Contact.SubscriptionStatus",
        "AppointmentStatusEnum": "contacts.models.Appointment.Status",
        "TaskStatusEnum": "contacts.models.Task.Status",
        "ChannelConnectionStatusEnum": "integrations.models.ChannelConnection.Status",
        "BroadcastStatusEnum": "messaging.models.Broadcast.Status",
        "MessageStatusEnum": "messaging.models.Message.Status",
        "ChannelEnum": "messaging.models.Channel",
        "MetaConnectionStatusEnum": "leads.models.MetaConnection.Status",
        "InboundEventStatusEnum": "leads.models.MetaLeadEvent.Status",
        "WorkflowStatusEnum": "automation.models.Workflow.Status",
        "WorkflowEnrollmentStatusEnum": "automation.models.WorkflowEnrollment.Status",
        "StepExecutionStatusEnum": "automation.models.StepExecution.Status",
        "ConversationStatusEnum": "inbox.models.Conversation.Status",
        "AssignmentStrategyEnum": "core.models.Tenant.AssignmentStrategy",
        "WebhookDeliveryStatusEnum": "webhooks.models.WebhookDelivery.Status",
        "APIKeyScopeEnum": "accounts.models.TenantAPIKey.Scope",
        "WhatsAppCategoryEnum": "messaging.models.WhatsAppCategory",
        "MessageDirectionEnum": "messaging.models.Message.Direction",
        "UsageDirectionEnum": "usage.models.Direction",
    },
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = False
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "max_retries": 1,
    "interval_start": 0,
    "interval_step": 0.1,
    "interval_max": 0.2,
    "socket_connect_timeout": 1,
    "socket_timeout": 1,
}

CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_DEFAULT_EXCHANGE = "default"
CELERY_TASK_DEFAULT_ROUTING_KEY = "default"

CELERY_TASK_QUEUES = (
    Queue("default", Exchange("default"), routing_key="default"),
    Queue("webhooks", Exchange("webhooks"), routing_key="webhooks"),
    Queue("messaging", Exchange("messaging"), routing_key="messaging"),
    Queue("automation", Exchange("automation"), routing_key="automation"),
)

CELERY_TASK_ROUTES = {
    "webhooks.tasks.*": {"queue": "webhooks"},
    "integrations.tasks.*": {"queue": "webhooks"},
    "leads.tasks.process_meta_lead_event_task": {"queue": "webhooks"},
    "leads.tasks.*": {"queue": "default"},
    "messaging.tasks.*": {"queue": "messaging"},
    "automation.tasks.*": {"queue": "automation"},
    "core.tasks.*": {"queue": "default"},
    "notifications.tasks.*": {"queue": "default"},
    "contacts.tasks.*": {"queue": "default"},
    "inbox.tasks.*": {"queue": "default"},
    "accounts.tasks.*": {"queue": "default"},
    "usage.tasks.*": {"queue": "default"},
}

DEFAULT_USAGE_CURRENCY = env("DEFAULT_USAGE_CURRENCY", default="USD")

CELERY_BEAT_SCHEDULE = {
    "detect-inactive-leads": {
        "task": "contacts.tasks.detect_inactive_leads_beat_task",
        "schedule": timedelta(hours=1),
    },
    "scan-due-soon-tasks": {
        "task": "contacts.tasks.scan_due_soon_tasks_beat_task",
        "schedule": timedelta(minutes=5),
    },
    "claim-due-broadcasts": {
        "task": "messaging.tasks.claim_due_broadcasts_beat_task",
        "schedule": timedelta(minutes=1),
    },
    "check-meta-connections-health": {
        "task": "leads.tasks.check_meta_connections_health_beat_task",
        "schedule": timedelta(hours=6),
    },
    "dispatch-due-workflow-enrollments": {
        "task": "automation.tasks.dispatch_due_enrollments_beat_task",
        "schedule": timedelta(seconds=15),
    },
    "recover-stale-workflow-locks": {
        "task": "automation.tasks.recover_stale_workflow_locks_beat_task",
        "schedule": timedelta(minutes=5),
    },
    "retry-due-webhook-deliveries": {
        "task": "webhooks.tasks.retry_due_webhook_deliveries_beat_task",
        "schedule": timedelta(minutes=1),
    },
}

EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="marketing@fitverx.com")
SENDGRID_API_KEY = env("SENDGRID_API_KEY", default=env("EMAIL_HOST_PASSWORD", default=""))

META_APP_ID = env("META_APP_ID", default="")
META_APP_SECRET = env("META_APP_SECRET", default="")
META_WEBHOOK_VERIFY_TOKEN = env("META_WEBHOOK_VERIFY_TOKEN", default="")
META_GRAPH_VERSION = env("META_GRAPH_VERSION", default="v20.0")
META_OAUTH_REDIRECT_URI = env("META_OAUTH_REDIRECT_URI", default="")

CORS_ALLOW_ALL_ORIGINS = env.bool("CORS_ALLOW_ALL_ORIGINS", default=True)
CORS_ALLOW_CREDENTIALS = True
