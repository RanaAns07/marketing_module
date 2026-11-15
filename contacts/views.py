from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from accounts.permissions import (
    CanAccessContactObject,
    IsOwnerOrAdminOrReadOnly,
    IsTenantMember,
)
from contacts.models import (
    Appointment,
    Contact,
    CustomFieldDefinition,
    LeadStatus,
    Tag,
    Task,
)
from contacts.selectors import (
    filter_contacts_queryset,
    get_appointments_queryset,
    get_contact_by_id,
    get_contact_timeline_queryset,
    get_contacts_by_ids,
    get_custom_field_definitions_queryset,
    get_lead_statuses_queryset,
    get_tags_queryset,
    get_tasks_queryset,
)
from contacts.serializers import (
    ActivitySerializer,
    AddNoteSerializer,
    AppointmentRescheduleSerializer,
    AppointmentSerializer,
    AssignContactSerializer,
    BulkAssignActionSerializer,
    BulkTagActionSerializer,
    ChangeLeadStatusSerializer,
    ContactSerializer,
    CustomFieldDefinitionSerializer,
    LeadStatusSerializer,
    SubscriptionUpdateSerializer,
    TagListActionSerializer,
    TagSerializer,
    TaskSerializer,
)
from contacts.services import (
    ContactService,
    book_appointment,
    cancel_appointment,
    complete_appointment,
    complete_task,
    create_custom_field_definition,
    create_lead_status,
    create_tag,
    create_task,
    delete_lead_status,
    reschedule_appointment,
    update_appointment,
    update_custom_field_definition,
    update_lead_status,
    update_tag,
    update_task,
)
from core.pagination import KeysetCursorPagination
from core.views import TenantScopedViewSet


