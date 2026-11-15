from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.permissions import IsOwnerOrAdmin, IsTenantMember
from accounts.services import update_tenant_settings
from contacts.selectors import get_contact_by_id
from core.views import TenantScopedViewSet
from inbox.models import AssignmentRule, Conversation
from inbox.selectors import (
    can_member_access_conversation,
    filter_conversations_queryset,
    get_assignment_rule_by_id,
    get_assignment_rules_queryset,
    get_conversation_by_id,
    get_conversation_notes_queryset,
    get_conversation_thread,
    get_inbox_unread_counts,
    get_team_workload_counts,
)
from inbox.serializers import (
    AssignmentRuleSerializer,
    ConversationAssignSerializer,
    ConversationContactStatusSerializer,
    ConversationContactTagsSerializer,
    ConversationCreateSerializer,
    ConversationNoteCreateSerializer,
    ConversationNoteSerializer,
    ConversationReplySerializer,
    ConversationSerializer,
    ConversationStatusUpdateSerializer,
    InboxUnreadCountsSerializer,
    TeamWorkloadSerializer,
    TenantAssignmentSettingsSerializer,
)
from inbox.services import (
    add_conversation_note,
    assign_conversation,
    claim_conversation,
    create_assignment_rule,
    delete_assignment_rule,
    get_or_create_conversation,
    mark_conversation_read,
    pause_conversation_automations,
    reply_to_conversation,
    resume_conversation_automations,
    set_conversation_status,
    update_assignment_rule,
    update_conversation_contact_status,
    update_conversation_contact_tags,
)
from messaging.serializers import MessageSerializer


