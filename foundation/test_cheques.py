"""Post-dated cheques: received from customers and written to suppliers; synthetic only."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .cash_accounts import cash_overview, create_account
from .master_admin_testing import by_master_admin
from .cash_forecast import cash_forecast
from .cheques import cheques_overview, record_cheque, update_cheque
from .models import Cheque, JournalEntry, PurchaseReceipt, QuotePayment, SalesQuote, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .supplier_bills import record_bill
from .supplier_payments import _received, request_payment
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action


@override_settings(APP_ENV="demo")
class ChequeTests(PurchasingFixtureMixin, TestCase):
    """Admin records bank and cash; sales can only view the ledger."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for user, action in [(cls.admin, A.MANAGE_CASH), (cls.admin, A.PAY_SUPPLIER), (cls.sales, A.VIEW_LEDGER)]:
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()
        start = (self.today - datetime.timedelta(days=60)).isoformat()
        self.default = by_master_admin(create_account)(actor=self.admin, business_unit=self.unit, name="TEST ONLY QNB current", kind="bank",
                                      bank_name="TEST ONLY QNB", opening_date=start, is_default=True)
        self.dukhan = by_master_admin(create_account)(actor=self.admin, business_unit=self.unit, name="TEST ONLY Dukhan current", kind="bank",
                                     bank_name="TEST ONLY Dukhan Bank", opening_date=start)
        self.make_receipt(key="stock", charges=[])
        self.quote = SalesQuote.objects.get(pk=self.make_quote(key="sale")["quote_id"])
        self.total = self.quote.total_quote_value

    def day(self, days):
        return (self.today + datetime.timedelta(days=days)).isoformat()

    def receive(self, amount, days=0, number="100200", **extra):
        return record_cheque(actor=self.admin, business_unit=self.unit, data={
            "direction": "received", "order_id": self.quote.pk, "number": number, "drawn_on": "TEST ONLY Commercial Bank",
            "amount": str(amount), "cheque_date": self.day(days), "received_on": self.day(-2), **extra})

    def act(self, cheque, action, reason="", account_id=None):
        return update_cheque(actor=self.admin, business_unit=self.unit, cheque_id=cheque["id"], action=action, reason=reason,
                             account_id=account_id)

    def test_a_cheque_is_paid_only_when_the_bank_clears_it(self):
        half = (self.total / 2).quantize(Decimal("0.01"))
        today_cheque = self.receive(half)
        later = self.receive(self.total - half, days=30, number="100201")
        self.assertEqual((today_cheque["status"], today_cheque["actions"]), ("held", ["deposit", "return"]))
        self.assertEqual((today_cheque["received_on"], today_cheque["cheque_date"], today_cheque["account"]),
                         (self.day(-2), self.day(0), None), "Both dates kept; no bank account until it clears")
        with self.assertRaisesMessage(InvalidTransaction, "cannot be in the future"):
            self.receive("1.00", number="100299", received_on=self.day(1))
        self.assertEqual(later["actions"], ["return"], "Not before the date on it")
        with self.assertRaisesMessage(InvalidTransaction, "can be deposited from that day"):
            self.act(later, "deposit")
        with self.assertRaisesMessage(InvalidTransaction, "is not paid or covered by another cheque"):
            self.receive("1.00", number="100202")
        self.assertFalse(self.quote.payments.exists(), "Nothing is booked while the cheque is held")

        deposited = self.act(today_cheque, "deposit")
        self.assertEqual((deposited["status"], deposited["actions"]), ("deposited", ["clear", "bounce"]))
        with self.assertRaisesMessage(InvalidTransaction, "Choose the bank account the money went into"):
            self.act(today_cheque, "clear")
        cleared = self.act(today_cheque, "clear", account_id=self.dukhan["id"])
        self.assertEqual(cleared["status"], "cleared")
        payment = QuotePayment.objects.get(cheque__pk=today_cheque["id"])
        self.assertEqual((payment.amount, payment.method), (half, QuotePayment.Method.SIMULATED_CHEQUE))
        entry = JournalEntry.objects.get(pk=cleared["journal_entry_id"])
        self.assertEqual(entry.lines.get(account__code="TEST_CASH").debit, half)
        accounts = {row["id"]: row for row in cash_overview(actor=self.admin, business_unit=self.unit)["accounts"]}
        self.assertEqual(Decimal(accounts[self.dukhan["id"]]["balance"]), half, "The money lands in the account chosen on clearing")
        self.assertEqual(cleared["account"]["id"], self.dukhan["id"])
        self.assertEqual(Decimal(accounts[self.default["id"]]["balance"]), Decimal("0.00"))

    def test_a_bounced_cheque_leaves_the_customer_owing_until_it_is_replaced(self):
        cheque = self.receive(self.total, number="300400")
        self.act(cheque, "deposit")
        with self.assertRaisesMessage(InvalidTransaction, "Give the reason"):
            self.act(cheque, "bounce")
        bounced = self.act(cheque, "bounce", "TEST ONLY insufficient funds")
        self.assertEqual((bounced["status_label"], bounced["actions"]), ("Bounced", ["replace"]))
        self.assertFalse(self.quote.payments.exists())
        overview = cheques_overview(actor=self.admin, business_unit=self.unit)
        self.assertEqual(overview["tiles"]["bounced"], {"count": 1, "total": f"{self.total:.2f}"})
        replacement = self.receive(self.total, number="300401", replaces_id=cheque["id"])
        self.assertEqual(replacement["replaces"], "300400")
        overview = cheques_overview(actor=self.admin, business_unit=self.unit)
        self.assertEqual(overview["tiles"]["bounced"]["count"], 0)
        self.assertEqual([row["replaced_by"] for row in overview["finished"]], ["300401"])
        with self.assertRaisesMessage(InvalidTransaction, "not been replaced yet"):
            self.receive("1.00", number="300402", replaces_id=cheque["id"])

    def test_returning_a_held_cheque_and_the_same_cheque_twice(self):
        cheque = self.receive("500.00", days=10)
        with self.assertRaisesMessage(InvalidTransaction, "already recorded for this customer"):
            self.receive("500.00", days=10)
        returned = self.act(cheque, "return", "TEST ONLY customer paid by card instead")
        self.assertEqual(returned["status"], "returned")
        with self.assertRaisesMessage(InvalidTransaction, "cannot be done now"):
            self.act(cheque, "deposit")
        with self.assertRaisesMessage(InvalidTransaction, "more than 6 months old"):
            self.receive("10.00", days=-200, number="555555")

    def test_cheques_written_and_the_forecast(self):
        receipt = PurchaseReceipt.objects.get()
        record_bill(actor=self.admin, idempotency_key="bill", receipt_id=receipt.pk, bill_number="INV-1",
                    bill_date=self.today.isoformat(), amount=_received(receipt).payable)
        payment = request_payment(actor=self.admin, idempotency_key="pay", receipt_id=receipt.pk, amount=str(_received(receipt).payable),
                                  paid_on=self.today.isoformat())
        written = record_cheque(actor=self.admin, business_unit=self.unit, data={
            "direction": "issued", "payee": "TEST ONLY Roche Bobois", "number": "000123", "amount": "2500.00",
            "cheque_date": self.day(21), "account_id": self.default["id"], "supplier_payment_id": payment["id"]})
        self.assertEqual((written["status"], written["actions"]), ("issued", ["clear", "cancel"]))
        with self.assertRaisesMessage(InvalidTransaction, "already recorded"):
            record_cheque(actor=self.admin, business_unit=self.unit, data={
                "direction": "issued", "payee": "TEST ONLY other", "number": "000123", "amount": "1.00",
                "cheque_date": self.day(1), "account_id": self.default["id"]})
        self.receive("700.00", days=9, number="777001")
        forecast = cash_forecast(actor=self.admin, business_unit=self.unit, weeks=8)
        lines = [line for week in forecast["weeks"] for line in week["lines"]]
        cheques = [(line["date"], line["direction"], line["amount"]) for line in lines if line["source"] == "cheques"]
        self.assertEqual(cheques, [(self.day(9), "in", "700.00"), (self.day(21), "out", "2500.00")])
        self.assertFalse([line for line in lines if line["source"] == "prepared"], "The cheque replaces the prepared payment's date")
        overview = cheques_overview(actor=self.admin, business_unit=self.unit)
        self.assertEqual(overview["tiles"]["to_cash"], {"count": 1, "total": "2500.00"})
        self.assertEqual(overview["weeks"][1]["in"], "700.00")
        self.assertEqual(overview["weeks"][3]["out"], "2500.00")
        cancelled = update_cheque(actor=self.admin, business_unit=self.unit, cheque_id=written["id"], action="cancel",
                                  reason="TEST ONLY stopped, paid by transfer")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(Cheque.objects.get(pk=written["id"]).finished_on, self.today)

    def test_who_can_see_and_record(self):
        self.receive("100.00")
        overview = cheques_overview(actor=self.sales, business_unit=self.unit)
        self.assertFalse(overview["can_manage"])
        self.assertEqual(overview["received"][0]["actions"], [])
        self.assertEqual(overview["orders"], [])
        with self.assertRaises(WorkflowPermissionDenied):
            record_cheque(actor=self.sales, business_unit=self.unit, data={"direction": "received"})
        with self.assertRaisesMessage(InvalidTransaction, "4 to 12 digits"):
            self.receive("1.00", number="12")
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.get(f"/api/finance/cheques/?unit={self.unit.pk}")
        self.assertEqual(response.status_code, 200)
        cheque_id = response.json()["received"][0]["id"]
        response = client.post(f"/api/finance/cheques/{cheque_id}/?unit={self.unit.pk}", {"action": "deposit"}, format="json")
        self.assertEqual(response.json()["status"], "deposited")
