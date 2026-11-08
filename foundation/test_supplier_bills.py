"""Supplier bills matched against receipts before goods are paid; synthetic only."""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import DemoUserMarker, JournalEntry, SupplierBill, UserActionGrant
from .payables import payables_aging
from .purchasing_services import WorkflowPermissionDenied
from .scenario_reconciliation import build_scenario_reconciliation
from .supplier_bills import accept_bill, bills_overview, record_bill, reject_bill, void_bill
from .supplier_payments import payments_overview
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction
from . import test_supplier_payments as payment_tests


@override_settings(APP_ENV="demo")
class SupplierBillTests(PurchasingFixtureMixin, TestCase):
    """Admin prepares payments and records bills; sales approves payments and decides bill differences."""

    setUp = payment_tests.SupplierPaymentTests.setUp
    receive = payment_tests.SupplierPaymentTests.receive
    prepare = payment_tests.SupplierPaymentTests.prepare
    approve = payment_tests.SupplierPaymentTests.approve
    pay = payment_tests.SupplierPaymentTests.pay

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.create(user=cls.admin, business_unit=cls.unit, action=payment_tests.PAY)
        UserActionGrant.objects.create(user=cls.sales, business_unit=cls.unit, action=payment_tests.APPROVE)

    def bill(self, receipt, amount, number="INV-1", *, key=None, actor=None, day=None):
        return record_bill(actor=actor or self.admin, idempotency_key=key or f"bill-{number}-{amount}", receipt_id=receipt,
                           bill_number=number, bill_date=day or self.today, amount=amount)

    def eur_receipt(self, key, **extra):
        # 2 × EUR 1,000 at 4.00 = QAR 8,000, plus QAR 1,800 clearance.
        return self.receive(key, currency="EUR", unit_cost="1000.00", rate="4.00000000", bill=False, charges=[
            {"category": "clearance", "amount": Decimal("1800.00"), "eligible": True},
        ], **extra)

    def test_goods_cannot_be_paid_until_a_matching_bill_is_recorded(self):
        receipt = self.eur_receipt("gate")
        with self.assertRaisesMessage(InvalidTransaction, f"Record the supplier's bill for receipt {receipt}"):
            self.prepare(receipt, "100.00", "3.90000000", key="early")
        clearance = payments_overview(actor=self.admin, business_unit=self.unit)["open_receipts"][0]["items"][1]
        self.assertIsNone(clearance["bill"])
        self.prepare(receipt, "1800.00", charge_id=clearance["charge_id"], payee_name="TEST ONLY broker", key="charge")
        goods = payments_overview(actor=self.admin, business_unit=self.unit)["open_receipts"][0]["items"][0]
        self.assertEqual((goods["bill"]["status"], goods["bill"]["payable"]), ("missing", False))

        bill = self.bill(receipt, "2000.00")
        self.assertEqual((bill["status"], bill["expected_amount"], bill["difference"], bill["currency"]),
                         ("matched", "2000.00", "0.00", "EUR"))
        self.assertFalse(JournalEntry.objects.filter(kind=JournalEntry.Kind.SUPPLIER_BILL).exists())
        paid = self.pay(receipt, "2000.00", "3.90000000", key="goods")
        self.assertEqual((paid["cleared_value"], paid["outstanding"]), ("8000.00", "0.00"))

    def test_a_bill_can_be_settled_in_parts(self):
        receipt = self.eur_receipt("parts")
        self.bill(receipt, "2000.00", "INV-P")
        row = lambda: next(bill for bill in bills_overview(actor=self.admin, business_unit=self.unit)["bills"] if bill["receipt_id"] == receipt)
        self.assertEqual((row()["settlement"], row()["outstanding"]), ("unpaid", "2000.00"))
        self.pay(receipt, "750.00", "3.90000000", key="part-1")
        self.prepare(receipt, "500.00", "3.95000000", key="part-2")
        self.assertEqual((row()["settlement_label"], row()["paid"], row()["waiting"], row()["outstanding"]),
                         ("Part paid", "750.00", "500.00", "750.00"))
        with self.assertRaisesMessage(InvalidTransaction, "more than the 750.00 EUR still to pay"):
            self.prepare(receipt, "800.00", "3.95000000", key="too-much")
        self.pay(receipt, "750.00", "3.90000000", key="part-3")
        self.assertEqual((row()["settlement"], row()["outstanding"]), ("part_paid", "0.00"), "500 still waits for approval")

    def test_a_higher_bill_waits_for_a_second_person_then_is_payable_in_full(self):
        receipt = self.eur_receipt("higher")
        bill = self.bill(receipt, "2050.00")
        self.assertEqual((bill["status"], bill["difference"]), ("on_hold", "50.00"))
        with self.assertRaisesMessage(InvalidTransaction, "differs from receipt"):
            self.prepare(receipt, "100.00", "3.90000000", key="held")
        UserActionGrant.objects.create(user=self.admin, business_unit=self.unit, action=payment_tests.APPROVE)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "someone else must accept"):
            accept_bill(actor=self.admin, idempotency_key="own", bill_id=bill["id"])
        accepted = accept_bill(actor=self.sales, idempotency_key="accept", bill_id=bill["id"], note="Late price rise agreed")
        self.assertEqual(accepted, accept_bill(actor=self.sales, idempotency_key="accept", bill_id=bill["id"]))
        self.assertEqual((accepted["status"], accepted["decided_by"], accepted["difference_value"]),
                         ("accepted", self.sales.username, "200.00"))
        entry = JournalEntry.objects.get(pk=accepted["journal_entry_id"])
        self.assertEqual((entry.kind, entry.document_date.isoformat()), (JournalEntry.Kind.SUPPLIER_BILL, self.today))
        self.assertEqual({line.account.code: (line.debit, line.credit) for line in entry.lines.select_related("account")}, {
            "TEST_BILL_DIFFERENCES": (Decimal("200.00"), Decimal("0.00")),
            "TEST_GRNI": (Decimal("0.00"), Decimal("200.00")),
        })
        goods = payments_overview(actor=self.admin, business_unit=self.unit)["open_receipts"][0]["items"][0]
        self.assertEqual((goods["payable"], goods["carried_value"], goods["bill"]["payable"]), ("2050.00", "8200.00", True))
        with self.assertRaisesMessage(InvalidTransaction, "difference to decide"):
            reject_bill(actor=self.sales, bill_id=bill["id"], note="Too late")
        self.pay(receipt, "1000.00", "3.90000000", key="part")
        last = self.pay(receipt, "1050.00", "3.90000000", key="rest")
        # The last payment clears the bill's full value: 8,000 received plus 200 accepted, less the 4,000 already cleared.
        self.assertEqual((last["cleared_value"], last["outstanding"]), ("4200.00", "0.00"))
        report = build_scenario_reconciliation(self.unit)
        checks = {row["label"]: row for row in report["checks"]}
        moved = checks["Accepted supplier bill differences moved receipt clearing by the same value"]
        self.assertEqual((moved["expected"], moved["matches"]), ("200.00", True))
        self.assertTrue(report["all_match"], [row["label"] for row in report["checks"] if not row["matches"]])

    def test_a_lower_bill_is_owed_at_the_bill_amount_and_aged_from_the_bill_date(self):
        receipt = self.eur_receipt("lower")
        bill_day = (datetime.date.fromisoformat(self.today) - datetime.timedelta(days=40)).isoformat()
        bill = self.bill(receipt, "1990.00", day=bill_day)
        aging = payables_aging(actor=self.admin, business_unit=self.unit)
        goods = aging["suppliers"][0]["items"][0]
        self.assertEqual((goods["owed"], goods["bill"]["status"], goods["age_days"], goods["bucket"]),
                         ("8000.00", "on_hold", 40, "days_31_60"))
        accepted = accept_bill(actor=self.sales, idempotency_key="accept-lower", bill_id=bill["id"])
        self.assertEqual(accepted["difference_value"], "-40.00")
        lines = {line.account.code: (line.debit, line.credit)
                 for line in JournalEntry.objects.get(pk=accepted["journal_entry_id"]).lines.select_related("account")}
        self.assertEqual(lines["TEST_GRNI"], (Decimal("40.00"), Decimal("0.00")))
        goods = payables_aging(actor=self.admin, business_unit=self.unit)["suppliers"][0]["items"][0]
        self.assertEqual((goods["owed"], goods["owed_in_currency"]), ("7960.00", "1990.00"))
        with self.assertRaisesMessage(InvalidTransaction, "more than the 1990.00 EUR still to pay"):
            self.prepare(receipt, "2000.00", "3.90000000", key="too-much")
        self.assertEqual(self.pay(receipt, "1990.00", "4.00000000", key="all")["exchange_difference"], "0.00")
        self.assertTrue(build_scenario_reconciliation(self.unit)["all_match"])

    def test_rejecting_or_voiding_frees_the_receipt_for_the_right_bill(self):
        receipt = self.eur_receipt("fix")
        held = self.bill(receipt, "2100.00", "INV-9")
        with self.assertRaisesMessage(InvalidTransaction, "already has bill INV-9"):
            self.bill(receipt, "2000.00", "INV-10")
        with self.assertRaisesMessage(InvalidTransaction, "Say why"):
            reject_bill(actor=self.sales, bill_id=held["id"], note=" ")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "approve supplier payments"):
            reject_bill(actor=self.admin, bill_id=held["id"], note="Wrong")
        rejected = reject_bill(actor=self.sales, bill_id=held["id"], note="Supplier to reissue at the order price")
        self.assertEqual(rejected["status"], "rejected")
        # A rejected bill number can be used again once the supplier corrects it.
        matched = self.bill(receipt, "2000.00", "inv-9")
        other = self.eur_receipt("other")
        with self.assertRaisesMessage(InvalidTransaction, f"is already recorded on receipt {receipt}"):
            self.bill(other, "2000.00", "INV-9")
        self.assertTrue(bills_overview(actor=self.admin, business_unit=self.unit)["bills"][0]["can_void"])
        self.prepare(receipt, "100.00", "3.90000000", key="started")
        self.assertFalse(next(row for row in bills_overview(actor=self.admin, business_unit=self.unit)["bills"]
                              if row["id"] == matched["id"])["can_void"])
        with self.assertRaisesMessage(InvalidTransaction, "cannot be voided"):
            void_bill(actor=self.admin, bill_id=matched["id"], note="Typo")
        clean = self.eur_receipt("void")
        typo = self.bill(clean, "2000.00", "INV-11")
        with self.assertRaisesMessage(InvalidTransaction, "Say why"):
            void_bill(actor=self.admin, bill_id=typo["id"], note="")
        self.assertEqual(void_bill(actor=self.admin, bill_id=typo["id"], note="Wrong receipt")["status"], "voided")
        self.assertEqual(self.bill(clean, "2000.00", "INV-11", key="again")["status"], "matched")

    def test_bill_rules_and_records(self):
        receipt = self.eur_receipt("rules")
        tomorrow = (datetime.date.fromisoformat(self.today) + datetime.timedelta(days=1)).isoformat()
        for kwargs, message in (({"number": " "}, "bill number"), ({"day": tomorrow}, "cannot be in the future"),
                                ({"amount": "0"}, "greater than zero")):
            with self.subTest(kwargs=kwargs), self.assertRaisesMessage(InvalidTransaction, message):
                self.bill(receipt, kwargs.get("amount", "2000.00"), kwargs.get("number", "INV-R"), day=kwargs.get("day"),
                          key=f"bad-{sorted(kwargs.items())}")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "record supplier bills"):
            self.bill(receipt, "2000.00", actor=self.sales)
        bill = SupplierBill.objects.get(pk=self.bill(receipt, "2001.00")["id"])
        accept_bill(actor=self.sales, idempotency_key="accept-rules", bill_id=bill.pk)
        bill.refresh_from_db()
        with self.assertRaises(ValidationError):
            bill.save()
        with self.assertRaises(ValidationError):
            bill.delete()
        with transaction.atomic(), self.assertRaises(IntegrityError):
            SupplierBill.objects.filter(pk=bill.pk).update(decided_by=self.admin)

    def test_overview_and_api(self):
        waiting = self.eur_receipt("api-wait")
        needing = self.eur_receipt("api-need")
        self.bill(waiting, "1999.99", "INV-W")
        overview = bills_overview(actor=self.sales, business_unit=self.unit)
        self.assertEqual((overview["can_record"], overview["can_decide"]), (False, True))
        self.assertEqual([row["id"] for row in overview["needing_bill"]], [needing])
        self.assertEqual(overview["needing_bill"][0]["lines"][0]["received_quantity"], "2.00")
        self.assertEqual([(row["bill_number"], row["difference"]) for row in overview["waiting"]], [("INV-W", "-0.01")])

        client = APIClient()
        client.force_authenticate(self.admin)
        body = {"receipt_id": needing, "bill_number": "INV-API", "bill_date": self.today, "amount": "2000.00"}
        url = f"/api/finance/supplier-bills/?unit={self.unit.pk}"
        self.assertEqual(client.post(url, body, format="json").status_code, 400, "Needs an idempotency key")
        created = client.post(url, body, format="json", HTTP_IDEMPOTENCY_KEY="api-bill")
        self.assertEqual((created.status_code, created.data["status"]), (201, "matched"))
        held = overview["waiting"][0]["id"]
        accept = f"/api/finance/supplier-bills/{held}/accept/?unit={self.unit.pk}"
        self.assertEqual(client.post(accept, {}, format="json", HTTP_IDEMPOTENCY_KEY="api-own").status_code, 403)
        checker = APIClient()
        checker.force_authenticate(self.sales)
        accepted = checker.post(accept, {}, format="json", HTTP_IDEMPOTENCY_KEY="api-accept")
        self.assertEqual((accepted.status_code, accepted.data["status"]), (200, "accepted"))
        self.assertEqual(client.get(url).data["needing_bill"], [])
        outsider = get_user_model().objects.create_user(username="test_bills_outsider", password="unused-test-password")
        DemoUserMarker.objects.create(user=outsider)
        stranger = APIClient()
        stranger.force_authenticate(outsider)
        self.assertEqual(stranger.get(url).status_code, 403)
