# CRM & Marketing Automation Backend

A multi-tenant CRM, Meta Lead Ads acquisition engine, multi-channel messaging (Email, SMS, WhatsApp), automated marketing workflow orchestrator, unified communications inbox, and external event ingestion platform built with Django, Django REST Framework, PostgreSQL, Redis, and Celery.

---

## 1. System Architecture Overview

The system is architected as a secure, fail-closed multi-tenant application where every tenant operates in logical isolation:

- **Multi-Tenancy**: All persistent tenant records inherit from `TenantOwnedModel`, enforcing UUID primary keys, non-nullable foreign keys to `Tenant`, and automatic audit timestamps (`created_at`, `updated_at`).
- **Fail-Closed Isolation**: `TenantScopedManager` rejects any query executed without an explicit active tenant context in thread-local storage (`core.context.tenant_context`).
- **Domain Event Driven Architecture**: Business actions publish domain events via `core.events.publish(...)`, which registers callbacks via `transaction.on_commit(...)` to fan out work to Celery worker tasks.
- **Resilient Message Dispatch**: Single path of egress through `MessagingService.send(...)` with consent checks, opt-out validation, WhatsApp 24-hour customer care window compliance, SMS GSM-7/UCS-2 segment calculation, message persistence, usage cost tracking, and asynchronous worker dispatch.
- **Durable Workflow Execution**: Finite directed graph execution where enrollment state is tracked via cursors. Steps support multi-branch conditional routing, time delays, and actions. Active enrollments can automatically pause on contact replies (`stop_on_reply=True`) and exit on external purchase events (`is_promotional=True`).
- **Unified Inbox**: Single multi-channel communications stream per contact with unread counters, assignment strategies (shared queue, dedicated owner, round-robin), internal agent notes, and direct bidirectional messaging.
- **External Business Ingestion & Webhooks**: HMAC-SHA256 authenticated REST APIs for external systems (e.g., Fitverx) with payload size guards, rate throttling, idempotency deduplication, and outbound webhook dispatch with SSRF protection.

---

## 2. Product Guide Section & Module Map

The codebase maps to the 18 sections of `docs/product_guide.pdf`:

| Section | Guide Title | Primary Modules & Code Locations | Description |
| :--- | :--- | :--- | :--- |
| **1** | **Introduction** | `docs/`, `core/` | System architecture, foundational models, fail-closed multi-tenancy |
| **2** | **Platform Overview** | `core/`, `config/` | Application routing, domain event engine, audit logs, crypto |
| **3** | **Main Dashboard** | `reports/`, `inbox/`, `contacts/` | High-level KPI aggregations, today's schedule, pipeline stage metrics |
| **4** | **CRM** | `contacts/` | Contacts, timeline activities (`idx_activity_tenant_type_dt`), tasks, appointments, custom fields |
| **5** | **Meta** | `leads/`, `integrations/providers/meta.py` | Lead Ads OAuth, Page webhooks, HMAC signature verification, Graph API lead fetch |
| **6** | **Tags** | `contacts/models.py`, `contacts/services.py` | Contact tag definitions, auto-tagging, tag-based assignment and filtering |
| **7** | **Automation** | `automation/` | Workflow graph engine, delay/wait_for_event resumption (15s beat), human handoff pauses |
| **8** | **Email** | `messaging/`, `integrations/providers/sendgrid.py` | SendGrid transactional/promotional email dispatch, templates, webhook signature check |
| **9** | **SMS** | `messaging/`, `integrations/providers/twilio.py` | Twilio SMS dispatch, segment calculations, webhook signature validation, delivery tracking |
| **10** | **WhatsApp** | `messaging/`, `integrations/providers/twilio.py` | Twilio WhatsApp dispatch, inbound message parsing, 24-hour service window compliance |
| **11** | **Unified Inbox** | `inbox/` | Multi-channel conversations, two-way messaging, internal notes, unread counters |
| **12** | **Team** | `accounts/` | RBAC (Owner, Admin, Marketer), marketer visibility scoping, team workload, routing strategies |
| **13** | **Notifications** | `notifications/` | Internal agent notifications for due tasks, inactive leads, human handoff alerts |
| **14** | **Usage** | `usage/`, `core/constants.py` | Real-time messaging cost metering, rate cards, exact `COST_ESTIMATE_DISCLAIMER` |
| **15** | **Reports** | `reports/`, `core/constants.py` | Attribution, acquisition, pipeline funnel, conversion analytics, exact `ROAS_SCOPE_NOTE` |
| **16** | **Journeys** | `automation/`, `leads/`, `contacts/`, `messaging/` | End-to-end customer journey scenarios from ad capture to conversion |
| **17** | **Dependencies** | `config/`, `docker-compose.yml`, `Dockerfile` | PostgreSQL 16, Redis 7, Celery worker/beat, Gunicorn, third-party provider clients |
| **18** | **Scope** | `webhooks/`, `accounts/`, `contacts/` | Scope boundaries; CSV import classified as **Pending Confirmation** |

