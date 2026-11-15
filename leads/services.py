from collections.abc import Iterable
import re
import secrets
from typing import Any
import uuid
from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from accounts.models import User
from accounts.services import AssignmentService, resolve_tenant_lead_assignee
from contacts.models import Contact, CustomFieldDefinition, Tag
from contacts.services import ContactService, normalize_phone_e164
from core.context import require_tenant, require_tenant_id, tenant_context
from core.exceptions import ApplicationError
from integrations.providers.registry import (
    NormalizedMetaLead,
    NormalizedMetaQuestion,
    NormalizedMetaWebhookChange,
    ProviderDispatchError,
    get_meta_provider,
)
from leads.models import MetaConnection, MetaLeadEvent, MetaLeadForm, MetaPage
from leads.selectors import get_meta_connection, get_meta_page_by_identifier

_META_OAUTH_SALT = "leads.meta.oauth.state"

STANDARD_CONTACT_TARGET_FIELDS = frozenset(
    {
        "first_name",
        "last_name",
        "full_name",
        "email",
        "phone",
        "city",
        "country",
        "timezone",
    }
)

DEFAULT_STANDARD_QUESTION_MAPPING: dict[str, str] = {
    "full_name": "full_name",
    "name": "full_name",
    "first_name": "first_name",
    "fname": "first_name",
    "last_name": "last_name",
    "lname": "last_name",
    "email": "email",
    "work_email": "email",
    "email_address": "email",
    "phone_number": "phone",
    "phone": "phone",
    "work_phone_number": "phone",
    "mobile_phone": "phone",
    "city": "city",
    "country": "country",
    "timezone": "timezone",
}


def generate_meta_oauth_connect_url(
    *,
    user: User,
    redirect_uri: str = "",
) -> dict[str, str]:
    tenant_id = require_tenant_id()
    state_payload = {
        "tenant_id": str(tenant_id),
        "user_id": str(user.id),
        "nonce": secrets.token_hex(8),
    }
    signed_state = signing.dumps(state_payload, salt=_META_OAUTH_SALT)
    provider = get_meta_provider()
    url = provider.build_oauth_url(state=signed_state, redirect_uri=redirect_uri)
    return {
        "connect_url": url,
        "state": signed_state,
    }


