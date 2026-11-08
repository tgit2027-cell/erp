"""Receivables aging and customer statements; synthetic only."""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import BusinessUnitRoleAssignment, DemoUserMarker, SalesQuote, UserActionGrant
from .purchasing_services import (
    WorkflowPermissionDenied, approve_post_handover_balance, deliver_sales_quote, notify_quote_ready,
)
from .receivables import customer_statement, receivables_aging
from . import test_purchasing_workflows as purchasing_tests
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction


@override_settings(APP_ENV="demo")
class ReceivablesTests(PurchasingFixtureMixin, TestCase):
    make_role_user = purchasing_tests.PurchasingWorkflowTests.make_role_user

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        cls.finance = get_user_model().objects.create_user(username="test_receivables_finance", password="unused-test-password")
        DemoUserMarker.objects.create(user=cls.finance)
        UserActionGrant.objects.create(user=cls.finance, business_unit=cls.unit, action=UserActionGrant.Action.VIEW_LEDGER)

    def deferred_sale(self, key="owed"):
        """A 4,000 sale where 3,600 was paid and 10% was approved to be paid after delivery."""
        self.make_receipt(key=f"{key}-receipt", charges=[])
        quote = SalesQuote.objects.get(pk=self.make_quote(key=f"{key}-quote")["quote_id"])
        self.pay(quote, key=f"{key}-deposit", amount="3600.00")
        approve_post_handover_balance(
            actor=self.make_role_user("synthetic_receivables_gm", BusinessUnitRoleAssignment.Role.BU_GM),
            quote=quote, idempotency_key=f"{key}-approval",
            deferred_balance_percent=Decimal("10.00"), reason="TEST ONLY delivery exception",
        )
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key=f"{key}-ready")
        deliver_sales_quote(actor=self.sales, quote=quote, idempotency_key=f"{key}-deliver")
        quote.refresh_from_db()
        return quote

    def day(self, quote, days):
        return (timezone.localtime(quote.invoice.issued_at).date() + datetime.timedelta(days=days)).isoformat()

    def test_unpaid_invoice_moves_through_the_age_buckets_and_leaves_once_paid(self):
        quote = self.deferred_sale()
        expected = {"current": "400.00", "days_31_60": "0.00", "days_61_90": "0.00", "days_91_120": "0.00", "over_120": "0.00"}
        aging = receivables_aging(actor=self.finance, business_unit=self.unit, as_of=self.day(quote, 0))
        self.assertEqual(aging["total"], "400.00")
        [row] = aging["customers"]
        self.assertEqual((row["id"], row["buckets"], row["oldest_days"]), (self.customer.pk, expected, 0))
        [invoice] = row["invoices"]
        self.assertEqual((invoice["number"], invoice["total"], invoice["paid"], invoice["owed"]),
                         (quote.invoice.number, "4000.00", "3600.00", "400.00"))
        for days, bucket in ((30, "current"), (31, "days_31_60"), (75, "days_61_90"), (91, "days_91_120"), (120, "days_91_120"),
                            (121, "over_120")):
            aging = receivables_aging(actor=self.finance, business_unit=self.unit, as_of=self.day(quote, days))
            self.assertEqual(aging["totals"][bucket], "400.00", days)
        before = receivables_aging(actor=self.finance, business_unit=self.unit, as_of=self.day(quote, -1))
        self.assertEqual((before["customers"], before["total"]), ([], "0.00"))
        self.pay(quote, key="owed-balance", amount="400.00")
        later = receivables_aging(actor=self.finance, business_unit=self.unit, as_of=self.day(quote, 1))
        self.assertEqual((later["customers"], later["total"]), ([], "0.00"))

    def test_aging_by_years_up_to_five_years(self):
        quote = self.deferred_sale()
        for days, bucket in ((0, "year_1"), (365, "year_1"), (366, "years_1_2"), (1100, "years_3_4"), (1825, "years_4_5"),
                             (1826, "over_5_years")):
            aging = receivables_aging(actor=self.finance, business_unit=self.unit, as_of=self.day(quote, days), by="years")
            self.assertEqual(aging["totals"][bucket], "400.00", days)
        self.assertEqual([bucket["label"] for bucket in aging["buckets"]],
                         ["Up to 1 year", "1–2 years", "2–3 years", "3–4 years", "4–5 years", "Over 5 years"])
        self.assertEqual(aging["by"], "years")
        with self.assertRaises(InvalidTransaction):
            receivables_aging(actor=self.finance, business_unit=self.unit, by="weeks")

    def test_statement_of_unpaid_invoices_only(self):
        paid = self.deferred_sale(key="unpaid-paid")
        self.pay(paid, key="unpaid-paid-balance", amount="400.00")
        owed = self.deferred_sale(key="unpaid-owed")
        end = self.day(owed, 40)
        statement = customer_statement(actor=self.finance, business_unit=self.unit, customer_id=self.customer.pk,
                                       end=end, show="unpaid")
        self.assertEqual(statement["show"], "unpaid")
        [invoice] = statement["unpaid"]
        self.assertEqual((invoice["number"], invoice["total"], invoice["paid"], invoice["owed"], invoice["age_days"],
                          invoice["bucket"], invoice["running"]),
                         (owed.invoice.number, "4000.00", "3600.00", "400.00", 40, "days_31_60", "400.00"))
        self.assertEqual((statement["lines"], statement["invoices_owed"]), ([], "400.00"))
        full = customer_statement(actor=self.finance, business_unit=self.unit, customer_id=self.customer.pk, end=end)
        self.assertEqual(full["show"], "all")
        self.assertEqual({line["reference"] for line in full["lines"] if line["kind"] == "invoice"},
                         {paid.invoice.number, owed.invoice.number})
        with self.assertRaises(InvalidTransaction):
            customer_statement(actor=self.finance, business_unit=self.unit, customer_id=self.customer.pk, show="some")

    def test_statement_runs_a_balance_from_deposit_to_final_payment(self):
        quote = self.deferred_sale()
        self.pay(quote, key="statement-balance", amount="400.00")
        statement = customer_statement(
            actor=self.finance, business_unit=self.unit, customer_id=self.customer.pk,
            start=self.day(quote, -5), end=self.day(quote, 5),
        )
        self.assertEqual([(line["kind"], line["debit"], line["credit"], line["balance"]) for line in statement["lines"]], [
            ("payment", "", "3600.00", "-3600.00"),
            ("invoice", "4000.00", "", "400.00"),
            ("payment", "", "400.00", "0.00"),
        ])
        self.assertEqual(statement["lines"][1]["reference"], quote.invoice.number)
        self.assertEqual((statement["opening_balance"], statement["closing_balance"], statement["invoices_owed"]),
                         ("0.00", "0.00", "0.00"))
        self.assertEqual((statement["total_debits"], statement["total_credits"]), ("4000.00", "4000.00"))

    def test_statement_carries_earlier_movements_into_the_opening_balance(self):
        quote = self.deferred_sale()
        statement = customer_statement(
            actor=self.finance, business_unit=self.unit, customer_id=self.customer.pk,
            start=self.day(quote, 1), end=self.day(quote, 40),
        )
        self.assertEqual((statement["opening_balance"], statement["closing_balance"], statement["lines"]),
                         ("400.00", "400.00", []))
        self.assertEqual(statement["aging"]["days_31_60"], "400.00")
        with self.assertRaisesMessage(InvalidTransaction, "start date"):
            customer_statement(actor=self.finance, business_unit=self.unit, customer_id=self.customer.pk,
                               start=self.day(quote, 2), end=self.day(quote, 1))

    def test_only_ledger_viewers_of_the_unit_can_see_receivables(self):
        self.deferred_sale()
        with self.assertRaises(WorkflowPermissionDenied):
            receivables_aging(actor=self.sales, business_unit=self.unit)
        with self.assertRaises(WorkflowPermissionDenied):
            customer_statement(actor=self.sales, business_unit=self.unit, customer_id=self.customer.pk)
        with self.assertRaisesMessage(InvalidTransaction, "no sales"):
            customer_statement(actor=self.finance, business_unit=self.unit, customer_id=self.supplier.pk)

    def test_api_returns_aging_and_statement_for_the_selected_unit(self):
        quote = self.deferred_sale()
        client = APIClient()
        client.force_login(self.finance)
        aging = client.get("/api/finance/receivables/", {"unit": self.unit.pk, "as_of": self.day(quote, 0)})
        self.assertEqual(aging.status_code, 200, aging.data)
        self.assertEqual(aging.data["total"], "400.00")
        statement = client.get(f"/api/finance/receivables/{self.customer.pk}/statement/", {"unit": self.unit.pk})
        self.assertEqual(statement.status_code, 200, statement.data)
        self.assertEqual(statement.data["customer"]["id"], self.customer.pk)
        client.force_login(self.sales)
        self.assertEqual(client.get("/api/finance/receivables/", {"unit": self.unit.pk}).status_code, 403)
