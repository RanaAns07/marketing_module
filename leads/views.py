from django.http import Http404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.models import Membership
from accounts.permissions import IsEmailVerified, IsOwnerOrAdmin
from core.context import get_current_tenant_id
from core.exceptions import ApplicationError
from core.views import TenantScopedViewSet
from leads.models import MetaConnection, MetaLeadEvent, MetaLeadForm, MetaPage
from leads.selectors import (
    get_meta_connection,
    get_meta_form_by_identifier,
    get_meta_forms_queryset,
    get_meta_lead_events_queryset,
    get_meta_page_by_identifier,
    get_meta_pages_queryset,
)
from leads.serializers import (
    MetaConnectionSerializer,
    MetaFormQuestionsResponseSerializer,
    MetaFormSelectSerializer,
    MetaFormUpdateSerializer,
    MetaLeadEventSerializer,
    MetaLeadFormSerializer,
    MetaOAuthCallbackSerializer,
    MetaOAuthConnectQuerySerializer,
    MetaOAuthConnectResponseSerializer,
    MetaPageSelectSerializer,
    MetaPageSerializer,
)
from leads.services import (
    complete_meta_oauth_callback,
    disconnect_meta_connection,
    fetch_and_sync_form_questions,
    generate_meta_oauth_connect_url,
    retry_failed_lead_event,
    select_meta_forms,
    select_meta_pages,
    sync_meta_page_forms,
    sync_meta_pages,
    update_meta_form_configuration,
    verify_meta_oauth_state,
)


class MetaOAuthConnectURLView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin, IsEmailVerified]

    @extend_schema(
        operation_id="leads_meta_connect_url",
        parameters=[
            OpenApiParameter(
                name="redirect_uri",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
            ),
        ],
        responses={200: MetaOAuthConnectResponseSerializer},
    )
    def get(self, request):
        query_serializer = MetaOAuthConnectQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        payload = generate_meta_oauth_connect_url(
            user=request.user,
            redirect_uri=query_serializer.validated_data.get("redirect_uri", ""),
        )
        return Response(payload, status=status.HTTP_200_OK)


