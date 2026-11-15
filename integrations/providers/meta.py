from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from typing import Any
from urllib.parse import urlencode
from django.conf import settings
from django.utils.dateparse import parse_datetime
import requests
from integrations.providers.base import (
    MetaLeadAdsProvider,
    NormalizedMetaLead,
    NormalizedMetaLeadForm,
    NormalizedMetaPage,
    NormalizedMetaQuestion,
    NormalizedMetaToken,
    NormalizedMetaWebhookChange,
    ProviderDispatchError,
)

DEFAULT_META_SCOPES = (
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_ads",
    "pages_manage_metadata",
    "leads_retrieval",
    "ads_read",
    "business_management",
)


def _get_header_case_insensitive(headers: Mapping[str, str], target: str) -> str:
    target_lower = target.lower()
    for key, val in headers.items():
        if key.lower() == target_lower:
            return str(val)
    return ""


def _parse_questions(raw_questions: Any) -> list[NormalizedMetaQuestion]:
    if not isinstance(raw_questions, list):
        return []
    questions: list[NormalizedMetaQuestion] = []
    for item in raw_questions:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or item.get("id") or "").strip()
        if not key:
            continue
        label = str(item.get("label") or key).strip()
        q_type = str(item.get("type") or "CUSTOM").strip()
        questions.append(
            NormalizedMetaQuestion(
                key=key,
                label=label,
                field_type=q_type,
            )
        )
    return questions


