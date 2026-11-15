# System Specification & Verification Status

## 1. Product Guide Conformance Matrix (Sections 1–18)

| Section | Guide Title | Status | Verification Evidence | Conformance Notes |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **Introduction** | Implemented only | Architectural foundation, `TenantOwnedModel`, thread-local tenant isolation | Core system goals and multi-tenant isolation patterns implemented. |
| **2** | **Platform Overview** | Implemented only | `core.events.publish`, `accounts.models.Tenant`, fail-closed scoping | Domain event pub/sub with transaction commit hooks and Fernet credential encryption. |
| **3** | **Main Dashboard** | Implemented and tested with fake providers | `reports.selectors.get_dashboard_data`, KPI aggregations, ROAS note | Real-time overview of new leads, pipeline stages, upcoming appointments, and exact `ROAS_SCOPE_NOTE`. |
| **4** | **CRM** | Implemented and tested with fake providers | `contacts.models.Contact`, `Activity` (`idx_activity_tenant_type_dt`), `Task`, `Appointment` | Full contact timeline, activity feed, task due reminders, appointment lifecycle, and custom fields. |
| **5** | **Meta** | Implemented and tested with fake providers | `leads.views.MetaLeadWebhookView`, `leads.tasks.process_meta_lead_event` | HMAC-SHA256 signature verification, Graph API lead fetching, form question mapping, auto-assignment. *(Note: Has never run against live Meta Graph API production credentials; tested via simulated Graph API payloads and signed webhooks).* |
| **6** | **Tags** | Implemented and tested with fake providers | `contacts.models.Tag`, `ContactTag`, `contacts.services.ContactService` | Tag creation, assignment, color coding, auto-tagging via workflow steps, and tag-based rule matching. |
| **7** | **Automation** | Implemented and tested with fake providers | `automation.engine.WorkflowExecutionEngine`, 15-second Celery beat dispatcher | Step locks, delay resumption, wait_for_event matching, exit event handling, and automatic pause on reply. |
| **8** | **Email** | Implemented and tested with fake providers | `messaging.services.MessagingService`, `integrations.providers.sendgrid.SendGridEmailProvider` | Transactional/promotional email dispatch, template rendering, webhook signature verification, usage logging. *(Note: Has never run against live SendGrid production credentials; tested via fake provider and signed webhook callbacks).* |
| **9** | **SMS** | Implemented and tested with fake providers | `messaging.services.MessagingService`, `integrations.providers.twilio.TwilioSMSProvider` | SMS dispatch, GSM-7/UCS-2 segment calculation, Twilio signature verification, status callbacks. *(Note: Has never run against live Twilio production credentials; tested via fake provider and signed webhooks).* |
| **10** | **WhatsApp** | Implemented and tested with fake providers | `messaging.services.MessagingService`, `integrations.providers.twilio.TwilioWhatsAppProvider` | WhatsApp dispatch, inbound message parsing, Twilio signature check, 24-hour service window compliance, inbound usage metering (service category), and abuse-throttled webhooks. *(Note: Has never run against live Twilio production credentials; tested via fake provider and signed webhooks).* |
| **11** | **Unified Inbox** | Implemented and tested with fake providers | `inbox.models.Conversation`, `inbox.views.ConversationViewSet`, `ConversationNote` | Two-way messaging, conversation threads, unread counters, internal team notes, and human handoff automation. |
| **12** | **Team** | Implemented and tested with fake providers | `accounts.permissions.RolePermission`, `contacts.selectors.apply_marketer_visibility` | RBAC (Owner, Admin, Marketer), marketer contact visibility flag, team workload selector, routing strategies (`shared_queue`, `dedicated_owner`, `round_robin`). |
| **13** | **Notifications** | Implemented and tested with fake providers | `notifications.models.Notification`, `notifications.services.notify` | In-app notification feed, alerts for task due soon, inactive leads, human handoff triggers, and lead assignment. |
| **14** | **Usage** | Implemented and tested with fake providers | `usage.models.UsageRecord`, `usage.models.RateCard`, `usage.selectors.compute_usage_summary` | Real-time messaging cost metering with direction support (`outbound`/`inbound`), inbound SMS segments and inbound WhatsApp service metering, rate card resolution hierarchy, formula `Total Cost = Quantity × Unit Cost`, and exact `COST_ESTIMATE_DISCLAIMER`. |
| **15** | **Reports** | Implemented and tested with fake providers | `reports.selectors.get_lead_acquisition_report`, `get_pipeline_report`, `get_messaging_report` | Attribution by source/campaign, conversion rates, messaging performance, pipeline funnel, exact `ROAS_SCOPE_NOTE`. |
| **16** | **Journeys** | Implemented and tested with fake providers | `automation.services.EnrollmentService`, `automation.tasks` | Multi-touch customer journeys: Meta ad capture -> instant email/WhatsApp -> reply handoff -> appointment -> external purchase conversion. |
| **17** | **Dependencies** | Implemented and tested with fake providers | `docker-compose.yml`, `Dockerfile`, Gunicorn, Celery, PostgreSQL 16, Redis 7 | Dedicated queues (`default`, `webhooks`, `messaging`, `automation`), beat schedules, database connection pooling. |
| **18** | **Scope** | Implemented only | Architectural boundary enforcement | Five core scope boundaries enforced: Messenger/IG DMs, VoIP phone calls, autonomous AI chatbots, and landing page builders are excluded. CSV contact import is classified as **Pending Confirmation**. |

