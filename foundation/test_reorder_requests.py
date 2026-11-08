"""Sending low-stock suggestions to purchasing; synthetic only."""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .low_stock import decide_request, low_stock_overview, send_to_purchasing, set_level
from .models import ReorderRequest, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied, create_purchase_commitment
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction


@override_settings(APP_ENV="demo")
class ReorderRequestTests(PurchasingFixtureMixin, TestCase):
    """Admin runs the warehouse (sets levels); sales can create purchase orders (purchasing)."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.create(user=cls.admin, business_unit=cls.unit, action=UserActionGrant.Action.MANAGE_REORDER)
        cls.variant.item.supplier = cls.supplier
        cls.variant.item.supplier_item_code = "RB-CH-01"
        cls.variant.item.save()

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.make_receipt(key="shelf", quantity="2.00", charges=[])
        self.low = set_level(actor=self.admin, business_unit=self.unit, variant_id=self.variant.pk, minimum_quantity="5.00",
                             reorder_quantity="10.00")
        self.out = set_level(actor=self.admin, business_unit=self.unit, variant_id=self.alternate_variant.pk,
                             minimum_quantity="1.00", reorder_quantity="3.00")

    def send(self, *lines, note="TEST ONLY weekly reorder"):
        return send_to_purchasing(actor=self.admin, business_unit=self.unit, lines=list(lines), note=note)

    def rows(self, actor=None):
        return {row["sku"]: row for row in low_stock_overview(actor=actor or self.admin, business_unit=self.unit)["rows"]}

    def test_sending_counts_as_on_order_so_it_is_not_suggested_twice(self):
        self.assertEqual(self.rows()[self.variant.sku]["suggested_order"], "10.00")
        result = self.send({"level_id": self.low["id"], "quantity": "12"}, {"level_id": self.out["id"], "quantity": "3"})
        self.assertEqual(result["sent"], 2)
        row = result["rows"][[r["sku"] for r in result["rows"]].index(self.variant.sku)]
        self.assertEqual((row["requested"], row["suggested_order"]), ("12.00", "0.00"))
        waiting = [r for r in result["requests"] if r["status"] == "sent"]
        self.assertEqual({r["sku"]: (r["quantity"], r["supplier"], r["supplier_item_code"]) for r in waiting}, {
            self.variant.sku: ("12.00", self.supplier.name, "RB-CH-01"),
            self.alternate_variant.sku: ("3.00", self.supplier.name, "RB-CH-01"),
        })
        with self.assertRaisesMessage(InvalidTransaction, "already waiting for purchasing"):
            self.send({"level_id": self.low["id"], "quantity": "1"})
        with self.assertRaisesMessage(InvalidTransaction, "more than zero"):
            self.send({"level_id": self.out["id"], "quantity": "0"})
        with self.assertRaises(WorkflowPermissionDenied):
            send_to_purchasing(actor=self.sales, business_unit=self.unit, lines=[{"level_id": self.low["id"], "quantity": "1"}])

    def test_purchasing_orders_it_or_says_why_not(self):
        self.send({"level_id": self.low["id"], "quantity": "10"}, {"level_id": self.out["id"], "quantity": "3"})
        requests = {r.variant_id: r for r in ReorderRequest.objects.all()}
        overview = low_stock_overview(actor=self.sales, business_unit=self.unit)
        self.assertTrue(overview["can_buy"])
        self.assertIsNone(next(r for r in overview["requests"] if r["sku"] == self.variant.sku)["matching_order"])
        order = create_purchase_commitment(
            actor=self.sales, idempotency_key="po", business_unit=self.unit, supplier=self.supplier,
            lines=[{"variant": self.variant, "ordered_quantity": Decimal("10.00"), "unit_cost": Decimal("900.00")}])
        overview = low_stock_overview(actor=self.sales, business_unit=self.unit)
        hint = next(r for r in overview["requests"] if r["sku"] == self.variant.sku)["matching_order"]
        self.assertEqual((hint["id"], hint["quantity"]), (order["purchase_commitment_id"], "10.00"))
        with self.assertRaises(WorkflowPermissionDenied):
            decide_request(actor=self.admin, business_unit=self.unit, request_id=requests[self.variant.pk].pk, action="ordered")
        result = decide_request(actor=self.sales, business_unit=self.unit, request_id=requests[self.variant.pk].pk, action="ordered")
        done = ReorderRequest.objects.get(pk=requests[self.variant.pk].pk)
        self.assertEqual((done.status, done.commitment_id, done.decided_by), ("ordered", order["purchase_commitment_id"], self.sales))
        row = next(r for r in result["rows"] if r["sku"] == self.variant.sku)
        self.assertEqual((row["requested"], row["on_order"], row["suggested_order"]), ("0.00", "10.00", "0.00"), "Now counted as a real order")
        with self.assertRaisesMessage(InvalidTransaction, "at least 5 characters"):
            decide_request(actor=self.sales, business_unit=self.unit, request_id=requests[self.alternate_variant.pk].pk, action="declined")
        decide_request(actor=self.sales, business_unit=self.unit, request_id=requests[self.alternate_variant.pk].pk, action="declined",
                       reason="TEST ONLY discontinued by the maker")
        self.assertEqual(self.rows()[self.alternate_variant.sku]["suggested_order"], "3.00", "Suggested again once declined")
        with self.assertRaisesMessage(InvalidTransaction, "already not ordered"):
            decide_request(actor=self.sales, business_unit=self.unit, request_id=requests[self.alternate_variant.pk].pk, action="ordered")

    def test_withdrawing_and_the_api(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        unit = f"?unit={self.unit.pk}"
        response = client.post(f"/api/inventory/reorder-requests/{unit}", {"lines": [{"level_id": self.out["id"], "quantity": "3"}]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        request_id = response.data["requests"][0]["id"]
        response = client.post(f"/api/inventory/reorder-requests/{request_id}/{unit}", {"action": "cancel"}, format="json")
        self.assertEqual((response.status_code, response.data["requests"][0]["status"]), (200, "cancelled"))
        buyer = APIClient()
        buyer.force_authenticate(self.sales)
        self.assertEqual(buyer.get(f"/api/inventory/low-stock/{unit}").status_code, 200)
        outsider = APIClient()
        outsider.force_authenticate(self.roche_user)
        self.assertEqual(outsider.post(f"/api/inventory/reorder-requests/{request_id}/{unit}", {"action": "ordered"}, format="json").status_code, 403)
