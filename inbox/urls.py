from django.urls import include, path
from rest_framework.routers import DefaultRouter
from inbox.views import (
    AssignmentRuleViewSet,
    ConversationViewSet,
    InboxUnreadCountsView,
    TeamWorkloadView,
    TenantAssignmentSettingsView,
)

router = DefaultRouter()
router.register("conversations", ConversationViewSet, basename="inbox-conversation")
router.register("assignment-rules", AssignmentRuleViewSet, basename="inbox-assignment-rule")

urlpatterns = [
    path("unread-counts/", InboxUnreadCountsView.as_view(), name="inbox-unread-counts"),
    path("assignment-settings/", TenantAssignmentSettingsView.as_view(), name="inbox-assignment-settings"),
    path("workload/", TeamWorkloadView.as_view(), name="inbox-workload"),
    path("team-workload/", TeamWorkloadView.as_view(), name="inbox-team-workload"),
    path("", include(router.urls)),
]
