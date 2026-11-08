"""Low-stock levels and alerts; synthetic only."""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .low_stock import alert_count, low_stock_overview, remove_level, set_level
from .models import SalesQuote, PurchaseCommitmentLine, PurchaseCommitment, StockReorderLevel, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied, create_purchase_commitment, receive_imported_purchase
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction


@override_settings(APP_ENV="demo")
class LowStockTests(PurchasingFixtureMixin, TestCase):
    """Admin sets levels; sales only reads stock."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.create(user=cls.admin, business_unit=cls.unit, action=UserActionGrant.Action.MANAGE_REORDER)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.make_receipt(key="shelf", quantity="6.00", charges=[])
        self.make_receipt(key="other", quantity="4.00", charges=[], location=self.other_location)

    def level(self, minimum, location=None, reorder=None, variant=None):
        return set_level(actor=self.admin, business_unit=self.unit, variant_id=(variant or self.variant).pk,
                         location_id=location.pk if location else None, minimum_quantity=minimum, reorder_quantity=reorder)

    def row(self, level_id):
        return next(row for row in low_stock_overview(actor=self.sales, business_unit=self.unit)["rows"] if row["id"] == level_id)

    def test_levels_compare_free_stock_at_a_location_or_across_the_unit(self):
        here = self.level("5.00", self.location, reorder="10.00")
        everywhere = self.level("7.00")
        self.assertEqual((here["free"], here["status"], here["suggested_order"]), ("6.00", "ok", "0.00"))
        self.assertEqual((everywhere["free"], everywhere["on_hand"], everywhere["status"]), ("10.00", "10.00", "ok"))
        quote = SalesQuote.objects.get(pk=self.make_quote(key="hold", quantity="2.00")["quote_id"])
        self.pay(quote, key="hold-pay", amount="4000.00")  # reserved for a paid order, no longer free
        row = self.row(here["id"])
        self.assertEqual((row["free"], row["on_hand"], row["status"], row["short_by"]), ("4.00", "6.00", "low", "1.00"))
        self.assertEqual(row["suggested_order"], "10.00", "At least the usual order quantity")
        self.assertEqual(self.row(everywhere["id"])["status"], "ok", "8 free across both locations, minimum 7")
        self.assertEqual(alert_count(actor=self.sales, business_unit=self.unit), 1)
        overview = low_stock_overview(actor=self.sales, business_unit=self.unit)
        self.assertEqual((overview["counts"], overview["can_manage"]), ({"out": 0, "low": 1, "ok": 1}, False))
        self.assertNotIn("variants", overview, "Only people who set levels get the pick lists")

    def test_out_of_stock_and_an_order_that_already_covers_it(self):
        out = self.level("1.00", variant=self.alternate_variant, reorder="3.00")
        self.assertEqual((out["status"], out["status_label"], out["suggested_order"]), ("out", "Out of stock", "3.00"))
        commitment = create_purchase_commitment(
            actor=self.sales, idempotency_key="order", business_unit=self.unit, supplier=self.supplier,
            lines=[{"variant": self.alternate_variant, "ordered_quantity": Decimal("5.00"), "unit_cost": Decimal("50.00")}],
        )
        row = self.row(out["id"])
        self.assertEqual((row["on_order"], row["covered_by_order"], row["suggested_order"]), ("5.00", True, "0.00"))
        receive_imported_purchase(
            actor=self.sales, idempotency_key="order-in", business_unit=self.unit, supplier=self.supplier, charges=[],
            purchase_commitment=PurchaseCommitment.objects.get(pk=commitment["purchase_commitment_id"]),
            lines=[{"variant": self.alternate_variant, "location": self.location, "quantity": Decimal("2.00"),
                    "unit_cost": Decimal("50.00"), "quote_line": None,
                    "commitment_line": PurchaseCommitmentLine.objects.get(pk=commitment["lines"][0]["commitment_line_id"])}],
        )
        row = self.row(out["id"])
        self.assertEqual((row["free"], row["on_order"], row["status"]), ("2.00", "3.00", "ok"), "Received stock clears the alert")

    def test_setting_changing_removing_and_rules(self):
        first = self.level("5.00", self.location)
        changed = self.level("7.00", self.location, reorder="12.00")
        self.assertEqual((first["created"], changed["created"], changed["id"]), (True, False, first["id"]))
        self.assertEqual(StockReorderLevel.objects.count(), 1)
        with self.assertRaisesMessage(InvalidTransaction, "more than zero"):
            self.level("5.00", self.location, reorder="0")
        with self.assertRaisesMessage(InvalidTransaction, "zero or more"):
            self.level("-1")
        with self.assertRaisesMessage(InvalidTransaction, "Choose an item"):
            set_level(actor=self.admin, business_unit=self.unit, variant_id=None, minimum_quantity="1")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "set low-stock levels"):
            set_level(actor=self.sales, business_unit=self.unit, variant_id=self.variant.pk, minimum_quantity="1")
        remove_level(actor=self.admin, business_unit=self.unit, level_id=first["id"])
        self.assertFalse(StockReorderLevel.objects.exists())
        with self.assertRaisesMessage(WorkflowPermissionDenied, "read stock or set low-stock levels"):
            low_stock_overview(actor=self.roche_user, business_unit=self.unit)
        self.assertEqual(alert_count(actor=self.roche_user, business_unit=self.unit), 0)

    def test_api(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        unit = f"?unit={self.unit.pk}"
        made = client.post(f"/api/inventory/low-stock/{unit}", {
            "variant_id": self.variant.pk, "location_id": self.location.pk, "minimum_quantity": "6.00", "reorder_quantity": "4.00",
        }, format="json")
        self.assertEqual((made.status_code, made.data["status"]), (201, "low"), made.data)
        again = client.post(f"/api/inventory/low-stock/{unit}", {"variant_id": self.variant.pk, "location_id": self.location.pk,
                                                                 "minimum_quantity": "2.00"}, format="json")
        self.assertEqual((again.status_code, again.data["status"]), (200, "ok"))
        overview = client.get(f"/api/inventory/low-stock/{unit}")
        self.assertEqual((overview.status_code, overview.data["can_manage"], len(overview.data["rows"])), (200, True, 1))
        self.assertIn(self.variant.pk, [each["id"] for each in overview.data["variants"]])
        self.assertEqual(client.get(f"/api/inventory/low-stock/{unit}&summary=1").data, {"alerts": 0})
        reader = APIClient()
        reader.force_authenticate(self.sales)
        self.assertEqual(reader.delete(f"/api/inventory/low-stock/{made.data['id']}/{unit}").status_code, 403)
        self.assertEqual(client.delete(f"/api/inventory/low-stock/{made.data['id']}/{unit}").status_code, 200)
        outsider = APIClient()
        outsider.force_authenticate(self.roche_user)
        self.assertEqual(outsider.get(f"/api/inventory/low-stock/{unit}").status_code, 403)
