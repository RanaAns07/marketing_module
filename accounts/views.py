from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView
from accounts.models import Membership, TenantAPIKey, TenantInvitation
from accounts.permissions import (
    IsEmailVerified,
    IsOwnerOrAdmin,
    IsOwnerOrAdminOrReadOnly,
    IsTenantMember,
)
from accounts.selectors import (
    get_team_member_by_id,
    get_team_members_queryset,
    get_tenant_api_keys_queryset,
    get_tenant_invitation_by_id,
    get_tenant_invitations_queryset,
    get_user_memberships,
)
from accounts.serializers import (
    AcceptInvitationSerializer,
    AddTeamMemberSerializer,
    AvailabilityToggleSerializer,
    ChangeEmailRequestSerializer,
    CreateTenantAPIKeySerializer,
    CreateTenantInvitationSerializer,
    DeactivateTenantSerializer,
    ForgotPasswordSerializer,
    LoginSerializer,
    LogoutSerializer,
    MembershipSummarySerializer,
    OnboardingStatusSerializer,
    PasswordChangeSerializer,
    RegisterTenantOwnerSerializer,
    ResendVerificationSerializer,
    ResetPasswordSerializer,
    RoleChangeSerializer,
    RotateTenantAPIKeySerializer,
    SwitchTenantSerializer,
    TeamMemberSerializer,
    TenantAPIKeySerializer,
    TenantInvitationSerializer,
    TenantSummarySerializer,
    TransferOwnershipSerializer,
    UpdateTeamMemberSerializer,
    UserSerializer,
    UserTenantMembershipSerializer,
    VerifyChangeEmailSerializer,
    VerifyEmailSerializer,
)
from accounts.services import (
    accept_tenant_invitation,
    activate_team_member,
    add_team_member,
    change_team_member_role,
    change_user_password,
    create_tenant_api_key,
    create_tenant_invitation,
    deactivate_team_member,
    deactivate_tenant,
    get_tenant_onboarding_status,
    get_user_tenants,
    leave_tenant,
    login_user,
    logout_all_user_sessions,
    logout_user,
    reactivate_tenant,
    register_tenant_and_owner,
    request_change_email,
    request_password_reset,
    resend_tenant_invitation,
    resend_verification_email,
    reset_password_with_token,
    revoke_tenant_api_key,
    revoke_tenant_invitation,
    rotate_tenant_api_key,
    set_member_availability,
    switch_user_tenant,
    transfer_tenant_ownership,
    update_team_member,
    update_tenant_settings,
    update_user_profile,
    verify_change_email,
    verify_email_token,
)
from accounts.throttling import (
    AcceptInvitationRateThrottle,
    ForgotPasswordRateThrottle,
    LoginRateThrottle,
    RegisterRateThrottle,
    ResendVerificationRateThrottle,
    get_client_ip,
)
from core.exceptions import ApplicationError


