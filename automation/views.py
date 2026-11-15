from django.http import Http404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.permissions import IsOwnerOrAdmin, IsTenantMember
from automation.models import StepExecution, Workflow, WorkflowEnrollment
from automation.selectors import (
    get_step_executions_queryset,
    get_workflow_enrollments_queryset,
    get_workflows_queryset,
)
from automation.serializers import (
    ContactPauseResumeSerializer,
    ManualEnrollSerializer,
    ManualUnenrollSerializer,
    StepExecutionSerializer,
    WorkflowDryRunValidateSerializer,
    WorkflowDuplicateSerializer,
    WorkflowEnrollmentSerializer,
    WorkflowSerializer,
    WorkflowValidationResultSerializer,
)
from automation.services import (
    EnrollmentService,
    activate_workflow,
    create_workflow,
    duplicate_workflow,
    pause_workflow,
    resume_workflow,
    update_workflow,
    validate_workflow_instance,
    validate_workflow_payload,
)
from contacts.models import Contact
from core.exceptions import ApplicationError
from core.pagination import KeysetCursorPagination
from core.views import TenantScopedViewSet


class WorkflowViewSet(TenantScopedViewSet):
    serializer_class = WorkflowSerializer
    permission_classes = [IsAuthenticated, IsTenantMember]
    queryset = Workflow.unscoped_objects.none()

    def get_permissions(self):
        write_actions = {
            "create",
            "update",
            "partial_update",
            "destroy",
            "activate",
            "pause",
            "resume",
            "duplicate",
        }
        if self.action in write_actions:
            return [IsAuthenticated(), IsOwnerOrAdmin()]
        return [IsAuthenticated(), IsTenantMember()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Workflow.unscoped_objects.none()
        return get_workflows_queryset(
            status_filter=self.request.query_params.get("status"),
            trigger_type=self.request.query_params.get("trigger_type"),
            search=self.request.query_params.get("search"),
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        workflow = create_workflow(
            name=data["name"],
            description=data.get("description", ""),
            status_value=data.get("status", Workflow.Status.DRAFT),
            trigger_type=data.get("trigger_type", Workflow.TriggerType.NEW_LEAD_CREATED),
            trigger_config=data.get("trigger_config"),
            re_entry=data.get("re_entry", Workflow.ReEntry.ONCE),
            stop_on_reply=data.get("stop_on_reply", True),
            is_promotional=data.get("is_promotional", False),
            exit_events=data.get("exit_events"),
            entry_step_key=data.get("entry_step_key", ""),
            steps_data=data.get("steps"),
        )
        return Response(
            WorkflowSerializer(workflow, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        updated = update_workflow(workflow=instance, **serializer.validated_data)
        return Response(
            WorkflowSerializer(updated, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="automation_workflows_validate_collection",
        request=WorkflowDryRunValidateSerializer,
        responses={200: WorkflowValidationResultSerializer},
    )
    @action(detail=False, methods=["post"], url_path="validate")
    def validate_collection(self, request):
        serializer = WorkflowDryRunValidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = validate_workflow_payload(
            trigger_type=data.get("trigger_type", Workflow.TriggerType.NEW_LEAD_CREATED),
            trigger_config=data.get("trigger_config", {}),
            steps_data=data.get("steps", []),
            entry_step_key=data.get("entry_step_key", ""),
            strict_activation=data.get("strict_activation", True),
        )
        return Response(result, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="automation_workflow_validate_detail",
        request=WorkflowDryRunValidateSerializer,
        responses={200: WorkflowValidationResultSerializer},
    )
    @action(detail=True, methods=["post"], url_path="validate")
    def validate_detail(self, request, pk=None):
        workflow = self.get_object()
        if isinstance(request.data, dict) and "steps" in request.data:
            serializer = WorkflowDryRunValidateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            result = validate_workflow_payload(
                trigger_type=data.get("trigger_type", workflow.trigger_type),
                trigger_config=data.get("trigger_config", workflow.trigger_config),
                steps_data=data.get("steps", []),
                entry_step_key=data.get("entry_step_key", workflow.entry_step_key),
                strict_activation=data.get("strict_activation", True),
            )
        else:
            result = validate_workflow_instance(workflow, strict_activation=True)
        return Response(result, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="automation_workflow_activate",
        request=None,
        responses={200: WorkflowSerializer},
    )
    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, pk=None):
        workflow = self.get_object()
        updated = activate_workflow(workflow=workflow)
        return Response(
            WorkflowSerializer(updated, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="automation_workflow_pause",
        request=None,
        responses={200: WorkflowSerializer},
    )
    @action(detail=True, methods=["post"], url_path="pause")
    def pause(self, request, pk=None):
        workflow = self.get_object()
        updated = pause_workflow(workflow=workflow)
        return Response(
            WorkflowSerializer(updated, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="automation_workflow_resume",
        request=None,
        responses={200: WorkflowSerializer},
    )
    @action(detail=True, methods=["post"], url_path="resume")
    def resume(self, request, pk=None):
        workflow = self.get_object()
        updated = resume_workflow(workflow=workflow)
        return Response(
            WorkflowSerializer(updated, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="automation_workflow_duplicate",
        request=WorkflowDuplicateSerializer,
        responses={201: WorkflowSerializer},
    )
    @action(detail=True, methods=["post"], url_path="duplicate")
    def duplicate(self, request, pk=None):
        workflow = self.get_object()
        serializer = WorkflowDuplicateSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        cloned = duplicate_workflow(
            workflow=workflow,
            name=serializer.validated_data.get("name", ""),
        )
        return Response(
            WorkflowSerializer(cloned, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        operation_id="automation_workflow_enrollments_list",
        parameters=[
            OpenApiParameter(name="status", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="contact", type=str, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={200: WorkflowEnrollmentSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="enrollments")
    def enrollments(self, request, pk=None):
        workflow = self.get_object()
        qs = get_workflow_enrollments_queryset(
            workflow=workflow,
            contact=request.query_params.get("contact"),
            status_filter=request.query_params.get("status"),
        )
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(WorkflowEnrollmentSerializer(page, many=True).data)
        return Response(WorkflowEnrollmentSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="automation_workflow_executions_list",
        parameters=[
            OpenApiParameter(name="status", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="contact", type=str, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={200: StepExecutionSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="executions")
    def executions(self, request, pk=None):
        workflow = self.get_object()
        qs = get_step_executions_queryset(
            workflow=workflow,
            contact=request.query_params.get("contact"),
            status_filter=request.query_params.get("status"),
        )
        paginator = KeysetCursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        if page is not None:
            return paginator.get_paginated_response(StepExecutionSerializer(page, many=True).data)
        return Response(StepExecutionSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="automation_workflow_enroll",
        request=ManualEnrollSerializer,
        responses={201: WorkflowEnrollmentSerializer(many=True)},
    )
    @action(detail=True, methods=["post"], url_path="enroll")
    def enroll(self, request, pk=None):
        workflow = self.get_object()
        serializer = ManualEnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        contacts_to_enroll: list[Contact] = []
        if data.get("contact"):
            contacts_to_enroll.append(data["contact"])
        for c in data.get("contacts") or []:
            if c not in contacts_to_enroll:
                contacts_to_enroll.append(c)

        enrolled: list[WorkflowEnrollment] = []
        for contact in contacts_to_enroll:
            enr, _ = EnrollmentService.enroll(
                workflow=workflow,
                contact=contact,
                allow_inactive_workflow=data.get("allow_inactive", True),
                actor=request.user,
            )
            if enr is not None:
                enrolled.append(enr)

        if len(contacts_to_enroll) == 1 and enrolled:
            return Response(
                WorkflowEnrollmentSerializer(enrolled[0]).data,
                status=status.HTTP_201_CREATED,
            )
        return Response(
            WorkflowEnrollmentSerializer(enrolled, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        operation_id="automation_workflow_unenroll",
        request=ManualUnenrollSerializer,
        responses={200: WorkflowEnrollmentSerializer},
    )
    @action(detail=True, methods=["post"], url_path="unenroll")
    def unenroll(self, request, pk=None):
        workflow = self.get_object()
        serializer = ManualUnenrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        reason = data.get("reason") or "manual_unenroll"

        enrollment = data.get("enrollment")
        if enrollment is None and data.get("contact"):
            enrollment = (
                WorkflowEnrollment.objects.filter(
                    workflow=workflow,
                    contact=data["contact"],
                    status__in=[WorkflowEnrollment.Status.ACTIVE, WorkflowEnrollment.Status.PAUSED],
                )
                .first()
            )
        if enrollment is None or enrollment.workflow_id != workflow.id:
            raise ApplicationError(
                detail="No active or paused enrollment found for this workflow and contact.",
                code="enrollment_not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        updated = EnrollmentService.unenroll(
            enrollment=enrollment,
            reason=reason,
            actor=request.user,
        )
        return Response(WorkflowEnrollmentSerializer(updated).data, status=status.HTTP_200_OK)


class WorkflowEnrollmentViewSet(TenantScopedViewSet):
    serializer_class = WorkflowEnrollmentSerializer
    permission_classes = [IsAuthenticated, IsTenantMember]
    queryset = WorkflowEnrollment.unscoped_objects.none()
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return WorkflowEnrollment.unscoped_objects.none()
        return get_workflow_enrollments_queryset(
            workflow=self.request.query_params.get("workflow"),
            contact=self.request.query_params.get("contact"),
            status_filter=self.request.query_params.get("status"),
        )

    @extend_schema(
        operation_id="automation_enrollments_create",
        request=ManualEnrollSerializer,
        responses={201: WorkflowEnrollmentSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = ManualEnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        workflow = data.get("workflow")
        if workflow is None:
            raise ApplicationError(
                detail="workflow_id is required when enrolling via /enrollments/.",
                code="workflow_required",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        contact = data.get("contact") or (data.get("contacts") or [None])[0]
        enr, _ = EnrollmentService.enroll(
            workflow=workflow,
            contact=contact,
            allow_inactive_workflow=data.get("allow_inactive", True),
            actor=request.user,
        )
        if enr is None:
            raise ApplicationError(
                detail="Contact could not be enrolled (already completed once or workflow has no steps).",
                code="enrollment_skipped",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            WorkflowEnrollmentSerializer(enr).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        operation_id="automation_enrollment_unenroll",
        request=ManualUnenrollSerializer,
        responses={200: WorkflowEnrollmentSerializer},
    )
    @action(detail=True, methods=["post"], url_path="unenroll")
    def unenroll(self, request, pk=None):
        enrollment = self.get_object()
        serializer = ManualUnenrollSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason") or "manual_unenroll"
        updated = EnrollmentService.unenroll(
            enrollment=enrollment,
            reason=reason,
            actor=request.user,
        )
        return Response(WorkflowEnrollmentSerializer(updated).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="automation_enrollment_pause",
        request=ContactPauseResumeSerializer,
        responses={200: WorkflowEnrollmentSerializer},
    )
    @action(detail=True, methods=["post"], url_path="pause")
    def pause(self, request, pk=None):
        enrollment = self.get_object()
        reason = str((request.data or {}).get("reason") or "manual").strip()
        updated = EnrollmentService.pause_enrollment(
            enrollment=enrollment,
            reason=reason,
            actor=request.user,
        )
        return Response(WorkflowEnrollmentSerializer(updated).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="automation_enrollment_resume",
        request=None,
        responses={200: WorkflowEnrollmentSerializer},
    )
    @action(detail=True, methods=["post"], url_path="resume")
    def resume(self, request, pk=None):
        enrollment = self.get_object()
        updated = EnrollmentService.resume_enrollment(
            enrollment=enrollment,
            actor=request.user,
        )
        return Response(WorkflowEnrollmentSerializer(updated).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="automation_enrollment_executions",
        responses={200: StepExecutionSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="executions")
    def executions(self, request, pk=None):
        enrollment = self.get_object()
        qs = get_step_executions_queryset(
            enrollment=enrollment,
            status_filter=request.query_params.get("status"),
        )
        paginator = KeysetCursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        if page is not None:
            return paginator.get_paginated_response(StepExecutionSerializer(page, many=True).data)
        return Response(StepExecutionSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="automation_enrollments_pause_all",
        request=ContactPauseResumeSerializer,
        responses={200: WorkflowEnrollmentSerializer(many=True)},
    )
    @action(detail=False, methods=["post"], url_path="pause-all")
    def pause_all(self, request):
        serializer = ContactPauseResumeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contact = serializer.validated_data.get("contact")
        if contact is None:
            raise ApplicationError(
                detail="contact_id is required.",
                code="contact_required",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        reason = serializer.validated_data.get("reason") or "manual_pause_all"
        paused = EnrollmentService.pause_all_for_contact(
            contact=contact,
            reason=reason,
            actor=request.user,
        )
        return Response(WorkflowEnrollmentSerializer(paused, many=True).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="automation_enrollments_resume_all",
        request=ContactPauseResumeSerializer,
        responses={200: WorkflowEnrollmentSerializer(many=True)},
    )
    @action(detail=False, methods=["post"], url_path="resume-all")
    def resume_all(self, request):
        serializer = ContactPauseResumeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contact = serializer.validated_data.get("contact")
        if contact is None:
            raise ApplicationError(
                detail="contact_id is required.",
                code="contact_required",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        resumed = EnrollmentService.resume_all_for_contact(
            contact=contact,
            actor=request.user,
        )
        return Response(WorkflowEnrollmentSerializer(resumed, many=True).data, status=status.HTTP_200_OK)


class StepExecutionViewSet(TenantScopedViewSet):
    serializer_class = StepExecutionSerializer
    permission_classes = [IsAuthenticated, IsTenantMember]
    pagination_class = KeysetCursorPagination
    queryset = StepExecution.unscoped_objects.none()
    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return StepExecution.unscoped_objects.none()
        return get_step_executions_queryset(
            workflow=self.request.query_params.get("workflow"),
            contact=self.request.query_params.get("contact"),
            enrollment=self.request.query_params.get("enrollment"),
            status_filter=self.request.query_params.get("status"),
        )


def _get_contact_or_404(contact_id) -> Contact:
    contact = Contact.objects.filter(pk=contact_id).first()
    if contact is None:
        raise Http404("Contact not found.")
    return contact


class ContactEnrollmentsView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    @extend_schema(
        operation_id="automation_contact_enrollments_list",
        parameters=[
            OpenApiParameter(name="status", type=str, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={200: WorkflowEnrollmentSerializer(many=True)},
    )
    def get(self, request, contact_id):
        contact = _get_contact_or_404(contact_id)
        qs = get_workflow_enrollments_queryset(
            contact=contact,
            status_filter=request.query_params.get("status"),
        )
        return Response(WorkflowEnrollmentSerializer(qs, many=True).data, status=status.HTTP_200_OK)


class ContactExecutionsView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    @extend_schema(
        operation_id="automation_contact_executions_list",
        parameters=[
            OpenApiParameter(name="status", type=str, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={200: StepExecutionSerializer(many=True)},
    )
    def get(self, request, contact_id):
        contact = _get_contact_or_404(contact_id)
        qs = get_step_executions_queryset(
            contact=contact,
            status_filter=request.query_params.get("status"),
        )
        paginator = KeysetCursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        if page is not None:
            return paginator.get_paginated_response(StepExecutionSerializer(page, many=True).data)
        return Response(StepExecutionSerializer(qs, many=True).data, status=status.HTTP_200_OK)


class ContactPauseAllEnrollmentsView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    @extend_schema(
        operation_id="automation_contact_pause_all",
        request=ContactPauseResumeSerializer,
        responses={200: WorkflowEnrollmentSerializer(many=True)},
    )
    def post(self, request, contact_id):
        contact = _get_contact_or_404(contact_id)
        serializer = ContactPauseResumeSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason") or "manual_pause_all"
        paused = EnrollmentService.pause_all_for_contact(
            contact=contact,
            reason=reason,
            actor=request.user,
        )
        return Response(WorkflowEnrollmentSerializer(paused, many=True).data, status=status.HTTP_200_OK)


class ContactResumeAllEnrollmentsView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    @extend_schema(
        operation_id="automation_contact_resume_all",
        request=None,
        responses={200: WorkflowEnrollmentSerializer(many=True)},
    )
    def post(self, request, contact_id):
        contact = _get_contact_or_404(contact_id)
        resumed = EnrollmentService.resume_all_for_contact(
            contact=contact,
            actor=request.user,
        )
        return Response(WorkflowEnrollmentSerializer(resumed, many=True).data, status=status.HTTP_200_OK)