class ConversationViewSet(TenantScopedViewSet):
    serializer_class = ConversationSerializer
    permission_classes = [IsAuthenticated, IsTenantMember]
    lookup_value_regex = "[0-9a-f-]{36}"
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Conversation.unscoped_objects.none()
        return filter_conversations_queryset(
            membership=getattr(self.request, "membership", None),
            params=self.request.query_params,
        )

    def get_object(self) -> Conversation:
        membership = getattr(self.request, "membership", None)
        conv = get_conversation_by_id(self.kwargs["pk"], membership=membership)
        if conv is None:
            raw_conv = Conversation.objects.filter(pk=self.kwargs["pk"]).first()
            if raw_conv is not None and not can_member_access_conversation(raw_conv, membership):
                raise PermissionDenied("You do not have permission to access this conversation.")
            raise NotFound("Conversation not found.")
        return conv

    def create(self, request, *args, **kwargs):
        serializer = ConversationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contact = get_contact_by_id(
            serializer.validated_data["contact"].id,
            membership=getattr(request, "membership", None),
        )
        if contact is None:
            raise NotFound("Contact not found.")
        conversation, created = get_or_create_conversation(contact=contact)
        conv_detail = get_conversation_by_id(
            conversation.id,
            membership=getattr(request, "membership", None),
        ) or conversation
        return Response(
            ConversationSerializer(conv_detail).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="thread")
    def thread(self, request, pk=None):
        conversation = self.get_object()
        cursor = request.query_params.get("cursor")
        raw_limit = request.query_params.get("page_size") or request.query_params.get("limit") or 50
        try:
            limit = int(raw_limit)
        except (ValueError, TypeError):
            limit = 50
        payload = get_conversation_thread(
            conversation=conversation,
            cursor=cursor,
            limit=limit,
        )
        return Response(payload, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="reply")
    def reply(self, request, pk=None):
        conversation = self.get_object()
        serializer = ConversationReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        updated_conv, message = reply_to_conversation(
            conversation=conversation,
            actor_membership=request.membership,
            channel=data["channel"],
            body=data.get("body", ""),
            subject=data.get("subject", ""),
            template=data.get("template"),
            set_status=data.get("status"),
            idempotency_key=data.get("idempotency_key") or None,
            extra_context=data.get("extra_context") or {},
        )
        return Response(
            {
                "conversation": ConversationSerializer(updated_conv).data,
                "message": MessageSerializer(message).data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get", "post"], url_path="notes")
    def notes(self, request, pk=None):
        conversation = self.get_object()
        if request.method == "GET":
            qs = get_conversation_notes_queryset(conversation)
            page = self.paginate_queryset(qs)
            if page is not None:
                return self.get_paginated_response(
                    ConversationNoteSerializer(page, many=True).data
                )
            return Response(ConversationNoteSerializer(qs, many=True).data)

        serializer = ConversationNoteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = add_conversation_note(
            conversation=conversation,
            body=serializer.validated_data["body"],
            author=request.user,
        )
        return Response(
            ConversationNoteSerializer(note).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post", "patch"], url_path="status")
    def set_status(self, request, pk=None):
        conversation = self.get_object()
        serializer = ConversationStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = set_conversation_status(
            conversation=conversation,
            new_status=serializer.validated_data["status"],
            actor=request.user,
        )
        return Response(ConversationSerializer(updated).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="assign")
    def assign(self, request, pk=None):
        conversation = self.get_object()
        serializer = ConversationAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = assign_conversation(
            conversation=conversation,
            assignee=serializer.validated_data["resolved_membership"],
            actor=request.user,
        )
        return Response(ConversationSerializer(updated).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="claim")
    def claim(self, request, pk=None):
        conversation = self.get_object()
        updated = claim_conversation(
            conversation=conversation,
            actor_membership=request.membership,
        )
        return Response(ConversationSerializer(updated).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        conversation = self.get_object()
        updated = mark_conversation_read(conversation=conversation)
        return Response(ConversationSerializer(updated).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="contact-status")
    def contact_status(self, request, pk=None):
        conversation = self.get_object()
        serializer = ConversationContactStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = update_conversation_contact_status(
            conversation=conversation,
            lead_status=serializer.validated_data["lead_status"],
            actor=request.user,
        )
        return Response(ConversationSerializer(updated).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="contact-tags")
    def contact_tags(self, request, pk=None):
        conversation = self.get_object()
        serializer = ConversationContactTagsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        updated = update_conversation_contact_tags(
            conversation=conversation,
            add_tags=data.get("add_tags"),
            remove_tags=data.get("remove_tags"),
            set_tags=data.get("tags"),
            actor=request.user,
        )
        return Response(ConversationSerializer(updated).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="pause-automations")
    def pause_automations(self, request, pk=None):
        conversation = self.get_object()
        paused = pause_conversation_automations(
            conversation=conversation,
            actor=request.user,
        )
        refreshed = get_conversation_by_id(
            conversation.id,
            membership=getattr(request, "membership", None),
        ) or conversation
        return Response(
            {
                "paused_count": len(paused),
                "conversation": ConversationSerializer(refreshed).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="resume-automations")
    def resume_automations(self, request, pk=None):
        conversation = self.get_object()
        resumed = resume_conversation_automations(
            conversation=conversation,
            actor=request.user,
        )
        refreshed = get_conversation_by_id(
            conversation.id,
            membership=getattr(request, "membership", None),
        ) or conversation
        return Response(
            {
                "resumed_count": len(resumed),
                "conversation": ConversationSerializer(refreshed).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="unread-counts")
    def unread_counts(self, request):
        counts = get_inbox_unread_counts(membership=getattr(request, "membership", None))
        return Response(counts, status=status.HTTP_200_OK)


class AssignmentRuleViewSet(TenantScopedViewSet):
    serializer_class = AssignmentRuleSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    lookup_value_regex = "[0-9a-f-]{36}"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return AssignmentRule.unscoped_objects.none()
        return get_assignment_rules_queryset()

    def get_object(self) -> AssignmentRule:
        rule = get_assignment_rule_by_id(self.kwargs["pk"])
        if rule is None:
            raise NotFound("Assignment rule not found.")
        return rule

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        rule = create_assignment_rule(
            priority=data.get("priority", 1),
            assignee=data["assignee"],
            name=data.get("name", ""),
            match_page=data.get("match_page", ""),
            match_form=data.get("match_form", ""),
            match_tag=data.get("match_tag"),
            match_tag_name=data.get("match_tag_name", ""),
            is_active=data.get("is_active", True),
        )
        return Response(
            AssignmentRuleSerializer(rule).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        rule = self.get_object()
        serializer = self.get_serializer(rule, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        updated = update_assignment_rule(
            rule=rule,
            **serializer.validated_data,
        )
        return Response(AssignmentRuleSerializer(updated).data, status=status.HTTP_200_OK)

    def destroy(self, request, *args, **kwargs):
        rule = self.get_object()
        delete_assignment_rule(rule=rule)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TenantAssignmentSettingsView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    serializer_class = TenantAssignmentSettingsSerializer

    def get(self, request):
        return Response(TenantAssignmentSettingsSerializer(request.tenant).data)

    def patch(self, request):
        serializer = TenantAssignmentSettingsSerializer(
            request.tenant,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        tenant = update_tenant_settings(
            tenant=request.tenant,
            **serializer.validated_data,
        )
        return Response(TenantAssignmentSettingsSerializer(tenant).data)

    def put(self, request):
        return self.patch(request)


class InboxUnreadCountsView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]
    serializer_class = InboxUnreadCountsSerializer

    def get(self, request):
        counts = get_inbox_unread_counts(membership=getattr(request, "membership", None))
        return Response(counts, status=status.HTTP_200_OK)


class TeamWorkloadView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]
    serializer_class = TeamWorkloadSerializer

    def get(self, request):
        data = get_team_workload_counts(membership=getattr(request, "membership", None))
        return Response(data, status=status.HTTP_200_OK)
