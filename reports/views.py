import hashlib
from django.core.cache import cache
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.models import Membership
from accounts.permissions import IsOwnerOrAdmin, IsTenantMember
from core.context import require_tenant
from reports.selectors import (
    get_dashboard_data,
    get_lead_acquisition_report,
    get_messaging_report,
    get_pipeline_report,
    get_workflow_performance_report,
    parse_date_range,
)
from reports.serializers import (
    DashboardResponseSerializer,
    LeadAcquisitionReportSerializer,
    MessagingReportSerializer,
    PipelineReportSerializer,
    TeamWorkloadMemberSerializer,
    WorkflowPerformanceReportSerializer,
)


class LeadAcquisitionReportView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    @extend_schema(
        parameters=[
            OpenApiParameter(name="preset", type=str, location=OpenApiParameter.QUERY, required=False, description="today, yesterday, last_7_days, last_14_days, last_30_days, last_90_days, this_month, last_month"),
            OpenApiParameter(name="start_date", type=str, location=OpenApiParameter.QUERY, required=False, description="YYYY-MM-DD"),
            OpenApiParameter(name="end_date", type=str, location=OpenApiParameter.QUERY, required=False, description="YYYY-MM-DD"),
            OpenApiParameter(name="bucket", type=str, location=OpenApiParameter.QUERY, required=False, description="day or week"),
        ],
        responses={200: LeadAcquisitionReportSerializer},
    )
    def get(self, request):
        tenant = require_tenant()
        start_utc, end_utc, _ = parse_date_range(request.query_params, tenant.timezone)
        bucket = str(request.query_params.get("bucket") or "day").strip().lower()
        report = get_lead_acquisition_report(
            start_dt_utc=start_utc,
            end_dt_utc=end_utc,
            membership=request.membership,
            bucket=bucket,
        )
        return Response(report, status=status.HTTP_200_OK)


class PipelineReportView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    @extend_schema(
        parameters=[
            OpenApiParameter(name="top_tags", type=int, location=OpenApiParameter.QUERY, required=False, default=10),
        ],
        responses={200: PipelineReportSerializer},
    )
    def get(self, request):
        top_tags_val = int(request.query_params.get("top_tags") or 10)
        report = get_pipeline_report(
            membership=request.membership,
            top_n_tags=top_tags_val,
        )
        return Response(report, status=status.HTTP_200_OK)


class TeamWorkloadReportView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]

    @extend_schema(
        responses={200: TeamWorkloadMemberSerializer(many=True)},
    )
    def get(self, request):
        from accounts.selectors import get_team_workload
        workload = get_team_workload()
        return Response(workload, status=status.HTTP_200_OK)


class WorkflowPerformanceReportView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]

    @extend_schema(
        parameters=[
            OpenApiParameter(name="preset", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="start_date", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="end_date", type=str, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={200: WorkflowPerformanceReportSerializer},
    )
    def get(self, request):
        tenant = require_tenant()
        start_utc, end_utc, _ = parse_date_range(request.query_params, tenant.timezone)
        report = get_workflow_performance_report(
            start_dt_utc=start_utc,
            end_dt_utc=end_utc,
        )
        return Response(report, status=status.HTTP_200_OK)


class MessagingReportView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    @extend_schema(
        parameters=[
            OpenApiParameter(name="preset", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="start_date", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="end_date", type=str, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={200: MessagingReportSerializer},
    )
    def get(self, request):
        tenant = require_tenant()
        start_utc, end_utc, _ = parse_date_range(request.query_params, tenant.timezone)
        report = get_messaging_report(
            start_dt_utc=start_utc,
            end_dt_utc=end_utc,
            membership=request.membership,
        )
        return Response(report, status=status.HTTP_200_OK)


class DashboardView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    @extend_schema(
        parameters=[
            OpenApiParameter(name="preset", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="start_date", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="end_date", type=str, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={200: DashboardResponseSerializer},
    )
    def get(self, request):
        tenant = require_tenant()
        membership: Membership = request.membership

        start_utc, end_utc, range_label = parse_date_range(request.query_params, tenant.timezone)
        start_iso = start_utc.isoformat()
        end_iso = end_utc.isoformat()

        is_marketer_restricted = (
            membership.role == Membership.Role.MARKETER
            and not tenant.marketer_can_view_all_contacts
        )
        scope_ident = str(membership.id) if is_marketer_restricted else membership.role
        raw_key = f"{tenant.id}:{scope_ident}:{range_label}:{start_iso}:{end_iso}"
        cache_key = f"dashboard:{hashlib.sha256(raw_key.encode('utf-8')).hexdigest()}"

        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached, status=status.HTTP_200_OK)

        data = get_dashboard_data(
            params=request.query_params,
            membership=membership,
        )

        try:
            cache.set(cache_key, data, timeout=60)
        except Exception:
            pass

        return Response(data, status=status.HTTP_200_OK)