def verify_meta_oauth_state(state: str, max_age: int = 900) -> dict[str, str]:
    try:
        data = signing.loads(state, salt=_META_OAUTH_SALT, max_age=max_age)
        if not isinstance(data, dict) or "tenant_id" not in data or "user_id" not in data:
            raise ValueError("Malformed state payload.")
        return {
            "tenant_id": str(data["tenant_id"]),
            "user_id": str(data["user_id"]),
        }
    except Exception as exc:
        raise ApplicationError(
            detail="Invalid or expired Meta OAuth state parameter.",
            code="invalid_oauth_state",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc


@transaction.atomic
def complete_meta_oauth_callback(
    *,
    code: str,
    state: str,
    redirect_uri: str = "",
) -> MetaConnection:
    state_data = verify_meta_oauth_state(state)
    tenant_id = state_data["tenant_id"]
    provider = get_meta_provider()

    try:
        token_dto = provider.exchange_code_for_token(
            code=code,
            redirect_uri=redirect_uri,
        )
    except ProviderDispatchError as exc:
        raise ApplicationError(
            detail=exc.message,
            code="meta_oauth_exchange_failed",
            status_code=status.HTTP_502_BAD_GATEWAY,
        ) from exc

    now = timezone.now()
    with tenant_context(tenant_id):
        connection = MetaConnection.objects.select_for_update().first()
        if connection is None:
            connection = MetaConnection.objects.create(
                user_access_token=token_dto.access_token,
                token_expires_at=token_dto.expires_at,
                meta_user_id=token_dto.meta_user_id,
                meta_user_name=token_dto.meta_user_name,
                status=MetaConnection.Status.CONNECTED,
                last_checked_at=now,
                last_error="",
                alert_notified_status="",
            )
        else:
            connection.user_access_token = token_dto.access_token
            connection.token_expires_at = token_dto.expires_at
            connection.meta_user_id = token_dto.meta_user_id
            connection.meta_user_name = token_dto.meta_user_name
            connection.status = MetaConnection.Status.CONNECTED
            connection.last_checked_at = now
            connection.last_error = ""
            connection.alert_notified_status = ""
            connection.save()

        try:
            sync_meta_pages(connection=connection)
        except Exception:
            pass

        return connection


@transaction.atomic
def sync_meta_pages(*, connection: MetaConnection | None = None) -> list[MetaPage]:
    conn = connection or get_meta_connection()
    if conn is None or not conn.user_access_token:
        raise ApplicationError(
            detail="Meta account is not connected for this tenant.",
            code="meta_not_connected",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    provider = get_meta_provider()
    try:
        remote_pages = provider.list_pages(conn.user_access_token)
    except ProviderDispatchError as exc:
        raise ApplicationError(
            detail=exc.message,
            code="meta_pages_fetch_failed",
            status_code=status.HTTP_502_BAD_GATEWAY,
        ) from exc

    tenant_id = require_tenant_id()
    synced: list[MetaPage] = []

    for page_dto in remote_pages:
        other_tenant_claim = (
            MetaPage.unscoped_objects.filter(page_id=page_dto.page_id)
            .exclude(tenant_id=tenant_id)
            .exists()
        )
        if other_tenant_claim:
            continue

        existing = MetaPage.objects.filter(page_id=page_dto.page_id).first()
        if existing is not None:
            existing.connection = conn
            existing.name = page_dto.name
            existing.page_access_token = page_dto.access_token
            existing.instagram_business_account_id = page_dto.instagram_business_account_id
            existing.save()
            synced.append(existing)
        else:
            created = MetaPage.objects.create(
                connection=conn,
                page_id=page_dto.page_id,
                name=page_dto.name,
                page_access_token=page_dto.access_token,
                instagram_business_account_id=page_dto.instagram_business_account_id,
                selected=False,
                subscribed=False,
            )
            synced.append(created)

    return list(MetaPage.objects.filter(connection=conn).order_by("name"))


@transaction.atomic
def select_meta_pages(*, page_identifiers: Iterable[str | uuid.UUID]) -> list[MetaPage]:
    conn = get_meta_connection()
    if conn is None or conn.status == MetaConnection.Status.DISCONNECTED:
        raise ApplicationError(
            detail="Meta account is not connected for this tenant.",
            code="meta_not_connected",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    provider = get_meta_provider()
    all_pages = list(MetaPage.objects.select_for_update().filter(connection=conn))
    desired_keys = {str(item).strip() for item in page_identifiers if str(item).strip()}

    for page in all_pages:
        should_select = (str(page.id) in desired_keys) or (page.page_id in desired_keys)
        if should_select:
            if not page.subscribed:
                try:
                    provider.subscribe_page(page.page_id, page.page_access_token)
                    page.subscribed = True
                except ProviderDispatchError as exc:
                    raise ApplicationError(
                        detail=f"Failed to subscribe page '{page.name}' to leadgen: {exc.message}",
                        code="meta_page_subscribe_failed",
                        status_code=status.HTTP_502_BAD_GATEWAY,
                    ) from exc
            page.selected = True
            page.save(update_fields=["selected", "subscribed", "updated_at"])
        else:
            if page.subscribed:
                try:
                    provider.unsubscribe_page(page.page_id, page.page_access_token)
                except ProviderDispatchError:
                    pass
                page.subscribed = False
            if page.selected or not page.subscribed:
                page.selected = False
                page.save(update_fields=["selected", "subscribed", "updated_at"])

    return list(MetaPage.objects.filter(connection=conn).order_by("name"))


def suggest_default_field_mapping(
    questions: Iterable[dict[str, Any] | NormalizedMetaQuestion],
) -> dict[str, str]:
    custom_defs = {
        defn.key.lower(): defn.key
        for defn in CustomFieldDefinition.objects.all()
    }
    for defn in CustomFieldDefinition.objects.all():
        slug_label = re.sub(r"[^a-z0-9]+", "_", defn.label.strip().lower()).strip("_")
        if slug_label and slug_label not in custom_defs:
            custom_defs[slug_label] = defn.key

    suggested: dict[str, str] = {}
    for q in questions:
        if isinstance(q, NormalizedMetaQuestion):
            raw_key = q.key
            q_type = q.field_type.upper()
        elif isinstance(q, dict):
            raw_key = str(q.get("key") or "")
            q_type = str(q.get("type") or q.get("field_type") or "").upper()
        else:
            continue

        clean_key = raw_key.strip()
        if not clean_key:
            continue
        norm_key = clean_key.lower()

        if norm_key in DEFAULT_STANDARD_QUESTION_MAPPING:
            suggested[clean_key] = DEFAULT_STANDARD_QUESTION_MAPPING[norm_key]
        elif q_type == "FULL_NAME":
            suggested[clean_key] = "full_name"
        elif q_type == "FIRST_NAME":
            suggested[clean_key] = "first_name"
        elif q_type == "LAST_NAME":
            suggested[clean_key] = "last_name"
        elif q_type == "EMAIL":
            suggested[clean_key] = "email"
        elif q_type in ("PHONE", "PHONE_NUMBER"):
            suggested[clean_key] = "phone"
        elif q_type == "CITY":
            suggested[clean_key] = "city"
        elif q_type == "COUNTRY":
            suggested[clean_key] = "country"
        elif norm_key in custom_defs:
            suggested[clean_key] = custom_defs[norm_key]

    return suggested


def validate_form_field_mapping(field_mapping: dict[str, Any]) -> dict[str, str]:
    if not isinstance(field_mapping, dict):
        raise ApplicationError(
            detail="field_mapping must be a JSON object mapping question keys to target fields.",
            code="invalid_field_mapping",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    valid_custom_keys = set(
        CustomFieldDefinition.objects.values_list("key", flat=True)
    )

    normalized_mapping: dict[str, str] = {}
    for question_key, target_raw in field_mapping.items():
        q_key = str(question_key).strip()
        if not q_key or target_raw in (None, ""):
            continue
        target = str(target_raw).strip()
        if target in STANDARD_CONTACT_TARGET_FIELDS:
            normalized_mapping[q_key] = target
            continue

        custom_key = (
            target[len("custom_fields."):]
            if target.startswith("custom_fields.")
            else target
        )
        if custom_key in valid_custom_keys:
            normalized_mapping[q_key] = target
            continue

        raise ApplicationError(
            detail=f"Mapped target field '{target}' does not exist as a Contact field or CustomFieldDefinition.",
            code="invalid_field_mapping_target",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return normalized_mapping


@transaction.atomic
def sync_meta_page_forms(*, page: MetaPage) -> list[MetaLeadForm]:
    provider = get_meta_provider()
    try:
        remote_forms = provider.list_lead_forms(
            page.page_id,
            page.page_access_token,
            has_instagram=bool(page.instagram_business_account_id),
        )
    except ProviderDispatchError as exc:
        raise ApplicationError(
            detail=exc.message,
            code="meta_forms_fetch_failed",
            status_code=status.HTTP_502_BAD_GATEWAY,
        ) from exc

    now = timezone.now()
    for form_dto in remote_forms:
        questions_json = [
            {
                "key": q.key,
                "label": q.label,
                "type": q.field_type,
            }
            for q in form_dto.questions
        ]
        default_map = suggest_default_field_mapping(form_dto.questions)
        existing = MetaLeadForm.objects.filter(form_id=form_dto.form_id).first()

        if existing is not None:
            existing.page = page
            existing.name = form_dto.name
            existing.platform = (
                MetaLeadForm.Platform.INSTAGRAM
                if form_dto.platform == "instagram"
                else MetaLeadForm.Platform.FACEBOOK
            )
            existing.status = form_dto.status
            if questions_json:
                existing.questions = questions_json
            if not existing.field_mapping and default_map:
                existing.field_mapping = default_map
            existing.last_synced_at = now
            existing.save()
        else:
            MetaLeadForm.objects.create(
                page=page,
                form_id=form_dto.form_id,
                name=form_dto.name,
                platform=(
                    MetaLeadForm.Platform.INSTAGRAM
                    if form_dto.platform == "instagram"
                    else MetaLeadForm.Platform.FACEBOOK
                ),
                status=form_dto.status,
                selected=False,
                questions=questions_json,
                field_mapping=default_map,
                last_synced_at=now,
            )

    return list(
        MetaLeadForm.objects.select_related("page", "tenant")
        .prefetch_related("default_tags")
        .filter(page=page)
        .order_by("name")
    )


@transaction.atomic
def select_meta_forms(
    *,
    form_identifiers: Iterable[str | uuid.UUID],
    page: MetaPage | None = None,
) -> list[MetaLeadForm]:
    qs = MetaLeadForm.objects.select_for_update()
    if page is not None:
        qs = qs.filter(page=page)
    forms = list(qs)
    desired = {str(item).strip() for item in form_identifiers if str(item).strip()}

    for form in forms:
        should_select = (str(form.id) in desired) or (form.form_id in desired)
        if form.selected != should_select:
            form.selected = should_select
            form.save(update_fields=["selected", "updated_at"])

    result_qs = MetaLeadForm.objects.select_related("page", "tenant").prefetch_related("default_tags")
    if page is not None:
        result_qs = result_qs.filter(page=page)
    return list(result_qs.order_by("name"))


@transaction.atomic
def fetch_and_sync_form_questions(*, form: MetaLeadForm) -> dict[str, Any]:
    provider = get_meta_provider()
    page_token = form.page.page_access_token
    if page_token:
        try:
            remote_questions = provider.fetch_form_questions(form.form_id, page_token)
            if remote_questions:
                form.questions = [
                    {"key": q.key, "label": q.label, "type": q.field_type}
                    for q in remote_questions
                ]
                form.last_synced_at = timezone.now()
                if not form.field_mapping:
                    form.field_mapping = suggest_default_field_mapping(remote_questions)
                form.save(update_fields=["questions", "field_mapping", "last_synced_at", "updated_at"])
        except ProviderDispatchError as exc:
            if not form.questions:
                raise ApplicationError(
                    detail=exc.message,
                    code="meta_questions_fetch_failed",
                    status_code=status.HTTP_502_BAD_GATEWAY,
                ) from exc

    suggested = suggest_default_field_mapping(form.questions or [])
    return {
        "form_id": str(form.id),
        "meta_form_id": form.form_id,
        "name": form.name,
        "questions": form.questions or [],
        "field_mapping": form.field_mapping or {},
        "suggested_mapping": suggested,
    }


@transaction.atomic
def update_meta_form_configuration(
    *,
    form: MetaLeadForm,
    field_mapping: dict[str, Any] | None = None,
    default_tags: Iterable[Any] | None = None,
    selected: bool | None = None,
    platform: str | None = None,
) -> MetaLeadForm:
    if field_mapping is not None:
        form.field_mapping = validate_form_field_mapping(field_mapping)
    if selected is not None:
        form.selected = selected
    if platform is not None and platform in (
        MetaLeadForm.Platform.FACEBOOK,
        MetaLeadForm.Platform.INSTAGRAM,
    ):
        form.platform = platform
    form.save()

    if default_tags is not None:
        resolved_tags: list[Tag] = []
        for item in default_tags:
            if isinstance(item, Tag):
                resolved_tags.append(item)
            else:
                raw_str = str(item).strip()
                if not raw_str:
                    continue
                try:
                    t_uuid = uuid.UUID(raw_str)
                    tag_obj = Tag.objects.filter(pk=t_uuid).first()
                except ValueError:
                    tag_obj = Tag.objects.filter(name__iexact=raw_str).first()
                    if tag_obj is None:
                        tag_obj = Tag.objects.create(name=raw_str)
                if tag_obj is None:
                    raise ApplicationError(
                        detail=f"Default tag '{raw_str}' does not exist in this tenant.",
                        code="invalid_default_tag",
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )
                resolved_tags.append(tag_obj)
        form.default_tags.set(resolved_tags)

    return (
        MetaLeadForm.objects.select_related("page", "tenant")
        .prefetch_related("default_tags")
        .get(pk=form.pk)
    )


@transaction.atomic
def disconnect_meta_connection() -> MetaConnection | None:
    conn = get_meta_connection()
    if conn is None:
        return None

    provider = get_meta_provider()
    pages = list(MetaPage.objects.select_for_update().filter(connection=conn))
    for page in pages:
        if page.subscribed and page.page_access_token:
            try:
                provider.unsubscribe_page(page.page_id, page.page_access_token)
            except ProviderDispatchError:
                pass
        page.page_access_token = ""
        page.selected = False
        page.subscribed = False
        page.save(update_fields=["page_access_token", "selected", "subscribed", "updated_at"])

    MetaLeadForm.objects.filter(page__connection=conn, selected=True).update(selected=False)

    conn.user_access_token = ""
    conn.token_expires_at = None
    conn.status = MetaConnection.Status.DISCONNECTED
    conn.last_error = ""
    conn.alert_notified_status = ""
    conn.save(
        update_fields=[
            "user_access_token",
            "token_expires_at",
            "status",
            "last_error",
            "alert_notified_status",
            "updated_at",
        ]
    )
    return conn


def ingest_meta_webhook_changes(
    changes: list[NormalizedMetaWebhookChange],
) -> list[MetaLeadEvent]:
    created_events: list[MetaLeadEvent] = []

    for change in changes:
        page = (
            MetaPage.unscoped_objects.select_related("tenant", "connection")
            .filter(page_id=change.page_id, tenant__is_active=True)
            .first()
        )
        if page is None:
            continue

        tenant_id_str = str(page.tenant_id)
        with tenant_context(page.tenant_id):
            with transaction.atomic():
                if MetaLeadEvent.objects.filter(leadgen_id=change.leadgen_id).exists():
                    continue

                form: MetaLeadForm | None = None
                if change.form_id:
                    form = MetaLeadForm.objects.filter(form_id=change.form_id).first()

                should_ignore = (not page.selected) or (form is not None and not form.selected)
                try:
                    lead_event = MetaLeadEvent.objects.create(
                        leadgen_id=change.leadgen_id,
                        page=page,
                        form=form,
                        raw_payload=change.raw_change,
                        status=(
                            MetaLeadEvent.Status.IGNORED
                            if should_ignore
                            else MetaLeadEvent.Status.RECEIVED
                        ),
                        processed_at=timezone.now() if should_ignore else None,
                    )
                except IntegrityError:
                    continue

                created_events.append(lead_event)
                if not should_ignore:
                    event_id_str = str(lead_event.id)
                    transaction.on_commit(
                        lambda t=tenant_id_str, e=event_id_str: _enqueue_meta_lead_event(t, e)
                    )

    return created_events


def _enqueue_meta_lead_event(tenant_id: str, lead_event_id: str) -> None:
    from leads.tasks import process_meta_lead_event_task

    try:
        process_meta_lead_event_task.apply_async(
            args=[tenant_id, lead_event_id],
            queue="webhooks",
        )
    except Exception:
        process_meta_lead_event_task(tenant_id, lead_event_id)


@transaction.atomic
def retry_failed_lead_event(*, lead_event: MetaLeadEvent) -> MetaLeadEvent:
    tenant_id = require_tenant_id()
    if lead_event.status != MetaLeadEvent.Status.FAILED:
        raise ApplicationError(
            detail="Only failed lead events can be retried.",
            code="lead_event_not_failed",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    lead_event.status = MetaLeadEvent.Status.RECEIVED
    lead_event.error = ""
    lead_event.save(update_fields=["status", "error", "updated_at"])

    tid_str = str(tenant_id)
    eid_str = str(lead_event.id)
    transaction.on_commit(lambda: _enqueue_meta_lead_event(tid_str, eid_str))
    return lead_event


def map_meta_lead_fields(
    *,
    lead_dto: NormalizedMetaLead,
    form: MetaLeadForm | None,
) -> tuple[dict[str, str], dict[str, Any]]:
    explicit_mapping = dict((form.field_mapping if form else None) or {})
    effective_mapping = dict(DEFAULT_STANDARD_QUESTION_MAPPING)
    effective_mapping.update(explicit_mapping)

    contact_fields: dict[str, str] = {
        "first_name": "",
        "last_name": "",
        "full_name": "",
        "email": "",
        "phone": "",
        "city": "",
        "country": "",
        "timezone": "UTC",
    }
    custom_fields: dict[str, Any] = {}
    valid_custom_keys = set(
        CustomFieldDefinition.objects.values_list("key", flat=True)
    )

    for raw_key, raw_val in (lead_dto.field_values or {}).items():
        if raw_val in (None, ""):
            continue
        str_val = str(raw_val).strip()
        if not str_val:
            continue

        target = effective_mapping.get(raw_key) or effective_mapping.get(raw_key.strip().lower())
        if target in STANDARD_CONTACT_TARGET_FIELDS:
            contact_fields[target] = str_val
        elif target:
            ckey = target[len("custom_fields."):] if target.startswith("custom_fields.") else target
            custom_fields[ckey] = raw_val
        elif raw_key in valid_custom_keys:
            custom_fields[raw_key] = raw_val

    return contact_fields, custom_fields


@transaction.atomic
def ingest_fetched_meta_lead(
    *,
    lead_event: MetaLeadEvent,
    lead_dto: NormalizedMetaLead,
) -> Contact | None:
    page = lead_event.page
    form = lead_event.form
    if form is None and lead_dto.form_id:
        form = (
            MetaLeadForm.objects.prefetch_related("default_tags")
            .filter(form_id=lead_dto.form_id)
            .first()
        )
        if form is not None:
            lead_event.form = form

    if form is not None and not form.selected:
        lead_event.status = MetaLeadEvent.Status.IGNORED
        lead_event.processed_at = timezone.now()
        lead_event.error = ""
        lead_event.save(update_fields=["form", "status", "processed_at", "error", "updated_at"])
        return None

    contact_fields, custom_fields = map_meta_lead_fields(lead_dto=lead_dto, form=form)
    raw_phone = contact_fields.get("phone") or None
    norm_phone = normalize_phone_e164(raw_phone) if raw_phone else None

    is_instagram = (
        lead_dto.platform == "instagram"
        or (form is not None and form.platform == MetaLeadForm.Platform.INSTAGRAM)
    )
    source_channel = "Instagram Lead Ads" if is_instagram else "Facebook Lead Ads"
    source_page = page.name if page else ""
    source_form = form.name if form else lead_dto.form_id

    campaign = "" if lead_dto.is_organic else lead_dto.campaign_name
    ad_set = "" if lead_dto.is_organic else lead_dto.adset_name
    ad = "" if lead_dto.is_organic else lead_dto.ad_name

    default_tags = list(form.default_tags.all()) if form is not None else []
    tag_names = [t.name for t in default_tags]
    tag_ids = [t.id for t in default_tags]

    assignee = AssignmentService.resolve_for_lead(
        source_page=source_page,
        source_form=source_form,
        tag_names=tag_names,
        tag_ids=tag_ids,
    )

    contact, _ = ContactService.upsert_from_source(
        first_name=contact_fields["first_name"],
        last_name=contact_fields["last_name"],
        full_name=contact_fields["full_name"],
        email=contact_fields["email"] or None,
        phone=norm_phone,
        city=contact_fields["city"],
        country=contact_fields["country"],
        timezone_name=contact_fields["timezone"] or "UTC",
        source_channel=source_channel,
        source_page=source_page,
        source_form=source_form,
        campaign=campaign,
        ad_set=ad_set,
        ad=ad,
        external_id=lead_dto.leadgen_id,
        custom_fields=custom_fields,
        tags=default_tags,
        assigned_to=assignee,
        acquired_at=lead_dto.created_time,
        raw_answers=lead_dto.raw_answers,
        is_form_submission=True,
    )

    lead_event.contact = contact
    lead_event.status = MetaLeadEvent.Status.PROCESSED
    lead_event.processed_at = timezone.now()
    lead_event.error = ""
    lead_event.save(
        update_fields=["form", "contact", "status", "processed_at", "error", "updated_at"]
    )
    return contact
