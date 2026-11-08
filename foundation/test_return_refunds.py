from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import return_refunds
from .customers import customer_detail
from .models import (
    CustomerCreditNote, DemoUserMarker, JournalEntry, StockBalance, StockMovement, UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied
from .return_refunds import approve_refund, refund_queue, reject_refund
from .return_services import create_return, inspect_return
from .test_reporting import ReportingFixtureMixin
from .transaction_services import InvalidTransaction

APPROVE = UserActionGrant.Action.APPROVE_REFUND


@override_settings(APP_ENV="demo")
class CustomerRefundTests(ReportingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        cls.finance = get_user_model().objects.create_user(username="test_refund_finance", password="unused-test-password")
        DemoUserMarker.objects.create(user=cls.finance)
        UserActionGrant.objects.create(user=cls.finance, business_unit=cls.unit, action=APPROVE)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        # Two units sold at 2,000 each; each cost 1,200 including its share of shipping.
        self.quote, _ = self.make_delivered_sale(key="refund-sale")
        self.quote.refresh_from_db()
        self.line = self.quote.lines.get()

    def returned(self, key="refund-return", quantity="1.00", condition="good"):
        data = {
            "request_date": timezone.localdate(self.quote.delivered_at).isoformat(), "receiving_mode": "showroom",
            "location_id": self.location.pk, "receipt_collection_details": "TEST ONLY showroom received",
            "proposed_refund_amount": "2000.00", "original_payment_method": "card", "requested_refund_method": "card",
            "refund_preference": "usually_original", "lines": [{"quote_line_id": self.line.pk, "quantity": quantity}],
        }
        intake = create_return(actor=self.sales, quote=self.quote, idempotency_key=key, data=data)
        from .models import SalesReturnIntake

        record = SalesReturnIntake.objects.get(pk=intake["id"])
        inspect_return(actor=self.sales, intake=record, idempotency_key=f"{key}-inspect", lines=[
            {"quote_line_id": self.line.pk, "condition": condition, "inspection_notes": "TEST ONLY looked at it"},
        ])
        return record.pk

    def approve(self, intake_id, credit="2000.00", key="refund-ok", note="", actor=None):
        return approve_refund(actor=actor or self.finance, business_unit=self.unit, intake_id=intake_id,
                              idempotency_key=key, credit_total=credit, refund_method="card", note=note)

    def on_hand(self):
        balance = StockBalance.objects.get(business_unit=self.unit, variant=self.variant, location=self.location)
        return balance.on_hand_quantity, balance.on_hand_value

    def test_approving_issues_a_credit_note_refunds_and_puts_the_goods_back(self):
        intake_id = self.returned()
        queue = refund_queue(actor=self.finance, business_unit=self.unit)
        review = queue["returns"][0]["review"]
        self.assertEqual((review["returned_value"], review["returned_cost"], review["outstanding_balance"]),
                         ("2000.00", "1200.00", "0.00"))
        quantity, value = self.on_hand()
        result = self.approve(intake_id)
        note = result["credit_note"]
        self.assertEqual(note["number"], f"{self.unit.code}-CN-{timezone.localdate().year}-00001")
        self.assertEqual((note["credit_total"], note["applied_to_balance"], note["refunded_amount"]),
                         ("2000.00", "0.00", "2000.00"))
        self.assertEqual(result["status"], "refunded")
        self.assertEqual(self.on_hand(), (quantity + 1, value + Decimal("1200.00")))
        movement = StockMovement.objects.get(kind=StockMovement.Kind.RETURN_IN)
        self.assertIn("inspected good", movement.memo)
        entry = JournalEntry.objects.get(kind=JournalEntry.Kind.SALES_RETURN)
        lines = {line.account.code: (line.debit, line.credit) for line in entry.lines.all()}
        self.assertEqual(lines, {
            "TEST_SALES_RETURNS": (Decimal("2000.00"), Decimal("0.00")),
            "TEST_CASH": (Decimal("0.00"), Decimal("2000.00")),
            "TEST_INVENTORY": (Decimal("1200.00"), Decimal("0.00")),
            "TEST_COGS": (Decimal("0.00"), Decimal("1200.00")),
        })
        # The customer's file shows the credit note next to the invoice.
        detail = customer_detail(actor=self.sales, customer_id=self.customer.pk)
        self.assertEqual([row["number"] for row in detail["credit_notes"]], [note["number"]])
        self.assertEqual(refund_queue(actor=self.finance, business_unit=self.unit)["waiting"], 0)
        decided = refund_queue(actor=self.finance, business_unit=self.unit, status="decided")["returns"]
        self.assertEqual(decided[0]["credit_note"]["number"], note["number"])
        # The same request again returns the same result and posts nothing more.
        self.assertEqual(self.approve(intake_id)["credit_note"]["number"], note["number"])
        self.assertEqual(CustomerCreditNote.objects.count(), 1)
        with self.assertRaisesMessage(InvalidTransaction, "already been decided"):
            self.approve(intake_id, key="refund-again")

    def test_credit_first_reduces_what_the_customer_still_owes(self):
        intake_id = self.returned()
        invoice = self.quote.invoice
        with patch.object(return_refunds, "_outstanding", return_value=(invoice, Decimal("500.00"))):
            note = self.approve(intake_id)["credit_note"]
        self.assertEqual((note["applied_to_balance"], note["refunded_amount"]), ("500.00", "1500.00"))
        entry = JournalEntry.objects.get(kind=JournalEntry.Kind.SALES_RETURN)
        receivable = entry.lines.get(account__code="TEST_RECEIVABLE")
        self.assertEqual(receivable.credit, Decimal("500.00"))

    def test_a_lower_credit_needs_a_note_and_cannot_exceed_the_sale(self):
        intake_id = self.returned(condition="damaged")
        with self.assertRaisesMessage(InvalidTransaction, "cannot be more than the returned goods were sold for"):
            self.approve(intake_id, credit="2000.01")
        with self.assertRaisesMessage(InvalidTransaction, "Give the note"):
            self.approve(intake_id, credit="1500.00", key="refund-lower")
        with self.assertRaisesMessage(InvalidTransaction, "more than zero"):
            self.approve(intake_id, credit="0.00", key="refund-zero")
        note = self.approve(intake_id, credit="1500.00", key="refund-lower-ok", note="Scratched leg, 25% kept")["credit_note"]
        self.assertEqual((note["credit_total"], note["note"]), ("1500.00", "Scratched leg, 25% kept"))
        self.assertIn("inspected damaged", StockMovement.objects.get(kind=StockMovement.Kind.RETURN_IN).memo)

    def test_finance_permission_and_maker_checker(self):
        intake_id = self.returned()
        with self.assertRaisesMessage(WorkflowPermissionDenied, "permission to approve customer refunds"):
            refund_queue(actor=self.sales, business_unit=self.unit)
        UserActionGrant.objects.create(user=self.sales, business_unit=self.unit, action=APPROVE)
        self.assertTrue(refund_queue(actor=self.sales, business_unit=self.unit)["returns"][0]["review"]["own_return"])
        with self.assertRaisesMessage(WorkflowPermissionDenied, "someone else in Finance"):
            self.approve(intake_id, actor=self.sales)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "permission to approve customer refunds"):
            refund_queue(actor=self.finance, business_unit=self.roche)

    def test_rejecting_needs_a_reason_and_posts_nothing(self):
        intake_id = self.returned()
        with self.assertRaisesMessage(InvalidTransaction, "reason for rejecting"):
            reject_refund(actor=self.finance, business_unit=self.unit, intake_id=intake_id, idempotency_key="no", reason="no")
        result = reject_refund(actor=self.finance, business_unit=self.unit, intake_id=intake_id, idempotency_key="no-2",
                               reason="Outside the condition rules: item was used")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["rejection"]["reason"], "Outside the condition rules: item was used")
        self.assertFalse(JournalEntry.objects.filter(kind=JournalEntry.Kind.SALES_RETURN).exists())
        self.assertFalse(StockMovement.objects.filter(kind=StockMovement.Kind.RETURN_IN).exists())

    def test_cannot_refund_more_than_was_delivered(self):
        self.approve(self.returned(quantity="2.00"), credit="4000.00")
        second = self.returned(key="refund-return-2", quantity="1.00")
        review = refund_queue(actor=self.finance, business_unit=self.unit)["returns"][0]["review"]
        self.assertIn("More of this item would be returned", review["problem"])
        with self.assertRaisesMessage(InvalidTransaction, "More of this item would be returned"):
            self.approve(second, key="refund-ok-2")

    def test_refund_endpoints(self):
        intake_id = self.returned()
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.finance)
        token = client.get("/api/auth/csrf/").data["csrfToken"]
        listed = client.get("/api/finance/refunds/", {"unit": self.unit.pk})
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data["waiting"], 1)
        missing_key = client.post(f"/api/finance/refunds/{intake_id}/approve/?unit={self.unit.pk}",
                                  {"credit_total": "2000.00", "refund_method": "card"}, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(missing_key.status_code, 400)
        approved = client.post(f"/api/finance/refunds/{intake_id}/approve/?unit={self.unit.pk}",
                               {"credit_total": "2000.00", "refund_method": "card"}, format="json",
                               HTTP_X_CSRFTOKEN=token, HTTP_IDEMPOTENCY_KEY="api-refund")
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data["status"], "refunded")
        client.force_login(self.sales)
        self.assertEqual(client.get("/api/finance/refunds/", {"unit": self.unit.pk}).status_code, 403)
