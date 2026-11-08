"""Stock value report by location, brand and category on a date; synthetic only."""

import datetime
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import StockBalance, StockMovement, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .stock_value import stock_value_report
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action


@override_settings(APP_ENV="demo")
class StockValueTests(PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for user, action in [(cls.admin, A.READ_STOCK), (cls.admin, A.VIEW_COSTS), (cls.admin, A.VIEW_LEDGER),
                             (cls.sales, A.READ_STOCK)]:
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        # The shelf stock arrived ten days ago (movements cannot be changed later, so give them that time as made).
        ten_days_ago = timezone.now() - datetime.timedelta(days=10)
        with patch.dict(StockMovement._meta.get_field("created_at").__dict__, {"_get_default": lambda: ten_days_ago}):
            self.make_receipt(key="shelf", quantity="6.00", unit_cost="1000.00", charges=[])
        self.make_receipt(key="other", quantity="4.00", unit_cost="500.00", charges=[], location=self.other_location,
                          variant=self.alternate_variant)

    def report(self, **kwargs):
        return stock_value_report(actor=self.admin, business_unit=self.unit, **kwargs)

    def test_today_matches_stock_on_hand_and_the_ledger(self):
        data = self.report()
        on_hand = sum(StockBalance.objects.filter(business_unit=self.unit).values_list("on_hand_value", flat=True), Decimal("0"))
        self.assertEqual(Decimal(data["total"]["value"]), on_hand)
        self.assertEqual((data["total"]["value"], data["total"]["item_count"]), ("8000.00", 2))
        self.assertEqual(data["ledger"], {"balance": "8000.00", "difference": "0.00", "agrees": True})
        self.assertEqual([(row["name"], row["value"], row["share"]) for row in data["groups"]["location"]],
                         [(self.location.name, "6000.00", "75.0"), (self.other_location.name, "2000.00", "25.0")])
        self.assertEqual([row["value"] for row in data["groups"]["brand"]], ["8000.00"])
        top = data["lines"][0]
        self.assertEqual((top["sku"], top["quantity"], top["average_cost"], top["value"]), (self.variant.sku, "6.00", "1000.00", "6000.00"))

    def test_an_earlier_date_leaves_out_later_movements_and_shows_the_gap_with_the_ledger(self):
        earlier = (timezone.localdate() - datetime.timedelta(days=5)).isoformat()
        data = self.report(as_of=earlier)
        self.assertEqual((data["as_of"], data["total"]["value"], len(data["lines"])), (earlier, "6000.00", 1))
        self.assertEqual(data["ledger"], {"balance": "0.00", "difference": "6000.00", "agrees": False},
                         "The receipt was booked in the ledger with today's date")
        with self.assertRaisesMessage(InvalidTransaction, "today or an earlier date"):
            self.report(as_of=(timezone.localdate() + datetime.timedelta(days=1)).isoformat())
        with self.assertRaisesMessage(InvalidTransaction, "YYYY-MM-DD"):
            self.report(as_of="last week")

    def test_costs_and_ledger_permissions_and_the_excel_download(self):
        with self.assertRaisesMessage(WorkflowPermissionDenied, "see costs"):
            stock_value_report(actor=self.sales, business_unit=self.unit)
        UserActionGrant.objects.get_or_create(user=self.sales, business_unit=self.unit, action=A.VIEW_COSTS)
        UserActionGrant.objects.filter(user=self.sales, business_unit=self.unit, action=A.VIEW_LEDGER).delete()
        self.assertIsNone(stock_value_report(actor=self.sales, business_unit=self.unit)["ledger"])
        with self.assertRaises(WorkflowPermissionDenied):
            stock_value_report(actor=self.admin, business_unit=self.roche)
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.get("/api/inventory/stock-value/", {"unit": self.unit.pk})
        self.assertEqual((response.status_code, response.json()["total"]["value"]), (200, "8000.00"))
        response = client.get("/api/inventory/stock-value/", {"unit": self.unit.pk, "export": "xlsx"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"stock-value-{self.unit.code}-{timezone.localdate().isoformat()}.xlsx", response["Content-Disposition"])
        sheet = ZipFile(BytesIO(response.content)).read("xl/worksheets/sheet1.xml").decode()
        self.assertIn(self.variant.sku, sheet)
        self.assertIn("Stock account in the ledger", sheet)
        client.force_authenticate(self.sales)
        UserActionGrant.objects.filter(user=self.sales, action=A.VIEW_COSTS).delete()
        self.assertEqual(client.get("/api/inventory/stock-value/", {"unit": self.unit.pk}).status_code, 403)