class MetaOAuthCallbackView(APIView):
    permission_classes = [AllowAny]

    def _process_callback(self, request, raw_data) -> Response:
        serializer = MetaOAuthCallbackSerializer(data=raw_data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["code"]
        state = serializer.validated_data["state"]
        redirect_uri = serializer.validated_data.get("redirect_uri", "")

        state_info = verify_meta_oauth_state(state)
        active_tenant_id = get_current_tenant_id()
        if active_tenant_id is not None and str(active_tenant_id) != state_info["tenant_id"]:
            raise ApplicationError(
                detail="OAuth state does not belong to the active tenant.",
                code="oauth_tenant_mismatch",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        has_admin_membership = Membership.unscoped_objects.filter(
            tenant_id=state_info["tenant_id"],
            user_id=state_info["user_id"],
            is_active=True,
            role__in=[Membership.Role.OWNER, Membership.Role.ADMIN],
        ).exists()
        if not has_admin_membership:
            raise ApplicationError(
                detail="User associated with OAuth state does not have owner/admin permissions.",
                code="permission_denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        connection = complete_meta_oauth_callback(
            code=code,
            state=state,
            redirect_uri=redirect_uri,
        )
        return Response(
            MetaConnectionSerializer(connection).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="leads_meta_oauth_callback_get",
        parameters=[
            OpenApiParameter(name="code", type=str, location=OpenApiParameter.QUERY, required=True),
            OpenApiParameter(name="state", type=str, location=OpenApiParameter.QUERY, required=True),
            OpenApiParameter(name="redirect_uri", type=str, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={200: MetaConnectionSerializer},
    )
    def get(self, request):
        return self._process_callback(request, request.query_params)

    @extend_schema(
        operation_id="leads_meta_oauth_callback_post",
        request=MetaOAuthCallbackSerializer,
        responses={200: MetaConnectionSerializer},
    )
    def post(self, request):
        return self._process_callback(request, request.data)


class MetaConnectionStatusView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]

    @extend_schema(
        operation_id="leads_meta_connection_status",
        responses={200: MetaConnectionSerializer},
    )
    def get(self, request):
        conn = get_meta_connection()
        if conn is None:
            return Response(
                {
                    "id": None,
                    "meta_user_id": "",
                    "meta_user_name": "",
                    "status": MetaConnection.Status.DISCONNECTED,
                    "token_expires_at": None,
                    "is_token_configured": False,
                    "last_checked_at": None,
                    "last_error": "",
                    "created_at": None,
                    "updated_at": None,
                },
                status=status.HTTP_200_OK,
            )
        return Response(MetaConnectionSerializer(conn).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="leads_meta_connection_disconnect_from_status",
        responses={204: None},
    )
    def delete(self, request):
        disconnect_meta_connection()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MetaReconnectView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin, IsEmailVerified]

    @extend_schema(
        operation_id="leads_meta_reconnect",
        request=MetaOAuthConnectQuerySerializer,
        responses={200: MetaOAuthConnectResponseSerializer},
    )
    def post(self, request):
        if isinstance(request.data, dict) and request.data.get("code") and request.data.get("state"):
            serializer = MetaOAuthCallbackSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            conn = complete_meta_oauth_callback(
                code=serializer.validated_data["code"],
                state=serializer.validated_data["state"],
                redirect_uri=serializer.validated_data.get("redirect_uri", ""),
            )
            return Response(MetaConnectionSerializer(conn).data, status=status.HTTP_200_OK)

        serializer = MetaOAuthConnectQuerySerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        payload = generate_meta_oauth_connect_url(
            user=request.user,
            redirect_uri=serializer.validated_data.get("redirect_uri", ""),
        )
        return Response(payload, status=status.HTTP_200_OK)


class MetaDisconnectView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin, IsEmailVerified]

    @extend_schema(
        operation_id="leads_meta_disconnect",
        responses={204: None},
    )
    def delete(self, request):
        disconnect_meta_connection()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MetaPageViewSet(TenantScopedViewSet):
    serializer_class = MetaPageSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin, IsEmailVerified]
    queryset = MetaPage.unscoped_objects.none()
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return MetaPage.unscoped_objects.none()
        return get_meta_pages_queryset()

    def get_object(self) -> MetaPage:
        lookup_val = self.kwargs.get(self.lookup_field)
        page = get_meta_page_by_identifier(lookup_val)
        if page is None:
            raise Http404("MetaPage not found.")
        self.check_object_permissions(self.request, page)
        return page

    @extend_schema(
        operation_id="leads_meta_pages_list",
        parameters=[
            OpenApiParameter(
                name="sync",
                type=bool,
                location=OpenApiParameter.QUERY,
                required=False,
            ),
        ],
        responses={200: MetaPageSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        sync_param = str(request.query_params.get("sync", "true")).lower()
        should_sync = sync_param not in ("0", "false", "no")
        conn = get_meta_connection()
        if should_sync and conn is not None and conn.user_access_token and conn.status == MetaConnection.Status.CONNECTED:
            try:
                pages = sync_meta_pages(connection=conn)
                return Response(
                    MetaPageSerializer(pages, many=True).data,
                    status=status.HTTP_200_OK,
                )
            except ApplicationError:
                if not get_meta_pages_queryset().exists():
                    raise
        pages = list(get_meta_pages_queryset())
        return Response(
            MetaPageSerializer(pages, many=True).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="leads_meta_pages_select",
        request=MetaPageSelectSerializer,
        responses={200: MetaPageSerializer(many=True)},
    )
    @action(detail=False, methods=["post"], url_path="select")
    def select(self, request):
        serializer = MetaPageSelectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pages = select_meta_pages(
            page_identifiers=serializer.validated_data["page_ids"]
        )
        return Response(
            MetaPageSerializer(pages, many=True).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="leads_meta_page_forms_list",
        responses={200: MetaLeadFormSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="forms")
    def forms(self, request, pk=None):
        page = self.get_object()
        sync_param = str(request.query_params.get("sync", "true")).lower()
        should_sync = sync_param not in ("0", "false", "no")
        if should_sync and page.page_access_token:
            try:
                forms = sync_meta_page_forms(page=page)
                return Response(
                    MetaLeadFormSerializer(forms, many=True).data,
                    status=status.HTTP_200_OK,
                )
            except ApplicationError:
                if not get_meta_forms_queryset(page=page).exists():
                    raise
        forms = list(get_meta_forms_queryset(page=page))
        return Response(
            MetaLeadFormSerializer(forms, many=True).data,
            status=status.HTTP_200_OK,
        )


class MetaLeadFormViewSet(TenantScopedViewSet):
    serializer_class = MetaLeadFormSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin, IsEmailVerified]
    queryset = MetaLeadForm.unscoped_objects.none()
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return MetaLeadForm.unscoped_objects.none()
        page_param = self.request.query_params.get("page") or self.request.query_params.get("page_id")
        return get_meta_forms_queryset(page_identifier=page_param)

    def get_object(self) -> MetaLeadForm:
        lookup_val = self.kwargs.get(self.lookup_field)
        form = get_meta_form_by_identifier(lookup_val)
        if form is None:
            raise Http404("MetaLeadForm not found.")
        self.check_object_permissions(self.request, form)
        return form

    @extend_schema(
        operation_id="leads_meta_forms_list",
        parameters=[
            OpenApiParameter(
                name="page_id",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
            ),
            OpenApiParameter(
                name="sync",
                type=bool,
                location=OpenApiParameter.QUERY,
                required=False,
            ),
        ],
        responses={200: MetaLeadFormSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        page_param = request.query_params.get("page") or request.query_params.get("page_id")
        sync_param = str(request.query_params.get("sync", "true")).lower()
        should_sync = sync_param not in ("0", "false", "no")

        if page_param:
            page = get_meta_page_by_identifier(page_param)
            if page is None:
                raise Http404("MetaPage not found.")
            if should_sync and page.page_access_token:
                try:
                    forms = sync_meta_page_forms(page=page)
                    return Response(
                        MetaLeadFormSerializer(forms, many=True).data,
                        status=status.HTTP_200_OK,
                    )
                except ApplicationError:
                    if not get_meta_forms_queryset(page=page).exists():
                        raise
            forms = list(get_meta_forms_queryset(page=page))
            return Response(
                MetaLeadFormSerializer(forms, many=True).data,
                status=status.HTTP_200_OK,
            )

        forms = list(self.get_queryset())
        return Response(
            MetaLeadFormSerializer(forms, many=True).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="leads_meta_form_partial_update",
        request=MetaFormUpdateSerializer,
        responses={200: MetaLeadFormSerializer},
    )
    def partial_update(self, request, *args, **kwargs):
        form = self.get_object()
        serializer = MetaFormUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        tags_input = None
        if "default_tag_ids" in data:
            tags_input = data["default_tag_ids"]
        elif "default_tags" in data:
            tags_input = data["default_tags"]

        updated = update_meta_form_configuration(
            form=form,
            field_mapping=data.get("field_mapping"),
            default_tags=tags_input,
            selected=data.get("selected"),
            platform=data.get("platform"),
        )
        return Response(
            MetaLeadFormSerializer(updated).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="leads_meta_forms_select",
        request=MetaFormSelectSerializer,
        responses={200: MetaLeadFormSerializer(many=True)},
    )
    @action(detail=False, methods=["post"], url_path="select")
    def select(self, request):
        serializer = MetaFormSelectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        page_id_val = serializer.validated_data.get("page_id", "")
        page = get_meta_page_by_identifier(page_id_val) if page_id_val else None
        if page_id_val and page is None:
            raise Http404("MetaPage not found.")
        forms = select_meta_forms(
            form_identifiers=serializer.validated_data["form_ids"],
            page=page,
        )
        return Response(
            MetaLeadFormSerializer(forms, many=True).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="leads_meta_form_questions",
        responses={200: MetaFormQuestionsResponseSerializer},
    )
    @action(detail=True, methods=["get"], url_path="questions")
    def questions(self, request, pk=None):
        form = self.get_object()
        result = fetch_and_sync_form_questions(form=form)
        return Response(result, status=status.HTTP_200_OK)


class MetaLeadEventViewSet(TenantScopedViewSet):
    serializer_class = MetaLeadEventSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    queryset = MetaLeadEvent.unscoped_objects.none()
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return MetaLeadEvent.unscoped_objects.none()
        status_filter = self.request.query_params.get("status")
        return get_meta_lead_events_queryset(status_filter=status_filter)

    @extend_schema(
        operation_id="leads_meta_events_list",
        parameters=[
            OpenApiParameter(
                name="status",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=[choice[0] for choice in MetaLeadEvent.Status.choices],
            ),
        ],
        responses={200: MetaLeadEventSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        operation_id="leads_meta_event_retry",
        request=None,
        responses={200: MetaLeadEventSerializer},
    )
    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, pk=None):
        event = self.get_object()
        updated = retry_failed_lead_event(lead_event=event)
        return Response(
            MetaLeadEventSerializer(updated).data,
            status=status.HTTP_200_OK,
        )
