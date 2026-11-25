from django.urls import re_path
from landing_pages.public_views import (
    DirectFormSubmitView,
    PublicFormSubmitView,
    PublicLandingPageView,
    TenantPublicFormSubmitView,
    TenantPublicLandingPageView,
)

urlpatterns = [
    re_path(
        r"^pages/(?P<tenant_slug_or_id>[^/]+)/(?P<slug_or_id>[^/]+)/submit/?$",
        TenantPublicFormSubmitView.as_view(),
        name="public-landing-page-tenant-submit",
    ),
    re_path(
        r"^pages/(?P<slug_or_id>[^/]+)/submit/?$",
        PublicFormSubmitView.as_view(),
        name="public-landing-page-submit",
    ),
    re_path(
        r"^pages/(?P<tenant_slug_or_id>[^/]+)/(?P<slug_or_id>[^/]+)/?$",
        TenantPublicLandingPageView.as_view(),
        name="public-landing-page-tenant-detail",
    ),
    re_path(
        r"^pages/(?P<slug_or_id>[^/]+)/?$",
        PublicLandingPageView.as_view(),
        name="public-landing-page-detail",
    ),
    re_path(
        r"^forms/(?P<form_id>[^/]+)/submit/?$",
        DirectFormSubmitView.as_view(),
        name="public-form-submit",
    ),
]
