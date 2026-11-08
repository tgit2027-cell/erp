"""Advances paid to suppliers before their bill, then set against the goods; synthetic only."""

from decimal import Decimal
from unittest.mock import patch

from django.db.models import Sum
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import JournalEntry, JournalLine, PurchaseReceipt, SupplierPayment, UserActionGrant
from .payables import payables_aging
from .purchasing_services import WorkflowPermissionDenied, receive_imported_purchase
from .supplier_advances import advances_overview, decide_advance, prepare_advance, use_advance
from .supplier_bills import record_bill
from .supplier_payments import _payable, _received, approve_payment, reject_payment, request_payment
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action


@override_settings(APP_ENV="demo")
class SupplierAdvanceTests(PurchasingFixtureMixin, TestCase):
    """Admin prepares; sales approves."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.get_or_create(user=cls.admin, business_unit=cls.unit, action=A.PAY_SUPPLIER)
        UserActionGrant.objects.get_or_create(user=cls.sales, business_unit=cls.unit, action=A.APPROVE_PAYMENT)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate().isoformat()

    def advance(self, amount="1000.00", currency="EUR", rate="4.00000000", approve=True, **extra):
        foreign = currency != "QAR"
        row = prepare_advance(actor=self.admin, business_unit=self.unit, data={
            "supplier_id": self.supplier.pk, "amount": amount, "currency": currency, "exchange_rate": rate if foreign else None,
            "rate_source": "TEST ONLY bank transfer advice" if foreign else "", "rate_date": self.today if foreign else None,
            "paid_on": self.today, "purpose": "TEST ONLY 30% deposit on order PO-118", "bank_reference": "TT-9", **extra})
        if approve:
            row = decide_advance(actor=self.sales, business_unit=self.unit, advance_id=row["id"], action="approve")
        return row

    def received(self, key="eur", currency="EUR", rate="4.10000000", unit_cost="1500.00", bill=True):
        foreign = currency != "QAR"
        receipt_id = receive_imported_purchase(
            actor=self.sales, idempotency_key=key, business_unit=self.unit, supplier=self.supplier,
            lines=[{"variant": self.variant, "location": self.location, "quantity": Decimal("2.00"), "unit_cost": Decimal(unit_cost)}],
            charges=[], currency=currency, exchange_rate=rate if foreign else None,
            rate_source="TEST ONLY bank rate" if foreign else "", rate_date=self.today if foreign else None,
        )["purchase_receipt_id"]
        receipt = PurchaseReceipt.objects.get(pk=receipt_id)
        if bill:
            record_bill(actor=self.admin, idempotency_key=f"bill-{key}", receipt_id=receipt_id, bill_number=f"INV-{key}",
                        bill_date=self.today, amount=_received(receipt).payable)
        return receipt

    def balance(self, code):
        sums = JournalLine.objects.filter(entry__business_unit=self.unit, account__code=code,
                                          entry__status=JournalEntry.Status.POSTED).aggregate(d=Sum("debit"), c=Sum("credit"))
        return (sums["d"] or Decimal("0")) - (sums["c"] or Decimal("0"))

    def use(self, advance, receipt, amount, key="use"):
        return use_advance(actor=self.admin, business_unit=self.unit, advance_id=advance["id"], receipt_id=receipt.pk,
                           amount=amount, idempotency_key=key)

    def test_an_advance_leaves_the_bank_only_after_a_second_person_approves_it(self):
        row = self.advance(approve=False)
        self.assertEqual((row["number"], row["status"], row["value"]), (f"ADV-TVL-{timezone.localdate().year}-00001", "requested", "4000.00"))
        self.assertEqual(self.balance("TEST_CASH"), 0)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "someone else must approve"):
            UserActionGrant.objects.create(user=self.admin, business_unit=self.unit, action=A.APPROVE_PAYMENT)
            decide_advance(actor=self.admin, business_unit=self.unit, advance_id=row["id"], action="approve")
        row = decide_advance(actor=self.sales, business_unit=self.unit, advance_id=row["id"], action="approve")
        self.assertEqual((row["status"], row["left"]), ("approved", "1000.00"))
        self.assertEqual((self.balance("TEST_SUPPLIER_ADVANCES"), self.balance("TEST_CASH")), (Decimal("4000.00"), Decimal("-4000.00")))
        rejected = self.advance(approve=False, amount="50.00")
        with self.assertRaisesMessage(InvalidTransaction, "Give the reason"):
            decide_advance(actor=self.sales, business_unit=self.unit, advance_id=rejected["id"], action="reject")
        rejected = decide_advance(actor=self.sales, business_unit=self.unit, advance_id=rejected["id"], action="reject",
                                  reason="TEST ONLY order cancelled")
        self.assertEqual((rejected["status"], rejected["left"]), ("rejected", "0.00"))
        with self.assertRaises(WorkflowPermissionDenied):
            advances_overview(actor=self.roche_user, business_unit=self.unit)

    def test_an_advance_is_set_against_the_goods_with_the_exchange_difference_booked(self):
        advance = self.advance()  # EUR 1,000 at 4.00 = QAR 4,000
        receipt = self.received()  # EUR 3,000 at 4.10 = QAR 12,300
        overview = advances_overview(actor=self.admin, business_unit=self.unit)
        self.assertEqual([(row["id"], row["remaining"]) for row in overview["open_goods"]], [(receipt.pk, "3000.00")])
        with self.assertRaisesMessage(InvalidTransaction, "has only 1000.00 EUR left to use"):
            self.use(advance, receipt, "1000.01")
        row = self.use(advance, receipt, "600.00")
        self.assertEqual((row["left"], row["waiting"], row["uses"][0]["status"]), ("400.00", "600.00", "requested"))
        self.assertEqual(payables_aging(actor=self.admin, business_unit=self.unit)["total"], "12300.00", "Not before approval")
        approve_payment(actor=self.sales, idempotency_key="approve-600", payment_id=row["uses"][0]["id"])
        # Goods clear 600 x 4.10 = 2,460; the advance gives up 600 x 4.00 = 2,400; 60 is an exchange gain.
        self.assertEqual(self.balance("TEST_SUPPLIER_ADVANCES"), Decimal("1600.00"))
        self.assertEqual(self.balance("TEST_FX_GAIN"), Decimal("-60.00"))
        self.assertEqual(self.balance("TEST_CASH"), Decimal("-4000.00"), "No more money left the bank")
        self.assertEqual(payables_aging(actor=self.admin, business_unit=self.unit)["total"], "9840.00")
        row = self.use(advance, receipt, "400.00", key="use-rest")
        reject_payment(actor=self.sales, payment_id=row["uses"][0]["id"], note="TEST ONLY wrong receipt")
        self.assertEqual(advances_overview(actor=self.admin, business_unit=self.unit)["advances"][0]["left"], "400.00",
                         "A rejected use frees the advance again")
        self.assertEqual(SupplierPayment.objects.filter(advance__isnull=False).count(), 2)

    def test_an_advance_matches_supplier_currency_and_goods(self):
        advance = self.advance()
        qar = self.received(key="qar", currency="QAR", unit_cost="500.00")
        with self.assertRaisesMessage(InvalidTransaction, "is in EUR but receipt"):
            self.use(advance, qar, "100.00")
        unbilled = self.received(key="nobill", bill=False)
        with self.assertRaisesMessage(InvalidTransaction, "Record the supplier's bill"):
            self.use(advance, unbilled, "100.00")
        waiting = self.advance(approve=False, amount="10.00")
        receipt = self.received(key="eur2")
        with self.assertRaisesMessage(InvalidTransaction, "is not approved and paid yet"):
            self.use(waiting, receipt, "5.00")
        # Paying the rest from the bank and the advance together never pays more than the bill.
        request_payment(actor=self.admin, idempotency_key="bank", receipt_id=receipt.pk, amount="2500.00",
                        exchange_rate="4.10000000", rate_source="TEST ONLY bank", rate_date=self.today, paid_on=self.today)
        with self.assertRaisesMessage(InvalidTransaction, "more than the 500.00 EUR still to pay"):
            self.use(advance, receipt, "600.00")
        self.assertEqual(_payable(receipt).committed(), Decimal("2500.00"))

    def test_the_api_prepares_approves_and_uses_an_advance(self):
        receipt = self.received(key="api", currency="QAR", unit_cost="900.00")
        client = APIClient()
        client.force_login(self.admin)
        response = client.post(f"/api/finance/advances/?unit={self.unit.pk}", {
            "supplier_id": self.supplier.pk, "amount": "1000.00", "currency": "QAR", "purpose": "TEST ONLY deposit",
            "paid_on": self.today}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        advance_id = response.json()["id"]
        client.force_login(self.sales)
        self.assertEqual(client.post(f"/api/finance/advances/{advance_id}/?unit={self.unit.pk}", {"action": "approve"},
                                     format="json").json()["status"], "approved")
        client.force_login(self.admin)
        response = client.post(f"/api/finance/advances/{advance_id}/?unit={self.unit.pk}",
                               {"action": "use", "receipt_id": receipt.pk, "amount": "1000.00"}, format="json",
                               HTTP_IDEMPOTENCY_KEY="use-api")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["uses"][0]["advance"], response.json()["number"])
