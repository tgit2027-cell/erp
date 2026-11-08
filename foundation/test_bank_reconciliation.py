"""Bank statements matched to our records and approved monthly reconciliations; synthetic only."""

import datetime
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .bank_reconciliation import (
    add_statement_lines,
    approve_reconciliation,
    auto_match,
    match_lines,
    prepare_reconciliation,
    reconciliation_view,
    record_from_statement,
    reject_reconciliation,
    remove_statement_line,
    unmatch_line,
)
from .master_admin_testing import by_master_admin
from .cash_accounts import cash_overview, create_account, move_line, record_entry
from .models import BankReconciliation, BankStatementLine, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction


@override_settings(APP_ENV="demo")
class BankReconciliationTests(PurchasingFixtureMixin, TestCase):
    """Admin records bank and cash and prepares; sales approves."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.create(user=cls.admin, business_unit=cls.unit, action=UserActionGrant.Action.MANAGE_CASH)
        UserActionGrant.objects.create(user=cls.sales, business_unit=cls.unit, action=UserActionGrant.Action.APPROVE_BANK_REC)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        today = timezone.localdate()
        self.last = today.replace(day=1) - datetime.timedelta(days=1)
        self.first = self.last.replace(day=1)
        self.month = self.first.isoformat()[:7]
        self.bank = by_master_admin(create_account)(actor=self.admin, business_unit=self.unit, name="TEST ONLY QNB current", kind="bank",
                                   opening_balance="50000.00", opening_date=self.first.isoformat())
        self.till = by_master_admin(create_account)(actor=self.admin, business_unit=self.unit, name="TEST ONLY Till", kind="cash",
                                   opening_balance="500.00", opening_date=self.first.isoformat())

    def day(self, number):
        return self.first.replace(day=number).isoformat()

    def entry(self, kind, amount, day, key, **extra):
        return record_entry(actor=self.admin, idempotency_key=key, business_unit=self.unit, kind=kind,
                            account_id=self.bank["id"], amount=amount, entry_date=day, **extra)

    def book_the_month(self):
        self.entry("interest", "100.00", self.day(5), "interest")
        self.entry("transfer", "1000.00", self.day(10), "to-till", to_account_id=self.till["id"])
        self.entry("bank_charge", "20.00", self.day(28), "fee")
        self.entry("transfer", "2000.00", self.last.isoformat(), "late-transfer", to_account_id=self.till["id"])

    def statement(self, key="statement", lines=None):
        lines = lines or [
            {"date": self.day(6), "description": "TEST ONLY interest credit", "amount": "100.00"},
            {"date": self.day(11), "description": "TEST ONLY cash withdrawal", "amount": "-1000.00", "reference": "CHQ 1"},
            {"date": self.day(28), "description": "TEST ONLY monthly fee", "amount": "-20.00"},
            {"date": self.day(28), "description": "TEST ONLY card terminal fee", "amount": "-15.50"},
        ]
        return add_statement_lines(actor=self.admin, idempotency_key=key, business_unit=self.unit,
                                   account_id=self.bank["id"], lines=lines)

    def view(self, month=None):
        return reconciliation_view(actor=self.admin, business_unit=self.unit, account_id=self.bank["id"], month=month or self.month)

    def prepare(self, closing="49064.50", opening="50000.00", key="prepare", month=None):
        return prepare_reconciliation(actor=self.admin, idempotency_key=key, business_unit=self.unit, account_id=self.bank["id"],
                                      month=month or self.month, statement_opening=opening, statement_closing=closing)

    def test_a_month_is_matched_explained_prepared_and_approved_by_someone_else(self):
        self.book_the_month()
        self.assertEqual(self.statement()["added"], 4)
        self.assertEqual(auto_match(actor=self.admin, business_unit=self.unit, account_id=self.bank["id"], month=self.month),
                         {"matched": 3})
        view = self.view()
        self.assertEqual((view["unmatched_count"], view["suggested_opening"], view["statement_movement"]), (1, "50000.00", "-935.50"))
        with self.assertRaisesMessage(InvalidTransaction, "1 statement lines are not matched yet"):
            self.prepare()
        fee = next(row for row in view["statement"] if row["matched_to"] is None)
        with self.assertRaisesMessage(InvalidTransaction, "this line is the other way"):
            record_from_statement(actor=self.admin, idempotency_key="wrong", business_unit=self.unit,
                                  statement_line_id=fee["id"], kind="interest")
        record_from_statement(actor=self.admin, idempotency_key="terminal-fee", business_unit=self.unit,
                              statement_line_id=fee["id"], kind="bank_charge")
        view = self.view()
        self.assertEqual((view["unmatched_count"], view["book_balance"], view["outstanding_out"], view["expected_closing"]),
                         (0, "47064.50", "2000.00", "49064.50"))
        self.assertEqual([row["amount"] for row in view["outstanding"]], ["-2000.00"])
        with self.assertRaisesMessage(InvalidTransaction, "does not add up"):
            self.prepare(closing="49000.00", key="bad-closing")
        prepared = self.prepare()
        self.assertEqual((prepared["status"], prepared["outstanding_out"]), ("prepared", "2000.00"))
        UserActionGrant.objects.create(user=self.admin, business_unit=self.unit, action=UserActionGrant.Action.APPROVE_BANK_REC)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "someone else must decide"):
            approve_reconciliation(actor=self.admin, business_unit=self.unit, reconciliation_id=prepared["id"])
        with self.assertRaisesMessage(WorkflowPermissionDenied, "approve bank reconciliations"):
            approve_reconciliation(actor=self.roche_user, business_unit=self.unit, reconciliation_id=prepared["id"])
        approved = approve_reconciliation(actor=self.sales, business_unit=self.unit, reconciliation_id=prepared["id"])
        self.assertEqual((approved["status"], approved["decided_by"]), ("approved", self.sales.username))
        self.assertTrue(self.view()["locked"])

        line = BankStatementLine.objects.filter(account_id=self.bank["id"]).first()
        for call in (lambda: remove_statement_line(actor=self.admin, business_unit=self.unit, line_id=line.pk),
                     lambda: unmatch_line(actor=self.admin, business_unit=self.unit, statement_line_id=line.pk),
                     lambda: self.entry("interest", "1.00", self.day(15), "into-locked")):
            with self.subTest(call=call), self.assertRaisesMessage(InvalidTransaction, "reconciled and approved"):
                call()
        transfer = next(row for row in cash_overview(actor=self.admin, business_unit=self.unit, account_id=self.till["id"],
                                                     start=self.first.isoformat())["movements"] if row["money_in"] == "2000.00")
        with self.assertRaisesMessage(InvalidTransaction, "stay on the account"):
            move_line(actor=self.admin, business_unit=self.unit, line_id=transfer["line_id"], account_id=self.bank["id"])
        with transaction.atomic(), self.assertRaises(IntegrityError):
            BankReconciliation.objects.filter(pk=approved["id"]).update(decided_by=self.admin)

    def test_the_next_month_carries_the_closing_balance_and_clears_what_was_outstanding(self):
        self.book_the_month()
        self.statement()
        auto_match(actor=self.admin, business_unit=self.unit, account_id=self.bank["id"], month=self.month)
        fee = next(row for row in self.view()["statement"] if row["matched_to"] is None)
        record_from_statement(actor=self.admin, idempotency_key="fee-2", business_unit=self.unit,
                              statement_line_id=fee["id"], kind="bank_charge")
        approve_reconciliation(actor=self.sales, business_unit=self.unit, reconciliation_id=self.prepare()["id"])
        # This month: the late transfer clears and a customer card payment arrives.
        this_month = timezone.localdate().isoformat()[:7]
        self.make_receipt(key="stock")
        from .models import QuotePayment, SalesQuote
        quote = SalesQuote.objects.get(pk=self.make_quote(key="quote", quantity="1.00")["quote_id"])
        self.pay(quote, key="card", amount="2000.00", method=QuotePayment.Method.SIMULATED_CARD)
        self.statement("statement-2", [
            {"date": f"{this_month}-01", "description": "TEST ONLY cash withdrawal", "amount": "-2000.00"},
            {"date": timezone.localdate().isoformat(), "description": "TEST ONLY card settlement", "amount": "2000.00"},
        ])
        # 2,000 out clears last month's transfer and 2,000 in is the card payment: opposite signs, one candidate each.
        self.assertEqual(auto_match(actor=self.admin, business_unit=self.unit, account_id=self.bank["id"]), {"matched": 2})
        view = self.view(this_month)
        self.assertEqual((view["suggested_opening"], view["unmatched_count"], view["outstanding"]), ("49064.50", 0, []))
        self.assertEqual(view["expected_closing"], "49064.50")
        with self.assertRaisesMessage(InvalidTransaction, "A month can be reconciled once it has ended"):
            self.prepare(opening="49064.50", closing="49064.50", key="too-early", month=this_month)

    def test_changed_since_prepared_is_refused_and_can_be_rejected_and_redone(self):
        self.entry("interest", "100.00", self.day(5), "interest")
        self.statement(lines=[{"date": self.day(5), "description": "TEST ONLY interest", "amount": "100.00"}])
        auto_match(actor=self.admin, business_unit=self.unit, account_id=self.bank["id"], month=self.month)
        prepared = self.prepare(closing="50100.00")
        with self.assertRaisesMessage(InvalidTransaction, "already reconciled or waiting"):
            self.prepare(closing="50100.00", key="again")
        self.entry("bank_charge", "5.00", self.day(20), "late-fee")
        with self.assertRaisesMessage(InvalidTransaction, "changed since this was prepared"):
            approve_reconciliation(actor=self.sales, business_unit=self.unit, reconciliation_id=prepared["id"])
        with self.assertRaisesMessage(InvalidTransaction, "Say why"):
            reject_reconciliation(actor=self.sales, business_unit=self.unit, reconciliation_id=prepared["id"], note="")
        reject_reconciliation(actor=self.sales, business_unit=self.unit, reconciliation_id=prepared["id"],
                              note="TEST ONLY a fee was booked after this was prepared")
        # The fee is in our records but not on the statement yet, so it is outstanding and the statement still ties.
        with self.assertRaisesMessage(InvalidTransaction, "differs from the statement closing 50095.00 by -5.00"):
            self.prepare(closing="50095.00", opening="49995.00", key="wrong-opening")
        redone = self.prepare(closing="50100.00", key="redo")
        self.assertEqual((redone["status"], redone["book_balance"], redone["outstanding_out"]), ("prepared", "50095.00", "5.00"))

    def test_statement_lines_and_matching_rules(self):
        self.entry("interest", "100.00", self.day(5), "interest")
        record_entry(actor=self.admin, idempotency_key="till-in", business_unit=self.unit, kind="transfer",
                     account_id=self.bank["id"], to_account_id=self.till["id"], amount="300.00", entry_date=self.day(7))
        first = self.statement(lines=[{"date": self.day(5), "description": "TEST ONLY interest", "amount": "100.00"}])
        self.assertEqual(first, self.statement(lines=[{"date": self.day(5), "description": "TEST ONLY interest", "amount": "100.00"}]))
        with self.assertRaisesMessage(InvalidTransaction, "Line 1 (" ):
            self.statement("dupe", [{"date": self.day(5), "description": "test only INTEREST", "amount": "100.00"}])
        before = (self.first - datetime.timedelta(days=1)).isoformat()
        for line, message in (({"amount": "0"}, "cannot be zero"), ({"amount": "1.005"}, "two decimals"),
                              ({"date": before}, "opened on"), ({"description": " "}, "Give the description")):
            with self.subTest(line=line), self.assertRaisesMessage(InvalidTransaction, message):
                self.statement(f"bad-{line}", [{"date": self.day(8), "description": "TEST ONLY x", "amount": "5.00", **line}])
        interest = BankStatementLine.objects.get(description="TEST ONLY interest")
        book = {row["amount"]: row["line_id"] for row in self.view()["outstanding"]}
        with self.assertRaisesMessage(InvalidTransaction, "amounts differ"):
            match_lines(actor=self.admin, business_unit=self.unit, statement_line_id=interest.pk, journal_line_id=book["-300.00"])
        till_line = next(row for row in cash_overview(actor=self.admin, business_unit=self.unit, account_id=self.till["id"],
                                                      start=self.first.isoformat())["movements"])
        self.statement("till-line", [{"date": self.day(7), "description": "TEST ONLY odd", "amount": "300.00"}])
        odd = BankStatementLine.objects.get(description="TEST ONLY odd")
        with self.assertRaisesMessage(InvalidTransaction, "not on TEST ONLY QNB current"):
            match_lines(actor=self.admin, business_unit=self.unit, statement_line_id=odd.pk, journal_line_id=till_line["line_id"])
        match_lines(actor=self.admin, business_unit=self.unit, statement_line_id=interest.pk, journal_line_id=book["100.00"])
        with self.assertRaisesMessage(InvalidTransaction, "already matched"):
            match_lines(actor=self.admin, business_unit=self.unit, statement_line_id=interest.pk, journal_line_id=book["100.00"])
        with self.assertRaisesMessage(InvalidTransaction, "Unmatch this line"):
            remove_statement_line(actor=self.admin, business_unit=self.unit, line_id=interest.pk)
        unmatch_line(actor=self.admin, business_unit=self.unit, statement_line_id=interest.pk)
        remove_statement_line(actor=self.admin, business_unit=self.unit, line_id=odd.pk)
        self.assertFalse(BankStatementLine.objects.filter(pk=odd.pk).exists())
        with self.assertRaisesMessage(WorkflowPermissionDenied, "record bank and cash"):
            self.statement("no-permission") if False else add_statement_lines(
                actor=self.sales, idempotency_key="x", business_unit=self.unit, account_id=self.bank["id"],
                lines=[{"date": self.day(9), "description": "TEST ONLY y", "amount": "1.00"}])

    def test_api(self):
        self.entry("interest", "100.00", self.day(5), "interest")
        client = APIClient()
        client.force_authenticate(self.admin)
        unit = f"?unit={self.unit.pk}"
        url = f"/api/finance/bank-reconciliation/statement/{unit}"
        add = {"action": "add", "account_id": self.bank["id"],
               "lines": [{"date": self.day(5), "description": "TEST ONLY interest", "amount": "100.00"}]}
        self.assertEqual(client.post(url, add, format="json").status_code, 400, "Adding needs an idempotency key")
        self.assertEqual(client.post(url, add, format="json", HTTP_IDEMPOTENCY_KEY="api-add").status_code, 201)
        matched = client.post(url, {"action": "auto_match", "account_id": self.bank["id"], "month": self.month}, format="json")
        self.assertEqual((matched.status_code, matched.data), (200, {"matched": 1}))
        prepared = client.post(f"/api/finance/bank-reconciliation/{unit}", {
            "account_id": self.bank["id"], "month": self.month, "statement_opening": "50000.00", "statement_closing": "50100.00",
        }, format="json", HTTP_IDEMPOTENCY_KEY="api-prepare")
        self.assertEqual(prepared.status_code, 201, prepared.data)
        decide = f"/api/finance/bank-reconciliation/{prepared.data['id']}/approve/{unit}"
        self.assertEqual(client.post(decide, {}, format="json").status_code, 403)
        checker = APIClient()
        checker.force_authenticate(self.sales)
        self.assertEqual(checker.post(decide, {}, format="json").data["status"], "approved")
        view = client.get(f"/api/finance/bank-reconciliation/{unit}&account={self.bank['id']}&month={self.month}")
        self.assertEqual((view.status_code, view.data["locked"], view.data["history"][0]["status"]), (200, True, "approved"))
        outsider = APIClient()
        outsider.force_authenticate(self.roche_user)
        self.assertEqual(outsider.get(f"/api/finance/bank-reconciliation/{unit}").status_code, 403)


class _FakeStream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.response


class _FakeClient:
    """Stands in for anthropic.Anthropic(): records the request and returns a prepared answer."""

    def __init__(self, answer, stop_reason="end_turn"):
        self.requests = []
        text = json.dumps(answer) if not isinstance(answer, str) else answer
        self.response = SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])
        self.messages = SimpleNamespace(stream=self.stream)

    def stream(self, **kwargs):
        self.requests.append(kwargs)
        return _FakeStream(self.response)


@override_settings(APP_ENV="demo")
class StatementReaderTests(PurchasingFixtureMixin, TestCase):
    """Uploading a statement returns draft lines to check; the AI service itself is replaced by a fake."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.create(user=cls.admin, business_unit=cls.unit, action=UserActionGrant.Action.MANAGE_CASH)
        UserActionGrant.objects.create(user=cls.sales, business_unit=cls.unit, action=UserActionGrant.Action.APPROVE_BANK_REC)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        today = timezone.localdate()
        self.last = today.replace(day=1) - datetime.timedelta(days=1)
        self.first = self.last.replace(day=1)
        self.month = self.first.isoformat()[:7]
        self.bank = by_master_admin(create_account)(actor=self.admin, business_unit=self.unit, name="TEST ONLY QNB current", kind="bank",
                                   account_last4="4417", opening_balance="50000.00", opening_date=self.first.isoformat())
        add_statement_lines(actor=self.admin, idempotency_key="existing", business_unit=self.unit, account_id=self.bank["id"],
                            lines=[{"date": self.day(3), "description": "TEST ONLY transfer in", "amount": "2000.00"}])
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.url = f"/api/finance/bank-reconciliation/read-statement/?unit={self.unit.pk}"

    def day(self, number):
        return self.first.replace(day=number).isoformat()

    def answer(self, **extra):
        return {
            "bank_name": "TEST ONLY QNB", "account_last4": "4417", "currency": "QAR",
            "period_start": self.first.isoformat(), "period_end": self.last.isoformat(),
            "opening_balance": "50,000.00", "closing_balance": "51,987.50",
            "lines": [
                {"date": self.day(3), "description": "TEST ONLY  transfer in", "reference": "FT-1", "amount": "2,000.00"},
                {"date": self.day(9), "description": "TEST ONLY bank charge", "reference": "", "amount": "(12.50)"},
                {"date": self.day(12), "description": "TEST ONLY card deposit", "reference": "", "amount": "see note"},
                {"date": "2001-01-01", "description": "TEST ONLY old line", "reference": "", "amount": "5.00"},
            ],
        } | extra

    def upload(self, name="statement.pdf", body=b"%PDF-1.4 synthetic", **extra):
        return self.client.post(self.url, {"file": SimpleUploadedFile(name, body), "account_id": self.bank["id"],
                                           "month": self.month, **extra}, format="multipart")

    def test_without_a_key_the_page_says_reading_is_off(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            response = self.upload()
            view = reconciliation_view(actor=self.admin, business_unit=self.unit, account_id=self.bank["id"], month=self.month)
        self.assertEqual(response.status_code, 400)
        self.assertIn("not switched on", response.data["detail"])
        self.assertFalse(view["can_read_documents"])

    def test_a_statement_is_read_into_draft_lines_that_say_what_to_check(self):
        fake = _FakeClient(self.answer())
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}), patch("foundation.document_ai._client", return_value=fake):
            response = self.upload()
        self.assertEqual(response.status_code, 200, response.data)
        request = fake.requests[0]
        self.assertEqual(request["model"], "claude-opus-5-5")
        self.assertEqual(request["output_config"]["format"]["type"], "json_schema")
        document, instructions = request["messages"][0]["content"]
        self.assertEqual((document["type"], document["source"]["media_type"]), ("document", "application/pdf"))
        self.assertIn("Positive for money in", instructions["text"])
        lines = response.data["lines"]
        self.assertEqual([(row["amount"], row["checks"], row["include"]) for row in lines], [
            ("2000.00", ["Already on the statement"], False),
            ("-12.50", [], True),
            ("see note", ["Amount not clear"], False),
            ("5.00", ["Before the account opened"], False),
        ])
        self.assertEqual(lines[0]["description"], "TEST ONLY transfer in", "Spacing is tidied")
        self.assertEqual((response.data["ready_count"], response.data["money_out"]), (1, "12.50"))
        self.assertIn("do not add up", response.data["warnings"][0], "Opening plus lines read is not the closing")
        self.assertEqual(BankStatementLine.objects.filter(account_id=self.bank["id"]).count(), 1, "Reading saves nothing")

    def test_other_accounts_csv_text_refusals_and_permissions(self):
        fake = _FakeClient(self.answer(account_last4="9999", currency="USD", closing_balance="", lines=[]))
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}), patch("foundation.document_ai._client", return_value=fake):
            response = self.upload("march.csv", b"Date,Details,Amount\n03/10/2026,Transfer,2000.00\n")
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(fake.requests[0]["messages"][0]["content"][0]["type"], "text", "CSV is sent as text")
            self.assertIn("Transfer,2000.00", fake.requests[0]["messages"][0]["content"][0]["text"])
            self.assertEqual(len(response.data["warnings"]), 2, "Currency and account number both differ")
            self.assertEqual(self.upload("statement.docx").status_code, 400)
            self.assertEqual(self.upload("empty.pdf", b"").status_code, 400)
        refused = _FakeClient({}, stop_reason="refusal")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}), patch("foundation.document_ai._client", return_value=refused):
            response = self.upload()
        self.assertEqual(response.status_code, 400)
        self.assertIn("declined", response.data["detail"])
        viewer = APIClient()
        viewer.force_authenticate(self.sales)
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}), patch("foundation.document_ai._client", return_value=fake):
            denied = viewer.post(self.url, {"file": SimpleUploadedFile("s.pdf", b"x"), "account_id": self.bank["id"]},
                                 format="multipart")
        self.assertEqual(denied.status_code, 403)
