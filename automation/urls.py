from django.urls import include, path
from rest_framework.routers import DefaultRouter
from automation.views import (
    ContactEnrollmentsView,
    ContactExecutionsView,
    ContactPauseAllEnrollmentsView,
    ContactResumeAllEnrollmentsView,
    StepExecutionViewSet,
    WorkflowEnrollmentViewSet,
    WorkflowViewSet,
)

router = DefaultRouter()
router.register("workflows", WorkflowViewSet, basename="workflow")
router.register("enrollments", WorkflowEnrollmentViewSet, basename="workflow-enrollment")
router.register("executions", StepExecutionViewSet, basename="step-execution")

urlpatterns = [
    path(
        "contacts/<uuid:contact_id>/enrollments/",
        ContactEnrollmentsView.as_view(),
        name="automation-contact-enrollments",
    ),
    path(
        "contacts/<uuid:contact_id>/executions/",
        ContactExecutionsView.as_view(),
        name="automation-contact-executions",
    ),
    path(
        "contacts/<uuid:contact_id>/pause-all/",
        ContactPauseAllEnrollmentsView.as_view(),
        name="automation-contact-pause-all",
    ),
    path(
        "contacts/<uuid:contact_id>/resume-all/",
        ContactResumeAllEnrollmentsView.as_view(),
        name="automation-contact-resume-all",
    ),
    path("", include(router.urls)),
]
