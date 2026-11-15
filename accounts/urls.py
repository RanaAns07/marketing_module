from django.urls import include, path
from rest_framework.routers import DefaultRouter
from accounts.views import (
    AcceptInvitationView,
    ChangeEmailRequestView,
    CurrentUserView,
    DeactivateTenantView,
    ForgotPasswordView,
    LeaveTenantView,
    LoginView,
    LogoutAllView,
    LogoutView,
    PasswordChangeView,
    ReactivateTenantView,
    RefreshTokenView,
    RegisterView,
    ResendVerificationView,
    ResetPasswordView,
    SwitchTenantView,
    TeamMemberViewSet,
    TenantAPIKeyViewSet,
    TenantInvitationViewSet,
    TenantOnboardingView,
    TenantSettingsView,
    TransferOwnershipView,
    UserTenantsListView,
    VerifyChangeEmailView,
    VerifyEmailView,
)

router = DefaultRouter()
router.register("team", TeamMemberViewSet, basename="team")
router.register("tenant/invitations", TenantInvitationViewSet, basename="tenant-invitations")
router.register("tenant/api-keys", TenantAPIKeyViewSet, basename="tenant-api-keys")

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="auth-register"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/refresh/", RefreshTokenView.as_view(), name="auth-refresh"),
    path("auth/verify-email/", VerifyEmailView.as_view(), name="auth-verify-email"),
    path("auth/resend-verification/", ResendVerificationView.as_view(), name="auth-resend-verification"),
    path("auth/forgot-password/", ForgotPasswordView.as_view(), name="auth-forgot-password"),
    path("auth/reset-password/", ResetPasswordView.as_view(), name="auth-reset-password"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("auth/logout-all/", LogoutAllView.as_view(), name="auth-logout-all"),
    path("auth/me/", CurrentUserView.as_view(), name="auth-me"),
    path("auth/change-email/", ChangeEmailRequestView.as_view(), name="auth-change-email"),
    path("auth/verify-email-change/", VerifyChangeEmailView.as_view(), name="auth-verify-email-change"),
    path("auth/password/change/", PasswordChangeView.as_view(), name="auth-password-change"),
    path("auth/tenants/", UserTenantsListView.as_view(), name="auth-tenants"),
    path("auth/switch-tenant/", SwitchTenantView.as_view(), name="auth-switch-tenant"),
    path("auth/invitations/accept/", AcceptInvitationView.as_view(), name="auth-invitations-accept"),
    path("tenant/settings/", TenantSettingsView.as_view(), name="tenant-settings"),
    path("tenant/onboarding/", TenantOnboardingView.as_view(), name="tenant-onboarding"),
    path("tenant/leave/", LeaveTenantView.as_view(), name="tenant-leave"),
    path("tenant/transfer-ownership/", TransferOwnershipView.as_view(), name="tenant-transfer-ownership"),
    path("tenant/deactivate/", DeactivateTenantView.as_view(), name="tenant-deactivate"),
    path("tenant/reactivate/", ReactivateTenantView.as_view(), name="tenant-reactivate"),
    path("", include(router.urls)),
]
