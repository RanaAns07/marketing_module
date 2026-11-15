from django.urls import include, path
from rest_framework.routers import DefaultRouter
from contacts.views import (
    AppointmentViewSet,
    ContactViewSet,
    CustomFieldDefinitionViewSet,
    LeadStatusViewSet,
    TagViewSet,
    TaskViewSet,
)

router = DefaultRouter()
router.register("contacts", ContactViewSet, basename="contacts")
router.register("tags", TagViewSet, basename="tags")
router.register("custom-fields", CustomFieldDefinitionViewSet, basename="custom-fields")
router.register("lead-statuses", LeadStatusViewSet, basename="lead-statuses")
router.register("appointments", AppointmentViewSet, basename="appointments")
router.register("tasks", TaskViewSet, basename="tasks")

urlpatterns = [
    path("", include(router.urls)),
]