---

## 3. Local and Docker Setup

### Prerequisites
- Docker Engine 24+ and Docker Compose v2+
- Python 3.12 (if running natively outside Docker)
- PostgreSQL 16
- Redis 7

### Running via Docker Compose

1. Clone repository and enter workspace:
   ```bash
   cd crm_backend
   ```

2. Configure environment:
   ```bash
   cp .env.example .env
   ```

3. Build and launch all services:
   ```bash
   docker compose up -d --build
   ```

4. Verify service health:
   ```bash
   docker compose ps
   ```

   The compose environment launches 5 containers:
   - `crm_backend-postgres-1`: PostgreSQL database on internal port 5432 (mapped to host 5433).
   - `crm_backend-redis-1`: Redis broker and cache on internal port 6379 (mapped to host 6380).
   - `crm_backend-web-1`: Gunicorn application server listening on port 8000.
   - `crm_backend-worker-1`: Celery worker listening on queues `default,webhooks,messaging,automation`.
   - `crm_backend-beat-1`: Celery beat database scheduler.

5. View real-time logs:
   ```bash
   docker compose logs -f web worker
   ```

### Running Natively for Local Development

1. Create and activate a Python virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Run database migrations:
   ```bash
   python manage.py migrate
   ```

3. Seed default global rate cards:
   ```bash
   python manage.py seed_rate_cards
   ```

### Rate Cards & Usage Metering
Rate cards define applicable unit costs by `channel`, `direction` (`outbound` or `inbound`), destination `country`, and `whatsapp_category`.
- **Unit Cost Formula**: `Total Cost = Quantity × Applicable Unit Cost`. The platform fee is included directly in the stored unit cost at the rate card level.
- **WhatsApp Pricing Requirement**: Tenant owners must enter provider fee plus Meta fee per category (Marketing, Utility, Authentication, Service) and destination country when configuring tenant rate card overrides.
- **Inbound Metering**: Inbound WhatsApp messages are metered under category `service` (quantity 1). Inbound SMS messages are metered by billable segments. Inbound email is unbilled.


4. Start development web server:
   ```bash
   python manage.py runserver 0.0.0.0:8000
   ```

5. Start Celery worker:
   ```bash
   celery -A config worker -l info -Q default,webhooks,messaging,automation
   ```

6. Start Celery beat scheduler:
   ```bash
   celery -A config beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
   ```

---

## 4. Environment Variables Reference

