"""Credit and debit notes on customer invoices and supplier receipts; synthetic only."""

from decimal import Decimal
from unittest.mock import patch

from django.db.models import Sum
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .adjustment_notes import decide_note, note_detail, notes_overview, prepare_note
from .models import (
    AdjustmentNote, BusinessUnitRoleAssignment, JournalEntry, JournalLine, PurchaseReceipt, SalesQuote, UserActionGrant,
)
from .payables import payables_aging
from .purchasing_services import (
    WorkflowPermissionDenied, approve_post_handover_balance, deliver_sales_quote, notify_quote_ready, receive_imported_purchase,
)
from .receivables import customer_statement, receivables_aging
from .supplier_bills import record_bill
from .supplier_payments import _payable, _received, approve_payment, request_payment
from . import test_purchasing_workflows as purchasing_tests
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action


@override_settings(APP_ENV="demo")
class AdjustmentNoteTests(PurchasingFixtureMixin, TestCase):
    """Admin prepares notes; sales approves them."""

    make_role_user = purchasing_tests.PurchasingWorkflowTests.make_role_user

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for user, action in [(cls.admin, A.PREPARE_NOTE), (cls.sales, A.APPROVE_NOTE), (cls.admin, A.VIEW_LEDGER),
                             (cls.admin, A.PAY_SUPPLIER), (cls.sales, A.APPROVE_PAYMENT)]:
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate().isoformat()

    def owed_sale(self, key="owed"):
        """A 4,000 sale delivered with 400 still owed."""
        self.make_receipt(key=f"{key}-receipt", charges=[])
        quote = SalesQuote.objects.get(pk=self.make_quote(key=f"{key}-quote")["quote_id"])
        self.pay(quote, key=f"{key}-deposit", amount="3600.00")
        approve_post_handover_balance(
            actor=self.make_role_user(f"synthetic_{key}_gm", BusinessUnitRoleAssignment.Role.BU_GM),
            quote=quote, idempotency_key=f"{key}-approval", deferred_balance_percent=Decimal("10.00"),
            reason="TEST ONLY delivery exception")
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key=f"{key}-ready")
        deliver_sales_quote(actor=self.sales, quote=quote, idempotency_key=f"{key}-deliver")
        quote.refresh_from_db()
        return quote.invoice

    def received(self, key="usd", currency="USD", rate="3.64000000", unit_cost="1000.00"):
        """2 units received and billed as received, in the given currency."""
        foreign = currency != "QAR"
        receipt_id = receive_imported_purchase(
            actor=self.sales, idempotency_key=key, business_unit=self.unit, supplier=self.supplier,
            lines=[{"variant": self.variant, "location": self.location, "quantity": Decimal("2.00"), "unit_cost": Decimal(unit_cost)}],
            charges=[], currency=currency, exchange_rate=rate if foreign else None,
            rate_source="TEST ONLY bank rate" if foreign else "", rate_date=self.today if foreign else None,
        )["purchase_receipt_id"]
        receipt = PurchaseReceipt.objects.get(pk=receipt_id)
        record_bill(actor=self.admin, idempotency_key=f"bill-{key}", receipt_id=receipt_id, bill_number=f"INV-{key}",
                    bill_date=self.today, amount=_received(receipt).payable)
        return receipt

    def note(self, actor=None, **data):
        return prepare_note(actor=actor or self.admin, business_unit=self.unit,
                            data={"date": self.today, "reason": "price", "description": "TEST ONLY agreed discount", **data})

    def decide(self, note, action="approve", actor=None, reason=""):
        return decide_note(actor=actor or self.sales, business_unit=self.unit, note_id=note["id"], action=action, reason=reason)

    def balance(self, code):
        sums = JournalLine.objects.filter(entry__business_unit=self.unit, account__code=code,
                                          entry__status=JournalEntry.Status.POSTED).aggregate(d=Sum("debit"), c=Sum("credit"))
        return (sums["d"] or Decimal("0")) - (sums["c"] or Decimal("0"))

    def test_a_customer_credit_note_lowers_what_is_owed_once_someone_else_approves_it(self):
        invoice = self.owed_sale()
        receivable = self.balance("TEST_RECEIVABLE")
        overview = notes_overview(actor=self.admin, business_unit=self.unit)
        self.assertEqual([(row["number"], row["room"]) for row in overview["invoices"]], [(invoice.number, "400.00")])
        with self.assertRaisesMessage(InvalidTransaction, "has 400.00 QAR left to credit"):
            self.note(kind="customer_credit", invoice_id=invoice.pk, amount="400.01")
        note = self.note(kind="customer_credit", invoice_id=invoice.pk, amount="150.00")
        self.assertEqual((note["status"], note["number"]), ("submitted", ""))
        with self.assertRaisesMessage(InvalidTransaction, "has 250.00 QAR left to credit"):
            self.note(kind="customer_credit", invoice_id=invoice.pk, amount="300.00")
        self.assertEqual(receivables_aging(actor=self.admin, business_unit=self.unit)["total"], "400.00", "Not before approval")
        UserActionGrant.objects.create(user=self.admin, business_unit=self.unit, action=A.APPROVE_NOTE)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Someone other than the person who prepared"):
            self.decide(note, actor=self.admin)
        note = self.decide(note)
        year = timezone.localdate().year
        self.assertEqual((note["status"], note["number"]), ("approved", f"TVL-CN-{year}-00001"))
        self.assertEqual(self.balance("TEST_RECEIVABLE"), receivable - Decimal("150.00"))
        self.assertEqual(self.balance("TEST_SALES_ALLOWANCES"), Decimal("150.00"))
        aging = receivables_aging(actor=self.admin, business_unit=self.unit)
        self.assertEqual((aging["total"], aging["customers"][0]["invoices"][0]["credited"]), ("250.00", "150.00"))
        statement = customer_statement(actor=self.admin, business_unit=self.unit, customer_id=self.customer.pk)
        self.assertIn(f"TVL-CN-{year}-00001", [row["reference"] for row in statement["lines"]])
        self.assertEqual(statement["closing_balance"], "250.00")
        with self.assertRaisesMessage(InvalidTransaction, "already approved and posted"):
            self.decide(note)

    def test_a_note_can_be_rejected_with_a_reason_and_needs_the_permission(self):
        invoice = self.owed_sale()
        with self.assertRaises(WorkflowPermissionDenied):
            self.note(actor=self.sales, kind="customer_credit", invoice_id=invoice.pk, amount="10.00")
        with self.assertRaises(WorkflowPermissionDenied):
            notes_overview(actor=self.roche_user, business_unit=self.unit)
        note = self.note(kind="customer_credit", invoice_id=invoice.pk, amount="100.00")
        with self.assertRaisesMessage(InvalidTransaction, "Give the reason"):
            self.decide(note, "reject")
        note = self.decide(note, "reject", reason="TEST ONLY discount was not agreed")
        self.assertEqual((note["status"], note["decision_note"], note["journal_entry_id"]),
                         ("rejected", "TEST ONLY discount was not agreed", None))
        self.assertEqual(notes_overview(actor=self.admin, business_unit=self.unit)["invoices"][0]["room"], "400.00")

    def test_supplier_notes_lower_or_raise_what_we_owe_at_the_receipt_rate(self):
        receipt = self.received()
        self.assertEqual((_payable(receipt).payable, _payable(receipt).carried), (Decimal("2000.00"), Decimal("7280.00")))
        overview = notes_overview(actor=self.admin, business_unit=self.unit, side="supplier")
        self.assertEqual([(row["id"], row["currency"], row["room"]) for row in overview["receipts"]], [(receipt.pk, "USD", "2000.00")])
        with self.assertRaisesMessage(InvalidTransaction, "Give the supplier's note number"):
            self.note(kind="supplier_credit", receipt_id=receipt.pk, amount="100.00")
        credit = self.decide(self.note(kind="supplier_credit", receipt_id=receipt.pk, amount="100.00", their_reference="CN-778",
                                       reason="damaged"))
        self.assertEqual((credit["currency"], credit["amount"], credit["value"], credit["number"]), ("USD", "100.00", "364.00", ""))
        ours = self.decide(self.note(kind="our_debit", receipt_id=receipt.pk, amount="50.00", reason="short"))
        self.assertEqual(ours["number"], f"TVL-DN-{timezone.localdate().year}-00001")
        extra = self.decide(self.note(kind="supplier_debit", receipt_id=receipt.pk, amount="20.00", their_reference="DN-12",
                                      reason="extra"))
        self.assertEqual(extra["effect"], "Raises what we owe")
        goods = _payable(receipt)
        self.assertEqual((goods.payable, goods.carried), (Decimal("1870.00"), Decimal("6806.80")))
        self.assertEqual(self.balance("TEST_BILL_DIFFERENCES"), Decimal("-473.20"))
        aging = payables_aging(actor=self.admin, business_unit=self.unit)
        self.assertEqual(aging["total"], "6806.80")
        # Paying the rest at the receipt rate clears goods received exactly.
        payment = request_payment(actor=self.admin, idempotency_key="pay-rest", receipt_id=receipt.pk, amount="1870.00",
                                  exchange_rate="3.64000000", rate_source="TEST ONLY bank", rate_date=self.today,
                                  paid_on=self.today, bank_reference="TT-1")
        approve_payment(actor=self.sales, idempotency_key="approve-rest", payment_id=payment["id"])
        self.assertEqual(payables_aging(actor=self.admin, business_unit=self.unit)["total"], "0.00")
        self.assertEqual(self.balance("TEST_GRNI"), Decimal("0.00"))
        with self.assertRaisesMessage(InvalidTransaction, "0.00 USD still unpaid"):
            self.note(kind="supplier_credit", receipt_id=receipt.pk, amount="1.00", their_reference="CN-779")
        self.assertEqual(note_detail(actor=self.admin, business_unit=self.unit, note_id=credit["id"])["document_now"]["owed"], "0.00")

    def test_approval_checks_again_that_the_amount_is_still_owed_and_the_api_works(self):
        receipt = self.received(key="qar", currency="QAR", unit_cost="500.00")
        client = APIClient()
        client.force_login(self.admin)
        response = client.post(f"/api/finance/notes/?unit={self.unit.pk}", {
            "kind": "supplier_credit", "receipt_id": receipt.pk, "amount": "800.00", "their_reference": "CN-1",
            "reason": "price", "description": "TEST ONLY year-end rebate", "date": self.today}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        payment = request_payment(actor=self.admin, idempotency_key="pay-part", receipt_id=receipt.pk, amount="300.00",
                                  paid_on=self.today, bank_reference="TT-2")
        self.assertEqual(payment["status"], "requested")
        client.force_login(self.sales)
        response = client.post(f"/api/finance/notes/{response.json()['id']}/?unit={self.unit.pk}", {"action": "approve"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("now has only 700.00 QAR unpaid", response.json()["detail"])
        self.assertEqual(AdjustmentNote.objects.get().status, "submitted")
        listed = client.get(f"/api/finance/notes/?unit={self.unit.pk}&side=supplier").json()
        self.assertEqual((listed["waiting"]["supplier"], listed["waiting"]["for_me"], listed["notes"][0]["can_decide"]), (1, 1, True))
