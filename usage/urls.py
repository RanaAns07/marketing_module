from django.urls import include, path
from rest_framework.routers import DefaultRouter
from usage.views import (
    TenantRateCardViewSet,
    UsageExportCSVView,
    UsageRecordListView,
    UsageSummaryDailyView,
    UsageSummaryMonthlyView,
)

router = DefaultRouter()
router.register("rate-cards", TenantRateCardViewSet, basename="tenant-rate-card")

urlpatterns = [
    path("summary/daily/", UsageSummaryDailyView.as_view(), name="usage-summary-daily"),
    path("summary/monthly/", UsageSummaryMonthlyView.as_view(), name="usage-summary-monthly"),
    path("daily/", UsageSummaryDailyView.as_view(), name="usage-daily"),
    path("monthly/", UsageSummaryMonthlyView.as_view(), name="usage-monthly"),
    path("records/", UsageRecordListView.as_view(), name="usage-records-list"),
    path("export/csv/", UsageExportCSVView.as_view(), name="usage-export-csv"),
    path("export/", UsageExportCSVView.as_view(), name="usage-export"),
    path("", include(router.urls)),
]