class MetaGraphProvider(MetaLeadAdsProvider):
    provider_name = "meta"

    def __init__(
        self,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
        verify_token: str | None = None,
        graph_version: str | None = None,
        redirect_uri: str | None = None,
    ):
        self.app_id = str(
            app_id if app_id is not None else getattr(settings, "META_APP_ID", "")
        ).strip()
        self.app_secret = str(
            app_secret if app_secret is not None else getattr(settings, "META_APP_SECRET", "")
        ).strip()
        self.verify_token = str(
            verify_token
            if verify_token is not None
            else getattr(settings, "META_WEBHOOK_VERIFY_TOKEN", "")
        ).strip()
        self.graph_version = str(
            graph_version
            if graph_version is not None
            else getattr(settings, "META_GRAPH_VERSION", "v20.0")
        ).strip() or "v20.0"
        self.redirect_uri = str(
            redirect_uri
            if redirect_uri is not None
            else getattr(settings, "META_OAUTH_REDIRECT_URI", "")
        ).strip()

    @property
    def graph_base_url(self) -> str:
        return f"https://graph.facebook.com/{self.graph_version}"

    def compute_appsecret_proof(self, access_token: str) -> str:
        if not self.app_secret or not access_token:
            return ""
        return hmac.new(
            self.app_secret.encode("utf-8"),
            access_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _is_mock_token(self, token_or_code: str) -> bool:
        val = (token_or_code or "").strip()
        return val.startswith(("mock_", "test_"))

    def _request_graph(
        self,
        method: str,
        path: str,
        *,
        access_token: str = "",
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query_params = dict(params or {})
        if access_token:
            query_params["access_token"] = access_token
            proof = self.compute_appsecret_proof(access_token)
            if proof:
                query_params["appsecret_proof"] = proof

        url = f"{self.graph_base_url}/{path.lstrip('/')}"
        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                params=query_params,
                data=data,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ProviderDispatchError(
                f"Meta Graph API request failed: {exc}",
                is_transient=True,
            ) from exc

        try:
            body_json = response.json() if response.content else {}
        except ValueError:
            body_json = {}

        if response.status_code in (200, 201):
            return body_json if isinstance(body_json, dict) else {"data": body_json}

        err_obj = body_json.get("error") if isinstance(body_json, dict) else None
        if isinstance(err_obj, dict):
            err_msg = str(err_obj.get("message") or response.text[:300])
            err_code = err_obj.get("code")
        else:
            err_msg = response.text[:300] or f"HTTP {response.status_code}"
            err_code = None

        is_transient = (
            response.status_code in (408, 429, 500, 502, 503, 504)
            or err_code in (1, 2, 4, 17, 341)
        )
        raise ProviderDispatchError(
            f"Meta Graph API error ({response.status_code}): {err_msg}",
            is_transient=is_transient,
            status_code=response.status_code,
        )

    def build_oauth_url(
        self,
        *,
        state: str,
        redirect_uri: str = "",
        scopes: list[str] | None = None,
    ) -> str:
        target_redirect = (redirect_uri or self.redirect_uri).strip()
        scope_list = list(scopes) if scopes else list(DEFAULT_META_SCOPES)
        query = urlencode(
            {
                "client_id": self.app_id,
                "redirect_uri": target_redirect,
                "state": state,
                "response_type": "code",
                "scope": ",".join(scope_list),
            }
        )
        return f"https://www.facebook.com/{self.graph_version}/dialog/oauth?{query}"

    def exchange_code_for_token(
        self,
        *,
        code: str,
        redirect_uri: str = "",
    ) -> NormalizedMetaToken:
        clean_code = (code or "").strip()
        if not clean_code:
            raise ProviderDispatchError("OAuth authorization code is required.", is_transient=False)

        if self._is_mock_token(clean_code):
            now = datetime.now(timezone.utc)
            return NormalizedMetaToken(
                access_token=f"mock_long_lived_{clean_code}",
                expires_at=now + timedelta(days=60),
                meta_user_id="mock_meta_user_1",
                meta_user_name="Meta Business User",
                is_valid=True,
                scopes=list(DEFAULT_META_SCOPES),
            )

        if not self.app_id or not self.app_secret:
            raise ProviderDispatchError(
                "META_APP_ID and META_APP_SECRET must be configured.",
                is_transient=False,
            )

        target_redirect = (redirect_uri or self.redirect_uri).strip()
        short_res = self._request_graph(
            "GET",
            "oauth/access_token",
            params={
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "redirect_uri": target_redirect,
                "code": clean_code,
            },
        )
        short_token = str(short_res.get("access_token") or "").strip()
        if not short_token:
            raise ProviderDispatchError(
                "Meta did not return a short-lived access token.",
                is_transient=False,
            )

        long_res = self._request_graph(
            "GET",
            "oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "fb_exchange_token": short_token,
            },
        )
        long_token = str(long_res.get("access_token") or short_token).strip()
        expires_in = long_res.get("expires_in") or short_res.get("expires_in") or (60 * 86400)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

        me_res = self._request_graph(
            "GET",
            "me",
            access_token=long_token,
            params={"fields": "id,name"},
        )
        return NormalizedMetaToken(
            access_token=long_token,
            expires_at=expires_at,
            meta_user_id=str(me_res.get("id") or ""),
            meta_user_name=str(me_res.get("name") or ""),
            is_valid=True,
        )

    def debug_token(self, access_token: str) -> NormalizedMetaToken:
        clean_token = (access_token or "").strip()
        if not clean_token:
            return NormalizedMetaToken(access_token="", is_valid=False)

        if self._is_mock_token(clean_token):
            if "expired" in clean_token:
                return NormalizedMetaToken(
                    access_token=clean_token,
                    expires_at=datetime.now(timezone.utc) - timedelta(days=1),
                    is_valid=False,
                )
            return NormalizedMetaToken(
                access_token=clean_token,
                expires_at=datetime.now(timezone.utc) + timedelta(days=45),
                meta_user_id="mock_meta_user_1",
                is_valid=True,
            )

        app_token = f"{self.app_id}|{self.app_secret}" if (self.app_id and self.app_secret) else clean_token
        res = self._request_graph(
            "GET",
            "debug_token",
            access_token=clean_token,
            params={
                "input_token": clean_token,
                "access_token": app_token,
            },
        )
        data = res.get("data") if isinstance(res.get("data"), dict) else res
        is_valid = bool(data.get("is_valid", False))
        exp_ts = data.get("expires_at") or data.get("data_access_expires_at")
        expires_at = (
            datetime.fromtimestamp(int(exp_ts), tz=timezone.utc)
            if isinstance(exp_ts, (int, float)) and int(exp_ts) > 0
            else None
        )
        scopes = data.get("scopes") if isinstance(data.get("scopes"), list) else []
        return NormalizedMetaToken(
            access_token=clean_token,
            expires_at=expires_at,
            meta_user_id=str(data.get("user_id") or ""),
            is_valid=is_valid,
            scopes=[str(s) for s in scopes],
        )

    def list_pages(self, user_access_token: str) -> list[NormalizedMetaPage]:
        clean_token = (user_access_token or "").strip()
        if not clean_token:
            raise ProviderDispatchError("Missing Meta user access token.", is_transient=False)

        if self._is_mock_token(clean_token):
            return [
                NormalizedMetaPage(
                    page_id="mock_page_1001",
                    name="Apex Fitness Downtown",
                    access_token="mock_page_token_1001",
                    instagram_business_account_id="mock_ig_2001",
                )
            ]

        res = self._request_graph(
            "GET",
            "me/accounts",
            access_token=clean_token,
            params={
                "fields": "id,name,access_token,instagram_business_account{id,username}",
                "limit": 100,
            },
        )
        items = res.get("data") if isinstance(res.get("data"), list) else []
        pages: list[NormalizedMetaPage] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            page_id = str(item.get("id") or "").strip()
            page_token = str(item.get("access_token") or "").strip()
            if not page_id or not page_token:
                continue
            ig_block = item.get("instagram_business_account")
            ig_id = str(ig_block.get("id") or "") if isinstance(ig_block, dict) else ""
            pages.append(
                NormalizedMetaPage(
                    page_id=page_id,
                    name=str(item.get("name") or page_id).strip(),
                    access_token=page_token,
                    instagram_business_account_id=ig_id,
                )
            )
        return pages

    def subscribe_page(self, page_id: str, page_access_token: str) -> bool:
        if self._is_mock_token(page_access_token):
            return True
        res = self._request_graph(
            "POST",
            f"{page_id}/subscribed_apps",
            access_token=page_access_token,
            data={"subscribed_fields": "leadgen"},
        )
        return bool(res.get("success", True))

    def unsubscribe_page(self, page_id: str, page_access_token: str) -> bool:
        if not page_access_token or self._is_mock_token(page_access_token):
            return True
        res = self._request_graph(
            "DELETE",
            f"{page_id}/subscribed_apps",
            access_token=page_access_token,
        )
        return bool(res.get("success", True))

    def list_lead_forms(
        self,
        page_id: str,
        page_access_token: str,
        has_instagram: bool = False,
    ) -> list[NormalizedMetaLeadForm]:
        if self._is_mock_token(page_access_token):
            return [
                NormalizedMetaLeadForm(
                    form_id=f"mock_form_{page_id}",
                    page_id=page_id,
                    name="28-Day Challenge — Lead Form",
                    platform="instagram" if has_instagram else "facebook",
                    status="ACTIVE",
                    questions=[
                        NormalizedMetaQuestion(key="full_name", label="Full Name", field_type="FULL_NAME"),
                        NormalizedMetaQuestion(key="email", label="Email", field_type="EMAIL"),
                        NormalizedMetaQuestion(key="phone_number", label="Phone Number", field_type="PHONE"),
                        NormalizedMetaQuestion(key="city", label="City", field_type="CITY"),
                    ],
                )
            ]

        res = self._request_graph(
            "GET",
            f"{page_id}/leadgen_forms",
            access_token=page_access_token,
            params={
                "fields": "id,name,status,questions,locale",
                "limit": 100,
            },
        )
        items = res.get("data") if isinstance(res.get("data"), list) else []
        forms: list[NormalizedMetaLeadForm] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            form_id = str(item.get("id") or "").strip()
            if not form_id:
                continue
            raw_platform = str(item.get("platform") or "").strip().lower()
            if raw_platform in ("instagram", "ig"):
                platform = "instagram"
            else:
                platform = "facebook"

            forms.append(
                NormalizedMetaLeadForm(
                    form_id=form_id,
                    page_id=page_id,
                    name=str(item.get("name") or form_id).strip(),
                    platform=platform,
                    status=str(item.get("status") or "ACTIVE").strip(),
                    questions=_parse_questions(item.get("questions")),
                )
            )
        return forms

    def fetch_form_questions(
        self,
        form_id: str,
        page_access_token: str,
    ) -> list[NormalizedMetaQuestion]:
        if self._is_mock_token(page_access_token):
            return [
                NormalizedMetaQuestion(key="full_name", label="Full Name", field_type="FULL_NAME"),
                NormalizedMetaQuestion(key="email", label="Email", field_type="EMAIL"),
                NormalizedMetaQuestion(key="phone_number", label="Phone Number", field_type="PHONE"),
                NormalizedMetaQuestion(key="city", label="City", field_type="CITY"),
            ]

        res = self._request_graph(
            "GET",
            form_id,
            access_token=page_access_token,
            params={"fields": "id,name,questions"},
        )
        return _parse_questions(res.get("questions"))

    def fetch_lead(
        self,
        leadgen_id: str,
        access_token: str,
    ) -> NormalizedMetaLead:
        if self._is_mock_token(access_token) or leadgen_id.startswith("mock_"):
            return NormalizedMetaLead(
                leadgen_id=leadgen_id,
                form_id="mock_form_mock_page_1001",
                created_time=datetime.now(timezone.utc),
                platform="facebook",
                is_organic=False,
                ad_id="ad_1",
                ad_name="Promo Video Ad",
                adset_id="adset_1",
                adset_name="Local Homeowners",
                campaign_id="camp_1",
                campaign_name="Q4 Transformation Promo",
                field_values={
                    "full_name": "Sarah Jenkins",
                    "email": "sarah.jenkins@example.com",
                    "phone_number": "+13125550194",
                    "city": "Chicago",
                },
                raw_answers=[
                    {"name": "full_name", "values": ["Sarah Jenkins"]},
                    {"name": "email", "values": ["sarah.jenkins@example.com"]},
                    {"name": "phone_number", "values": ["+13125550194"]},
                    {"name": "city", "values": ["Chicago"]},
                ],
            )

        res = self._request_graph(
            "GET",
            leadgen_id,
            access_token=access_token,
            params={
                "fields": (
                    "id,created_time,form_id,ad_id,ad_name,adset_id,adset_name,"
                    "campaign_id,campaign_name,platform,is_organic,field_data"
                )
            },
        )
        raw_field_data = res.get("field_data")
        field_values: dict[str, Any] = {}
        raw_answers: list[dict[str, Any]] = []
        if isinstance(raw_field_data, list):
            for entry in raw_field_data:
                if not isinstance(entry, dict):
                    continue
                key = str(entry.get("name") or "").strip()
                vals = entry.get("values")
                if not key:
                    continue
                raw_answers.append({"name": key, "values": vals if isinstance(vals, list) else []})
                if isinstance(vals, list) and vals:
                    field_values[key] = vals[0] if len(vals) == 1 else ", ".join(str(v) for v in vals)
                elif vals is not None:
                    field_values[key] = str(vals)

        raw_created = str(res.get("created_time") or "").strip()
        created_dt = parse_datetime(raw_created) if raw_created else None
        if created_dt is not None and created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)

        is_organic = bool(res.get("is_organic", False))
        raw_platform = str(res.get("platform") or "").strip().lower()
        platform = "instagram" if raw_platform in ("ig", "instagram") else "facebook"

        campaign_name = "" if is_organic else str(res.get("campaign_name") or "").strip()
        adset_name = "" if is_organic else str(res.get("adset_name") or "").strip()
        ad_name = "" if is_organic else str(res.get("ad_name") or "").strip()

        return NormalizedMetaLead(
            leadgen_id=str(res.get("id") or leadgen_id).strip(),
            form_id=str(res.get("form_id") or "").strip(),
            created_time=created_dt,
            platform=platform,
            is_organic=is_organic,
            ad_id="" if is_organic else str(res.get("ad_id") or "").strip(),
            ad_name=ad_name,
            adset_id="" if is_organic else str(res.get("adset_id") or "").strip(),
            adset_name=adset_name,
            campaign_id="" if is_organic else str(res.get("campaign_id") or "").strip(),
            campaign_name=campaign_name,
            field_values=field_values,
            raw_answers=raw_answers,
        )

    def verify_webhook_challenge(
        self,
        mode: str,
        token: str,
        challenge: str,
    ) -> str | None:
        if (mode or "").strip() != "subscribe":
            return None
        if not self.verify_token or not token:
            return None
        if hmac.compare_digest(self.verify_token, token.strip()):
            return str(challenge or "")
        return None

    def verify_signature(
        self,
        *,
        payload: bytes | str,
        headers: Mapping[str, str],
        url: str = "",
    ) -> bool:
        if not self.app_secret:
            return False
        sig_header = _get_header_case_insensitive(headers, "X-Hub-Signature-256").strip()
        if not sig_header.startswith("sha256="):
            return False
        provided_hex = sig_header.split("=", 1)[1].strip()
        raw_bytes = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
        expected_hex = hmac.new(
            self.app_secret.encode("utf-8"),
            raw_bytes,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected_hex, provided_hex)

    def parse_webhook_changes(
        self,
        payload: Mapping[str, Any],
    ) -> list[NormalizedMetaWebhookChange]:
        if not isinstance(payload, Mapping):
            return []
        entries = payload.get("entry")
        if not isinstance(entries, list):
            return []

        results: list[NormalizedMetaWebhookChange] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_page_id = str(entry.get("id") or "").strip()
            changes = entry.get("changes")
            if not isinstance(changes, list):
                continue
            for change in changes:
                if not isinstance(change, dict):
                    continue
                if str(change.get("field") or "").strip().lower() != "leadgen":
                    continue
                val = change.get("value")
                if not isinstance(val, dict):
                    continue
                page_id = str(val.get("page_id") or entry_page_id).strip()
                leadgen_id = str(val.get("leadgen_id") or val.get("id") or "").strip()
                if not page_id or not leadgen_id:
                    continue
                form_id = str(val.get("form_id") or "").strip()
                ad_id = str(val.get("ad_id") or "").strip()
                adgroup_id = str(val.get("adgroup_id") or "").strip()
                raw_ts = val.get("created_time")
                created_ts = int(raw_ts) if isinstance(raw_ts, (int, float)) else None
                results.append(
                    NormalizedMetaWebhookChange(
                        page_id=page_id,
                        leadgen_id=leadgen_id,
                        form_id=form_id,
                        ad_id=ad_id,
                        adgroup_id=adgroup_id,
                        created_time=created_ts,
                        raw_change=dict(val),
                    )
                )
        return results
