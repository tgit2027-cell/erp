"""Provisional customer invoices issued at delivery; synthetic only."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    BusinessUnitRoleAssignment, CustomerInvoice, CustomerInvoiceLine, JournalEntry, JournalLine, SalesQuote,
)
from .purchasing_services import (
    approve_post_handover_balance, create_sales_quote, deliver_sales_quote, notify_quote_ready,
)
from . import test_purchasing_workflows as purchasing_tests
from .test_purchasing_workflows import PurchasingFixtureMixin


class CustomerInvoiceTests(PurchasingFixtureMixin, TestCase):
    make_role_user = purchasing_tests.PurchasingWorkflowTests.make_role_user

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()

    def deliver(self, key, *, quantity="2.00", discount="0.00", paid=None):
        quote = SalesQuote.objects.get(pk=self.make_quote(
            key=f"{key}-quote", quantity=quantity, discount=discount,
        )["quote_id"])
        self.pay(quote, key=f"{key}-pay", amount=str(paid or quote.total_quote_value))
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key=f"{key}-ready")
        return quote, deliver_sales_quote(actor=self.sales, quote=quote, idempotency_key=f"{key}-deliver")

    def test_delivery_issues_one_numbered_invoice_linked_to_quote_and_journals(self):
        self.make_receipt()
        quote, result = self.deliver("first", discount="10.00")
        invoice = CustomerInvoice.objects.get()
        year = timezone.localtime(invoice.issued_at).year
        self.assertEqual(result["invoice_number"], f"TVL-INV-{year}-00001")
        self.assertEqual((result["invoice_id"], result["invoice_total"]), (invoice.pk, "3600.00"))
        self.assertEqual(invoice.quote, quote)
        self.assertEqual(
            (invoice.list_total, invoice.discount_total, invoice.net_total, invoice.tax_total),
            (Decimal("4000.00"), Decimal("400.00"), Decimal("3600.00"), Decimal("0.00")),
        )
        self.assertEqual((invoice.paid_before_delivery, invoice.balance_due), (Decimal("3600.00"), Decimal("0.00")))
        line = invoice.lines.get()
        self.assertEqual((line.quantity, line.net_unit_price, line.line_total),
                         (Decimal("2.00"), Decimal("1800.00"), Decimal("3600.00")))
        journal_ids = [row["journal_entry_id"] for row in result["delivery_results"]]
        self.assertEqual(list(line.journal_entries.values_list("pk", flat=True)), journal_ids)
        self.assertEqual(JournalEntry.objects.get(pk=journal_ids[0]).kind, JournalEntry.Kind.DELIVERY)
        replay = deliver_sales_quote(actor=self.sales, quote=quote, idempotency_key="first-deliver")
        self.assertEqual(replay, result)
        self.assertEqual(CustomerInvoice.objects.count(), 1)

    def test_numbers_run_in_sequence_per_business_unit(self):
        self.make_receipt()
        _, first = self.deliver("seq-a", quantity="1.00")
        _, second = self.deliver("seq-b", quantity="1.00")
        self.assertEqual([first["invoice_number"][-5:], second["invoice_number"][-5:]], ["00001", "00002"])

    def test_post_handover_balance_stays_due_on_the_invoice(self):
        self.make_receipt(charges=[])
        quote = SalesQuote.objects.get(pk=self.make_quote(key="due-quote")["quote_id"])
        self.pay(quote, key="due-deposit", amount="3600.00")
        approve_post_handover_balance(
            actor=self.make_role_user("synthetic_invoice_gm", BusinessUnitRoleAssignment.Role.BU_GM),
            quote=quote, idempotency_key="due-approval",
            deferred_balance_percent=Decimal("10.00"), reason="TEST ONLY delivery exception",
        )
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key="due-ready")
        deliver_sales_quote(actor=self.sales, quote=quote, idempotency_key="due-deliver")
        invoice = quote.invoice
        self.assertEqual((invoice.invoice_total, invoice.paid_before_delivery, invoice.balance_due),
                         (Decimal("4000.00"), Decimal("3600.00"), Decimal("400.00")))

    def test_multiline_quote_gets_one_invoice_line_per_quote_line(self):
        self.make_receipt(key="multi-a")
        self.make_receipt(key="multi-b", variant=self.alternate_variant)
        quote = SalesQuote.objects.get(pk=create_sales_quote(
            actor=self.sales, idempotency_key="multi-quote", business_unit=self.unit, customer=self.customer,
            kind="stock", discount_percent=Decimal("0.00"), discount_reason="TEST ONLY no discount",
            lines=[{"variant": variant, "location": self.location, "quantity": Decimal(quantity),
                    "specifications": {}} for variant, quantity in ((self.variant, "1.25"), (self.alternate_variant, "2.75"))],
        )["quote_id"])
        self.pay(quote, key="multi-pay", amount=str(quote.total_quote_value))
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key="multi-ready")
        deliver_sales_quote(actor=self.sales, quote=quote, idempotency_key="multi-deliver")
        lines = list(quote.invoice.lines.order_by("pk"))
        self.assertEqual([line.quantity for line in lines], [Decimal("1.25"), Decimal("2.75")])
        self.assertEqual(sum(line.line_total for line in lines), quote.invoice.net_total)

    def test_invoices_are_append_only_and_totals_are_checked_by_the_database(self):
        self.make_receipt()
        self.deliver("immutable")
        invoice = CustomerInvoice.objects.get()
        with self.assertRaises(ValidationError):
            invoice.save()
        with self.assertRaises(ValidationError):
            invoice.lines.get().delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustomerInvoice.objects.update(balance_due=Decimal("1.00"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustomerInvoice.objects.update(tax_total=Decimal("5.00"))

    def test_api_lists_and_shows_invoices_only_in_the_users_scope(self):
        self.make_receipt()
        self.deliver("api")
        invoice = CustomerInvoice.objects.get()
        client = APIClient()
        client.force_login(self.sales)
        listing = client.get("/api/purchasing/invoices/", {"business_unit_id": self.unit.pk})
        self.assertEqual(listing.status_code, 200, listing.data)
        self.assertEqual([row["number"] for row in listing.data["rows"]], [invoice.number])
        detail = client.get(f"/api/purchasing/invoices/{invoice.pk}/", {"business_unit_id": self.unit.pk})
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["invoice_total"], "4000.00")
        self.assertEqual(set(detail.data["lines"][0]), {
            "item_code", "description", "brand_name", "quantity", "list_unit_price",
            "discount_percent", "net_unit_price", "line_total", "journal_entry_ids",
        })
        self.assertNotIn("cost", str(detail.data).lower())
        found = client.get("/api/purchasing/invoices/", {"business_unit_id": self.unit.pk, "number": invoice.number.lower()})
        self.assertEqual(found.data["pagination"]["total_count"], 1)
        # Wrong unit or no grant: nothing is revealed.
        self.assertEqual(client.get(f"/api/purchasing/invoices/{invoice.pk}/", {"business_unit_id": self.roche.pk}).status_code, 404)
        other = APIClient()
        other.force_login(self.roche_user)
        self.assertEqual(other.get("/api/purchasing/invoices/", {"business_unit_id": self.unit.pk}).status_code, 404)
        self.assertEqual(other.get(f"/api/purchasing/invoices/{invoice.pk}/", {"business_unit_id": self.unit.pk}).status_code, 404)


class DepositLiabilityTests(PurchasingFixtureMixin, TestCase):
    """Owner decision F4: deposits are owed to the customer until delivery."""

    make_role_user = purchasing_tests.PurchasingWorkflowTests.make_role_user

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()

    def account(self, code):
        lines = JournalLine.objects.filter(entry__business_unit=self.unit, entry__status="posted", account__code=code)
        return sum((line.debit - line.credit for line in lines), Decimal("0.00"))

    def test_deposit_is_a_liability_until_delivery_then_settles_the_receivable(self):
        self.make_receipt(charges=[])
        quote = SalesQuote.objects.get(pk=self.make_quote(key="liability-quote")["quote_id"])
        paid = self.pay(quote, key="liability-deposit", amount="3600.00")
        entry = JournalEntry.objects.get(pk=paid["journal_entry_id"])
        self.assertEqual(entry.kind, JournalEntry.Kind.PAYMENT)
        self.assertEqual(self.account("TEST_CASH"), Decimal("3600.00"))
        self.assertEqual(self.account("TEST_CUSTOMER_DEPOSITS"), Decimal("-3600.00"))
        self.assertEqual(self.account("TEST_RECEIVABLE"), Decimal("0.00"))
        approve_post_handover_balance(
            actor=self.make_role_user("synthetic_liability_gm", BusinessUnitRoleAssignment.Role.BU_GM),
            quote=quote, idempotency_key="liability-approval",
            deferred_balance_percent=Decimal("10.00"), reason="TEST ONLY delivery exception",
        )
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key="liability-ready")
        result = deliver_sales_quote(actor=self.sales, quote=quote, idempotency_key="liability-deliver")
        self.assertEqual(self.account("TEST_CUSTOMER_DEPOSITS"), Decimal("0.00"))
        self.assertEqual(self.account("TEST_RECEIVABLE"), Decimal("400.00"))
        self.assertEqual(self.account("TEST_REVENUE"), Decimal("-4000.00"))
        # A replayed delivery does not apply the deposits twice.
        self.assertEqual(deliver_sales_quote(actor=self.sales, quote=quote, idempotency_key="liability-deliver"), result)
        self.assertEqual(self.account("TEST_CUSTOMER_DEPOSITS"), Decimal("0.00"))
        self.pay(quote, key="liability-final", amount="400.00")
        self.assertEqual(self.account("TEST_RECEIVABLE"), Decimal("0.00"))
        self.assertEqual(self.account("TEST_CASH"), Decimal("4000.00"))

    def test_a_full_payment_before_delivery_is_one_deposit_posting(self):
        self.make_receipt(charges=[])
        quote = SalesQuote.objects.get(pk=self.make_quote(key="storage-quote")["quote_id"])
        self.pay(quote, key="storage-full", amount="4000.00")
        self.assertEqual(self.account("TEST_CUSTOMER_DEPOSITS"), Decimal("-4000.00"))
        self.assertEqual(JournalEntry.objects.filter(kind=JournalEntry.Kind.PAYMENT).count(), 1)
