import uuid
from unittest.mock import patch
from django.db import connection
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient
from accounts.models import Membership, User
from contacts.models import Contact
from core.exceptions import ApplicationError
from core.models import Tenant
from core.context import clear_current_tenant, set_current_tenant
from landing_pages.models import (
    LandingPage,
    LandingPageForm,
    LandingPageSection,
    LandingPageTemplate,
)
from landing_pages.selectors import (
    get_landing_pages_queryset,
    get_public_landing_page_by_slug_or_id,
)
from landing_pages.serializers import LandingPageListSerializer
from landing_pages.throttling import LandingPageFormSubmissionThrottle
from packages.models import Package
from packages.services import upsert_package_from_external


class FitverxAndLandingPagesAuditTests(TestCase):
    def setUp(self):
        clear_current_tenant()
        self.tenant_a = Tenant.objects.create(
            name="Gym Tenant Alpha",
            slug=f"gym-alpha-{uuid.uuid4().hex[:6]}",
        )
        self.tenant_b = Tenant.objects.create(
            name="Gym Tenant Beta",
            slug=f"gym-beta-{uuid.uuid4().hex[:6]}",
        )
        self.user_a = User.objects.create_user(
            email=f"user_a_{uuid.uuid4().hex[:6]}@test.com",
            password="Password123!",
        )
        self.user_b = User.objects.create_user(
            email=f"user_b_{uuid.uuid4().hex[:6]}@test.com",
            password="Password123!",
        )
        set_current_tenant(self.tenant_a)
        Membership.objects.create(
            tenant=self.tenant_a,
            user=self.user_a,
            role=Membership.Role.OWNER,
        )
        set_current_tenant(self.tenant_b)
        Membership.objects.create(
            tenant=self.tenant_b,
            user=self.user_b,
            role=Membership.Role.OWNER,
        )
        clear_current_tenant()
        self.client_a = APIClient()
        self.client_a.force_authenticate(user=self.user_a)
        self.client_a.credentials(HTTP_X_TENANT=str(self.tenant_a.id))
        self.client_b = APIClient()
        self.client_b.force_authenticate(user=self.user_b)
        self.client_b.credentials(HTTP_X_TENANT=str(self.tenant_b.id))
        self.public_client = APIClient()

    def tearDown(self):
        clear_current_tenant()

    def test_finding_01_landing_page_submission_throttle_rate(self):
        throttle = LandingPageFormSubmissionThrottle()
        rate = throttle.get_rate()
        self.assertIsNotNone(rate)
        self.assertTrue(isinstance(rate, str))
        self.assertIn("/", rate)

    def test_finding_02_cross_tenant_slug_collision_resolution(self):
        page_a = LandingPage.unscoped_objects.create(
            tenant=self.tenant_a,
            title="Alpha Summer Pass",
            slug="summer-pass",
            status=LandingPage.Status.PUBLISHED,
        )
        page_b = LandingPage.unscoped_objects.create(
            tenant=self.tenant_b,
            title="Beta Summer Pass",
            slug="summer-pass",
            status=LandingPage.Status.PUBLISHED,
        )

        resolved_a_by_id = get_public_landing_page_by_slug_or_id(
            "summer-pass", tenant_identifier=str(self.tenant_a.id)
        )
        self.assertEqual(resolved_a_by_id.id, page_a.id)

        resolved_b_by_slug = get_public_landing_page_by_slug_or_id(
            "summer-pass", tenant_identifier=self.tenant_b.slug
        )
        self.assertEqual(resolved_b_by_slug.id, page_b.id)

        with self.assertRaises(ApplicationError):
            get_public_landing_page_by_slug_or_id("summer-pass")

        url_a = f"/api/v1/public/pages/{self.tenant_a.slug}/summer-pass/"
        resp_a = self.public_client.get(url_a)
        self.assertEqual(resp_a.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_a.data["title"], "Alpha Summer Pass")

        url_b = f"/api/v1/public/pages/{self.tenant_b.slug}/summer-pass/"
        resp_b = self.public_client.get(url_b)
        self.assertEqual(resp_b.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_b.data["title"], "Beta Summer Pass")

    def test_finding_03_template_tenant_isolation(self):
        tmpl_a = LandingPageTemplate.objects.create(
            tenant=self.tenant_a,
            name="Alpha Private Template",
            is_system=False,
            is_active=True,
        )
        sys_tmpl = LandingPageTemplate.objects.create(
            tenant=None,
            name="System Template",
            is_system=True,
            is_active=True,
        )

        set_current_tenant(self.tenant_b)
        payload_cross_tenant = {
            "title": "Beta Page Illegal",
            "slug": "beta-illegal",
            "template_id": str(tmpl_a.id),
        }
        resp_cross = self.client_b.post(
            "/api/v1/landing-pages/",
            data=payload_cross_tenant,
            format="json",
        )
        self.assertEqual(resp_cross.status_code, status.HTTP_404_NOT_FOUND)

        payload_sys = {
            "title": "Beta Page Valid",
            "slug": "beta-valid",
            "template_id": str(sys_tmpl.id),
        }
        resp_sys = self.client_b.post(
            "/api/v1/landing-pages/",
            data=payload_sys,
            format="json",
        )
        self.assertEqual(resp_sys.status_code, status.HTTP_201_CREATED)

    def test_finding_04_direct_submissions_on_draft_and_preview(self):
        draft_page = LandingPage.unscoped_objects.create(
            tenant=self.tenant_a,
            title="Draft Page",
            slug="draft-page",
            status=LandingPage.Status.DRAFT,
            preview_token="secret-preview-token-12345",
        )
        LandingPageForm.unscoped_objects.create(
            tenant=self.tenant_a,
            page=draft_page,
            title="Draft Form",
            fields=[{"name": "email", "type": "email", "required": True}],
        )

        submit_url = f"/api/v1/public/pages/{self.tenant_a.slug}/draft-page/submit/"
        resp_blocked = self.public_client.post(
            submit_url,
            data={"form_data": {"email": "lead@test.com"}},
            format="json",
        )
        self.assertEqual(resp_blocked.status_code, status.HTTP_404_NOT_FOUND)

        resp_preview = self.public_client.post(
            f"{submit_url}?preview=true&token=secret-preview-token-12345",
            data={"form_data": {"email": "preview_lead@test.com"}},
            format="json",
        )
        self.assertEqual(resp_preview.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_preview.data["status"], "success")

    def test_finding_05_package_synchronization_concurrency_safety(self):
        set_current_tenant(self.tenant_a)
        pkg1, created1 = upsert_package_from_external(
            external_id="fitverx-pkg-999",
            name="Gold Plan v1",
            price=99.00,
        )
        self.assertTrue(created1)
        self.assertEqual(pkg1.name, "Gold Plan v1")

        pkg2, created2 = upsert_package_from_external(
            external_id="fitverx-pkg-999",
            name="Gold Plan v2",
            price=120.00,
        )
        self.assertFalse(created2)
        self.assertEqual(pkg2.id, pkg1.id)
        self.assertEqual(pkg2.name, "Gold Plan v2")
        self.assertEqual(float(pkg2.price), 120.00)

    def test_finding_06_form_submission_email_and_phone_validation(self):
        page = LandingPage.unscoped_objects.create(
            tenant=self.tenant_a,
            title="Capture Page",
            slug="capture-page",
            status=LandingPage.Status.PUBLISHED,
        )
        LandingPageForm.unscoped_objects.create(
            tenant=self.tenant_a,
            page=page,
            title="Signup Form",
            fields=[
                {"name": "email", "type": "email", "required": False},
                {"name": "phone", "type": "phone", "required": False},
            ],
        )

        submit_url = f"/api/v1/public/pages/{self.tenant_a.slug}/capture-page/submit/"

        resp_invalid_email = self.public_client.post(
            submit_url,
            data={"form_data": {"email": "not-a-valid-email"}},
            format="json",
        )
        self.assertEqual(resp_invalid_email.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            Contact.unscoped_objects.filter(tenant=self.tenant_a).count(), 0
        )

        resp_empty_contacts = self.public_client.post(
            submit_url,
            data={"form_data": {"name": "Anonymous"}},
            format="json",
        )
        self.assertEqual(resp_empty_contacts.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            Contact.unscoped_objects.filter(tenant=self.tenant_a).count(), 0
        )

        resp_valid = self.public_client.post(
            submit_url,
            data={"form_data": {"email": "valid.lead@example.com"}},
            format="json",
        )
        self.assertEqual(resp_valid.status_code, status.HTTP_200_OK)
        self.assertEqual(
            Contact.unscoped_objects.filter(
                tenant=self.tenant_a, email="valid.lead@example.com"
            ).count(),
            1,
        )

    def test_finding_07_and_08_query_optimization_and_prefetched_reuse(self):
        page = LandingPage.unscoped_objects.create(
            tenant=self.tenant_a,
            title="Optimized Page",
            slug="opt-page",
            status=LandingPage.Status.PUBLISHED,
        )
        pkg = Package.unscoped_objects.create(
            tenant=self.tenant_a,
            external_id="pkg-opt-1",
            name="Opt Package",
            price=50.00,
            status=Package.Status.ACTIVE,
        )
        page.packages.add(pkg)
        LandingPageSection.unscoped_objects.create(
            tenant=self.tenant_a,
            page=page,
            section_type="packages",
            order=1,
            is_visible=True,
        )

        set_current_tenant(self.tenant_a)
        qs = get_landing_pages_queryset()
        page_obj = qs.filter(id=page.id).first()
        serializer = LandingPageListSerializer(page_obj)
        self.assertEqual(serializer.data["packages_count"], 1)
        self.assertEqual(serializer.data["sections_count"], 1)

        retrieved_page = get_public_landing_page_by_slug_or_id(
            "opt-page", tenant_identifier=self.tenant_a.slug
        )
        with self.assertNumQueries(0):
            packages = [p for p in retrieved_page.packages.all() if p.status == "active"]
            sections = [s for s in retrieved_page.sections.all() if s.is_visible]
            self.assertEqual(len(packages), 1)
            self.assertEqual(len(sections), 1)

    @patch("landing_pages.services.publish")
    def test_finding_09_form_submitted_event_payload_alignment(self, mock_publish):
        page = LandingPage.unscoped_objects.create(
            tenant=self.tenant_a,
            title="Trigger Matching Page",
            slug="trigger-page",
            status=LandingPage.Status.PUBLISHED,
        )
        form = LandingPageForm.unscoped_objects.create(
            tenant=self.tenant_a,
            page=page,
            title="Trigger Form",
            fields=[{"name": "email", "type": "email", "required": True}],
        )

        submit_url = f"/api/v1/public/pages/{self.tenant_a.slug}/trigger-page/submit/"
        resp = self.public_client.post(
            submit_url,
            data={"form_data": {"email": "automation.lead@example.com"}},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.assertTrue(mock_publish.called)
        call_kwargs = mock_publish.call_args[1]
        payload = call_kwargs["payload"]
        self.assertEqual(payload["page_id"], str(page.id))
        self.assertEqual(payload["landing_page_id"], str(page.id))
        self.assertEqual(payload["source_page"], "Trigger Matching Page")
        self.assertEqual(payload["landing_page_title"], "Trigger Matching Page")
        self.assertEqual(payload["form_id"], str(form.id))
        self.assertEqual(payload["source_form"], "Trigger Form")
        self.assertEqual(payload["source_channel"], "landing_page")

    def test_finding_10_external_package_sync_payload_size_limit(self):
        url = "/api/v1/external/packages/sync/"
        headers = {
            "HTTP_CONTENT_LENGTH": str(6 * 1024 * 1024),
            "CONTENT_LENGTH": str(6 * 1024 * 1024),
        }
        resp = self.public_client.post(
            url,
            data={"packages": []},
            format="json",
            **headers,
        )
        self.assertEqual(
            resp.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        )

    def test_finding_11_spectacular_schema_generation(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("spectacular", "--validate", stdout=out)
        output = out.getvalue()
        self.assertIn("openapi: 3.0.3", output)
        self.assertIn("PackageStatusEnum", output)
        self.assertIn("LandingPageStatusEnum", output)
