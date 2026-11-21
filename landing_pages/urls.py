from django.urls import include, path
from rest_framework.routers import DefaultRouter
from landing_pages.views import (
    FormSubmissionViewSet,
    LandingPageSectionViewSet,
    LandingPageTemplateViewSet,
    LandingPageViewSet,
)

router = DefaultRouter()
router.register("templates", LandingPageTemplateViewSet, basename="landing-page-template")
router.register("sections", LandingPageSectionViewSet, basename="landing-page-section")
router.register("submissions", FormSubmissionViewSet, basename="landing-page-submission")
router.register("", LandingPageViewSet, basename="landing-page")

urlpatterns = [
    path("", include(router.urls)),
]
