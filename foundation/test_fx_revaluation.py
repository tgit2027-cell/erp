"""Month-end revaluation of foreign currency owed to suppliers; synthetic only."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .fx_revaluation import fx_revaluation_overview, post_fx_revaluation
from .models import ClosedMonth, DemoUserMarker, FxRevaluation, PurchaseReceipt, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied, receive_imported_purchase
from .supplier_bills import record_bill
from .supplier_payments import _received, approve_payment, request_payment
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action


@override_settings(APP_ENV="demo")
class FxRevaluationTests(PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for user, action in [(cls.admin, A.PAY_SUPPLIER), (cls.sales, A.APPROVE_PAYMENT), (cls.admin, A.CLOSE_MONTH),
                             (cls.admin, A.VIEW_LEDGER), (cls.sales, A.VIEW_LEDGER)]:
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)
        for user in (cls.admin, cls.sales):
            DemoUserMarker.objects.get_or_create(user=user)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()
        self.month = self.today.replace(day=1)
        self.key = self.month.strftime("%Y-%m")
        self.last_day = (self.month + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
        # Pretend we are in the next month, so this month has ended.
        later = patch("foundation.fx_revaluation.timezone.localdate", return_value=self.last_day + datetime.timedelta(days=3))
        later.start()
        self.addCleanup(later.stop)
        # 2 x EUR 1,000 at 4.00 = QAR 8,000, plus USD 850 shipping at 3.64 = QAR 3,094 and QAR customs.
        self.receipt = receive_imported_purchase(
            actor=self.sales, idempotency_key="eur", business_unit=self.unit, supplier=self.supplier,
            lines=[{"variant": self.variant, "location": self.location, "quantity": Decimal("2.00"), "unit_cost": Decimal("1000.00")}],
            charges=[{"category": "shipping", "amount": Decimal("850.00"), "eligible": True, "currency": "USD",
                      "exchange_rate": "3.64000000", "rate_source": "TEST ONLY freight invoice", "rate_date": self.today.isoformat()},
                     {"category": "clearance", "amount": Decimal("1800.00"), "eligible": True}],
            currency="EUR", exchange_rate="4.00000000", rate_source="TEST ONLY bank rate on arrival", rate_date=self.today.isoformat(),
        )["purchase_receipt_id"]
        receipt = PurchaseReceipt.objects.get(pk=self.receipt)
        record_bill(actor=self.admin, idempotency_key="bill", receipt_id=self.receipt, bill_number="INV-1",
                    bill_date=self.today.isoformat(), amount=_received(receipt).payable)

    def pay(self, amount, rate, key):
        payment = request_payment(actor=self.admin, idempotency_key=key, receipt_id=self.receipt, amount=amount, exchange_rate=rate,
                                  rate_source="TEST ONLY bank advice", rate_date=self.today.isoformat(), paid_on=self.today.isoformat(),
                                  bank_reference="TT-1")
        return approve_payment(actor=self.sales, idempotency_key=f"{key}-ok", payment_id=payment["id"])

    def post(self, rates, source="TEST ONLY QCB closing rates", actor=None):
        return post_fx_revaluation(actor=actor or self.admin, business_unit=self.unit, month=self.key, rates=rates, source=source)

    @staticmethod
    def lines(entry):
        return {line.account.code: (line.debit, line.credit) for line in entry.lines.select_related("account")}

    def test_a_weaker_riyal_books_an_unrealised_loss_reversed_next_month(self):
        self.pay("500.00", "3.95000000", "part")
        preview = fx_revaluation_overview(actor=self.admin, business_unit=self.unit, rates={"EUR": "4.10"})
        selected = preview["selected"]
        self.assertEqual((selected["month"], selected["currencies"], selected["can_post"]), (self.key, ["EUR", "USD"], True))
        self.assertEqual(selected["last_rates"]["EUR"]["rate"], "3.95", "The latest rate used is offered as a start")
        self.assertEqual([(row["what"], row["owed"], row["carried"], row["booked_rate"], row["difference"]) for row in selected["rows"]], [
            ("Goods from " + self.supplier.name, "1500.00", "6000.00", "4.0000", "150.00"),
            ("Shipping", "850.00", "3094.00", "3.6400", None),
        ])
        self.assertFalse(selected["summary"]["complete"], "USD rate not given yet")
        with self.assertRaisesMessage(InvalidTransaction, "Enter the USD rate"):
            self.post({"EUR": "4.10"})
        with self.assertRaisesMessage(InvalidTransaction, "Say where the rates come from"):
            self.post({"EUR": "4.10", "USD": "3.64"}, source="")
        with self.assertRaisesMessage(InvalidTransaction, "The EUR rate looks wrong"):
            self.post({"EUR": "410", "USD": "3.64"})

        result = self.post({"EUR": "4.10", "USD": "3.6450"})
        run = FxRevaluation.objects.get()
        # EUR 1,500 at 4.10 = 6,150 (loss 150); USD 850 at 3.645 = 3,098.25 (loss 4.25).
        self.assertEqual((run.net, run.rates), (Decimal("154.25"), {"EUR": "4.10", "USD": "3.6450"}))
        self.assertEqual(run.journal_entry.document_date, self.last_day)
        self.assertEqual(self.lines(run.journal_entry), {
            "TEST_GRNI": (Decimal("0.00"), Decimal("150.00")),
            "TEST_LANDED_CLEARING": (Decimal("0.00"), Decimal("4.25")),
            "TEST_FX_UNREALISED": (Decimal("154.25"), Decimal("0.00")),
        })
        self.assertEqual(run.reversal_entry.document_date, self.last_day + datetime.timedelta(days=1))
        self.assertEqual(self.lines(run.reversal_entry), {
            "TEST_GRNI": (Decimal("150.00"), Decimal("0.00")),
            "TEST_LANDED_CLEARING": (Decimal("4.25"), Decimal("0.00")),
            "TEST_FX_UNREALISED": (Decimal("0.00"), Decimal("154.25")),
        })
        self.assertEqual(result["history"][0]["net"], "154.25")
        self.assertEqual(result["selected"]["blocked"], f"{self.month:%B %Y} is already revalued.")
        with self.assertRaisesMessage(InvalidTransaction, "already revalued"):
            self.post({"EUR": "4.10", "USD": "3.6450"})

    def test_a_gain_and_the_later_payment_still_works_out_the_full_realised_gain(self):
        self.post({"EUR": "3.80", "USD": "3.64"})
        run = FxRevaluation.objects.get()
        self.assertEqual(run.net, Decimal("-400.00"), "EUR 2,000 now worth 7,600 instead of 8,000")
        self.assertEqual(self.lines(run.journal_entry), {
            "TEST_GRNI": (Decimal("400.00"), Decimal("0.00")),
            "TEST_FX_UNREALISED": (Decimal("0.00"), Decimal("400.00")),
        })
        paid = self.pay("2000.00", "3.80000000", "full")
        self.assertEqual((paid["cleared_value"], paid["exchange_difference"]), ("8000.00", "400.00"))

    def test_who_can_post_and_closed_months(self):
        with self.assertRaises(WorkflowPermissionDenied):
            self.post({"EUR": "4.10", "USD": "3.64"}, actor=self.sales)
        self.assertFalse(fx_revaluation_overview(actor=self.sales, business_unit=self.unit)["selected"]["can_post"])
        ClosedMonth.objects.create(legal_entity=self.unit.legal_entity, business_unit=self.unit, month=self.month,
                                   closed_by=self.admin, entry_count=0, total_debit=0)
        with self.assertRaisesMessage(InvalidTransaction, "Revalue before closing the month"):
            self.post({"EUR": "4.10", "USD": "3.64"})
        ClosedMonth.objects.all().delete()
        self.pay("2000.00", "4.00000000", "goods")
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.get(f"/api/finance/ledger/fx-revaluation/?unit={self.unit.pk}&month={self.key}&rate_USD=3.70")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row["difference"] for row in response.json()["selected"]["rows"]], ["51.00"], "Only the shipping is still owed")
        response = client.post(f"/api/finance/ledger/fx-revaluation/?unit={self.unit.pk}",
                               {"month": self.key, "rates": {"USD": "3.70"}, "source": "TEST ONLY QCB"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["history"][0]["net"], "51.00")
