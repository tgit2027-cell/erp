"""Unpaid invoices and supplier bills from the old system: import, aging, statements, and payments on them."""

import datetime
from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .go_live_imports import decide, preview, submit, template
from .models import CashAccount, CashLineAssignment, JournalLine, OldBalance, UserActionGrant
from .old_balances import decide as decide_payment
from .old_balances import overview, prepare
from .payables import payables_aging
from .purchasing_services import WorkflowPermissionDenied
from .receivables import customer_statement, receivables_aging
from . import test_go_live_imports as go_live
from .test_go_live_imports import xlsx
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action
CUSTOMER_HEAD = ["Business unit*", "Client number*", "Invoice number*", "Invoice date*", "Still owed QAR*", "Note"]
SUPPLIER_HEAD = ["Business unit*", "Supplier code*", "Bill number*", "Bill date*", "Currency", "Still owed*", "Still owed QAR", "Note"]
RATE_HEAD = ["Business unit*", "Supplier code*", "Bill number*", "Bill date*", "Currency", "Still owed*", "Still owed QAR", "Rate"]


@override_settings(APP_ENV="demo")
class OldBalanceTests(PurchasingFixtureMixin, TestCase):
    setUp = go_live.GoLiveImportTests.setUp

    @classmethod
    def setUpTestData(cls):
        go_live.GoLiveItemImportTests.setUpTestData.__func__(cls)
        for user, action in ((cls.sales, A.MANAGE_CASH), (cls.roche_user, A.APPROVE_JOURNAL),
                             (cls.sales, A.PAY_SUPPLIER), (cls.roche_user, A.APPROVE_PAYMENT), (cls.admin, A.VIEW_LEDGER)):
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)
        cls.bank = CashAccount.objects.create(business_unit=cls.unit, name="TEST ONLY QNB current", kind="bank", account_type="current",
                                              created_by=cls.admin, opening_date=timezone.localdate() - datetime.timedelta(days=400))

    def day(self, back=1):
        return (timezone.localdate() - datetime.timedelta(days=back)).isoformat()

    def ledger(self, code):
        totals = JournalLine.objects.filter(account__code=code, entry__business_unit=self.unit).aggregate(d=Sum("debit"), c=Sum("credit"))
        return (totals["d"] or 0) - (totals["c"] or 0)

    def bring_in(self, kind, head, rows):
        batch = submit(actor=self.admin, kind=kind, file_name=f"TEST ONLY {kind}.xlsx", content=xlsx([head, *rows]), balances_on=self.day())
        decide(actor=self.roche_user, batch_id=batch["id"], action="approve")
        return batch

    def customer_rows(self):
        number = self.customer.client_number.lower()
        return [["tvl", number, "INV-0815", self.day(45), "18,500", "Sofa balance"], ["TVL", number, "INV-0901", self.day(10), "1500", ""]]

    def test_customer_balances_are_booked_aged_and_on_the_statement(self):
        before = self.ledger("TEST_RECEIVABLE")
        self.bring_in("customer_balances", CUSTOMER_HEAD, self.customer_rows())
        self.assertEqual(self.ledger("TEST_RECEIVABLE") - before, Decimal("20000.00"))
        self.assertEqual(self.ledger("TEST_OPENING_BALANCES"), Decimal("-20000.00"))
        aging = receivables_aging(actor=self.admin, business_unit=self.unit)
        row = next(row for row in aging["customers"] if row["id"] == self.customer.pk)
        old = [invoice for invoice in row["invoices"] if invoice["kind"] == "old_balance"]
        self.assertEqual([(invoice["number"], invoice["owed"], invoice["bucket"]) for invoice in old],
                         [("INV-0815", "18500.00", "days_31_60"), ("INV-0901", "1500.00", "current")])
        statement = customer_statement(actor=self.admin, business_unit=self.unit, customer_id=self.customer.pk,
                                       start=self.day(60), show="all")
        self.assertIn("INV-0815", [line["reference"] for line in statement["lines"] if line["kind"] == "old_balance"])

        # A receipt: prepared by one person, approved by another, into the named bank account.
        balance = OldBalance.objects.get(reference="INV-0815")
        with self.assertRaises(InvalidTransaction):
            prepare(actor=self.sales, business_unit=self.unit, data={"balance_id": balance.pk, "amount": "18500.01",
                                                                     "paid_on": self.day(0), "cash_account_id": self.bank.pk})
        with self.assertRaises(WorkflowPermissionDenied):
            prepare(actor=self.roche_user, business_unit=self.unit, data={"balance_id": balance.pk, "amount": "100",
                                                                          "paid_on": self.day(0), "cash_account_id": self.bank.pk})
        waiting = prepare(actor=self.sales, business_unit=self.unit, data={"balance_id": balance.pk, "amount": "8500",
                                                                           "paid_on": self.day(0), "cash_account_id": self.bank.pk,
                                                                           "bank_reference": "TT 4471"})
        self.assertEqual(waiting["status"], "requested")
        with self.assertRaises(WorkflowPermissionDenied):
            decide_payment(actor=self.sales, business_unit=self.unit, settlement_id=waiting["id"], action="approve")
        cash = self.ledger("TEST_CASH")
        done = decide_payment(actor=self.roche_user, business_unit=self.unit, settlement_id=waiting["id"], action="approve")
        self.assertEqual((done["status"], done["cleared_value"]), ("approved", "8500.00"))
        self.assertEqual(self.ledger("TEST_CASH") - cash, Decimal("8500.00"))
        self.assertTrue(CashLineAssignment.objects.filter(account=self.bank, journal_line__entry_id=done["journal_entry_id"]).exists())
        listed = overview(actor=self.sales, business_unit=self.unit, side="customer")
        self.assertEqual({row["reference"]: row["owed"] for row in listed["balances"]}, {"INV-0815": "10000.00", "INV-0901": "1500.00"})
        aging = receivables_aging(actor=self.admin, business_unit=self.unit)
        row = next(row for row in aging["customers"] if row["id"] == self.customer.pk)
        self.assertIn(("INV-0815", "10000.00"), [(invoice["number"], invoice["owed"]) for invoice in row["invoices"]])

    def test_customer_balance_rows_are_checked(self):
        number = self.customer.client_number
        content = xlsx([CUSTOMER_HEAD, ["TVL", "C-999999", "", "someday", "0", ""], ["TVL", number, "INV-1", self.day(-5), "10", ""],
                        ["TVL", number, "INV-2", self.day(5), "10", ""], ["TVL", number, "inv-2", self.day(5), "10", ""]])
        result = preview(actor=self.admin, kind="customer_balances", file_name="x.xlsx", content=content, balances_on=self.day())
        problems = {row["row"]: row["problems"] for row in result["problems"]}
        self.assertEqual(problems[2], ["No customer has the client number C-999999. Import the customers first.", "Invoice number is missing.",
                                       "Invoice date must be a date, for example 2026-08-15 or 15/08/2026.",
                                       "Still owed QAR must be more than zero."])
        self.assertEqual(len(problems[3]), 1)
        self.assertIn("is after the balances day", problems[3][0])
        self.assertEqual(problems[5], [f"inv-2 for {self.customer.name} is also on row 4."])

    def test_supplier_balances_in_euro_with_an_exchange_difference(self):
        before = self.ledger("TEST_GRNI")
        self.bring_in("supplier_balances", SUPPLIER_HEAD, [["TVL", "IMPORT-01", "GT-55120", self.day(100), "eur", "12000", "48600", ""],
                                                           ["TVL", self.supplier.code, "GT-55200", self.day(20), "", "5000", "", ""]])
        self.assertEqual(self.ledger("TEST_GRNI") - before, Decimal("-53600.00"))
        aging = payables_aging(actor=self.admin, business_unit=self.unit)
        row = next(row for row in aging["suppliers"] if row["id"] == self.supplier.pk)
        old = {item["bill"]["number"]: (item["owed"], item["owed_in_currency"], item["bucket"]) for item in row["items"]
               if item["paying_for"] == "old_balance"}
        self.assertEqual(old, {"GT-55120": ("48600.00", "12000.00", "days_91_120"), "GT-55200": ("5000.00", None, "current")})
        balance = OldBalance.objects.get(reference="GT-55120")
        with self.assertRaises(InvalidTransaction):  # a EUR bill needs the day's rate
            prepare(actor=self.sales, business_unit=self.unit, data={"balance_id": balance.pk, "amount": "6000",
                                                                     "paid_on": self.day(0), "cash_account_id": self.bank.pk})
        terms = {"exchange_rate": "4.10000000", "rate_source": "TEST ONLY QNB board rate", "rate_date": self.day(0)}
        first = prepare(actor=self.sales, business_unit=self.unit, data={"balance_id": balance.pk, "amount": "6000",
                                                                         "paid_on": self.day(0), "cash_account_id": self.bank.pk, **terms})
        with self.assertRaises(InvalidTransaction):  # 6,000 is waiting, so only 6,000 is left
            prepare(actor=self.sales, business_unit=self.unit, data={"balance_id": balance.pk, "amount": "6000.01",
                                                                     "paid_on": self.day(0), "cash_account_id": self.bank.pk, **terms})
        second = prepare(actor=self.sales, business_unit=self.unit, data={"balance_id": balance.pk, "amount": "6000",
                                                                          "paid_on": self.day(0), "cash_account_id": self.bank.pk, **terms})
        loss = self.ledger("TEST_FX_LOSS")
        decide_payment(actor=self.roche_user, business_unit=self.unit, settlement_id=first["id"], action="approve")
        last = decide_payment(actor=self.roche_user, business_unit=self.unit, settlement_id=second["id"], action="approve")
        self.assertEqual((last["paid_value"], last["cleared_value"]), ("24600.00", "24300.00"))
        self.assertEqual(self.ledger("TEST_FX_LOSS") - loss, Decimal("600.00"))
        self.assertEqual(self.ledger("TEST_GRNI") - before, Decimal("-5000.00"))
        self.assertEqual([row["reference"] for row in overview(actor=self.sales, business_unit=self.unit, side="supplier")["balances"]],
                         ["GT-55200"])

    def test_rejection_needs_a_reason_and_the_screen_works(self):
        self.bring_in("customer_balances", CUSTOMER_HEAD, self.customer_rows())
        balance = OldBalance.objects.get(reference="INV-0901")
        client = APIClient()
        client.force_authenticate(self.sales)
        listed = client.get("/api/finance/old-balances/?side=customer", HTTP_X_BUSINESS_UNIT=str(self.unit.pk))
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual(len(listed.json()["balances"]), 2)
        made = client.post("/api/finance/old-balances/", {"balance_id": balance.pk, "amount": "1500", "paid_on": self.day(0),
                                                          "cash_account_id": self.bank.pk}, format="json",
                           HTTP_X_BUSINESS_UNIT=str(self.unit.pk))
        self.assertEqual(made.status_code, 201, made.content)
        client.force_authenticate(self.roche_user)
        url = f"/api/finance/old-balances/{made.json()['id']}/"
        self.assertEqual(client.post(url, {"action": "reject"}, format="json", HTTP_X_BUSINESS_UNIT=str(self.unit.pk)).status_code, 400)
        rejected = client.post(url, {"action": "reject", "note": "Wrong customer"}, format="json", HTTP_X_BUSINESS_UNIT=str(self.unit.pk))
        self.assertEqual(rejected.json()["status"], "rejected")

    def test_templates_examples_pass(self):
        for kind in ("customer_balances", "supplier_balances"):
            with self.subTest(kind):
                name, content = template(actor=self.admin, kind=kind)
                result = preview(actor=self.admin, kind=kind, file_name=name, content=content, balances_on=self.day())
                # The examples name parties that only exist after the customers and suppliers imports.
                self.assertEqual(result["rows"], 1)

    def test_foreign_bills_can_come_in_at_the_old_monthly_rate(self):
        before = self.ledger("TEST_GRNI")
        self.bring_in("supplier_balances", RATE_HEAD, [["TVL", "IMPORT-01", "GT-60001", self.day(30), "EUR", "12000", "", "4.05"],
                                                       ["TVL", "IMPORT-01", "GT-60002", self.day(30), "EUR", "1000", "4050.40", "4.05"]])
        self.assertEqual(self.ledger("TEST_GRNI") - before, Decimal("-52650.40"))
        content = xlsx([RATE_HEAD, ["TVL", "IMPORT-01", "GT-60003", self.day(30), "EUR", "1000", "", ""],
                        ["TVL", "IMPORT-01", "GT-60004", self.day(30), "EUR", "1000", "", "four"],
                        ["TVL", "IMPORT-01", "GT-60005", self.day(30), "EUR", "1000", "4100", "4.05"]])
        result = preview(actor=self.admin, kind="supplier_balances", file_name="x.xlsx", content=content, balances_on=self.day())
        problems = {row["row"]: row["problems"] for row in result["problems"]}
        self.assertEqual(problems[2], ["Give the QAR value of this EUR bill, or the monthly rate the old system used for it."])
        self.assertEqual(problems[3], ["Rate must be the QAR for one EUR, for example 4.05."])
        self.assertEqual(problems[4], ["Still owed QAR (4,100.00) does not match the amount at the rate (4,050.00). Give one of them, or correct it."])
