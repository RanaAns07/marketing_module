import csv
from datetime import datetime, time as dt_time, timedelta
import io
import zoneinfo
from django.http import StreamingHttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.permissions import IsOwnerOrAdmin
from core.context import require_tenant
from core.pagination import KeysetCursorPagination
from usage.models import RateCard, UsageRecord
from usage.selectors import (
    compute_usage_summary,
    get_tenant_rate_cards_queryset,
    get_usage_records_queryset,
)
from usage.serializers import (
    RateCardSerializer,
    UsageRecordSerializer,
    UsageSummarySerializer,
)
from usage.services import (
    create_tenant_rate_card,
    delete_tenant_rate_card,
    update_tenant_rate_card,
)


def _resolve_tenant_tz() -> zoneinfo.ZoneInfo:
    try:
        tenant = require_tenant()
        tz_name = tenant.timezone or "UTC"
        return zoneinfo.ZoneInfo(tz_name)
    except Exception:
        return zoneinfo.ZoneInfo("UTC")


def _get_day_bounds_utc(day_str: str | None = None) -> tuple[datetime, datetime]:
    tz = _resolve_tenant_tz()
    now_local = timezone.now().astimezone(tz)
    target_date = parse_date(day_str) if day_str else now_local.date()
    if target_date is None:
        target_date = now_local.date()

    start_local = datetime.combine(target_date, dt_time.min, tzinfo=tz)
    end_local = datetime.combine(target_date, dt_time.max, tzinfo=tz)
    return (
        start_local.astimezone(zoneinfo.ZoneInfo("UTC")),
        end_local.astimezone(zoneinfo.ZoneInfo("UTC")),
    )


def _get_month_bounds_utc(month_str: str | None = None) -> tuple[datetime, datetime]:
    tz = _resolve_tenant_tz()
    now_local = timezone.now().astimezone(tz)

    year = now_local.year
    month = now_local.month

    if month_str:
        parts = month_str.strip().split("-")
        if len(parts) == 2:
            try:
                year = int(parts[0])
                month = int(parts[1])
            except ValueError:
                pass

    start_date = datetime(year, month, 1, 0, 0, 0, tzinfo=tz)
    if month == 12:
        next_month = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=tz)
    else:
        next_month = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=tz)
    end_date = next_month - timedelta(microseconds=1)

    return (
        start_date.astimezone(zoneinfo.ZoneInfo("UTC")),
        end_date.astimezone(zoneinfo.ZoneInfo("UTC")),
    )


class UsageSummaryDailyView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]

    @extend_schema(
        parameters=[
            OpenApiParameter(name="day", type=str, location=OpenApiParameter.QUERY, required=False, description="YYYY-MM-DD"),
        ],
        responses={200: UsageSummarySerializer},
    )
    def get(self, request):
        day_str = request.query_params.get("day") or request.query_params.get("date")
        start_utc, end_utc = _get_day_bounds_utc(day_str)
        qs = UsageRecord.objects.filter(occurred_at__gte=start_utc, occurred_at__lte=end_utc)
        summary = compute_usage_summary(qs, period="daily")
        return Response(summary, status=status.HTTP_200_OK)


class UsageSummaryMonthlyView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]

    @extend_schema(
        parameters=[
            OpenApiParameter(name="month", type=str, location=OpenApiParameter.QUERY, required=False, description="YYYY-MM"),
        ],
        responses={200: UsageSummarySerializer},
    )
    def get(self, request):
        month_str = request.query_params.get("month")
        start_utc, end_utc = _get_month_bounds_utc(month_str)
        qs = UsageRecord.objects.filter(occurred_at__gte=start_utc, occurred_at__lte=end_utc)
        summary = compute_usage_summary(qs, period="monthly")
        return Response(summary, status=status.HTTP_200_OK)


class UsageRecordListView(generics.ListAPIView):
    serializer_class = UsageRecordSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    pagination_class = KeysetCursorPagination

    @extend_schema(
        parameters=[
            OpenApiParameter(name="channel", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="status", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="country", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="from_date", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="to_date", type=str, location=OpenApiParameter.QUERY, required=False),
        ]
    )
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return UsageRecord.unscoped_objects.none()
        return get_usage_records_queryset(self.request.query_params)


class TenantRateCardViewSet(viewsets.ModelViewSet):
    serializer_class = RateCardSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return RateCard.objects.none()
        return get_tenant_rate_cards_queryset()

    def perform_create(self, serializer):
        v = serializer.validated_data
        instance = create_tenant_rate_card(
            channel=v["channel"],
            unit_cost=v["unit_cost"],
            currency=v.get("currency", "USD"),
            destination_country=v.get("destination_country", ""),
            whatsapp_category=v.get("whatsapp_category", ""),
            is_active=v.get("is_active", True),
        )
        serializer.instance = instance

    def perform_update(self, serializer):
        update_tenant_rate_card(
            rate_card=self.get_object(),
            **serializer.validated_data,
        )

    def perform_destroy(self, instance):
        delete_tenant_rate_card(rate_card=instance)


class UsageExportCSVView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]

    @extend_schema(
        parameters=[
            OpenApiParameter(name="day", type=str, location=OpenApiParameter.QUERY, required=False, description="YYYY-MM-DD"),
            OpenApiParameter(name="month", type=str, location=OpenApiParameter.QUERY, required=False, description="YYYY-MM"),
        ],
        responses={200: str},
    )
    def get(self, request):
        day_str = request.query_params.get("day") or request.query_params.get("date")
        month_str = request.query_params.get("month")

        if day_str:
            start_utc, end_utc = _get_day_bounds_utc(day_str)
            filename_suffix = day_str
        elif month_str:
            start_utc, end_utc = _get_month_bounds_utc(month_str)
            filename_suffix = month_str
        else:
            start_utc, end_utc = _get_day_bounds_utc(None)
            filename_suffix = timezone.now().strftime("%Y-%m-%d")

        qs = (
            UsageRecord.objects.select_related("message")
            .filter(occurred_at__gte=start_utc, occurred_at__lte=end_utc)
            .order_by("-occurred_at", "-id")
        )

        def csv_stream():
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(
                [
                    "Record ID",
                    "Message ID",
                    "Channel",
                    "Quantity",
                    "Destination Country",
                    "WhatsApp Category",
                    "Unit Cost",
                    "Total Cost",
                    "Currency",
                    "Status",
                    "Occurred At",
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

            for rec in qs.iterator(chunk_size=1000):
                writer.writerow(
                    [
                        str(rec.id),
                        str(rec.message_id),
                        rec.channel,
                        rec.quantity,
                        rec.destination_country,
                        rec.whatsapp_category,
                        str(rec.unit_cost),
                        str(rec.total_cost),
                        rec.currency,
                        rec.status,
                        rec.occurred_at.isoformat(),
                    ]
                )
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)

        response = StreamingHttpResponse(csv_stream(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="usage_export_{filename_suffix}.csv"'
        return response
