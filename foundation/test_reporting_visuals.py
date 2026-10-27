from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import ReportDataCapability as Cap, ReportingPanel as Panel
from .test_reporting import ReportingFixtureMixin


class ReportVisualTests(ReportingFixtureMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.sales)
        today = timezone.localdate().isoformat()
        self.params = {"unit": self.unit.pk, "period": "daily", "start": today, "end": today}

    def get(self, path):
        return self.client.get(path, self.params)

    def test_sales_visual_matches_the_standard_report_and_returns_no_rows(self):
        self.make_delivered_sale()
        self.grant_report_access(self.sales, Panel.SALES, [Cap.SALES], unit=self.unit)
        report = self.get("/api/reports/sales/").data
        visual = self.get("/api/reports/sales/visual/")
        self.assertEqual(visual.status_code, 200, visual.data)
        self.assertNotIn("rows", visual.data)
        kpis = {kpi["label"]: kpi["value"] for kpi in visual.data["kpis"]}
        self.assertEqual(Decimal(kpis["Sales"]), Decimal(report["summary"]["sales_amount"]))
        self.assertEqual(Decimal(kpis["Quantity sold"]), Decimal(report["summary"]["quantity"]))
        self.assertEqual(visual.data["row_count"], report["pagination"]["total_count"])
        by_id = {chart["id"]: chart for chart in visual.data["charts"]}
        self.assertEqual(list(by_id), ["sales-by-period", "quantity-by-period"])
        self.assertEqual(by_id["sales-by-period"]["categories"], [row["period"] for row in report["trend"]])
        self.assertEqual(
            [Decimal(value) for value in by_id["sales-by-period"]["series"][0]["values"]],
            [Decimal(row["sales_amount"]) for row in report["trend"]],
        )

    def test_visual_keeps_the_same_permissions_as_the_report(self):
        self.assertEqual(self.get("/api/reports/sales/visual/").status_code, 403)
        self.grant_report_access(self.sales, Panel.SALES, [], unit=self.unit)
        self.assertEqual(self.get("/api/reports/sales/visual/").status_code, 403, "A panel alone does not grant data")
        self.assertEqual(self.get("/api/reports/not_a_panel/visual/").status_code, 400)

    def test_best_sellers_adds_ranked_item_bars(self):
        self.make_delivered_sale()
        self.grant_report_access(self.sales, Panel.BEST_SELLERS, [Cap.SALES], unit=self.unit)
        visual = self.get("/api/reports/best_sellers/visual/").data
        bars = next(chart for chart in visual["charts"] if chart["id"] == "top-items")
        self.assertEqual(bars["kind"], "bar")
        self.assertEqual(len(bars["categories"]), 1)
        self.assertTrue(bars["categories"][0].endswith(self.variant.item.description))

    def test_gross_margin_visual_totals_revenue_and_cost(self):
        self.make_delivered_sale()
        self.grant_report_access(
            self.sales, Panel.GROSS_MARGIN, [Cap.SALES, Cap.GROSS_MARGIN, Cap.SUPPLIER_COST], unit=self.unit,
        )
        report = self.get("/api/reports/gross_margin/").data
        visual = self.get("/api/reports/gross_margin/visual/").data
        kpis = {kpi["label"]: Decimal(kpi["value"]) for kpi in visual["kpis"]}
        revenue = sum(Decimal(row["revenue"]) for row in report["rows"])
        cogs = sum(Decimal(row["cost_of_goods_sold"]) for row in report["rows"])
        self.assertEqual((kpis["Revenue"], kpis["Cost of goods sold"], kpis["Gross margin"]), (revenue, cogs, revenue - cogs))
        chart = visual["charts"][0]
        self.assertEqual([series["key"] for series in chart["series"]], ["revenue", "cost_of_goods_sold"])

    def test_inventory_visual_uses_quantity_when_cost_is_not_granted(self):
        self.make_receipt(key="visual-receipt")
        self.grant_report_access(self.sales, Panel.INVENTORY, [Cap.INVENTORY], unit=self.unit)
        visual = self.get("/api/reports/inventory/visual/").data
        self.assertEqual([kpi["label"] for kpi in visual["kpis"]], ["Units on hand", "Units reserved"])
        location = visual["charts"][0]
        self.assertEqual((location["unit"], location["title"]), ("units", "Units on hand by location"))
        self.assertEqual(location["categories"], [self.location.name])
        self.assertEqual(Decimal(location["series"][0]["values"][0]), Decimal("10.00"))
        grant_cost = [Cap.SUPPLIER_COST, Cap.LANDED_COST]
        self.grant_report_access(self.sales, Panel.INVENTORY, grant_cost, unit=self.unit)
        valued = self.get("/api/reports/inventory/visual/").data
        self.assertEqual(valued["charts"][0]["unit"], "QAR")
        self.assertIn("Inventory value", [kpi["label"] for kpi in valued["kpis"]])

    def test_cost_and_margin_never_reach_a_user_without_those_grants(self):
        self.make_delivered_sale()
        self.grant_report_access(self.sales, Panel.GROSS_MARGIN, [Cap.SALES], unit=self.unit)
        denied = self.get("/api/reports/gross_margin/visual/")
        self.assertEqual(denied.status_code, 403, "Margins need the gross-margin data grant on the server")
        self.grant_report_access(self.sales, Panel.PURCHASE_HISTORY, [Cap.PURCHASE_HISTORY], unit=self.unit)
        purchases = self.get("/api/reports/purchase_history/visual/")
        self.assertEqual(purchases.status_code, 200, purchases.data)
        body = str(purchases.data)
        for hidden in ("supplier_unit_cost", "purchase_value", "landed_cost"):
            self.assertNotIn(hidden, body, "Supplier cost stays out of the response without the cost grant")
