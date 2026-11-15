from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView
from accounts.models import Membership, TenantAPIKey
from accounts.permissions import IsOwnerOrAdmin, IsOwnerOrAdminOrReadOnly, IsTenantMember
from accounts.selectors import (
    get_team_member_by_id,
    get_team_members_queryset,
    get_tenant_api_keys_queryset,
    get_user_memberships,
)
from accounts.serializers import (
    AddTeamMemberSerializer,
    AvailabilityToggleSerializer,
    CreateTenantAPIKeySerializer,
    LoginSerializer,
    MembershipSummarySerializer,
    PasswordChangeSerializer,
    RegisterTenantOwnerSerializer,
    RoleChangeSerializer,
    RotateTenantAPIKeySerializer,
    TeamMemberSerializer,
    TenantAPIKeySerializer,
    TenantSummarySerializer,
    UpdateTeamMemberSerializer,
    UserSerializer,
)
from accounts.services import (
    activate_team_member,
    add_team_member,
    change_team_member_role,
    change_user_password,
    create_tenant_api_key,
    deactivate_team_member,
    login_user,
    register_tenant_and_owner,
    revoke_tenant_api_key,
    rotate_tenant_api_key,
    set_member_availability,
    update_team_member,
    update_tenant_settings,
    update_user_profile,
)


