from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import ReportDashboardLayout, ReportDataCapability as Cap, ReportDataGrant, ReportExportAudit, ReportingPanel as Panel
from .reporting_services import grant_report_data
from .test_reporting import ReportingFixtureMixin


class ReportExportPermissionTests(ReportingFixtureMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.sales)
        today = timezone.localdate().isoformat()
        self.unit_query = {"unit": self.unit.pk, "period": "daily", "start": today, "end": today}

    def export(self, query):
        return self.client.post("/api/reports/sales/export/", {**query, "format": "xlsx"}, format="json")

    def test_seeing_a_report_does_not_allow_exporting_it(self):
        self.make_delivered_sale()
        self.grant_report_access(self.sales, Panel.SALES, [Cap.SALES], unit=self.unit)
        self.assertEqual(self.client.get("/api/reports/sales/", self.unit_query).status_code, 200)
        refused = self.export(self.unit_query)
        self.assertEqual(refused.status_code, 403)
        self.assertIn("export permission", refused.data["detail"])
        self.assertFalse(ReportExportAudit.objects.exists())
        grant_report_data(actor=self.admin, target_user=self.sales, capability=Cap.EXPORT, business_unit=self.unit)
        self.assertEqual(self.export(self.unit_query).status_code, 200)
        self.assertEqual(ReportExportAudit.objects.count(), 1)
        ReportDataGrant.objects.filter(user=self.sales, capability=Cap.EXPORT).update(revoked_at=timezone.now())
        self.assertEqual(self.export(self.unit_query).status_code, 403, "Revoking export takes effect at once")

    def test_company_wide_exports_need_a_company_wide_export_grant(self):
        entity = self.unit.legal_entity
        self.grant_report_access(self.sales, Panel.SALES, [Cap.SALES], company=entity)
        company_query = {**self.unit_query, "company": entity.pk}
        company_query.pop("unit")
        grant_report_data(actor=self.admin, target_user=self.sales, capability=Cap.EXPORT, business_unit=self.unit)
        self.assertEqual(self.export(self.unit_query).status_code, 200, "Unit export works for that unit")
        refused = self.export(company_query)
        self.assertEqual(refused.status_code, 403)
        self.assertIn("company-wide export", refused.data["detail"])
        grant_report_data(actor=self.admin, target_user=self.sales, capability=Cap.EXPORT, legal_entity=entity)
        self.assertEqual(self.export(company_query).status_code, 200)

    def test_the_catalog_tells_the_page_where_exporting_is_allowed(self):
        self.grant_report_access(self.sales, Panel.SALES, [Cap.SALES], unit=self.unit)
        scopes = self.client.get("/api/reports/").data["scopes"]
        self.assertEqual(scopes["export"]["business_units"], [])
        grant_report_data(actor=self.admin, target_user=self.sales, capability=Cap.EXPORT, business_unit=self.unit)
        scopes = self.client.get("/api/reports/").data["scopes"]
        self.assertEqual([unit["id"] for unit in scopes["export"]["business_units"]], [self.unit.pk])


class DashboardLayoutTests(ReportingFixtureMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.sales)

    def test_users_without_report_access_have_no_dashboard(self):
        self.assertEqual(self.client.get("/api/reports/dashboard-layout/").data,
                         {"panels": [], "customized": False, "assigned": []})
        refused = self.client.put("/api/reports/dashboard-layout/", {"panels": ["sales"]}, format="json")
        self.assertEqual(refused.status_code, 403)
        self.assertFalse(ReportDashboardLayout.objects.exists())

    def test_users_arrange_only_the_panels_they_are_assigned(self):
        for panel in (Panel.SALES, Panel.INVENTORY, Panel.BEST_SELLERS):
            self.grant_report_access(self.sales, panel, [], unit=self.unit)
        default = self.client.get("/api/reports/dashboard-layout/").data
        self.assertFalse(default["customized"])
        self.assertEqual(set(default["panels"]), {"sales", "inventory", "best_sellers"})

        saved = self.client.put("/api/reports/dashboard-layout/", {"panels": ["inventory", "sales"]}, format="json")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data["panels"], ["inventory", "sales"])
        self.assertTrue(saved.data["customized"])
        refused = self.client.put("/api/reports/dashboard-layout/", {"panels": ["inventory", "gross_margin"]}, format="json")
        self.assertEqual(refused.status_code, 403, "A panel the user is not assigned cannot be added")
        for bad in ([], ["sales", "sales"], "sales"):
            self.assertEqual(self.client.put("/api/reports/dashboard-layout/", {"panels": bad}, format="json").status_code, 400)

        self.sales.report_panel_grants.filter(panel=Panel.INVENTORY).update(revoked_at=timezone.now())
        self.assertEqual(self.client.get("/api/reports/dashboard-layout/").data["panels"], ["sales"],
                         "A revoked panel drops out of the saved layout")

        reset = self.client.delete("/api/reports/dashboard-layout/")
        self.assertEqual(reset.status_code, 200)
        self.assertFalse(reset.data["customized"])
        self.assertFalse(ReportDashboardLayout.objects.exists())
