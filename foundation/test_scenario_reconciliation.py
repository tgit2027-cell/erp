"""Scenario reconciliation ties receipts, stock, deliveries, invoices and payments."""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from .models import BusinessUnitRoleAssignment, SalesQuote, StockBalance
from .purchasing_services import approve_post_handover_balance, deliver_sales_quote, notify_quote_ready
from .scenario_reconciliation import build_scenario_reconciliation
from . import test_purchasing_workflows as purchasing_tests
from .test_purchasing_workflows import PurchasingFixtureMixin


class ScenarioReconciliationTests(PurchasingFixtureMixin, TestCase):
    make_role_user = purchasing_tests.PurchasingWorkflowTests.make_role_user

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()

    def failing(self, report):
        return [row["label"] for row in report["checks"] if not row["matches"]]

    def test_seeded_unit_reconciles_before_any_transaction(self):
        report = build_scenario_reconciliation(self.unit)
        self.assertEqual(self.failing(report), [])

    def test_specification_fixture_matches_the_expected_values(self):
        # Technical foundation spec: 10 chairs at QAR 1,000 plus QAR 2,000 landed
        # charges, then deliver two at QAR 2,000 each.
        before = build_scenario_reconciliation(self.unit)["summary"]
        self.make_receipt()
        quote = SalesQuote.objects.get(pk=self.make_quote()["quote_id"])
        self.pay(quote, key="spec-pay", amount="4000.00")
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key="spec-ready")
        deliver_sales_quote(actor=self.sales, quote=quote, idempotency_key="spec-deliver")
        report = build_scenario_reconciliation(self.unit)
        self.assertTrue(report["all_match"], self.failing(report))
        summary = report["summary"]
        delta = {key: Decimal(summary[key]) - Decimal(before[key]) for key in (
            "supplier_value", "landed_charges", "received_value", "stock_value", "revenue",
            "cost_of_goods_sold", "gross_margin", "invoiced_total", "collected_on_invoices", "still_to_collect",
        )}
        self.assertEqual(delta, {
            "supplier_value": Decimal("10000.00"), "landed_charges": Decimal("2000.00"),
            "received_value": Decimal("12000.00"), "stock_value": Decimal("9600.00"),
            "revenue": Decimal("4000.00"), "cost_of_goods_sold": Decimal("2400.00"),
            "gross_margin": Decimal("1600.00"), "invoiced_total": Decimal("4000.00"),
            "collected_on_invoices": Decimal("4000.00"), "still_to_collect": Decimal("0.00"),
        })

    def test_balance_after_handover_is_still_to_collect_and_still_reconciles(self):
        self.make_receipt(charges=[])
        quote = SalesQuote.objects.get(pk=self.make_quote(key="due-quote")["quote_id"])
        self.pay(quote, key="due-deposit", amount="3600.00")
        approve_post_handover_balance(
            actor=self.make_role_user("synthetic_reconcile_gm", BusinessUnitRoleAssignment.Role.BU_GM),
            quote=quote, idempotency_key="due-approval",
            deferred_balance_percent=Decimal("10.00"), reason="TEST ONLY delivery exception",
        )
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key="due-ready")
        deliver_sales_quote(actor=self.sales, quote=quote, idempotency_key="due-deliver")
        report = build_scenario_reconciliation(self.unit)
        self.assertTrue(report["all_match"], self.failing(report))
        self.assertEqual(report["summary"]["still_to_collect"], "400.00")
        self.pay(quote, key="due-final", amount="400.00")
        report = build_scenario_reconciliation(self.unit)
        self.assertEqual(report["summary"]["still_to_collect"], "0.00")
        self.assertTrue(report["all_match"], self.failing(report))

    def test_deposit_before_delivery_is_held_and_reconciles(self):
        self.make_receipt()
        quote = SalesQuote.objects.get(pk=self.make_quote(key="held-quote")["quote_id"])
        self.pay(quote, key="held-pay", amount="2000.00")
        report = build_scenario_reconciliation(self.unit)
        self.assertTrue(report["all_match"], self.failing(report))
        self.assertEqual(report["summary"]["deposits_held"], "2000.00")

    def test_a_stock_value_edited_outside_the_services_is_flagged(self):
        self.make_receipt()
        StockBalance.objects.filter(business_unit=self.unit, variant=self.variant, location=self.location).update(
            on_hand_value=Decimal("11999.00"),
        )
        report = build_scenario_reconciliation(self.unit)
        self.assertFalse(report["all_match"])
        self.assertEqual(self.failing(report), [
            "Stock on hand value equals the sum of all stock movements",
            "Inventory account balance equals stock on hand value",
        ])
        row = next(row for row in report["checks"] if not row["matches"])
        self.assertEqual(row["difference"], "-1.00")

    def test_api_is_staff_only(self):
        self.make_receipt()
        staff, other = APIClient(), APIClient()
        staff.force_login(self.admin)
        other.force_login(self.sales)
        response = staff.get("/api/reports/scenario-reconciliation/", {"business_unit_id": self.unit.pk})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["all_match"])
        self.assertEqual(set(response.data["checks"][0]), {"area", "label", "unit", "expected", "actual", "difference", "matches"})
        self.assertEqual(other.get("/api/reports/scenario-reconciliation/", {"business_unit_id": self.unit.pk}).status_code, 403)
        self.assertEqual(staff.get("/api/reports/scenario-reconciliation/").status_code, 400)
