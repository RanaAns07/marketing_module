from datetime import datetime, timedelta, timezone
from typing import Any
import uuid
from integrations.providers.base import (
    NormalizedMetaLead,
    NormalizedMetaLeadForm,
    NormalizedMetaPage,
    NormalizedMetaQuestion,
    NormalizedMetaToken,
    ProviderSendResult,
)
from integrations.providers.meta import MetaGraphProvider
from integrations.providers.sendgrid import SendGridEmailProvider
from integrations.providers.twilio import TwilioSMSProvider, TwilioWhatsAppProvider


class FakeSendGridEmailProvider(SendGridEmailProvider):
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
        mock_id = f"sg_{uuid.uuid4().hex[:20]}"
        return ProviderSendResult(
            provider=self.provider_name,
            provider_message_id=mock_id,
            status="sent",
            raw_response={"mock": True, "x_message_id": mock_id},
        )

    def test_connection(self) -> tuple[bool, str]:
        return True, "SendGrid connection verified (fake mode)."


class FakeTwilioSMSProvider(TwilioSMSProvider):
    def send(
        self,
        *,
        to_phone: str,
        body: str,
        from_phone: str | None = None,
        status_callback_url: str = "",
    ) -> ProviderSendResult:
        mock_sid = f"SM{uuid.uuid4().hex[:32]}"
        return ProviderSendResult(
            provider=self.provider_name,
            provider_message_id=mock_sid,
            status="sent",
            raw_response={"mock": True, "sid": mock_sid},
        )

    def _check_account_credentials(self) -> tuple[bool, str]:
        return True, "Twilio connection verified (fake mode)."


class FakeTwilioWhatsAppProvider(TwilioWhatsAppProvider):
    def send(
        self,
        *,
        to_phone: str,
        body: str = "",
        from_phone: str | None = None,
        status_callback_url: str = "",
        content_sid: str = "",
        content_variables: dict[str, Any] | None = None,
    ) -> ProviderSendResult:
        mock_sid = f"WA{uuid.uuid4().hex[:32]}"
        return ProviderSendResult(
            provider=self.provider_name,
            provider_message_id=mock_sid,
            status="sent",
            raw_response={"mock": True, "sid": mock_sid, "content_sid": content_sid},
        )

    def _check_account_credentials(self) -> tuple[bool, str]:
        return True, "Twilio WhatsApp connection verified (fake mode)."

    def fetch_remote_templates(self) -> list[Any]:
        return []


class FakeMetaGraphProvider(MetaGraphProvider):
    def exchange_code_for_long_lived_token(
        self,
        code: str,
        redirect_uri: str = "",
    ) -> NormalizedMetaToken:
        now = datetime.now(timezone.utc)
        return NormalizedMetaToken(
            access_token=f"mock_long_lived_{code}",
            expires_at=now + timedelta(days=60),
            meta_user_id="mock_meta_user_1",
            meta_user_name="Meta Business User",
            is_valid=True,
            scopes=["leads_retrieval", "pages_manage_ads", "pages_show_list"],
        )

    def debug_token(self, access_token: str) -> NormalizedMetaToken:
        return NormalizedMetaToken(
            access_token=access_token,
            expires_at=datetime.now(timezone.utc) + timedelta(days=45),
            meta_user_id="mock_meta_user_1",
            is_valid=True,
        )

    def get_subscribed_pages(self, user_access_token: str) -> list[NormalizedMetaPage]:
        return [
            NormalizedMetaPage(
                page_id="mock_page_1001",
                name="Apex Fitness Downtown",
                access_token="mock_page_token_1001",
                instagram_business_account_id="mock_ig_2001",
            )
        ]

    def subscribe_page(self, page_id: str, page_access_token: str) -> bool:
        return True

    def unsubscribe_page(self, page_id: str, page_access_token: str) -> bool:
        return True

    def list_lead_forms(
        self,
        page_id: str,
        page_access_token: str,
        has_instagram: bool = False,
    ) -> list[NormalizedMetaLeadForm]:
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

    def fetch_form_questions(
        self,
        form_id: str,
        page_access_token: str,
    ) -> list[NormalizedMetaQuestion]:
        return [
            NormalizedMetaQuestion(key="full_name", label="Full Name", field_type="FULL_NAME"),
            NormalizedMetaQuestion(key="email", label="Email", field_type="EMAIL"),
            NormalizedMetaQuestion(key="phone_number", label="Phone Number", field_type="PHONE"),
            NormalizedMetaQuestion(key="city", label="City", field_type="CITY"),
        ]

    def fetch_lead(
        self,
        leadgen_id: str,
        access_token: str,
    ) -> NormalizedMetaLead:
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


FAKE_PROVIDER_CLASSES = {
    ("email", "sendgrid"): FakeSendGridEmailProvider,
    ("sms", "twilio"): FakeTwilioSMSProvider,
    ("whatsapp", "twilio"): FakeTwilioWhatsAppProvider,
}