---

## 2. Live External Provider Verification Status

| Provider Integration | Live Production Credentials | Simulated / Fake Test Doubles | Status & Operational Scope |
| :--- | :--- | :--- | :--- |
| **Meta Graph API (Lead Ads)** | Never run against live credentials | Verified with simulated Graph API payloads & valid HMAC-SHA256 signatures | Live App Review required for `leads_retrieval` and `pages_manage_ads` scopes. Webhook endpoint strictly enforces `X-Hub-Signature-256`. |
| **Twilio SMS** | Never run against live credentials | Verified with simulated Twilio REST API & valid HMAC-SHA1 signatures | Live A2P 10DLC brand registration required for US traffic. Inbound and status webhooks strictly enforce `X-Twilio-Signature`. |
| **Twilio WhatsApp** | Never run against live credentials | Verified with simulated Twilio WhatsApp API & valid HMAC-SHA1 signatures | Live WhatsApp Business Account (WABA) approval required. Inbound and status webhooks strictly enforce `X-Twilio-Signature`. |
| **SendGrid Email** | Never run against live credentials | Verified with simulated SendGrid API & valid ECDSA/HMAC signatures | Live domain authentication (SPF/DKIM/DMARC) required. Event webhook strictly enforces `X-Twilio-Email-Event-Webhook-Signature`. |
| **Fitverx External API** | Fully operational | Verified with live HMAC-SHA256 client (`X-API-Key`, `X-Timestamp`, `X-Signature`) | Production REST API under `/api/v1/external/` with scoped API key permissions. |
| **Outbound Webhooks** | Fully operational | Verified with live HTTP receiver and valid `X-Webhook-Signature` | HMAC-SHA256 signatures with SSRF protection and exponential retry backoff. |

---

## 3. Production Code Path Hardening & Mock Branch Removals

All mock branches, test-mode shortcuts, signature bypasses, and local allowances have been permanently removed from production code paths:

1. **SendGrid Provider (`integrations/providers/sendgrid.py`)**:
   - Removed `_is_mock_mode()` method that inspected API keys for `mock_` or `test_` prefixes.
   - Removed mock branch in `send()` that returned simulated `ProviderSendResult`.
   - Removed mock branch in `test_connection()` that bypassed network verification.
   - Removed signature skip condition in `verify_signature()`: returns `False` if neither public key nor signing secret is configured.
2. **Twilio Provider (`integrations/providers/twilio.py`)**:
   - Removed `_is_mock_mode()` method that checked Account SID/Token for `ACmock` or `test_` prefixes.
   - Removed mock branch in `TwilioSMSProvider.send()` that returned simulated SID.
   - Removed mock branch in `TwilioWhatsAppProvider.send()` that returned simulated SID.
   - Removed mock branch in `_check_account_credentials()` that bypassed Twilio API verification.
   - Removed mock branch in `fetch_remote_templates()` that returned empty list on mock SID.
