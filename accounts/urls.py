from django.urls import include, path
from rest_framework.routers import DefaultRouter
from accounts.views import (
    CurrentUserView,
    LoginView,
    PasswordChangeView,
    RefreshTokenView,
    RegisterView,
    TeamMemberViewSet,
    TenantAPIKeyViewSet,
    TenantSettingsView,
)

router = DefaultRouter()
router.register("team", TeamMemberViewSet, basename="team")
router.register("tenant/api-keys", TenantAPIKeyViewSet, basename="tenant-api-keys")

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="auth-register"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/refresh/", RefreshTokenView.as_view(), name="auth-refresh"),
    path("auth/me/", CurrentUserView.as_view(), name="auth-me"),
    path("auth/password/change/", PasswordChangeView.as_view(), name="auth-password-change"),
    path("tenant/settings/", TenantSettingsView.as_view(), name="tenant-settings"),
    path("", include(router.urls)),
]