| Variable Name | Default in `.env.example` | Description |
|---|---|---|
| `DEBUG` | `True` | Enable Django debug mode (set `False` in production) |
| `SECRET_KEY` | `change-me-in-production...` | Django cryptographic signing key |
| `FIELD_ENCRYPTION_KEY` | *(empty)* | Optional 32-byte Fernet key for database field encryption |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,0.0.0.0` | Comma-separated list of valid Host header hostnames |
| `DATABASE_URL` | `postgres://crm_user:...@5432/crm_db` | PostgreSQL connection URL |
| `CONN_MAX_AGE` | `60` | Database connection persistence in seconds |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection URL |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Celery broker URL |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/0` | Celery task result store backend |
| `CACHE_BACKEND` | `django.core.cache.backends.redis.RedisCache` | Django cache backend |
| `EMAIL_BACKEND` | `django.core.mail.backends.console.EmailBackend` | Django internal email backend |
| `EMAIL_HOST` | `smtp.example.com` | SMTP host for system notifications |
| `EMAIL_PORT` | `587` | SMTP port |
| `EMAIL_USE_TLS` | `True` | Use TLS for SMTP connections |
| `EMAIL_HOST_USER` | *(empty)* | SMTP username |
| `EMAIL_HOST_PASSWORD` | *(empty)* | SMTP password |
| `DEFAULT_FROM_EMAIL` | `noreply@crm.local` | Default sender email address |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Canonical public URL used for webhooks and signature checking |
| `USE_X_FORWARDED_HOST` | `True` | Honor X-Forwarded-Host reverse proxy headers |
| `USE_X_FORWARDED_PROTO` | `True` | Honor X-Forwarded-Proto reverse proxy headers |
| `CORS_ALLOW_ALL_ORIGINS` | `True` | Allow CORS requests from all origins in development |
| `CORS_ALLOWED_ORIGINS` | *(empty)* | Comma-separated list of allowed CORS origins in production |
| `SESSION_COOKIE_SECURE` | `False` | Require HTTPS for session cookies |
| `CSRF_COOKIE_SECURE` | `False` | Require HTTPS for CSRF cookies |
| `SECURE_SSL_REDIRECT` | `False` | Redirect HTTP requests to HTTPS |
| `SECURE_HSTS_SECONDS` | `0` | HTTP Strict Transport Security max-age (31536000 in prod) |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `False` | Apply HSTS to subdomains |
| `SECURE_HSTS_PRELOAD` | `False` | Enable HSTS preload list |
| `META_APP_ID` | *(empty)* | Facebook/Meta Developer App ID |
| `META_APP_SECRET` | *(empty)* | Facebook/Meta Developer App Secret |
| `META_WEBHOOK_VERIFY_TOKEN` | *(empty)* | Shared challenge token for Meta webhook subscription |
| `META_GRAPH_VERSION` | `v20.0` | Meta Graph API version |
| `META_OAUTH_REDIRECT_URI` | `http://localhost:8000/api/v1/leads/callback/` | Meta OAuth callback redirect URI |
| `DEFAULT_USAGE_CURRENCY` | `USD` | ISO currency code for messaging rate cards and spend tracking |
| `THROTTLE_RATE_API_KEY` | `600/minute` | Rate limit per API Key |
| `THROTTLE_RATE_TENANT` | `3000/minute` | Rate limit per Tenant |
| `THROTTLE_RATE_WEBHOOK` | `600/minute` | Rate limit per public webhook receiver IP |
| `ALLOW_LOCAL_WEBHOOKS` | `False` | Allow private/loopback IP webhook delivery (dev only; rejected in prod) |
| `ENABLE_TEST_DOUBLES` | `False` | Enable offline mock providers (dev/testing only; rejected in prod) |
| `SENTRY_DSN` | *(empty)* | Sentry DSN for error monitoring |
| `DJANGO_SETTINGS_MODULE` | `config.settings.base` | Django settings module path |

---

## 5. Meta App Configuration Guide

1. **Create Meta App**:
   - In Meta for Developers, create an app of type **Business**.
   - Add the **Webhooks** and **Facebook Login for Business** products.

2. **Configure App Credentials**:
   - Copy **App ID** and **App Secret** to `META_APP_ID` and `META_APP_SECRET`.
   - Set `META_WEBHOOK_VERIFY_TOKEN` to a secure random string.

3. **Configure Webhook Subscription in Meta**:
   - Callback URL: `https://<your-domain>/api/v1/webhooks/meta/`
   - Verify Token: value matching `META_WEBHOOK_VERIFY_TOKEN`
   - Field Subscriptions: subscribe to `leadgen` on the `page` object.

4. **Permissions Required**:
   - `pages_show_list`
   - `pages_read_engagement`
   - `pages_manage_metadata`
   - `pages_manage_ads`
   - `leads_retrieval`

5. **Tenant Connection Flow**:
   - Tenant administrators initiate connection via `GET /api/v1/leads/connect-url/`.
   - Meta redirects to `META_OAUTH_REDIRECT_URI` (`POST /api/v1/leads/callback/`).
   - The platform exchanges authorization code for a long-lived access token, subscribes connected pages to webhooks, discovers Lead Ads forms, and enables field mapping.

---

## 6. Twilio & SendGrid Webhook URLs Reference

Webhooks resolve tenant context dynamically from provider metadata before touching database records.

### Twilio Webhooks (SMS & WhatsApp)
- **Twilio SMS Inbound**: `POST https://<your-domain>/api/v1/webhooks/twilio/sms/inbound/`
- **Twilio SMS Status Callback**: `POST https://<your-domain>/api/v1/webhooks/twilio/sms/status/`
- **Twilio WhatsApp Inbound**: `POST https://<your-domain>/api/v1/webhooks/twilio/whatsapp/inbound/`
- **Twilio WhatsApp Status Callback**: `POST https://<your-domain>/api/v1/webhooks/twilio/whatsapp/status/`