3. **Meta Graph Provider (`integrations/providers/meta.py`)**:
   - Removed `_is_mock_token()` method that checked tokens for `mock_` or `test_` prefixes.
   - Removed mock branch in `exchange_code_for_long_lived_token()`.
   - Removed mock branch in `debug_token()`.
   - Removed mock branch in `get_subscribed_pages()`.
   - Removed mock branch in `subscribe_page()`.
   - Removed mock branch in `unsubscribe_page()`.
   - Removed mock branch in `list_lead_forms()`.
   - Removed mock branch in `fetch_form_questions()`.
   - Removed mock branch in `fetch_lead()`.
4. **Isolated Test Doubles (`integrations/providers/fakes.py`)**:
   - Test doubles live strictly behind `settings.ENABLE_TEST_DOUBLES` (defaults to `False`).
   - `ENABLE_TEST_DOUBLES` is rejected with `ImproperlyConfigured` at startup in `config.settings.prod`.
   - Test doubles inherit real `verify_signature()` methods and cannot bypass cryptographic signatures, HMAC, or SSRF checks.
5. **Decryption Hardening (`core/crypto.py`)**:
   - Decryption failures raise `DecryptionError`; never return empty string or plaintext.
   - Missing `FIELD_ENCRYPTION_KEY` in production fails at startup via `ImproperlyConfigured`.
   - `ALLOW_LOCAL_WEBHOOKS` defaults to `False` and is rejected with `ImproperlyConfigured` at startup in `config.settings.prod`.

---

## 4. Production Readiness Checklist

| Category | Verification Item | Result | Operational Details |
| :--- | :--- | :--- | :--- |
| **Configuration** | Django Production Settings Check | PASS | `python manage.py check --deploy --settings=config.settings.prod` passes with 0 warnings, 0 errors. Missing `FIELD_ENCRYPTION_KEY`, enabled `ALLOW_LOCAL_WEBHOOKS`, or enabled `ENABLE_TEST_DOUBLES` raises `ImproperlyConfigured` at startup. |
| **Migrations** | Schema Drift Check | PASS | `python manage.py makemigrations --check` reports "No changes detected". All 33 models in sync. |
| **Indexes** | Activity Composite Indexes | PASS | `Activity` model has composite index `idx_activity_tenant_type_dt` on `(tenant, activity_type, -created_at)` and `idx_activity_tenant_ct_dt` on `(tenant, contact, -created_at, -id)`. |
| **Linter / Imports** | Pyflakes Cleanliness | PASS | Pyflakes exited with code 0 across the entire repository. Zero unused imports and zero undefined names. |
| **Code Hygiene** | AST & Token Scan | PASS | Verified 0 comments (`#`), 0 docstrings (`"""`), and 0 debug `print` statements across all Python files. |
| **Beat Dispatcher** | 15-Second Workflow Executor | PASS | `dispatch-due-workflow-enrollments` scheduled every 15 seconds, handling both delay resumption and `wait_for_event` timeouts. |

---

## 5. Architectural Boundaries & Scope Confirmations

In accordance with **Section 18** of `docs/product_guide.pdf`:

1. **Meta Messenger & Instagram Direct Messaging**: Excluded per spec. Real-time direct chat on social platforms is not supported; lead capture is restricted to Meta Lead Ads.
2. **VoIP Calling & Telephony Infrastructure**: Excluded per spec. Voice calls, SIP routing, and softphones are not supported; telephony is restricted to SMS and WhatsApp.
3. **Autonomous Conversational AI Chatbots**: Excluded per spec. Customer conversations route to human agents in the Unified Inbox upon inbound replies.
4. **Landing Page & Website Builders**: Excluded per spec. Form capture is restricted to Meta Lead Ads forms and external REST API integrations.
5. **CSV / Spreadsheet Contact Import**: Classified as **Pending Confirmation**. Not currently built; ingestion is handled programmatically via webhooks, Meta Lead Ads, and `/api/v1/external/contacts/upsert/`.