class ContactViewSet(TenantScopedViewSet):
    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated, CanAccessContactObject]
    lookup_value_regex = "[0-9a-f-]{36}"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Contact.unscoped_objects.none()
        return filter_contacts_queryset(
            membership=getattr(self.request, "membership", None),
            params=self.request.query_params,
        )

    def filter_queryset(self, queryset):
        return queryset

    def get_object(self):
        contact = get_contact_by_id(
            self.kwargs["pk"],
            membership=getattr(self.request, "membership", None),
        )
        if contact is None:
            raise NotFound("Contact not found.")
        self.check_object_permissions(self.request, contact)
        return contact

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        contact = ContactService.create_contact(
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            full_name=data.get("full_name", ""),
            email=data.get("email"),
            phone=data.get("phone"),
            city=data.get("city", ""),
            country=data.get("country", ""),
            timezone_name=data.get("timezone", "UTC"),
            lead_status=data.get("lead_status"),
            assigned_to=data.get("assigned_to"),
            source_channel=data.get("source_channel", "manual"),
            source_page=data.get("source_page", ""),
            source_form=data.get("source_form", ""),
            campaign=data.get("campaign", ""),
            ad_set=data.get("ad_set", ""),
            ad=data.get("ad", ""),
            external_id=data.get("external_id", ""),
            email_subscription=data.get("email_subscription", Contact.SubscriptionStatus.SUBSCRIBED),
            sms_subscription=data.get("sms_subscription", Contact.SubscriptionStatus.SUBSCRIBED),
            whatsapp_subscription=data.get("whatsapp_subscription", Contact.SubscriptionStatus.SUBSCRIBED),
            custom_fields=data.get("custom_fields"),
            tags=data.get("tag_ids"),
            actor=request.user,
        )
        refreshed = get_contact_by_id(contact.id)
        return Response(
            ContactSerializer(refreshed or contact).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        contact = self.get_object()
        serializer = self.get_serializer(contact, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        changes = dict(serializer.validated_data)
        if "tag_ids" in changes:
            changes["tags"] = changes.pop("tag_ids")
        updated = ContactService.update_contact(
            contact=contact,
            actor=request.user,
            **changes,
        )
        return Response(ContactSerializer(updated).data)

    @action(detail=True, methods=["get"], url_path="timeline")
    def timeline(self, request, pk=None):
        contact = self.get_object()
        qs = get_contact_timeline_queryset(contact)
        activity_type = request.query_params.get("activity_type")
        if activity_type:
            qs = qs.filter(activity_type=activity_type)
        paginator = KeysetCursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = ActivitySerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @action(detail=True, methods=["post"], url_path="notes")
    def add_note(self, request, pk=None):
        contact = self.get_object()
        serializer = AddNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        activity = ContactService.add_note(
            contact=contact,
            body=serializer.validated_data["body"],
            actor=request.user,
        )
        return Response(ActivitySerializer(activity).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="tags/add")
    def tags_add(self, request, pk=None):
        contact = self.get_object()
        serializer = TagListActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ContactService.add_tags(
            contact=contact,
            tags=serializer.validated_data["tags"],
            actor=request.user,
            create_missing=True,
        )
        refreshed = get_contact_by_id(contact.id, membership=request.membership)
        return Response(ContactSerializer(refreshed).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="tags/remove")
    def tags_remove(self, request, pk=None):
        contact = self.get_object()
        serializer = TagListActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ContactService.remove_tags(
            contact=contact,
            tags=serializer.validated_data["tags"],
            actor=request.user,
        )
        refreshed = get_contact_by_id(contact.id, membership=request.membership)
        return Response(ContactSerializer(refreshed).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="status")
    def change_status(self, request, pk=None):
        contact = self.get_object()
        serializer = ChangeLeadStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = ContactService.change_status(
            contact=contact,
            new_status=serializer.validated_data["lead_status"],
            actor=request.user,
        )
        refreshed = get_contact_by_id(updated.id, membership=request.membership)
        return Response(ContactSerializer(refreshed).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="assign")
    def assign(self, request, pk=None):
        contact = self.get_object()
        serializer = AssignContactSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = ContactService.assign(
            contact=contact,
            assignee=serializer.validated_data["assigned_to"],
            actor=request.user,
        )
        refreshed = get_contact_by_id(updated.id, membership=request.membership)
        return Response(ContactSerializer(refreshed or updated).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post", "patch"], url_path="subscriptions")
    def subscriptions(self, request, pk=None):
        contact = self.get_object()
        serializer = SubscriptionUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = ContactService.update_subscriptions(
            contact=contact,
            email_subscription=serializer.validated_data.get("email_subscription"),
            sms_subscription=serializer.validated_data.get("sms_subscription"),
            whatsapp_subscription=serializer.validated_data.get("whatsapp_subscription"),
            actor=request.user,
        )
        refreshed = get_contact_by_id(updated.id, membership=request.membership)
        return Response(ContactSerializer(refreshed).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get", "post"], url_path="appointments")
    def appointments(self, request, pk=None):
        contact = self.get_object()
        if request.method == "GET":
            qs = get_appointments_queryset(membership=request.membership, contact=contact)
            page = self.paginate_queryset(qs)
            if page is not None:
                return self.get_paginated_response(AppointmentSerializer(page, many=True).data)
            return Response(AppointmentSerializer(qs, many=True).data)

        serializer = AppointmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        appt = book_appointment(
            contact=contact,
            starts_at=data["starts_at"],
            external_ref=data.get("external_ref", ""),
            location=data.get("location", ""),
            notes=data.get("notes", ""),
            actor=request.user,
        )
        return Response(AppointmentSerializer(appt).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"], url_path="tasks")
    def tasks(self, request, pk=None):
        contact = self.get_object()
        if request.method == "GET":
            qs = get_tasks_queryset(membership=request.membership, contact=contact)
            page = self.paginate_queryset(qs)
            if page is not None:
                return self.get_paginated_response(TaskSerializer(page, many=True).data)
            return Response(TaskSerializer(qs, many=True).data)

        serializer = TaskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        task = create_task(
            title=data["title"],
            description=data.get("description", ""),
            contact=contact,
            assignee=data.get("assignee"),
            due_at=data.get("due_at"),
            created_by=request.user,
            from_workflow=data.get("from_workflow", False),
        )
        return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="bulk-tags/add")
    def bulk_tags_add(self, request):
        serializer = BulkTagActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contacts = get_contacts_by_ids(
            serializer.validated_data["contact_ids"],
            membership=request.membership,
        )
        updated_count = ContactService.bulk_add_tags(
            contacts=contacts,
            tags=serializer.validated_data["tags"],
            actor=request.user,
            create_missing=True,
        )
        return Response({"updated_count": updated_count}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bulk-tags/remove")
    def bulk_tags_remove(self, request):
        serializer = BulkTagActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contacts = get_contacts_by_ids(
            serializer.validated_data["contact_ids"],
            membership=request.membership,
        )
        updated_count = ContactService.bulk_remove_tags(
            contacts=contacts,
            tags=serializer.validated_data["tags"],
            actor=request.user,
        )
        return Response({"updated_count": updated_count}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bulk-assign")
    def bulk_assign(self, request):
        serializer = BulkAssignActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contacts = get_contacts_by_ids(
            serializer.validated_data["contact_ids"],
            membership=request.membership,
        )
        updated_count = ContactService.bulk_assign(
            contacts=contacts,
            assignee=serializer.validated_data["assigned_to"],
            actor=request.user,
        )
        return Response({"updated_count": updated_count}, status=status.HTTP_200_OK)


class TagViewSet(TenantScopedViewSet):
    serializer_class = TagSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdminOrReadOnly]
    lookup_value_regex = "[0-9a-f-]{36}"
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Tag.unscoped_objects.none()
        return get_tags_queryset()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tag = create_tag(
            name=serializer.validated_data["name"],
            color=serializer.validated_data.get("color", "#3B82F6"),
        )
        return Response(TagSerializer(tag).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        tag = self.get_object()
        serializer = self.get_serializer(tag, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        updated = update_tag(
            tag=tag,
            name=serializer.validated_data.get("name"),
            color=serializer.validated_data.get("color"),
        )
        return Response(TagSerializer(updated).data)


class CustomFieldDefinitionViewSet(TenantScopedViewSet):
    serializer_class = CustomFieldDefinitionSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdminOrReadOnly]
    lookup_value_regex = "[0-9a-f-]{36}"
    filterset_fields = ["field_type", "is_required"]
    search_fields = ["key", "label"]
    ordering_fields = ["label", "created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CustomFieldDefinition.unscoped_objects.none()
        return get_custom_field_definitions_queryset()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        definition = create_custom_field_definition(
            key=data["key"],
            label=data["label"],
            field_type=data.get("field_type", CustomFieldDefinition.FieldType.TEXT),
            options=data.get("options", []),
            is_required=data.get("is_required", False),
        )
        return Response(CustomFieldDefinitionSerializer(definition).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        definition = self.get_object()
        serializer = self.get_serializer(definition, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        updated = update_custom_field_definition(
            definition=definition,
            label=data.get("label"),
            field_type=data.get("field_type"),
            options=data.get("options"),
            is_required=data.get("is_required"),
        )
        return Response(CustomFieldDefinitionSerializer(updated).data)


class LeadStatusViewSet(TenantScopedViewSet):
    serializer_class = LeadStatusSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdminOrReadOnly]
    lookup_value_regex = "[0-9a-f-]{36}"
    ordering_fields = ["order", "name", "created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return LeadStatus.unscoped_objects.none()
        return get_lead_statuses_queryset()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        lead_status = create_lead_status(
            name=data["name"],
            order=data.get("order", 0),
            is_default=data.get("is_default", False),
        )
        return Response(LeadStatusSerializer(lead_status).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        lead_status = self.get_object()
        serializer = self.get_serializer(lead_status, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        updated = update_lead_status(
            lead_status=lead_status,
            name=data.get("name"),
            order=data.get("order"),
            is_default=data.get("is_default"),
        )
        return Response(LeadStatusSerializer(updated).data)

    def destroy(self, request, *args, **kwargs):
        lead_status = self.get_object()
        delete_lead_status(lead_status=lead_status)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AppointmentViewSet(TenantScopedViewSet):
    serializer_class = AppointmentSerializer
    permission_classes = [IsAuthenticated, IsTenantMember]
    lookup_value_regex = "[0-9a-f-]{36}"
    filterset_fields = ["contact", "status"]
    ordering_fields = ["starts_at", "created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Appointment.unscoped_objects.none()
        return get_appointments_queryset(membership=getattr(self.request, "membership", None))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        contact = data.get("contact")
        if contact is None:
            raise ValidationError({"contact": ["This field is required."]})
        appt = book_appointment(
            contact=contact,
            starts_at=data["starts_at"],
            external_ref=data.get("external_ref", ""),
            location=data.get("location", ""),
            notes=data.get("notes", ""),
            actor=request.user,
        )
        return Response(AppointmentSerializer(appt).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        appt = self.get_object()
        serializer = self.get_serializer(appt, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        updated = update_appointment(
            appointment=appt,
            actor=request.user,
            **serializer.validated_data,
        )
        return Response(AppointmentSerializer(updated).data)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        appt = self.get_object()
        updated = cancel_appointment(appointment=appt, actor=request.user)
        return Response(AppointmentSerializer(updated).data)

    @action(detail=True, methods=["post"], url_path="reschedule")
    def reschedule(self, request, pk=None):
        appt = self.get_object()
        serializer = AppointmentRescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = reschedule_appointment(
            appointment=appt,
            starts_at=serializer.validated_data["starts_at"],
            location=serializer.validated_data.get("location"),
            notes=serializer.validated_data.get("notes"),
            actor=request.user,
        )
        return Response(AppointmentSerializer(updated).data)

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        appt = self.get_object()
        updated = complete_appointment(appointment=appt, actor=request.user)
        return Response(AppointmentSerializer(updated).data)


class TaskViewSet(TenantScopedViewSet):
    serializer_class = TaskSerializer
    permission_classes = [IsAuthenticated, IsTenantMember]
    lookup_value_regex = "[0-9a-f-]{36}"
    filterset_fields = ["contact", "assignee", "status", "from_workflow"]
    search_fields = ["title", "description"]
    ordering_fields = ["due_at", "created_at", "completed_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Task.unscoped_objects.none()
        return get_tasks_queryset(membership=getattr(self.request, "membership", None))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        task = create_task(
            title=data["title"],
            description=data.get("description", ""),
            contact=data.get("contact"),
            assignee=data.get("assignee"),
            due_at=data.get("due_at"),
            created_by=request.user,
            from_workflow=data.get("from_workflow", False),
        )
        return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        task = self.get_object()
        serializer = self.get_serializer(task, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        updated = update_task(
            task=task,
            actor=request.user,
            **serializer.validated_data,
        )
        return Response(TaskSerializer(updated).data)

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        task = self.get_object()
        updated = complete_task(task=task, actor=request.user)
        return Response(TaskSerializer(updated).data)