*Tenant Resolution*: Twilio routes resolve the tenant by matching the `To` phone number (for inbound) or `From` number (for status) against registered `ChannelConnection.sender_identity`. Signatures are validated against that specific connection's `auth_token` via standard `X-Twilio-Signature`.

### SendGrid Webhooks (Email)
- **SendGrid Inbound Parse**: `POST https://<your-domain>/api/v1/webhooks/sendgrid/<webhook_token>/inbound/`
- **SendGrid Event Webhook**: `POST https://<your-domain>/api/v1/webhooks/sendgrid/<webhook_token>/`

*Tenant Resolution*: Each email `ChannelConnection` generates a unique 32-byte cryptographic `webhook_token` upon creation (`GET /api/v1/integrations/connections/`). SendGrid webhooks include this token directly in their URL path, isolating tenants at the routing layer.

### Email Unsubscribe Links
- **One-Click Unsubscribe**: `POST /webhooks/unsubscribe/<signed_token>/` or `GET /webhooks/unsubscribe/confirm/`

---

## 7. Celery Architecture & Beat Schedules

### Queues
| Queue | Routing Key Pattern | Workload |
|---|---|---|
| `webhooks` | `webhooks.tasks.*`, `leads.tasks.*`, `integrations.tasks.*` | Inbound payload processing, Meta lead fetching, outbound webhook delivery |
| `messaging` | `messaging.tasks.*` | Email/SMS/WhatsApp provider dispatch, broadcast batching |
| `automation` | `automation.tasks.*` | Workflow graph evaluation, step transitions, batch enrollment cursors |
| `default` | `core.tasks.*`, `contacts.tasks.*`, `notifications.tasks.*` | Scheduled sweepers, health checks, email notifications, maintenance |

### Periodic Tasks (Celery Beat Schedule)
1. **`dispatch-due-workflow-enrollments`** (every 15 seconds): Claims and advances active enrollments where `next_run_at <= now`.
2. **`claim-due-broadcasts`** (every 60 seconds): Scans scheduled broadcasts reaching `scheduled_at`.
3. **`retry-due-webhook-deliveries`** (every 60 seconds): Re-enqueues due `WebhookDelivery` retries (exponential backoff) and stale pending `ExternalEvent` rows.
4. **`recover-stale-workflow-locks`** (every 2 minutes): Releases enrollments stuck with `locked_at` older than 5 minutes.
5. **`scan-inactive-leads`** (every 1 hour): Detects contacts with no activity past threshold and fires `lead_inactive` domain events.
6. **`scan-due-soon-tasks`** (every 15 minutes): Alerts assignees about upcoming tasks.
7. **`check-meta-connection-health`** (every 6 hours): Validates page token validity and triggers re-authentication alerts if expired.

---

## 8. API Authentication

All endpoints under `/api/v1/` require one of two authentication mechanisms:

### A. JWT Bearer Authentication (Internal Web App & Team Members)
- **Login**: `POST /api/v1/auth/login/` returning `access` and `refresh` tokens.
- **Header**: `Authorization: Bearer <access_token>`
- **Tenant Context**: Pass `X-Tenant: <tenant_uuid>` header.

### B. API Key HMAC-SHA256 Authentication (External Systems like Fitverx)
Used exclusively for `/api/v1/external/*` endpoints.

Headers required:
- `X-API-Key`: Public API Key ID (e.g., `crm_bb5e...`).
- `X-Timestamp`: Current UTC UNIX epoch timestamp (seconds). Must be within 300 seconds of server time.
- `X-Signature`: Hex-encoded HMAC-SHA256 signature.

*Security rule*: External API Key requests bind tenant context directly from the validated key. Client-supplied `X-Tenant` headers are rejected with `403 Forbidden`.

---

## 9. Fitverx External Event Contract

External fitness and business management platforms (such as Fitverx) ingest purchases, subscriptions, and appointments via `/api/v1/external/events/`.

### Supported Event Types
- `package.purchased`: Customer bought a coaching or membership package (automatically stops active promotional workflows).
- `membership.created`: New subscription started.
- `payment.received`: Payment processed.
- `appointment.booked`: Appointment scheduled externally.
- `appointment.cancelled`: Appointment cancelled externally.

