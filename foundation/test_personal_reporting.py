"""Real PostgreSQL/session tests; fixtures exist only in Django's test database."""

from io import BytesIO
from zipfile import ZipFile

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    DemoReportSalesFact, Item, Location, PersonalReportView, ReportDataCapability, ReportDataGrant, ReportExportAudit,
    ReportPanelGrant, ReportingPanel, ReportingRoleMembership, UserActionGrant,
)
from .reporting_services import (
    add_reporting_role_member, assign_report_panel, create_reporting_role, grant_report_data,
)
from .test_reporting import ReportingFixtureMixin


class PersonalReportingWorkspaceTests(ReportingFixtureMixin, TestCase):
    def setUp(self):
        self.grant_report_access(
            self.sales, ReportingPanel.SALES, [ReportDataCapability.SALES], unit=self.unit
        )
        assign_report_panel(actor=self.admin, target_user=self.sales, panel=ReportingPanel.BEST_SELLERS)
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.sales)
        self.csrf = self.client.get("/api/auth/csrf/").data["csrfToken"]
        self.filters = {
            "unit": self.unit.pk, "location": self.location.pk,
            "period": "quarterly", "start": "2020-01-01", "end": "2099-12-31",
            "page": 1, "page_size": 25,
        }
        self.payload = {
            "name": "Synthetic quarterly review",
            "panels": ["best_sellers", "sales"], "filters": self.filters,
        }

    def mutate(self, method, path, payload=None, client=None, token=None):
        client = client or self.client
        return getattr(client, method)(
            path, payload, format="json", HTTP_X_CSRFTOKEN=token or self.csrf
        )

    def save(self, payload=None):
        response = self.mutate("post", "/api/report-views/", payload or self.payload)
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_preferences_round_trip_order_filters_update_delete_without_grant_or_data_storage(self):
        self.make_delivered_sale()
        example_item = Item.objects.create(
            brand=self.variant.item.brand,
            item_code="DEMO-SAVED-VIEW-ONLY",
            description="TEST ONLY saved view report-only decoy",
            is_synthetic=True,
        )
        DemoReportSalesFact.objects.create(
            demo_fixture_key="saved-view-report-only-decoy",
            business_unit=self.unit,
            location=self.location,
            item=example_item,
            quantity="99.00",
            selling_price="9999.00",
            revenue="989901.00",
            created_at=timezone.now(),
        )
        before = (ReportPanelGrant.objects.count(), ReportDataGrant.objects.count(), UserActionGrant.objects.count())
        saved = self.save()
        path = f"/api/report-views/{saved['id']}/"
        fresh = self.client.get(path)
        self.assertEqual(fresh.status_code, 200, fresh.data)
        self.assertEqual(fresh.data["panels"], self.payload["panels"])
        self.assertEqual(fresh.data["filters"], self.filters)
        self.assertTrue(all(panel["available"] for panel in fresh.data["availability"]))
        self.assertIn("no-store", fresh["Cache-Control"])
        persisted = PersonalReportView.objects.get(pk=saved["id"])
        self.assertEqual(persisted.panels, ["best_sellers", "sales"])
        self.assertEqual(persisted.filters, self.filters)
        self.assertEqual(
            set(fresh.data), {"id", "name", "panels", "filters", "updated_at", "availability"}
        )
        self.assertNotIn("rows", str(fresh.data))
        for panel in ("sales", "best_sellers"):
            report = self.client.get(f"/api/reports/{panel}/", fresh.data["filters"])
            self.assertEqual(report.status_code, 200, report.data)
            self.assertEqual(report.data["pagination"]["total_count"], 1)
            self.assertNotIn("DEMO-SAVED-VIEW-ONLY", str(report.data))
            self.assertEqual(report.data["summary"], {"sales_amount": "4000.00", "quantity": "2.00"})
        self.assertEqual(self.client.get("/api/report-views/").data["views"], [saved])
        changed = {**self.payload, "name": "Synthetic revised review", "panels": ["sales"]}
        updated = self.mutate("put", path, changed)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(self.client.get(path).data["panels"], ["sales"])
        self.assertEqual(before, (ReportPanelGrant.objects.count(), ReportDataGrant.objects.count(), UserActionGrant.objects.count()))
        self.assertEqual(ReportExportAudit.objects.count(), 0)
        self.assertEqual(self.mutate("delete", path).status_code, 204)
        self.assertEqual(self.client.get(path).status_code, 404)

    def test_owner_isolation_including_staff_and_new_authenticated_session(self):
        saved = self.save()
        path = f"/api/report-views/{saved['id']}/"
        for user in (self.roche_user, self.admin):
            other = APIClient(enforce_csrf_checks=True)
            other.force_login(user)
            token = other.get("/api/auth/csrf/").data["csrfToken"]
            self.assertEqual(other.get("/api/report-views/").data["views"], [])
            self.assertEqual(other.get(path).status_code, 404)
            self.assertEqual(self.mutate("put", path, self.payload, other, token).status_code, 404)
            self.assertEqual(self.mutate("delete", path, client=other, token=token).status_code, 404)
        self.client.logout()
        other_session = APIClient()
        other_session.force_login(self.sales)
        self.assertEqual(other_session.get(path).data["filters"], self.filters)
        self.assertEqual(PersonalReportView.objects.count(), 1)

    def test_invalid_unassigned_duplicate_and_result_payloads_are_rejected(self):
        invalid = [
            ({**self.payload, "name": ""}, 400),
            ({**self.payload, "name": "x" * 101}, 400),
            ({**self.payload, "rows": [{"supplier_cost": "999"}]}, 400),
            ({**self.payload, "user": self.admin.pk}, 400),
            ({**self.payload, "panels": []}, 400),
            ({**self.payload, "panels": ["sales", "sales"]}, 400),
            ({**self.payload, "panels": ["unknown"]}, 400),
            ({**self.payload, "panels": ["gross_margin"]}, 403),
            ({**self.payload, "filters": {**self.filters, "rows": []}}, 400),
            ({**self.payload, "filters": {**self.filters, "company": self.unit.legal_entity_id}}, 400),
            ({**self.payload, "filters": {"period": "monthly"}}, 400),
            ({**self.payload, "filters": {**self.filters, "start": "invalid"}}, 400),
            ({**self.payload, "filters": {**self.filters, "page_size": 101}}, 400),
            ({**self.payload, "filters": {**self.filters, "unit": True}}, 400),
        ]
        for payload, expected in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.mutate("post", "/api/report-views/", payload).status_code, expected)
        self.assertEqual(PersonalReportView.objects.count(), 0)
        self.save()
        self.assertEqual(self.mutate("post", "/api/report-views/", self.payload).status_code, 400)
        self.assertEqual(PersonalReportView.objects.count(), 1)

    def test_panel_revocation_clears_availability_and_denies_current_queries_and_both_exports(self):
        saved = self.save()
        ReportPanelGrant.objects.filter(user=self.sales, panel="sales").update(revoked_at=timezone.now())
        fresh = self.client.get(f"/api/report-views/{saved['id']}/")
        availability = {item["panel"]: item for item in fresh.data["availability"]}
        self.assertFalse(availability["sales"]["available"])
        self.assertTrue(availability["best_sellers"]["available"])
        self.assertNotIn("rows", fresh.data)
        self.assertEqual(self.client.get("/api/reports/sales/", self.filters).status_code, 403)
        for format in ("xlsx", "docx"):
            denied = self.mutate("post", "/api/reports/sales/export/", {**self.filters, "format": format})
            self.assertEqual(denied.status_code, 403)
        self.assertEqual(ReportExportAudit.objects.count(), 0)
        self.assertEqual(
            self.mutate("put", f"/api/report-views/{saved['id']}/", self.payload).status_code, 403
        )

    def test_data_revocation_does_not_turn_saved_preferences_into_authority(self):
        saved = self.save()
        ReportDataGrant.objects.filter(user=self.sales).update(revoked_at=timezone.now())
        fresh = self.client.get(f"/api/report-views/{saved['id']}/")
        self.assertTrue(all(not item["available"] for item in fresh.data["availability"]))
        self.assertEqual(fresh.data["filters"], self.filters)
        self.assertEqual(self.client.get("/api/reports/sales/", self.filters).status_code, 403)
        self.assertEqual(
            self.mutate("post", "/api/reports/sales/export/", {**self.filters, "format": "xlsx"}).status_code, 403
        )
        self.assertEqual(ReportExportAudit.objects.count(), 0)
        # Assignment permits presentation preferences, not data access.
        self.assertEqual(
            self.mutate("put", f"/api/report-views/{saved['id']}/", self.payload).status_code, 200
        )

    def test_cross_unit_location_and_ungranted_company_scopes_are_never_substituted_or_widened(self):
        location = Location.objects.create(
            legal_entity=self.roche.legal_entity, code="test-roche-report-only",
            name="TEST ONLY Roche location",
        )
        location.business_units.add(self.roche)
        forbidden = [
            {"unit": self.roche.pk},
            {"unit": self.unit.pk, "location": location.pk},
            {"company": self.unit.legal_entity_id},
        ]
        for index, scope in enumerate(forbidden):
            filters = {key: value for key, value in self.filters.items() if key not in ("unit", "location")}
            filters.update(scope)
            saved = self.save({**self.payload, "name": f"Synthetic denied scope {index}", "filters": filters})
            fresh = self.client.get(f"/api/report-views/{saved['id']}/")
            self.assertEqual(fresh.data["filters"], filters)
            self.assertTrue(all(not panel["available"] for panel in fresh.data["availability"]))
            self.assertEqual(self.client.get("/api/reports/sales/", filters).status_code, 403)
            for format in ("xlsx", "docx"):
                self.assertEqual(self.mutate(
                    "post", "/api/reports/sales/export/", {**filters, "format": format}
                ).status_code, 403)
        self.assertEqual(ReportExportAudit.objects.count(), 0)

    def test_role_membership_revocation_is_rechecked_on_saved_view_load(self):
        role = create_reporting_role(actor=self.admin, code="test-personal-view", name="TEST ONLY personal view role")
        membership, _ = add_reporting_role_member(actor=self.admin, role=role, target_user=self.roche_user)
        assign_report_panel(actor=self.admin, target_role=role, panel="sales")
        grant_report_data(actor=self.admin, target_role=role, capability="sales", business_unit=self.unit)
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.roche_user)
        token = client.get("/api/auth/csrf/").data["csrfToken"]
        saved = self.mutate(
            "post", "/api/report-views/", {**self.payload, "panels": ["sales"]}, client, token
        )
        self.assertEqual(saved.status_code, 201, saved.data)
        path = f"/api/report-views/{saved.data['id']}/"
        self.assertTrue(client.get(path).data["availability"][0]["available"])
        ReportingRoleMembership.objects.filter(pk=membership.pk).update(revoked_at=timezone.now())
        self.assertFalse(client.get(path).data["availability"][0]["available"])
        self.assertEqual(client.get("/api/reports/sales/", self.filters).status_code, 403)

    def test_current_sensitive_masking_and_exports_not_saved_rows_are_used_after_cost_revocation(self):
        self.make_receipt(key="personal-purchase-fixture")
        self.grant_report_access(
            self.sales, ReportingPanel.PURCHASE_HISTORY,
            [ReportDataCapability.PURCHASE_HISTORY, ReportDataCapability.SUPPLIER_COST], unit=self.unit,
        )
        payload = {**self.payload, "panels": ["purchase_history"]}
        saved = self.save(payload)
        before = self.client.get("/api/reports/purchase_history/", self.filters)
        self.assertIn("purchase_value", before.data["fields"])
        ReportDataGrant.objects.filter(user=self.sales, capability="supplier_cost").update(revoked_at=timezone.now())
        fresh = self.client.get(f"/api/report-views/{saved['id']}/")
        self.assertTrue(fresh.data["availability"][0]["available"])
        self.assertNotIn("rows", fresh.data)
        masked = self.client.get("/api/reports/purchase_history/", self.filters)
        self.assertNotIn("purchase_value", masked.data["fields"])
        self.assertTrue(masked.data["rows"])
        for format, path in (("xlsx", "xl/worksheets/sheet1.xml"), ("docx", "word/document.xml")):
            exported = self.mutate(
                "post", "/api/reports/purchase_history/export/", {**saved["filters"], "format": format}
            )
            self.assertEqual(exported.status_code, 200)
            with ZipFile(BytesIO(exported.content)) as archive:
                document = archive.read(path).decode()
            self.assertNotIn("purchase_value", document)
            self.assertNotIn("supplier_unit_cost", document)
        self.assertEqual(ReportExportAudit.objects.count(), 2)
        for audit in ReportExportAudit.objects.all():
            self.assertEqual(audit.business_unit_ids, [self.unit.pk])
            self.assertNotIn("purchase_value", audit.fields)

    def test_session_csrf_authentication_and_demo_only_guards(self):
        saved = self.save()
        path = f"/api/report-views/{saved['id']}/"
        no_csrf = self.client.post("/api/report-views/", self.payload, format="json")
        self.assertEqual(no_csrf.status_code, 403)
        self.assertEqual(self.client.put(path, self.payload, format="json").status_code, 403)
        self.assertEqual(self.client.delete(path).status_code, 403)
        anonymous = APIClient()
        for method, url in (("get", "/api/report-views/"), ("get", path), ("post", "/api/report-views/"), ("delete", path)):
            self.assertEqual(getattr(anonymous, method)(url).status_code, 403)
        with override_settings(APP_ENV="production"):
            self.assertEqual(self.client.get(path).status_code, 403)
            self.assertEqual(self.client.get("/api/report-views/").status_code, 403)
        self.assertEqual(PersonalReportView.objects.count(), 1)
