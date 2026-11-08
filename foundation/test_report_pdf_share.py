import re
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    ReportDataCapability as Cap,
    ReportExportAudit,
    ReportShareLink,
    ReportShareLinkOpen,
    ReportingPanel as Panel,
)
from .report_share_links import _hash
from .reporting_exports import render_report_export
from .reporting_services import build_report, grant_report_data
from .test_reporting import ReportingFixtureMixin


class ReportPdfExportTests(ReportingFixtureMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.sales)
        today = timezone.localdate().isoformat()
        self.query = {"unit": self.unit.pk, "period": "daily", "start": today, "end": today}

    def test_pdf_export_needs_the_export_permission_and_is_audited(self):
        self.make_delivered_sale()
        self.grant_report_access(self.sales, Panel.SALES, [Cap.SALES], unit=self.unit)
        refused = self.client.post("/api/reports/sales/export/", {**self.query, "format": "pdf"}, format="json")
        self.assertEqual(refused.status_code, 403)
        grant_report_data(actor=self.admin, target_user=self.sales, capability=Cap.EXPORT, business_unit=self.unit)
        response = self.client.post("/api/reports/sales/export/", {**self.query, "format": "pdf"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn('filename="synthetic-sales-report.pdf"', response["Content-Disposition"])
        content = response.content
        self.assertTrue(content.startswith(b"%PDF-1.4"))
        self.assertTrue(content.rstrip().endswith(b"%%EOF"))
        self.assertIn(b"Page 1 of", content)
        self.assertEqual(ReportExportAudit.objects.get().file_format, "pdf")
        unknown = self.client.post("/api/reports/sales/export/", {**self.query, "format": "csv"}, format="json")
        self.assertEqual(unknown.status_code, 400)

    def test_long_reports_run_over_several_pages_with_valid_offsets(self):
        self.grant_report_access(self.sales, Panel.SALES, [Cap.SALES], unit=self.unit)
        report = build_report(actor=self.sales, panel=Panel.SALES, query_params=self.query, paginate=False)
        row = dict(report["rows"][0]) if report["rows"] else {field: "Sofa (linen) \\ 2" for field in report["fields"]}
        report["rows"] = [row] * 120
        content = render_report_export(report, "pdf")
        pages = int(re.search(rb"/Count (\d+)", content).group(1))
        self.assertGreaterEqual(pages, 3)
        self.assertIn(f"Page {pages} of {pages}".encode(), content)
        # Every cross-reference offset points at the object it names.
        xref = content[content.rindex(b"xref"):]
        offsets = [int(line.split()[0]) for line in xref.splitlines()[3:] if line.endswith(b" n ") or line.endswith(b" n")]
        for number, offset in enumerate(offsets, start=1):
            self.assertTrue(content[offset:].startswith(f"{number} 0 obj".encode()), number)
        start = int(content[content.rindex(b"startxref"):].split()[1])
        self.assertTrue(content[start:].startswith(b"xref"))


class ReportShareLinkTests(ReportingFixtureMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.sales)
        self.other = APIClient()
        self.other.force_authenticate(user=self.roche_user)
        today = timezone.localdate().isoformat()
        self.query = {"panel": "sales", "unit": self.unit.pk, "period": "daily", "start": today, "end": today}
        self.grant_report_access(self.sales, Panel.SALES, [Cap.SALES], unit=self.unit)

    def create(self, **extra):
        return self.client.post("/api/reports/share-links/", {**self.query, **extra}, format="json")

    def token_of(self, response):
        return response.data["url"].split("shared_report=")[1].split("#")[0]

    def test_sharing_needs_the_export_permission_and_stores_only_a_hash(self):
        refused = self.create()
        self.assertEqual(refused.status_code, 403)
        self.assertFalse(ReportShareLink.objects.exists())
        grant_report_data(actor=self.admin, target_user=self.sales, capability=Cap.EXPORT, business_unit=self.unit)
        created = self.create(note="Daily sales for the team")
        self.assertEqual(created.status_code, 201, created.data)
        token = self.token_of(created)
        link = ReportShareLink.objects.get()
        self.assertEqual(link.token_hash, _hash(token))
        self.assertNotIn(token, str(link.filters) + link.token_hash)
        self.assertEqual(link.filters["unit"], self.unit.pk)
        self.assertEqual(link.expires_at - link.created_at, timedelta(days=7))
        listed = self.client.get("/api/reports/share-links/").data["links"]
        self.assertEqual([item["state"] for item in listed], ["active"])
        self.assertNotIn("url", listed[0], "The link itself is only shown once")

    def test_recipients_see_the_report_only_through_their_own_grants(self):
        grant_report_data(actor=self.admin, target_user=self.sales, capability=Cap.EXPORT, business_unit=self.unit)
        token = self.token_of(self.create())
        refused = self.other.get(f"/api/reports/shared/{token}/")
        self.assertEqual(refused.status_code, 403)
        self.assertIn("not allowed to see", refused.data["detail"])
        self.assertNotIn("rows", refused.data)
        self.grant_report_access(self.roche_user, Panel.SALES, [Cap.SALES], unit=self.roche)
        self.assertEqual(self.other.get(f"/api/reports/shared/{token}/").status_code, 403,
                         "Access to another unit does not open this unit's report")
        self.grant_report_access(self.roche_user, Panel.SALES, [Cap.SALES], unit=self.unit)
        opened = self.other.get(f"/api/reports/shared/{token}/")
        self.assertEqual(opened.status_code, 200, opened.data)
        self.assertEqual(opened.data["panel"], "sales")
        self.assertEqual(opened.data["filters"]["unit"], self.unit.pk)
        self.assertEqual(
            list(ReportShareLinkOpen.objects.order_by("pk").values_list("allowed", flat=True)), [False, False, True]
        )
        self.assertEqual(ReportShareLink.objects.get().open_count, 3)
        with self.assertRaises(ValidationError):
            ReportShareLinkOpen.objects.first().delete()

    def test_revoked_expired_and_unknown_links_do_not_open(self):
        grant_report_data(actor=self.admin, target_user=self.sales, capability=Cap.EXPORT, business_unit=self.unit)
        token = self.token_of(self.create())
        link = ReportShareLink.objects.get()
        self.assertEqual(self.client.get("/api/reports/shared/not-a-real-token/").status_code, 404)
        self.assertEqual(self.other.post(f"/api/reports/share-links/{link.pk}/revoke/").status_code, 404,
                         "Only the creator can withdraw a link")
        ReportShareLink.objects.filter(pk=link.pk).update(expires_at=timezone.now() - timedelta(minutes=1),
                                                          created_at=timezone.now() - timedelta(days=8))
        expired = self.client.get(f"/api/reports/shared/{token}/")
        self.assertEqual(expired.status_code, 400)
        self.assertIn("expired", expired.data["detail"])
        ReportShareLink.objects.filter(pk=link.pk).update(expires_at=timezone.now() + timedelta(days=1))
        self.assertEqual(self.client.get(f"/api/reports/shared/{token}/").status_code, 200)
        revoked = self.client.post(f"/api/reports/share-links/{link.pk}/revoke/")
        self.assertEqual(revoked.data["state"], "revoked")
        withdrawn = self.client.get(f"/api/reports/shared/{token}/")
        self.assertEqual(withdrawn.status_code, 400)
        self.assertIn("withdrawn", withdrawn.data["detail"])

    def test_a_link_records_the_scope_the_report_was_built_for(self):
        grant_report_data(actor=self.admin, target_user=self.sales, capability=Cap.EXPORT, business_unit=self.unit)
        query = dict(self.query)
        query.pop("unit")
        response = self.client.post("/api/reports/share-links/", query, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(ReportShareLink.objects.get().filters["unit"], self.unit.pk,
                         "A recipient with wider access still sees only the sender's unit")
