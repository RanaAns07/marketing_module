from django.urls import path
from reports.views import (
    DashboardView,
    LeadAcquisitionReportView,
    MessagingReportView,
    PipelineReportView,
    TeamWorkloadReportView,
    WorkflowPerformanceReportView,
)

urlpatterns = [
    path("lead-acquisition/", LeadAcquisitionReportView.as_view(), name="report-lead-acquisition"),
    path("leads/", LeadAcquisitionReportView.as_view(), name="report-leads"),
    path("pipeline/", PipelineReportView.as_view(), name="report-pipeline"),
    path("team-workload/", TeamWorkloadReportView.as_view(), name="report-team-workload"),
    path("workload/", TeamWorkloadReportView.as_view(), name="report-workload"),
    path("workflow-performance/", WorkflowPerformanceReportView.as_view(), name="report-workflow-performance"),
    path("workflows/", WorkflowPerformanceReportView.as_view(), name="report-workflows"),
    path("messaging/", MessagingReportView.as_view(), name="report-messaging"),
    path("dashboard/", DashboardView.as_view(), name="report-dashboard"),
]
