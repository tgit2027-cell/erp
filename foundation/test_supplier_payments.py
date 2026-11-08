import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import JournalEntry, PurchaseReceipt, SupplierPayment, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied, receive_imported_purchase
from .scenario_reconciliation import build_scenario_reconciliation
from .supplier_bills import record_bill
from .supplier_payments import _received, approve_payment, payments_overview, reject_payment, request_payment
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import IdempotencyConflict, InvalidTransaction
from .transfer_permissions import set_transfer_permission, transfer_permission_overview

PAY = UserActionGrant.Action.PAY_SUPPLIER
APPROVE = UserActionGrant.Action.APPROVE_PAYMENT


@override_settings(APP_ENV="demo")
class SupplierPaymentTests(PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.create(user=cls.admin, business_unit=cls.unit, action=PAY)
        UserActionGrant.objects.create(user=cls.sales, business_unit=cls.unit, action=APPROVE)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate().isoformat()

    def receive(self, key, *, currency, unit_cost, quantity="2.00", rate=None, charges=(), bill=True):
        """Receive goods and, unless bill=False, record a supplier bill that matches them."""
        foreign = currency != "QAR"
        receipt_id = receive_imported_purchase(
            actor=self.sales, idempotency_key=key, business_unit=self.unit, supplier=self.supplier,
            lines=[{"variant": self.variant, "location": self.location, "quantity": Decimal(quantity),
                    "unit_cost": Decimal(unit_cost)}],
            charges=list(charges), currency=currency, exchange_rate=rate,
            rate_source="TEST ONLY bank rate on arrival" if foreign else "",
            rate_date=self.today if foreign else None,
        )["purchase_receipt_id"]
        if bill:
            expected = _received(PurchaseReceipt.objects.get(pk=receipt_id)).payable
            record_bill(actor=self.admin, idempotency_key=f"bill-{key}", receipt_id=receipt_id,
                        bill_number=f"INV-{key}", bill_date=self.today, amount=expected)
        return receipt_id

    def prepare(self, receipt_id, amount, rate=None, *, key=None, actor=None, **extra):
        return request_payment(
            actor=actor or self.admin, idempotency_key=key or f"pay-{receipt_id}-{amount}-{rate}",
            receipt_id=receipt_id, amount=amount, exchange_rate=rate,
            rate_source="TEST ONLY bank transfer advice" if rate else "", rate_date=self.today if rate else None,
            paid_on=extra.pop("paid_on", self.today), bank_reference="TT-0001", **extra,
        )

    def approve(self, payment_id, key=None, actor=None):
        return approve_payment(actor=actor or self.sales, idempotency_key=key or f"approve-{payment_id}", payment_id=payment_id)

    def pay(self, receipt_id, amount, rate=None, *, key=None, **extra):
        """Prepared by one person, approved by another."""
        requested = self.prepare(receipt_id, amount, rate, key=key, **extra)
        return self.approve(requested["id"], key=f"{key}-approve" if key else None)

    def journal(self, payment_id):
        entry = SupplierPayment.objects.get(pk=payment_id).journal_entry
        return entry, {
            line.account.code: (line.debit, line.credit) for line in entry.lines.select_related("account")
        }

    def test_full_payment_at_a_better_rate_books_an_exchange_gain(self):
        # 2 × EUR 1,000 received at 4.00 = QAR 8,000; paid at 3.90 = QAR 7,800.
        receipt = self.receive("eur", currency="EUR", unit_cost="1000.00", rate="4.00000000")
        result = self.pay(receipt, "2000.00", "3.90000000")
        self.assertEqual(
            (result["paid_value"], result["cleared_value"], result["exchange_difference"], result["outstanding"]),
            ("7800.00", "8000.00", "200.00", "0.00"),
        )
        entry, lines = self.journal(result["id"])
        self.assertEqual(entry.kind, JournalEntry.Kind.SUPPLIER_PAYMENT)
        self.assertEqual(lines, {
            "TEST_GRNI": (Decimal("8000.00"), Decimal("0.00")),
            "TEST_CASH": (Decimal("0.00"), Decimal("7800.00")),
            "TEST_FX_GAIN": (Decimal("0.00"), Decimal("200.00")),
        })
        with self.assertRaisesMessage(InvalidTransaction, "already paid in full or waiting for approval"):
            self.pay(receipt, "1.00", "3.90000000")

    def test_part_payments_at_worse_rates_book_losses_and_clear_the_exact_receipt_value(self):
        # USD 999.99 received at 3.64 = QAR 3,639.96.
        receipt = self.receive("usd", currency="USD", unit_cost="333.33", quantity="3.00", rate="3.64000000")
        first = self.pay(receipt, "500.00", "3.65000000")
        self.assertEqual((first["paid_value"], first["cleared_value"], first["exchange_difference"]),
                         ("1825.00", "1820.00", "-5.00"))
        self.assertEqual(first["outstanding"], "499.99")
        with self.assertRaisesMessage(InvalidTransaction, "more than the 499.99 USD still to pay"):
            self.pay(receipt, "500.00", "3.66000000")
        last = self.pay(receipt, "499.99", "3.66000000")
        # The last payment clears what the receipt still carries, not a re-rounded figure.
        self.assertEqual((last["paid_value"], last["cleared_value"], last["exchange_difference"]),
                         ("1829.96", "1819.96", "-10.00"))
        _, lines = self.journal(last["id"])
        self.assertEqual(lines["TEST_FX_LOSS"], (Decimal("10.00"), Decimal("0.00")))
        cleared = sum(SupplierPayment.objects.filter(receipt_id=receipt).values_list("cleared_value", flat=True))
        self.assertEqual(cleared, Decimal("3639.96"))

    def test_qar_receipt_is_paid_without_exchange_difference(self):
        receipt = self.receive("qar", currency="QAR", unit_cost="1500.00")
        paid_on = datetime.date.fromisoformat(str(self.today)) - datetime.timedelta(days=3)
        result = self.pay(receipt, "3000.00", paid_on=paid_on.isoformat())
        self.assertEqual((result["paid_value"], result["exchange_difference"]), ("3000.00", "0.00"))
        entry, lines = self.journal(result["id"])
        self.assertEqual(entry.document_date, paid_on, "The journal is dated on the payment date")
        self.assertEqual(set(lines), {"TEST_GRNI", "TEST_CASH"})
        with self.assertRaisesMessage(InvalidTransaction, "exchange rate of 1"):
            self.pay(self.receive("qar-2", currency="QAR", unit_cost="10.00"), "5.00", "2.00000000")

    def test_rate_and_amount_rules(self):
        receipt = self.receive("eur-rules", currency="EUR", unit_cost="100.00", rate="4.00000000")
        for kwargs in ({"rate": None}, {"amount": "0"}, {"amount": "-5"}, {"amount": 10.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(InvalidTransaction):
                self.pay(receipt, kwargs.get("amount", "10.00"), kwargs.get("rate", "3.90000000"),
                         key=f"bad-{sorted(kwargs.items())}")
        with self.assertRaisesMessage(InvalidTransaction, "Choose the purchase receipt"):
            self.pay("1", "10.00", "3.90000000")
        self.assertFalse(SupplierPayment.objects.exists())

    def test_permission_is_per_business_unit(self):
        receipt = self.receive("eur-perm", currency="EUR", unit_cost="100.00", rate="4.00000000")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "prepare supplier payments"):
            self.prepare(receipt, "10.00", "3.90000000", actor=self.sales)
        UserActionGrant.objects.create(user=self.sales, business_unit=self.roche, action=PAY)
        with self.assertRaises(WorkflowPermissionDenied):
            self.prepare(receipt, "10.00", "3.90000000", actor=self.sales)
        with self.assertRaises(WorkflowPermissionDenied):
            payments_overview(actor=self.roche_user, business_unit=self.unit)
        requested = self.prepare(receipt, "10.00", "3.90000000")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "approve supplier payments"):
            self.approve(requested["id"], actor=self.roche_user)

    def test_replay_returns_the_same_payment_and_a_changed_request_conflicts(self):
        receipt = self.receive("eur-replay", currency="EUR", unit_cost="100.00", rate="4.00000000")
        first = self.pay(receipt, "50.00", "3.90000000", key="same-key")
        self.assertEqual(self.pay(receipt, "50.00", "3.90000000", key="same-key"), first)
        self.assertEqual(SupplierPayment.objects.count(), 1)
        self.assertEqual(JournalEntry.objects.filter(kind=JournalEntry.Kind.SUPPLIER_PAYMENT).count(), 1)
        with self.assertRaises(IdempotencyConflict):
            self.prepare(receipt, "50.00", "3.95000000", key="same-key")
        with self.assertRaisesMessage(InvalidTransaction, "already approved and paid"):
            self.approve(first["id"], key="a-different-key")

    def test_payments_are_append_only_and_the_difference_must_match(self):
        receipt = self.receive("eur-db", currency="EUR", unit_cost="100.00", rate="4.00000000")
        payment = SupplierPayment.objects.get(pk=self.pay(receipt, "50.00", "3.90000000")["id"])
        with self.assertRaises(ValidationError):
            payment.save()
        with self.assertRaises(ValidationError):
            payment.delete()
        with transaction.atomic(), self.assertRaises(IntegrityError):
            SupplierPayment.objects.filter(pk=payment.pk).update(exchange_difference=Decimal("1.00"))

    def test_reconciliation_ties_cash_receipt_clearing_and_exchange_results(self):
        eur = self.receive("eur-rec", currency="EUR", unit_cost="1000.00", rate="4.00000000")
        usd = self.receive("usd-rec", currency="USD", unit_cost="333.33", quantity="3.00", rate="3.64000000")
        self.pay(eur, "2000.00", "3.90000000")
        self.pay(usd, "500.00", "3.65000000")
        report = build_scenario_reconciliation(self.unit)
        checks = {row["label"]: row for row in report["checks"]}
        cash = checks["Cash in the journal equals customer payments received less supplier payments made, plus interest less bank charges"]
        self.assertEqual((cash["expected"], cash["matches"]), ("-9625.00", True))
        self.assertTrue(checks["Supplier payments released the same receipt value from receipt clearing"]["matches"])
        fx = checks["Exchange gain less loss in the journal equals the supplier payment differences"]
        self.assertEqual((fx["actual"], fx["matches"]), ("195.00", True))
        self.assertTrue(report["all_match"], [row["label"] for row in report["checks"] if not row["matches"]])

    def test_overview_lists_open_items_waiting_payments_and_totals(self):
        eur = self.receive("eur-ov", currency="EUR", unit_cost="1000.00", rate="4.00000000")
        usd = self.receive("usd-ov", currency="USD", unit_cost="333.33", quantity="3.00", rate="3.64000000")
        self.pay(eur, "2000.00", "3.90000000")
        self.pay(usd, "500.00", "3.65000000")
        self.prepare(usd, "100.00", "3.65000000", key="usd-waiting")
        overview = payments_overview(actor=self.admin, business_unit=self.unit)
        self.assertEqual((overview["can_prepare"], overview["can_approve"]), (True, False))
        self.assertEqual([row["id"] for row in overview["open_receipts"]], [usd])
        item = overview["open_receipts"][0]["items"][0]
        self.assertEqual((item["paying_for"], item["payable"], item["paid"], item["waiting"], item["remaining"]),
                         ("goods", "999.99", "500.00", "100.00", "399.99"))
        self.assertEqual(overview["totals"], {"exchange_gain": "200.00", "exchange_loss": "5.00", "net": "195.00"})
        self.assertEqual(len(overview["payments"]), 2)
        self.assertEqual([row["amount"] for row in overview["waiting"]], ["100.00"])

    def test_api_requires_an_idempotency_key_and_pays(self):
        receipt = self.receive("eur-api", currency="EUR", unit_cost="100.00", rate="4.00000000")
        client = APIClient()
        client.force_authenticate(self.admin)
        body = {"receipt_id": receipt, "amount": "200.00", "exchange_rate": "3.90000000",
                "rate_source": "TEST ONLY bank advice", "rate_date": self.today, "paid_on": self.today}
        self.assertEqual(client.post("/api/finance/supplier-payments/", body, format="json").status_code, 400)
        response = client.post("/api/finance/supplier-payments/", body, format="json", HTTP_IDEMPOTENCY_KEY="api-1")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual((response.data["status"], response.data["exchange_difference"]), ("requested", None))
        approve = f"/api/finance/supplier-payments/{response.data['id']}/approve/?unit={self.unit.pk}"
        self.assertEqual(client.post(approve, {}, format="json", HTTP_IDEMPOTENCY_KEY="api-own").status_code, 403)
        checker = APIClient()
        checker.force_authenticate(self.sales)
        self.assertEqual(checker.post(approve, {}, format="json").status_code, 400, "Approval needs an idempotency key")
        approved = checker.post(approve, {}, format="json", HTTP_IDEMPOTENCY_KEY="api-approve")
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual((approved.data["status"], approved.data["exchange_difference"]), ("approved", "20.00"))
        listing = client.get(f"/api/finance/supplier-payments/?unit={self.unit.pk}")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.data["open_receipts"], [])
        outsider = APIClient()
        outsider.force_authenticate(self.roche_user)
        self.assertEqual(outsider.get(f"/api/finance/supplier-payments/?unit={self.unit.pk}").status_code, 403)

    def test_pay_supplier_is_a_per_unit_permission_on_the_permissions_screen(self):
        sysadmin = get_user_model().objects.create_user(
            username="test_payments_sysadmin", password="unused-test-password", is_staff=True, is_superuser=True,
        )
        overview = transfer_permission_overview(actor=sysadmin, business_unit=self.unit)
        self.assertIn({"action": PAY, "label": "Prepare supplier payments", "scope": "unit", "can_change": True}, overview["actions"])
        self.assertIn({"action": APPROVE, "label": "Approve supplier payments", "scope": "unit", "can_change": True}, overview["actions"])
        set_transfer_permission(actor=sysadmin, business_unit=self.unit, user_id=self.sales.pk, action=PAY, enabled=True)
        self.assertTrue(UserActionGrant.objects.filter(
            user=self.sales, business_unit=self.unit, action=PAY, revoked_at__isnull=True,
        ).exists())
        self.assertFalse(UserActionGrant.objects.filter(user=self.sales, business_unit=self.roche, action=PAY).exists())

    def test_nothing_posts_until_someone_else_approves(self):
        receipt = self.receive("mc", currency="QAR", unit_cost="1500.00")
        requested = self.prepare(receipt, "1000.00")
        self.assertEqual((requested["status"], requested["journal_entry_id"]), ("requested", None))
        self.assertFalse(JournalEntry.objects.filter(kind=JournalEntry.Kind.SUPPLIER_PAYMENT).exists())
        UserActionGrant.objects.create(user=self.admin, business_unit=self.unit, action=APPROVE)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "someone else must approve"):
            self.approve(requested["id"], actor=self.admin)
        with self.assertRaisesMessage(InvalidTransaction, "more than the 2000.00 QAR still to pay"):
            self.prepare(receipt, "2000.01", key="too-much")  # 1,000 is already waiting
        with self.assertRaisesMessage(InvalidTransaction, "reason for rejecting"):
            reject_payment(actor=self.sales, payment_id=requested["id"], note=" ")
        rejected = reject_payment(actor=self.sales, payment_id=requested["id"], note="Wrong bank account")
        self.assertEqual((rejected["status"], rejected["decided_by"], rejected["decision_note"]),
                         ("rejected", self.sales.username, "Wrong bank account"))
        with self.assertRaisesMessage(InvalidTransaction, "already rejected"):
            self.approve(requested["id"])
        paid = self.pay(receipt, "3000.00", key="full")  # the rejected amount is free again
        self.assertEqual((paid["status"], paid["decided_by"]), ("approved", self.sales.username))
        with transaction.atomic(), self.assertRaises(IntegrityError):
            SupplierPayment.objects.filter(pk=paid["id"]).update(decided_by=self.admin)

    def test_shipping_and_customs_are_paid_to_other_parties(self):
        receipt = self.receive("charges", currency="EUR", unit_cost="1000.00", rate="4.00000000", charges=[
            {"category": "shipping", "amount": Decimal("850.00"), "eligible": True, "currency": "USD",
             "exchange_rate": "3.64000000", "rate_source": "TEST ONLY freight invoice", "rate_date": self.today},
            {"category": "clearance", "amount": Decimal("1800.00"), "eligible": True},
        ])
        overview = payments_overview(actor=self.admin, business_unit=self.unit)
        items = {item["paying_for"]: item for item in overview["open_receipts"][0]["items"]}
        self.assertEqual({key: (item["currency"], item["payable"], item["carried_value"]) for key, item in items.items()}, {
            "goods": ("EUR", "2000.00", "8000.00"),
            "shipping": ("USD", "850.00", "3094.00"),
            "clearance": ("QAR", "1800.00", "1800.00"),
        })
        with self.assertRaisesMessage(InvalidTransaction, "Say who is being paid"):
            self.prepare(receipt, "500.00", "3.65000000", charge_id=items["shipping"]["charge_id"], key="no-payee")
        # Shipping split between two parties; the second pays the rest at a better rate.
        first = self.pay(receipt, "500.00", "3.65000000", charge_id=items["shipping"]["charge_id"],
                         payee_name="TEST ONLY Gulf Freight", key="ship-1")
        last = self.pay(receipt, "350.00", "3.63000000", charge_id=items["shipping"]["charge_id"],
                        payee_name="TEST ONLY Doha Haulage", key="ship-2")
        self.assertEqual((first["cleared_value"], first["paid_value"], first["exchange_difference"]), ("1820.00", "1825.00", "-5.00"))
        self.assertEqual((last["cleared_value"], last["paid_value"], last["exchange_difference"]), ("1274.00", "1270.50", "3.50"))
        customs = self.pay(receipt, "1800.00", charge_id=items["clearance"]["charge_id"],
                           payee_name="TEST ONLY Customs broker", key="customs")
        entry, lines = self.journal(customs["id"])
        self.assertEqual(lines, {"TEST_LANDED_CLEARING": (Decimal("1800.00"), Decimal("0.00")),
                                 "TEST_CASH": (Decimal("0.00"), Decimal("1800.00"))})
        self.assertIn("TEST ONLY Customs broker", entry.description)
        remaining = payments_overview(actor=self.admin, business_unit=self.unit)["open_receipts"][0]["items"]
        self.assertEqual([(item["paying_for"], item["remaining"]) for item in remaining], [("goods", "2000.00"), ("shipping", "0.00"), ("clearance", "0.00")])
        self.pay(receipt, "2000.00", "3.90000000", key="goods")
        report = build_scenario_reconciliation(self.unit)
        self.assertTrue(report["all_match"], [row["label"] for row in report["checks"] if not row["matches"]])
