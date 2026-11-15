import base64
from collections.abc import Mapping
import hashlib
import hmac
import json
import re
from typing import Any
import uuid
from urllib.parse import urlsplit
from django.conf import settings
import requests
from integrations.providers.base import (
    NormalizedInboundMessage,
    NormalizedStatusEvent,
    NormalizedTemplate,
    ProviderDispatchError,
    ProviderSendResult,
    SMSProvider,
    WhatsAppProvider,
)

_TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
_TWILIO_CONTENT_API_BASE = "https://content.twilio.com/v1"
_VAR_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def _get_header_case_insensitive(headers: Mapping[str, str], target: str) -> str:
    target_lower = target.lower()
    for key, val in headers.items():
        if key.lower() == target_lower:
            return str(val)
    return ""


def strip_whatsapp_prefix(raw_number: str) -> str:
    val = (raw_number or "").strip()
    if val.lower().startswith("whatsapp:"):
        val = val.split(":", 1)[1].strip()
    return val


def ensure_whatsapp_prefix(raw_number: str) -> str:
    clean = strip_whatsapp_prefix(raw_number)
    if not clean:
        return ""
    return f"whatsapp:{clean}"


def _compute_twilio_sig(
    auth_token: str,
    target_url: str,
    payload: Mapping[str, Any] | bytes | str,
) -> str:
    data_str = target_url
    if isinstance(payload, Mapping):
        for key in sorted(payload.keys()):
            val = payload[key]
            if isinstance(val, list):
                for item in sorted(str(v) for v in val):
                    data_str += f"{key}{item}"
            else:
                data_str += f"{key}{'' if val is None else val}"

    digest = hmac.new(
        auth_token.encode("utf-8"),
        data_str.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def verify_twilio_signature(
    *,
    auth_token: str,
    url: str,
    payload: Mapping[str, Any] | bytes | str,
    headers: Mapping[str, str],
) -> bool:
    if not auth_token or not url:
        return False
    provided_sig = _get_header_case_insensitive(headers, "X-Twilio-Signature").strip()
    if not provided_sig:
        return False

    candidate_urls: list[str] = [url]
    public_base = str(getattr(settings, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if public_base:
        parsed = urlsplit(url)
        path_and_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        rebuilt = f"{public_base}{path_and_query}"
        if rebuilt not in candidate_urls:
            candidate_urls.insert(0, rebuilt)

    for candidate in candidate_urls:
        expected_sig = _compute_twilio_sig(auth_token, candidate, payload)
        if hmac.compare_digest(expected_sig, provided_sig):
            return True
    return False


def _map_twilio_status(raw_status: str) -> str:
    mapping = {
        "queued": "queued",
        "accepted": "queued",
        "scheduled": "queued",
        "sending": "sent",
        "sent": "sent",
        "delivered": "delivered",
        "read": "read",
        "undelivered": "undelivered",
        "failed": "failed",
        "canceled": "failed",
    }
    return mapping.get((raw_status or "").strip().lower(), "")


class _TwilioBaseMixin:
    def _account_sid(self) -> str:
        return str(self.credentials.get("account_sid") or "").strip()

    def _auth_token(self) -> str:
        return str(self.credentials.get("auth_token") or "").strip()

    def _is_mock_mode(self) -> bool:
        sid = self._account_sid()
        token = self._auth_token()
        return (
            bool(self.credentials.get("mock"))
            or sid.startswith(("ACmock", "mock_", "test_"))
            or token.startswith(("mock_", "test_"))
        )

    def _check_account_credentials(self) -> tuple[bool, str]:
        sid = self._account_sid()
        token = self._auth_token()
        if not sid or not token:
            return False, "Missing Twilio account_sid or auth_token."
        if not self.sender_identity:
            return False, "Missing Twilio sender phone number."
        if self._is_mock_mode():
            return True, "Twilio connection verified (mock mode)."
        try:
            response = requests.get(
                f"{_TWILIO_API_BASE}/Accounts/{sid}.json",
                auth=(sid, token),
                timeout=10,
            )
            if response.status_code == 200:
                return True, "Twilio account credentials verified."
            return False, f"Twilio verification failed ({response.status_code})."
        except requests.RequestException as exc:
            return False, f"Twilio connection check error: {exc}"


class TwilioSMSProvider(_TwilioBaseMixin, SMSProvider):
    provider_name = "twilio"

    def send(
        self,
        *,
        to_phone: str,
        body: str,
        from_phone: str | None = None,
        status_callback_url: str = "",
    ) -> ProviderSendResult:
        sid = self._account_sid()
        token = self._auth_token()
        sender = strip_whatsapp_prefix(from_phone or self.sender_identity)
        recipient = strip_whatsapp_prefix(to_phone)
        if not sid or not token:
            raise ProviderDispatchError("Twilio credentials are not configured.", is_transient=False)
        if not sender:
            raise ProviderDispatchError("Twilio SMS sender phone number is not configured.", is_transient=False)

        if self._is_mock_mode():
            mock_sid = f"SM{uuid.uuid4().hex[:32]}"
            return ProviderSendResult(
                provider=self.provider_name,
                provider_message_id=mock_sid,
                status="sent",
                raw_response={"mock": True, "sid": mock_sid},
            )

        form_data: dict[str, str] = {
            "To": recipient,
            "From": sender,
            "Body": body,
        }
        if status_callback_url:
            form_data["StatusCallback"] = status_callback_url

        try:
            response = requests.post(
                f"{_TWILIO_API_BASE}/Accounts/{sid}/Messages.json",
                auth=(sid, token),
                data=form_data,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ProviderDispatchError(str(exc), is_transient=True) from exc

        if response.status_code in (200, 201):
            body_json = response.json()
            msg_sid = str(body_json.get("sid") or f"SM{uuid.uuid4().hex[:32]}")
            status_val = _map_twilio_status(str(body_json.get("status") or "sent")) or "sent"
            return ProviderSendResult(
                provider=self.provider_name,
                provider_message_id=msg_sid,
                status=status_val,
                error=str(body_json.get("error_message") or ""),
                raw_response=body_json,
            )

        is_transient = response.status_code in (408, 429, 500, 502, 503, 504)
        raise ProviderDispatchError(
            f"Twilio SMS error ({response.status_code}): {response.text[:500]}",
            is_transient=is_transient,
            status_code=response.status_code,
        )

    def parse_status(self, payload: Mapping[str, Any]) -> list[NormalizedStatusEvent]:
        msg_sid = str(payload.get("MessageSid") or payload.get("SmsSid") or "").strip()
        raw_status = str(payload.get("MessageStatus") or payload.get("SmsStatus") or "").strip()
        mapped = _map_twilio_status(raw_status)
        if not msg_sid or not mapped:
            return []

        err_code = str(payload.get("ErrorCode") or "").strip()
        err_msg = str(payload.get("ErrorMessage") or "").strip()
        error_text = f"{err_code}: {err_msg}".strip(": ") if (err_code or err_msg) else ""

        return [
            NormalizedStatusEvent(
                provider=self.provider_name,
                channel="sms",
                provider_message_id=msg_sid,
                status=mapped,
                error=error_text,
                recipient=strip_whatsapp_prefix(str(payload.get("To") or "")),
                sender=strip_whatsapp_prefix(str(payload.get("From") or "")),
                raw_payload=dict(payload),
            )
        ]

    def parse_inbound(self, payload: Mapping[str, Any]) -> list[NormalizedInboundMessage]:
        raw_status = str(payload.get("MessageStatus") or payload.get("SmsStatus") or "").strip().lower()
        body_raw = payload.get("Body")
        if body_raw is None and raw_status in (
            "queued",
            "sent",
            "delivered",
            "undelivered",
            "failed",
            "read",
            "sending",
        ):
            return []

        sender = strip_whatsapp_prefix(str(payload.get("From") or ""))
        recipient = strip_whatsapp_prefix(str(payload.get("To") or self.sender_identity))
        if not sender:
            return []

        msg_sid = str(
            payload.get("MessageSid")
            or payload.get("SmsMessageSid")
            or payload.get("SmsSid")
            or f"SMin_{uuid.uuid4().hex[:24]}"
        ).strip()
        body = str(body_raw or "").strip()
        sender_name = str(payload.get("ProfileName") or payload.get("FromName") or "").strip()

        return [
            NormalizedInboundMessage(
                provider=self.provider_name,
                channel="sms",
                provider_message_id=msg_sid,
                sender=sender,
                recipient=recipient,
                body=body,
                sender_name=sender_name,
                raw_payload=dict(payload),
            )
        ]

    def verify_signature(
        self,
        *,
        payload: Mapping[str, Any] | bytes | str,
        headers: Mapping[str, str],
        url: str = "",
    ) -> bool:
        return verify_twilio_signature(
            auth_token=self._auth_token(),
            url=url,
            payload=payload,
            headers=headers,
        )

    def test_connection(self) -> tuple[bool, str]:
        return self._check_account_credentials()


class TwilioWhatsAppProvider(_TwilioBaseMixin, WhatsAppProvider):
    provider_name = "twilio"

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
        sid = self._account_sid()
        token = self._auth_token()
        sender = ensure_whatsapp_prefix(from_phone or self.sender_identity)
        recipient = ensure_whatsapp_prefix(to_phone)
        if not sid or not token:
            raise ProviderDispatchError("Twilio credentials are not configured.", is_transient=False)
        if not sender:
            raise ProviderDispatchError("Twilio WhatsApp sender number is not configured.", is_transient=False)

        if self._is_mock_mode():
            mock_sid = f"WA{uuid.uuid4().hex[:32]}"
            return ProviderSendResult(
                provider=self.provider_name,
                provider_message_id=mock_sid,
                status="sent",
                raw_response={
                    "mock": True,
                    "sid": mock_sid,
                    "content_sid": content_sid,
                },
            )

        form_data: dict[str, str] = {
            "To": recipient,
            "From": sender,
        }
        if content_sid:
            form_data["ContentSid"] = content_sid
            if content_variables:
                form_data["ContentVariables"] = json.dumps(
                    {str(k): str(v) for k, v in content_variables.items()}
                )
            elif body:
                form_data["Body"] = body
        else:
            form_data["Body"] = body

        if status_callback_url:
            form_data["StatusCallback"] = status_callback_url

        try:
            response = requests.post(
                f"{_TWILIO_API_BASE}/Accounts/{sid}/Messages.json",
                auth=(sid, token),
                data=form_data,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ProviderDispatchError(str(exc), is_transient=True) from exc

        if response.status_code in (200, 201):
            body_json = response.json()
            msg_sid = str(body_json.get("sid") or f"WA{uuid.uuid4().hex[:32]}")
            status_val = _map_twilio_status(str(body_json.get("status") or "sent")) or "sent"
            return ProviderSendResult(
                provider=self.provider_name,
                provider_message_id=msg_sid,
                status=status_val,
                error=str(body_json.get("error_message") or ""),
                raw_response=body_json,
            )

        is_transient = response.status_code in (408, 429, 500, 502, 503, 504)
        raise ProviderDispatchError(
            f"Twilio WhatsApp error ({response.status_code}): {response.text[:500]}",
            is_transient=is_transient,
            status_code=response.status_code,
        )

    def parse_status(self, payload: Mapping[str, Any]) -> list[NormalizedStatusEvent]:
        msg_sid = str(payload.get("MessageSid") or payload.get("SmsSid") or "").strip()
        raw_status = str(payload.get("MessageStatus") or payload.get("SmsStatus") or "").strip()
        mapped = _map_twilio_status(raw_status)
        if not msg_sid or not mapped:
            return []

        err_code = str(payload.get("ErrorCode") or "").strip()
        err_msg = str(payload.get("ErrorMessage") or "").strip()
        error_text = f"{err_code}: {err_msg}".strip(": ") if (err_code or err_msg) else ""

        return [
            NormalizedStatusEvent(
                provider=self.provider_name,
                channel="whatsapp",
                provider_message_id=msg_sid,
                status=mapped,
                error=error_text,
                recipient=strip_whatsapp_prefix(str(payload.get("To") or "")),
                sender=strip_whatsapp_prefix(str(payload.get("From") or "")),
                raw_payload=dict(payload),
            )
        ]

    def parse_inbound(self, payload: Mapping[str, Any]) -> list[NormalizedInboundMessage]:
        raw_status = str(payload.get("MessageStatus") or payload.get("SmsStatus") or "").strip().lower()
        body_raw = payload.get("Body")
        button_payload = str(payload.get("ButtonPayload") or "").strip()
        button_text = str(payload.get("ButtonText") or "").strip()

        if (
            body_raw is None
            and not button_payload
            and not button_text
            and raw_status in ("queued", "sent", "delivered", "undelivered", "failed", "read", "sending")
        ):
            return []

        sender = strip_whatsapp_prefix(str(payload.get("From") or ""))
        recipient = strip_whatsapp_prefix(str(payload.get("To") or self.sender_identity))
        if not sender:
            return []

        msg_sid = str(
            payload.get("MessageSid")
            or payload.get("SmsMessageSid")
            or payload.get("SmsSid")
            or f"WAin_{uuid.uuid4().hex[:24]}"
        ).strip()

        body = str(body_raw or "").strip()
        if not body and (button_text or button_payload):
            body = button_text or button_payload

        sender_name = str(payload.get("ProfileName") or payload.get("FromName") or "").strip()

        return [
            NormalizedInboundMessage(
                provider=self.provider_name,
                channel="whatsapp",
                provider_message_id=msg_sid,
                sender=sender,
                recipient=recipient,
                body=body,
                sender_name=sender_name,
                button_payload=button_payload,
                button_text=button_text,
                raw_payload=dict(payload),
            )
        ]

    def verify_signature(
        self,
        *,
        payload: Mapping[str, Any] | bytes | str,
        headers: Mapping[str, str],
        url: str = "",
    ) -> bool:
        return verify_twilio_signature(
            auth_token=self._auth_token(),
            url=url,
            payload=payload,
            headers=headers,
        )

    def fetch_templates(self) -> list[NormalizedTemplate]:
        sid = self._account_sid()
        token = self._auth_token()
        if not sid or not token:
            raise ProviderDispatchError("Twilio credentials are not configured.", is_transient=False)

        seed_templates = self.credentials.get("templates")
        if isinstance(seed_templates, list):
            normalized_list: list[NormalizedTemplate] = []
            for item in seed_templates:
                if not isinstance(item, dict) or not item.get("content_sid"):
                    continue
                tpl_body = str(item.get("body") or "")
                vars_found = item.get("variables") or _VAR_PLACEHOLDER_RE.findall(tpl_body)
                normalized_list.append(
                    NormalizedTemplate(
                        provider=self.provider_name,
                        content_sid=str(item["content_sid"]).strip(),
                        name=str(item.get("name") or item["content_sid"]).strip(),
                        body=tpl_body,
                        variables=[str(v) for v in vars_found],
                        approval_status=str(item.get("approval_status") or "approved").lower(),
                        category=str(item.get("category") or "utility").lower(),
                        language=str(item.get("language") or "en"),
                    )
                )
            return normalized_list

        if self._is_mock_mode():
            return []

        try:
            response = requests.get(
                f"{_TWILIO_CONTENT_API_BASE}/ContentAndApprovals",
                auth=(sid, token),
                params={"PageSize": 100},
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ProviderDispatchError(f"Failed to fetch Twilio Content templates: {exc}", is_transient=True) from exc

        if response.status_code != 200:
            raise ProviderDispatchError(
                f"Twilio Content API error ({response.status_code}): {response.text[:500]}",
                is_transient=response.status_code >= 500,
                status_code=response.status_code,
            )

        data = response.json()
        contents = data.get("contents") or []
        results: list[NormalizedTemplate] = []

        for item in contents:
            if not isinstance(item, dict):
                continue
            content_sid = str(item.get("sid") or "").strip()
            if not content_sid:
                continue
            name = str(item.get("friendly_name") or content_sid).strip()
            language = str(item.get("language") or "en").strip()

            types_block = item.get("types") or {}
            tpl_body = ""
            for type_key in (
                "twilio/text",
                "twilio/quick-reply",
                "twilio/call-to-action",
                "whatsapp/card",
                "twilio/card",
            ):
                if isinstance(types_block.get(type_key), dict):
                    tpl_body = str(types_block[type_key].get("body") or "").strip()
                    if tpl_body:
                        break

            raw_vars = item.get("variables")
            if isinstance(raw_vars, dict):
                variables = list(map(str, raw_vars.keys()))
            elif isinstance(raw_vars, list):
                variables = [str(v) for v in raw_vars]
            else:
                variables = _VAR_PLACEHOLDER_RE.findall(tpl_body)

            approval_requests = item.get("approval_requests") or {}
            wa_approval = approval_requests.get("whatsapp") if isinstance(approval_requests, dict) else {}
            if not isinstance(wa_approval, dict):
                wa_approval = {}

            raw_approval_status = str(
                wa_approval.get("status")
                or approval_requests.get("status")
                or "pending"
            ).strip().lower()
            if raw_approval_status in ("approved", "active"):
                approval_status = "approved"
            elif raw_approval_status in ("rejected", "failed"):
                approval_status = "rejected"
            else:
                approval_status = "pending"

            raw_category = str(wa_approval.get("category") or "utility").strip().lower()
            if raw_category not in ("marketing", "utility", "authentication", "service"):
                raw_category = "utility"

            results.append(
                NormalizedTemplate(
                    provider=self.provider_name,
                    content_sid=content_sid,
                    name=name,
                    body=tpl_body,
                    variables=variables,
                    approval_status=approval_status,
                    category=raw_category,
                    language=language,
                )
            )

        return results

    def test_connection(self) -> tuple[bool, str]:
        return self._check_account_credentials()