class RegisterView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = RegisterTenantOwnerSerializer

    def post(self, request):
        serializer = RegisterTenantOwnerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = register_tenant_and_owner(
            tenant_name=data["tenant_name"],
            email=data["email"],
            password=data["password"],
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            phone=data.get("phone", ""),
            timezone_name=data.get("timezone", "UTC"),
        )
        return Response(
            {
                "user": UserSerializer(result["user"]).data,
                "tenant": TenantSummarySerializer(result["tenant"]).data,
                "membership": MembershipSummarySerializer(result["membership"]).data,
                "tokens": result["tokens"],
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = LoginSerializer

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = login_user(
            email=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )
        user = result["user"]
        memberships = get_user_memberships(user)
        return Response(
            {
                "user": UserSerializer(user).data,
                "memberships": MembershipSummarySerializer(memberships, many=True).data,
                "tokens": result["tokens"],
            },
            status=status.HTTP_200_OK,
        )


class RefreshTokenView(TokenRefreshView):
    authentication_classes = []
    permission_classes = [AllowAny]


class CurrentUserView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserSerializer

    def get(self, request):
        memberships = get_user_memberships(request.user)
        active_membership = getattr(request, "membership", None)
        return Response(
            {
                "user": UserSerializer(request.user).data,
                "memberships": MembershipSummarySerializer(memberships, many=True).data,
                "active_membership": (
                    MembershipSummarySerializer(active_membership).data
                    if active_membership is not None
                    else None
                ),
            }
        )

    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = update_user_profile(
            user=request.user,
            first_name=serializer.validated_data.get("first_name"),
            last_name=serializer.validated_data.get("last_name"),
            phone=serializer.validated_data.get("phone"),
            email_notifications_enabled=serializer.validated_data.get("email_notifications_enabled"),
        )
        return Response(UserSerializer(user).data)


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PasswordChangeSerializer

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_user_password(
            user=request.user,
            old_password=serializer.validated_data["old_password"],
            new_password=serializer.validated_data["new_password"],
        )
        return Response({"detail": "Password updated successfully."}, status=status.HTTP_200_OK)


class TenantSettingsView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerOrAdminOrReadOnly]
    serializer_class = TenantSummarySerializer

    def get(self, request):
        return Response(TenantSummarySerializer(request.tenant).data)

    def patch(self, request):
        serializer = TenantSummarySerializer(request.tenant, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        tenant = update_tenant_settings(tenant=request.tenant, **serializer.validated_data)
        return Response(TenantSummarySerializer(tenant).data)


class TeamMemberViewSet(viewsets.ModelViewSet):
    serializer_class = TeamMemberSerializer
    permission_classes = [IsAuthenticated, IsTenantMember]
    lookup_value_regex = "[0-9a-f-]{36}"
    filterset_fields = ["role", "is_active", "is_available"]
    search_fields = ["user__email", "user__first_name", "user__last_name", "user__phone"]
    ordering_fields = ["created_at", "last_assigned_at", "role"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Membership.unscoped_objects.none()
        return get_team_members_queryset()

    def get_object(self):
        member = get_team_member_by_id(self.kwargs["pk"])
        if member is None:
            raise NotFound("Team member not found.")
        return member

    def create(self, request, *args, **kwargs):
        serializer = AddTeamMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        membership = add_team_member(
            actor_membership=request.membership,
            email=data["email"],
            role=data["role"],
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            phone=data.get("phone", ""),
            password=data.get("password"),
            is_available=data.get("is_available", True),
            email_notifications_enabled=data.get("email_notifications_enabled", True),
        )
        return Response(TeamMemberSerializer(membership).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        target = self.get_object()
        serializer = UpdateTeamMemberSerializer(data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        updated = update_team_member(
            actor_membership=request.membership,
            target_membership=target,
            **serializer.validated_data,
        )
        return Response(TeamMemberSerializer(updated).data)

    def destroy(self, request, *args, **kwargs):
        target = self.get_object()
        deactivated = deactivate_team_member(
            actor_membership=request.membership,
            target_membership=target,
        )
        return Response(TeamMemberSerializer(deactivated).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="role")
    def change_role(self, request, pk=None):
        target = self.get_object()
        serializer = RoleChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = change_team_member_role(
            actor_membership=request.membership,
            target_membership=target,
            new_role=serializer.validated_data["role"],
        )
        return Response(TeamMemberSerializer(updated).data)

    @action(detail=True, methods=["post"], url_path="deactivate")
    def deactivate(self, request, pk=None):
        target = self.get_object()
        updated = deactivate_team_member(
            actor_membership=request.membership,
            target_membership=target,
        )
        return Response(TeamMemberSerializer(updated).data)

    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, pk=None):
        target = self.get_object()
        updated = activate_team_member(
            actor_membership=request.membership,
            target_membership=target,
        )
        return Response(TeamMemberSerializer(updated).data)

    @action(detail=True, methods=["post"], url_path="availability")
    def availability(self, request, pk=None):
        target = self.get_object()
        serializer = AvailabilityToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = set_member_availability(
            actor_membership=request.membership,
            target_membership=target,
            is_available=serializer.validated_data["is_available"],
        )
        return Response(TeamMemberSerializer(updated).data)


class TenantAPIKeyViewSet(viewsets.ModelViewSet):
    serializer_class = TenantAPIKeySerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    lookup_value_regex = "[0-9a-f-]{36}"
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TenantAPIKey.unscoped_objects.none()
        return get_tenant_api_keys_queryset()

    def create(self, request, *args, **kwargs):
        serializer = CreateTenantAPIKeySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        api_key, raw_secret = create_tenant_api_key(
            name=serializer.validated_data["name"],
            scopes=serializer.validated_data.get("scopes"),
        )
        data = TenantAPIKeySerializer(api_key).data
        data["secret"] = raw_secret
        return Response(data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        target = self.get_object()
        revoked = revoke_tenant_api_key(api_key=target)
        return Response(TenantAPIKeySerializer(revoked).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="revoke")
    def revoke(self, request, pk=None):
        target = self.get_object()
        revoked = revoke_tenant_api_key(api_key=target)
        return Response(TenantAPIKeySerializer(revoked).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="rotate")
    def rotate(self, request, pk=None):
        target = self.get_object()
        serializer = RotateTenantAPIKeySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vdata = serializer.validated_data
        grace_seconds = int(vdata.get("grace_period_seconds") or 0)
        grace_minutes = int(vdata.get("grace_period_minutes") or 0)
        if grace_minutes > 0 and grace_seconds == 0:
            grace_seconds = grace_minutes * 60

        new_key, raw_secret, old_key = rotate_tenant_api_key(
            api_key=target,
            grace_period_seconds=grace_seconds,
            name=vdata.get("name"),
            scopes=vdata.get("scopes"),
        )
        data = TenantAPIKeySerializer(new_key).data
        data["secret"] = raw_secret
        data["replaced_key_id"] = str(old_key.id)
        data["old_key_revoked_at"] = old_key.revoked_at.isoformat() if old_key.revoked_at else None
        return Response(data, status=status.HTTP_201_CREATED)
