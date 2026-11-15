from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class ProviderDispatchError(Exception):
    def __init__(self, message: str, *, is_transient: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.is_transient = is_transient
        self.status_code = status_code


@dataclass(frozen=True)
class ProviderSendResult:
    provider: str
    provider_message_id: str
    status: str = "sent"
    error: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedStatusEvent:
    provider: str
    channel: str
    provider_message_id: str
    status: str
    error: str = ""
    recipient: str = ""
    sender: str = ""
    occurred_at: datetime | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedInboundMessage:
    provider: str
    channel: str
    provider_message_id: str
    sender: str
    recipient: str
    body: str
    subject: str = ""
    sender_name: str = ""
    button_payload: str = ""
    button_text: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedTemplate:
    provider: str
    content_sid: str
    name: str
    body: str
    variables: list[str] = field(default_factory=list)
    approval_status: str = "approved"
    category: str = "utility"
    language: str = "en"


@dataclass(frozen=True)
class NormalizedMetaToken:
    access_token: str
    expires_at: datetime | None = None
    meta_user_id: str = ""
    meta_user_name: str = ""
    is_valid: bool = True
    scopes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NormalizedMetaPage:
    page_id: str
    name: str
    access_token: str
    instagram_business_account_id: str = ""


@dataclass(frozen=True)
class NormalizedMetaQuestion:
    key: str
    label: str
    field_type: str = "CUSTOM"


@dataclass(frozen=True)
class NormalizedMetaLeadForm:
    form_id: str
    page_id: str
    name: str
    platform: str = "facebook"
    status: str = "ACTIVE"
    questions: list[NormalizedMetaQuestion] = field(default_factory=list)


@dataclass(frozen=True)
class NormalizedMetaLead:
    leadgen_id: str
    form_id: str
    page_id: str = ""
    created_time: datetime | None = None
    platform: str = "facebook"
    is_organic: bool = False
    ad_id: str = ""
    ad_name: str = ""
    adset_id: str = ""
    adset_name: str = ""
    campaign_id: str = ""
    campaign_name: str = ""
    field_values: dict[str, Any] = field(default_factory=dict)
    raw_answers: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class NormalizedMetaWebhookChange:
    page_id: str
    leadgen_id: str
    form_id: str = ""
    ad_id: str = ""
    adgroup_id: str = ""
    created_time: int | None = None
    raw_change: dict[str, Any] = field(default_factory=dict)


class EmailProvider(ABC):
    provider_name: str = ""

    def __init__(self, credentials: dict[str, Any], sender_identity: str = "", sender_name: str = ""):
        self.credentials = dict(credentials or {})
        self.sender_identity = sender_identity
        self.sender_name = sender_name

    @abstractmethod
    def send(
        self,
        *,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: str = "",
        from_email: str | None = None,
        from_name: str = "",
        reply_to: str = "",
        custom_args: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ProviderSendResult:
        raise NotImplementedError

    @abstractmethod
    def parse_status(self, payload: Any) -> list[NormalizedStatusEvent]:
        raise NotImplementedError

    @abstractmethod
    def parse_inbound(self, payload: Mapping[str, Any]) -> list[NormalizedInboundMessage]:
        raise NotImplementedError

    @abstractmethod
    def verify_signature(
        self,
        *,
        payload: bytes | str | Mapping[str, Any],
        headers: Mapping[str, str],
        url: str = "",
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        raise NotImplementedError


class SMSProvider(ABC):
    provider_name: str = ""

    def __init__(self, credentials: dict[str, Any], sender_identity: str = ""):
        self.credentials = dict(credentials or {})
        self.sender_identity = sender_identity

    @abstractmethod
    def send(
        self,
        *,
        to_phone: str,
        body: str,
        from_phone: str | None = None,
        status_callback_url: str = "",
    ) -> ProviderSendResult:
        raise NotImplementedError

    @abstractmethod
    def parse_status(self, payload: Mapping[str, Any]) -> list[NormalizedStatusEvent]:
        raise NotImplementedError

    @abstractmethod
    def parse_inbound(self, payload: Mapping[str, Any]) -> list[NormalizedInboundMessage]:
        raise NotImplementedError

    @abstractmethod
    def verify_signature(
        self,
        *,
        payload: Mapping[str, Any] | bytes | str,
        headers: Mapping[str, str],
        url: str = "",
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        raise NotImplementedError


class WhatsAppProvider(ABC):
    provider_name: str = ""

    def __init__(self, credentials: dict[str, Any], sender_identity: str = ""):
        self.credentials = dict(credentials or {})
        self.sender_identity = sender_identity

    @abstractmethod
    def send(
        self,
        *,
        to_phone: str,
        body: str = "",
        content_sid: str = "",
        content_variables: dict[str, Any] | None = None,
        from_phone: str | None = None,
        status_callback_url: str = "",
    ) -> ProviderSendResult:
        raise NotImplementedError

    @abstractmethod
    def parse_status(self, payload: Mapping[str, Any]) -> list[NormalizedStatusEvent]:
        raise NotImplementedError

    @abstractmethod
    def parse_inbound(self, payload: Mapping[str, Any]) -> list[NormalizedInboundMessage]:
        raise NotImplementedError

    @abstractmethod
    def verify_signature(
        self,
        *,
        payload: Mapping[str, Any] | bytes | str,
        headers: Mapping[str, str],
        url: str = "",
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    def fetch_templates(self) -> list[NormalizedTemplate]:
        raise NotImplementedError

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        raise NotImplementedError


class MetaLeadAdsProvider(ABC):
    provider_name: str = "meta"

    @abstractmethod
    def build_oauth_url(
        self,
        *,
        state: str,
        redirect_uri: str = "",
        scopes: list[str] | None = None,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def exchange_code_for_token(
        self,
        *,
        code: str,
        redirect_uri: str = "",
    ) -> NormalizedMetaToken:
        raise NotImplementedError

    @abstractmethod
    def debug_token(self, access_token: str) -> NormalizedMetaToken:
        raise NotImplementedError

    @abstractmethod
    def list_pages(self, user_access_token: str) -> list[NormalizedMetaPage]:
        raise NotImplementedError

    @abstractmethod
    def subscribe_page(self, page_id: str, page_access_token: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def unsubscribe_page(self, page_id: str, page_access_token: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def list_lead_forms(
        self,
        page_id: str,
        page_access_token: str,
        has_instagram: bool = False,
    ) -> list[NormalizedMetaLeadForm]:
        raise NotImplementedError

    @abstractmethod
    def fetch_form_questions(
        self,
        form_id: str,
        page_access_token: str,
    ) -> list[NormalizedMetaQuestion]:
        raise NotImplementedError

    @abstractmethod
    def fetch_lead(
        self,
        leadgen_id: str,
        access_token: str,
    ) -> NormalizedMetaLead:
        raise NotImplementedError

    @abstractmethod
    def verify_webhook_challenge(
        self,
        mode: str,
        token: str,
        challenge: str,
    ) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def verify_signature(
        self,
        *,
        payload: bytes | str,
        headers: Mapping[str, str],
        url: str = "",
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    def parse_webhook_changes(
        self,
        payload: Mapping[str, Any],
    ) -> list[NormalizedMetaWebhookChange]:
        raise NotImplementedError