### Envelope Structure
```json
{
  "event_id": "evt_purchase_1794754542",
  "type": "package.purchased",
  "occurred_at": "2026-11-15T14:40:00Z",
  "contact": {
    "email": "sarah.jenkins@example.com",
    "phone": "+13125550194",
    "first_name": "Sarah",
    "last_name": "Jenkins"
  },
  "data": {
    "package_name": "Gold 12-Week Transformation",
    "amount": 499.00,
    "currency": "USD"
  }
}
```

### Signature Computation (Python Example)
```python
import hmac
import hashlib
import time
import requests

api_key_id = "crm_bb5e50c6babc77be70c833b122822a5a"
api_key_secret = "your_raw_api_key_secret"
timestamp = str(int(time.time()))
raw_body = b'{"event_id":"evt_1","type":"package.purchased","occurred_at":"2026-11-15T14:40:00Z","contact":{"email":"sarah@example.com"},"data":{"amount":499.0}}'

signature = hmac.new(
    api_key_secret.encode("utf-8"),
    f"{timestamp}.".encode("utf-8") + raw_body,
    hashlib.sha256,
).hexdigest()

headers = {
    "Content-Type": "application/json",
    "X-API-Key": api_key_id,
    "X-Timestamp": timestamp,
    "X-Signature": signature,
}

response = requests.post("https://crm.local/api/v1/external/events/", data=raw_body, headers=headers)
```

---

## 10. Outbound Webhooks & HMAC Verification

When domain events occur, subscribed endpoints receive signed HTTP POST notifications.

### Outbound Webhook Headers
- `Content-Type`: `application/json`
- `X-Webhook-Event`: Domain event name (e.g., `package.purchased`, `lead_created`)
- `X-Webhook-Event-Id`: Unique event UUID
- `X-Webhook-Delivery-Id`: Delivery attempt UUID
- `X-Webhook-Timestamp`: UNIX epoch timestamp
- `X-Webhook-Signature`: `sha256=<hex_digest>` computed as `HMAC-SHA256(signing_secret, timestamp + "." + raw_body)`

### Signature Verification in Receiver
```python
import hmac
import hashlib

def verify_crm_webhook(signing_secret: str, request_body: bytes, headers: dict) -> bool:
    timestamp = headers.get("X-Webhook-Timestamp", "")
    received_sig = headers.get("X-Webhook-Signature", "").removeprefix("sha256=").strip()
    
    expected_sig = hmac.new(
        signing_secret.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + request_body,
        hashlib.sha256,
    ).hexdigest()
    
    return hmac.compare_digest(expected_sig, received_sig)
```

### SSRF Protection
Outbound webhook dispatchers enforce strict Server-Side Request Forgery guards:
- Only `http` and `https` protocols are allowed (`https` strictly enforced in production).
- Target hostnames are resolved to IP addresses and evaluated against private, loopback, link-local, multicast, and cloud metadata ranges (`169.254.0.0/16`, `100.64.0.0/10`, `fe80::/10`).
- Any attempt to target internal network services is rejected with `webhook_ssrf_blocked`.

---

## 11. Operational & Scaling Architecture

The system is engineered for reliable horizontal scalability:
- **No Provider Calls in Web Request Cycle**: All third-party interactions (SendGrid, Twilio, Meta Graph API, webhook deliveries) execute asynchronously in dedicated Celery queues.
- **Fail-Safe Idempotency**: External events enforce unique `(tenant, event_id)` constraints with immediate duplicate detection. Outbound message dispatches enforce idempotency keys to prevent duplicate billing.
- **Optimized Database Access**: High-frequency queries utilize composite indices prefixed by tenant (`tenant+status+next_run_at`, `tenant+occurred_at`, `tenant+contact`).
- **Keyset Pagination**: Messages, contact activities, and audit logs implement `KeysetCursorPagination` preventing deep offset performance degradation on large tables.
- **Redis Caching**: Executive dashboard metrics utilize Redis caching with tenant and marketer visibility keys.
- **Structured JSON Logging**: Production logging produces structured JSON formatting compatible with Datadog, CloudWatch, and GCP Cloud Logging.

*(Note: In accordance with architectural principles, system throughput scales horizontally with database connection pools and Celery worker concurrency; specific throughput capacity metrics must be confirmed through production load testing.)*
