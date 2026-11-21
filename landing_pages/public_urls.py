from django.urls import path
from landing_pages.public_views import (
    DirectFormSubmitView,
    PublicFormSubmitView,
    PublicLandingPageView,
    TenantPublicFormSubmitView,
    TenantPublicLandingPageView,
)

urlpatterns = [
    path(
        "pages/<str:tenant_slug_or_id>/<str:slug_or_id>/submit/",
        TenantPublicFormSubmitView.as_view(),
        name="public-landing-page-tenant-submit",
    ),
    path(
        "pages/<str:tenant_slug_or_id>/<str:slug_or_id>/",
        TenantPublicLandingPageView.as_view(),
        name="public-landing-page-tenant-detail",
    ),
    path(
        "pages/<str:slug_or_id>/submit/",
        PublicFormSubmitView.as_view(),
        name="public-landing-page-submit",
    ),
    path(
        "pages/<str:slug_or_id>/",
        PublicLandingPageView.as_view(),
        name="public-landing-page-detail",
    ),
    path(
        "forms/<str:form_id>/submit/",
        DirectFormSubmitView.as_view(),
        name="public-form-submit",
    ),
]