class RegisterView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [RegisterRateThrottle]
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
    throttle_classes = [LoginRateThrottle]
    serializer_class = LoginSerializer

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = login_user(
            email=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
            client_ip=get_client_ip(request),
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


class VerifyEmailView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = VerifyEmailSerializer

    def get(self, request):
        token = request.query_params.get("token")
        user = verify_email_token(token=token or "")
        return Response(
            {
                "detail": "Email verified successfully.",
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = verify_email_token(token=serializer.validated_data["token"])
        return Response(
            {
                "detail": "Email verified successfully.",
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


class ResendVerificationView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ResendVerificationRateThrottle]
    serializer_class = ResendVerificationSerializer

    def post(self, request):
        serializer = ResendVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resend_verification_email(email=serializer.validated_data["email"])
        return Response(
            {"detail": "If the account exists and is unverified, a verification email has been sent."},
            status=status.HTTP_200_OK,
        )


class ForgotPasswordView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ForgotPasswordRateThrottle]
    serializer_class = ForgotPasswordSerializer

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request_password_reset(email=serializer.validated_data["email"])
        return Response(
            {"detail": "If an account with that email exists, a password reset link has been sent."},
            status=status.HTTP_200_OK,
        )


class ResetPasswordView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = ResetPasswordSerializer

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reset_password_with_token(
            token=serializer.validated_data["token"],
            new_password=serializer.validated_data["new_password"],
        )
        return Response(
            {"detail": "Password has been reset successfully. Please log in with your new password."},
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = LogoutSerializer

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        logout_user(refresh_token=serializer.validated_data["refresh"])
        return Response({"detail": "Successfully logged out."}, status=status.HTTP_200_OK)


class LogoutAllView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: dict})
    def post(self, request):
        logout_all_user_sessions(user=request.user)
        return Response({"detail": "All sessions logged out successfully."}, status=status.HTTP_200_OK)


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


class ChangeEmailRequestView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChangeEmailRequestSerializer

    def post(self, request):
        serializer = ChangeEmailRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request_change_email(
            user=request.user,
            new_email=serializer.validated_data["new_email"],
            password=serializer.validated_data["password"],
        )
        return Response(
            {"detail": "A verification link has been sent to your new email address."},
            status=status.HTTP_200_OK,
        )


class VerifyChangeEmailView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = VerifyChangeEmailSerializer

    def get(self, request):
        token = request.query_params.get("token")
        user = verify_change_email(token=token or "")
        return Response(
            {
                "detail": "Email address updated successfully.",
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        serializer = VerifyChangeEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = verify_change_email(token=serializer.validated_data["token"])
        return Response(
            {
                "detail": "Email address updated successfully.",
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


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


class UserTenantsListView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserTenantMembershipSerializer

    def get(self, request):
        tenants_data = get_user_tenants(user=request.user)
        return Response(UserTenantMembershipSerializer(tenants_data, many=True).data, status=status.HTTP_200_OK)


class SwitchTenantView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = SwitchTenantSerializer

    def post(self, request):
        serializer = SwitchTenantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = switch_user_tenant(
            user=request.user,
            target_tenant_id=serializer.validated_data["tenant_id"],
        )
        return Response(
            {
                "detail": "Active tenant switched successfully.",
                "tenant": TenantSummarySerializer(result["tenant"]).data,
                "membership": MembershipSummarySerializer(result["membership"]).data,
                "tokens": result["tokens"],
            },
            status=status.HTTP_200_OK,
        )


class LeaveTenantView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    @extend_schema(request=None, responses={200: dict})
    def post(self, request):
        leave_tenant(actor_membership=request.membership)
        return Response({"detail": "Successfully left the tenant."}, status=status.HTTP_200_OK)


class TransferOwnershipView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]
    serializer_class = TransferOwnershipSerializer

    def post(self, request):
        serializer = TransferOwnershipSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        transfer_tenant_ownership(
            actor_membership=request.membership,
            new_owner_membership_id=serializer.validated_data["new_owner_membership_id"],
            password=serializer.validated_data["password"],
        )
        return Response({"detail": "Tenant ownership transferred successfully."}, status=status.HTTP_200_OK)


class DeactivateTenantView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]
    serializer_class = DeactivateTenantSerializer

    def post(self, request):
        serializer = DeactivateTenantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant = deactivate_tenant(
            actor_membership=request.membership,
            password=serializer.validated_data["password"],
            reason=serializer.validated_data.get("reason", ""),
            grace_period_days=serializer.validated_data.get("grace_period_days", 30),
        )
        return Response(
            {
                "detail": "Tenant deactivated. It will enter read-only mode during the grace period.",
                "tenant": TenantSummarySerializer(tenant).data,
            },
            status=status.HTTP_200_OK,
        )


class ReactivateTenantView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]
    allow_during_deactivation = True

    @extend_schema(request=None, responses={200: TenantSummarySerializer})
    def post(self, request):
        tenant = reactivate_tenant(actor_membership=request.membership)
        return Response(
            {
                "detail": "Tenant reactivated successfully.",
                "tenant": TenantSummarySerializer(tenant).data,
            },
            status=status.HTTP_200_OK,
        )


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


class TenantOnboardingView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]
    serializer_class = OnboardingStatusSerializer

    def get(self, request):
        status_data = get_tenant_onboarding_status(
            tenant=request.tenant,
            user=request.user,
        )
        return Response(status_data, status=status.HTTP_200_OK)


class TeamMemberViewSet(viewsets.ModelViewSet):
    serializer_class = TeamMemberSerializer
    permission_classes = [IsAuthenticated, IsTenantMember, IsEmailVerified]
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
        if request.membership.role != Membership.Role.OWNER:
            raise ApplicationError(
                message="Only tenant owners can add members directly. Admins must invite members.",
                code="invitation_required",
            )
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


class TenantInvitationViewSet(viewsets.ModelViewSet):
    serializer_class = TenantInvitationSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin, IsEmailVerified]
    lookup_value_regex = "[0-9a-f-]{36}"
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TenantInvitation.unscoped_objects.none()
        return get_tenant_invitations_queryset()

    def get_object(self):
        invitation = get_tenant_invitation_by_id(self.kwargs["pk"])
        if invitation is None:
            raise NotFound("Invitation not found.")
        return invitation

    def create(self, request, *args, **kwargs):
        serializer = CreateTenantInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        invitation = create_tenant_invitation(
            actor_membership=request.membership,
            email=data["email"],
            role=data["role"],
        )
        return Response(TenantInvitationSerializer(invitation).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        target = self.get_object()
        revoked = revoke_tenant_invitation(
            actor_membership=request.membership,
            invitation_id=target.id,
        )
        return Response(TenantInvitationSerializer(revoked).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="resend")
    def resend(self, request, pk=None):
        target = self.get_object()
        invitation = resend_tenant_invitation(
            actor_membership=request.membership,
            invitation_id=target.id,
        )
        return Response(TenantInvitationSerializer(invitation).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="revoke")
    def revoke(self, request, pk=None):
        target = self.get_object()
        revoked = revoke_tenant_invitation(
            actor_membership=request.membership,
            invitation_id=target.id,
        )
        return Response(TenantInvitationSerializer(revoked).data, status=status.HTTP_200_OK)


class AcceptInvitationView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AcceptInvitationRateThrottle]
    serializer_class = AcceptInvitationSerializer

    def post(self, request):
        serializer = AcceptInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = accept_tenant_invitation(
            token=data["token"],
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            phone=data.get("phone", ""),
            password=data.get("password"),
        )
        return Response(
            {
                "detail": "Invitation accepted successfully.",
                "user": UserSerializer(result["user"]).data,
                "membership": MembershipSummarySerializer(result["membership"]).data,
                "tokens": result["tokens"],
            },
            status=status.HTTP_200_OK,
        )


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
