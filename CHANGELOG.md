# Changelog

All notable changes to the Fitverx CRM backend platform are documented in this file.

## [Unreleased] - 2026-11-16

### Added
- **Meta OAuth Scopes & Permission Reauthorization**:
  - Pinned Meta OAuth scopes to exactly `pages_show_list`, `pages_read_engagement`, `pages_manage_metadata`, `pages_manage_ads`, and `leads_retrieval` (`public_profile` implicit).
  - Removed unused `ads_read` and `business_management` scopes.
  - Added `scopes` JSON field and `reauthorization_required` status to `MetaConnection`.
  - Added debug token inspection at connect time and periodic Celery beat health checks flagging missing scopes and notifying owners/admins.
  - Enhanced page selection and subscription to return clear `meta_missing_scope_pages_manage_metadata` or `meta_reauthorization_required` errors instead of generic 502 failures.
- **Email Verification**:
  - Added `email_verified` and `email_verified_at` to the `User` model.
  - Registration automatically dispatches signed 24-hour verification emails via platform transactional email configuration.
  - Added `POST` and `GET /api/v1/auth/verify-email/` and `POST /api/v1/auth/resend-verification/`.
  - Added shared permission `IsEmailVerified` gating member invitations, channel connections, Meta integration setup, and message dispatching.
- **Password Reset**:
  - Added `POST /api/v1/auth/forgot-password/` with enumeration-resistant constant response.
  - Added `POST /api/v1/auth/reset-password/` with signed single-use expiring token validation and immediate refresh token invalidation.
- **Team Invitations**:
  - Added `TenantInvitation` model supporting `pending`, `accepted`, `revoked`, and `expired` states.
  - Added `/api/v1/tenant/invitations/` endpoints for listing, creating, resending, and revoking invitations.
  - Added `POST /api/v1/auth/invitations/accept/` to create or attach users and memberships.
  - Restricted direct member addition (`POST /api/v1/team/`) strictly to Business Owners.
- **Session Revocation & Blacklisting**:
  - Added `POST /api/v1/auth/logout/` blacklisting refresh tokens.
  - Added `POST /api/v1/auth/logout-all/` blacklisting all user sessions.
  - Integrated automatic session revocation on password changes, member deactivations, and role updates.
- **Abuse Protection & Rate Limiting**:
  - Added IP and compound IP+email rate limiters for registration, login, forgot-password, resend-verification, and invitation accept endpoints.
  - Added 5-attempt / 15-minute login lockout with backoff mechanism.
- **Onboarding Checklist**:
  - Added `GET /api/v1/tenant/onboarding/` returning live workspace setup completion status with frontend route hints.
- **Account & Tenant Lifecycle**:
  - Added email change request and verification with re-verification (`/api/v1/auth/change-email/`, `/api/v1/auth/verify-email-change/`).
  - Added workspace discovery and context switching (`/api/v1/auth/tenants/`, `/api/v1/auth/switch-tenant/`).
  - Added tenant leave support (`POST /api/v1/tenant/leave/`) protecting the last owner.
  - Added secure ownership transfer (`POST /api/v1/tenant/transfer-ownership/`).
  - Added tenant deactivation (`POST /api/v1/tenant/deactivate/`) with grace period enforcement blocking writes and outbound sends while preserving data, alongside reactivation (`POST /api/v1/tenant/reactivate/`).
- **Nginx & SSL Configuration**:
  - Configured Let's Encrypt SSL certificates for `ads.fitverx.com` on port 443 with TLS 1.3 and reverse proxy upstream routing.
