import base64
from collections.abc import Mapping
from datetime import datetime, timezone
from email.utils import parseaddr
import hashlib
import hmac
import json
import re
from typing import Any
import uuid
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
import requests
from integrations.providers.base import (
    EmailProvider,
    NormalizedInboundMessage,
    NormalizedStatusEvent,
    ProviderDispatchError,
    ProviderSendResult,
)

_SENDGRID_API_BASE = "https://api.sendgrid.com/v3"
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _get_header_case_insensitive(headers: Mapping[str, str], target: str) -> str:
    target_lower = target.lower()
    for key, val in headers.items():
        if key.lower() == target_lower:
            return str(val)
    return ""


def _strip_html(html: str) -> str:
    if not html:
        return ""
    return _HTML_TAG_RE.sub(" ", html).strip()


def _extract_message_id_header(headers_raw: str) -> str:
    if not headers_raw:
        return ""
    for line in headers_raw.splitlines():
        if line.lower().startswith("message-id:"):
            val = line.split(":", 1)[1].strip().strip("<>")
            if val:
                return val
    return ""


class SendGridEmailProvider(EmailProvider):
    provider_name = "sendgrid"

    def _api_key(self) -> str:
        return str(self.credentials.get("api_key") or "").strip()

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
        api_key = self._api_key()
        sender = (from_email or self.sender_identity or self.credentials.get("from_email") or "").strip()
        display_name = (from_name or self.sender_name or self.credentials.get("from_name") or "").strip()
        if not api_key:
            raise ProviderDispatchError("SendGrid API key is not configured.", is_transient=False)
        if not sender:
            raise ProviderDispatchError("SendGrid sender email identity is not configured.", is_transient=False)

        plain_text = text_body or _strip_html(html_body) or subject
        personalization: dict[str, Any] = {"to": [{"email": to_email}]}
        if custom_args:
            personalization["custom_args"] = {str(k): str(v) for k, v in custom_args.items()}

        from_block: dict[str, str] = {"email": sender}
        if display_name:
            from_block["name"] = display_name

        content_list = [{"type": "text/plain", "value": plain_text}]
        if html_body:
            content_list.append({"type": "text/html", "value": html_body})

        payload: dict[str, Any] = {
            "personalizations": [personalization],
            "from": from_block,
            "subject": subject,
            "content": content_list,
        }
        if reply_to:
            payload["reply_to"] = {"email": reply_to}
        if headers:
            payload["headers"] = dict(headers)

        try:
            response = requests.post(
                f"{_SENDGRID_API_BASE}/mail/send",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ProviderDispatchError(str(exc), is_transient=True) from exc

        if response.status_code in (200, 201, 202):
            msg_id = (
                response.headers.get("X-Message-Id")
                or response.headers.get("x-message-id")
                or f"sg_{uuid.uuid4().hex[:20]}"
            )
            return ProviderSendResult(
                provider=self.provider_name,
                provider_message_id=str(msg_id).strip(),
                status="sent",
                raw_response={"status_code": response.status_code},
            )

        is_transient = response.status_code in (408, 429, 500, 502, 503, 504)
        raise ProviderDispatchError(
            f"SendGrid error ({response.status_code}): {response.text[:500]}",
            is_transient=is_transient,
            status_code=response.status_code,
        )

    def parse_status(self, payload: Any) -> list[NormalizedStatusEvent]:
        events_raw: list[dict[str, Any]] = []
        if isinstance(payload, (bytes, str)):
            try:
                parsed = json.loads(payload)
            except Exception:
                return []
        else:
            parsed = payload

        if isinstance(parsed, list):
            events_raw = [item for item in parsed if isinstance(item, dict)]
        elif isinstance(parsed, dict):
            if isinstance(parsed.get("events"), list):
                events_raw = [item for item in parsed["events"] if isinstance(item, dict)]
            else:
                events_raw = [parsed]

        status_map = {
            "processed": "sent",
            "deferred": "sent",
            "delivered": "delivered",
            "open": "read",
            "bounce": "bounced",
            "blocked": "bounced",
            "dropped": "failed",
            "unsubscribe": "unsubscribed",
            "group_unsubscribe": "unsubscribed",
            "spamreport": "unsubscribed",
        }

        results: list[NormalizedStatusEvent] = []
        for item in events_raw:
            raw_event = str(item.get("event") or "").strip().lower()
            mapped_status = status_map.get(raw_event)
            if not mapped_status:
                continue

            raw_sg_id = str(item.get("sg_message_id") or item.get("message_id") or "").strip()
            base_msg_id = raw_sg_id.split(".")[0] if raw_sg_id else ""
            recipient = str(item.get("email") or "").strip().lower()
            reason = str(item.get("reason") or item.get("response") or "").strip()
            ts = item.get("timestamp")
            occurred_at = None
            if isinstance(ts, (int, float)):
                occurred_at = datetime.fromtimestamp(ts, tz=timezone.utc)

            results.append(
                NormalizedStatusEvent(
                    provider=self.provider_name,
                    channel="email",
                    provider_message_id=base_msg_id or raw_sg_id,
                    status=mapped_status,
                    error=reason,
                    recipient=recipient,
                    occurred_at=occurred_at,
                    raw_payload=dict(item),
                )
            )
        return results

    def parse_inbound(self, payload: Mapping[str, Any]) -> list[NormalizedInboundMessage]:
        raw_from = str(payload.get("from") or payload.get("sender") or "").strip()
        sender_name, sender_email = parseaddr(raw_from)
        sender_email = (sender_email or raw_from).strip().lower()
        if not sender_email:
            return []

        raw_to = str(payload.get("to") or payload.get("recipient") or "").strip()
        _, recipient_email = parseaddr(raw_to)
        recipient_email = (recipient_email or raw_to or self.sender_identity).strip().lower()

        subject = str(payload.get("subject") or "").strip()
        text_body = str(payload.get("text") or payload.get("body") or "").strip()
        html_body = str(payload.get("html") or "").strip()
        body = text_body or _strip_html(html_body)

        headers_raw = str(payload.get("headers") or "")
        msg_id = (
            str(payload.get("Message-Id") or payload.get("message_id") or "").strip().strip("<>")
            or _extract_message_id_header(headers_raw)
        )
        if not msg_id:
            digest = hashlib.sha256(
                f"{sender_email}:{recipient_email}:{subject}:{body}".encode("utf-8")
            ).hexdigest()[:24]
            msg_id = f"sgin_{digest}"

        return [
            NormalizedInboundMessage(
                provider=self.provider_name,
                channel="email",
                provider_message_id=msg_id,
                sender=sender_email,
                recipient=recipient_email,
                body=body,
                subject=subject,
                sender_name=sender_name.strip(),
                raw_payload=dict(payload),
            )
        ]

    def verify_signature(
        self,
        *,
        payload: bytes | str | Mapping[str, Any],
        headers: Mapping[str, str],
        url: str = "",
    ) -> bool:
        signature_b64 = _get_header_case_insensitive(
            headers, "X-Twilio-Email-Event-Webhook-Signature"
        )
        timestamp = _get_header_case_insensitive(
            headers, "X-Twilio-Email-Event-Webhook-Timestamp"
        )
        public_key_raw = str(
            self.credentials.get("webhook_public_key")
            or self.credentials.get("verification_key")
            or ""
        ).strip()

        if isinstance(payload, bytes):
            payload_bytes = payload
        elif isinstance(payload, str):
            payload_bytes = payload.encode("utf-8")
        else:
            payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        if public_key_raw:
            if not signature_b64 or not timestamp:
                return False
            try:
                signed_payload = timestamp.encode("utf-8") + payload_bytes
                sig_bytes = base64.b64decode(signature_b64)
                if "BEGIN PUBLIC KEY" in public_key_raw:
                    pub_key = serialization.load_pem_public_key(public_key_raw.encode("utf-8"))
                else:
                    der_bytes = base64.b64decode(public_key_raw)
                    pub_key = serialization.load_der_public_key(der_bytes)
                if not isinstance(pub_key, ec.EllipticCurvePublicKey):
                    return False
                pub_key.verify(sig_bytes, signed_payload, ec.ECDSA(hashes.SHA256()))
                return True
            except (InvalidSignature, ValueError, TypeError):
                return False

        signing_secret = str(self.credentials.get("webhook_signing_secret") or "").strip()
        if signing_secret:
            if not signature_b64 or not timestamp:
                return False
            signed_payload = timestamp.encode("utf-8") + payload_bytes
            expected = base64.b64encode(
                hmac.new(
                    signing_secret.encode("utf-8"),
                    signed_payload,
                    hashlib.sha256,
                ).digest()
            ).decode("utf-8")
            return hmac.compare_digest(expected, signature_b64)

        return False

    def test_connection(self) -> tuple[bool, str]:
        api_key = self._api_key()
        if not api_key:
            return False, "Missing SendGrid API key."
        if not self.sender_identity:
            return False, "Missing sender email address."
        try:
            response = requests.get(
                f"{_SENDGRID_API_BASE}/scopes",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            if response.status_code == 200:
                return True, "SendGrid API key verified."
            return False, f"SendGrid verification failed ({response.status_code})."
        except requests.RequestException as exc:
            return False, f"SendGrid connection check error: {exc}"
