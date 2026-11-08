import datetime
import io
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .financial_statements import balance_sheet, close_month, income_statement, month_close_overview, reopen_month
from .general_ledger import export_ledger
from .models import (
    CashAccount, ClosedMonth, DemoUserMarker, IdempotencyRecord, JournalEntry, MonthCloseEvent, StockCount, UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied
from .test_reporting import ReportingFixtureMixin
from .transaction_services import InvalidTransaction, _post_journal

VIEW = UserActionGrant.Action.VIEW_LEDGER
CLOSE = UserActionGrant.Action.CLOSE_MONTH


def sale(amount, cost):
    return [
        ("TEST_RECEIVABLE", amount, "0.00", "sale"), ("TEST_REVENUE", "0.00", amount, "sale"),
        ("TEST_COGS", cost, "0.00", "cost"), ("TEST_INVENTORY", "0.00", cost, "cost"),
    ]


def receipt(amount):
    return [("TEST_INVENTORY", amount, "0.00", "goods in"), ("TEST_GRNI", "0.00", amount, "goods in")]


@override_settings(APP_ENV="demo")
class FinancialStatementTests(ReportingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.create(user=cls.sales, business_unit=cls.unit, action=VIEW)
        cls.controller = get_user_model().objects.create_user(username="test_finance_controller", password="unused-test-password")
        DemoUserMarker.objects.create(user=cls.controller)
        for action in (VIEW, CLOSE):
            UserActionGrant.objects.create(user=cls.controller, business_unit=cls.unit, action=action)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()
        self.this_month = self.today.replace(day=1)
        self.last_month = (self.this_month - datetime.timedelta(days=1)).replace(day=1)
        self.two_back = (self.last_month - datetime.timedelta(days=1)).replace(day=1)

    def post(self, key, lines, day=None, unit=None):
        unit = unit or self.unit
        with transaction.atomic():
            operation = IdempotencyRecord.objects.create(
                key=key, operation="test_financial_statements", payload_hash="0" * 64,
                status=IdempotencyRecord.Status.COMPLETE, completed_at=timezone.now(),
            )
            return _post_journal(
                legal_entity=unit.legal_entity, business_unit=unit, idempotency=operation,
                kind=JournalEntry.Kind.RECEIPT, description=f"TEST ONLY {key}", document_date=day, lines=lines,
            )

    def test_profit_and_loss_shows_gross_and_net_profit(self):
        self.make_delivered_sale(key="fs-sale")
        self.post("fs-fx", [("TEST_CASH", "0.00", "30.00", "fx"), ("TEST_FX_LOSS", "30.00", "0.00", "fx")])
        report = income_statement(actor=self.sales, business_unit=self.unit)
        sections = {section["key"]: section for section in report["sections"]}
        self.assertEqual(sections["sales"]["total"]["period"], "4000.00")
        self.assertEqual(sections["cost_of_sales"]["total"]["period"], "2400.00")
        self.assertEqual([row["code"] for row in sections["other_expenses"]["rows"]], ["TEST_FX_LOSS"])
        self.assertEqual(report["gross_profit"]["period"], "1600.00")
        self.assertEqual(report["gross_margin_percent"]["period"], "40.0")
        self.assertEqual(report["net_profit"], {"period": "1570.00", "year_to_date": "1570.00"})
        # Balance sheet accounts never appear on the profit and loss.
        codes = {row["code"] for section in report["sections"] for row in section["rows"]}
        self.assertNotIn("TEST_INVENTORY", codes)

    def test_period_and_year_to_date_differ_by_document_date(self):
        if self.last_month.year != self.today.year:
            self.skipTest("Last month falls in the previous year.")
        self.post("fs-old-sale", sale("1000.00", "600.00"), day=self.last_month)
        self.post("fs-new-sale", sale("500.00", "200.00"))
        report = income_statement(actor=self.sales, business_unit=self.unit)
        self.assertEqual(report["net_profit"], {"period": "300.00", "year_to_date": "700.00"})

    def test_balance_sheet_balances_and_profit_becomes_equity(self):
        last_year = self.today.replace(year=self.today.year - 1, month=6, day=15)
        self.post("fs-prior-receipt", receipt("5000.00"), day=last_year)
        self.post("fs-prior-sale", sale("2000.00", "1500.00"), day=last_year)
        self.make_delivered_sale(key="fs-bs-sale")
        sheet = balance_sheet(actor=self.sales, business_unit=self.unit)
        self.assertTrue(sheet["balanced"], sheet)
        self.assertEqual(sheet["difference"], "0.00")
        self.assertEqual(sheet["total_assets"], sheet["total_liabilities_and_equity"])
        equity = next(section for section in sheet["sections"] if section["key"] == "equity")
        self.assertEqual([row["balance"] for row in equity["rows"]], ["500.00", "1600.00"])
        earlier = balance_sheet(actor=self.sales, business_unit=self.unit, as_of=last_year.isoformat())
        self.assertTrue(earlier["balanced"])
        self.assertEqual(earlier["sections"][2]["rows"][1]["balance"], "500.00")

    def test_statements_need_the_ledger_permission(self):
        with self.assertRaisesMessage(WorkflowPermissionDenied, "permission to view the general ledger"):
            income_statement(actor=self.roche_user, business_unit=self.unit)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "permission to view the general ledger"):
            balance_sheet(actor=self.sales, business_unit=self.roche)
        with self.assertRaisesMessage(InvalidTransaction, "YYYY-MM-DD"):
            balance_sheet(actor=self.sales, business_unit=self.unit, as_of="31/10/2026")

    def test_statements_export_to_excel(self):
        self.make_delivered_sale(key="fs-xlsx-sale")
        for view, expected in (("income-statement", "Gross profit"), ("balance-sheet", "Total liabilities and equity")):
            name, content = export_ledger(actor=self.sales, business_unit=self.unit, view=view)
            self.assertTrue(name.startswith(f"ledger-{view}-TVL-"), name)
            book = ZipFile(io.BytesIO(content))
            text = book.read("xl/worksheets/sheet1.xml").decode() + book.read("xl/sharedStrings.xml").decode() \
                if "xl/sharedStrings.xml" in book.namelist() else book.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn(expected, text)

    def test_closing_a_month_stops_postings_dated_in_it(self):
        self.post("fs-last-month", receipt("800.00"), day=self.last_month)
        overview = month_close_overview(actor=self.controller, business_unit=self.unit)
        current, previous = overview["months"][0], overview["months"][1]
        self.assertEqual((current["status"], current["can_close"]), ("current", False))
        self.assertEqual(current["close_blocked"], "This month has not ended yet.")
        self.assertTrue(previous["can_close"], previous)
        closed = close_month(actor=self.controller, business_unit=self.unit, month=self.last_month.strftime("%Y-%m"))
        row = closed["months"][1]
        self.assertEqual((row["status"], row["entry_count"], row["debit"]), ("closed", 1, "800.00"))
        # Closing is the controller's job; reopening is the Master Admin's only (setup lock, Nov 2026).
        self.assertFalse(row["can_reopen"])
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only the Master Admin can reopen a closed month."):
            reopen_month(actor=self.controller, business_unit=self.unit, month=self.last_month.strftime("%Y-%m"),
                         reason="A late supplier invoice arrived")
        with self.assertRaisesMessage(InvalidTransaction, f"{self.last_month:%B %Y} is closed for"):
            self.post("fs-late", receipt("100.00"), day=self.last_month + datetime.timedelta(days=3))
        # The database refuses it too, whatever code path tries.
        with self.assertRaises(IntegrityError), transaction.atomic():
            operation = IdempotencyRecord.objects.create(
                key="fs-raw", operation="test", payload_hash="0" * 64,
                status=IdempotencyRecord.Status.COMPLETE, completed_at=timezone.now(),
            )
            JournalEntry.objects.create(
                idempotency=operation, legal_entity=self.unit.legal_entity, business_unit=self.unit,
                kind=JournalEntry.Kind.RECEIPT, description="TEST ONLY raw", document_date=self.last_month,
            )
        # Other units and open months are not affected.
        self.post("fs-roche", receipt("100.00"), day=self.last_month, unit=self.roche)
        self.post("fs-today", receipt("100.00"))
        with self.assertRaisesMessage(InvalidTransaction, "already closed"):
            close_month(actor=self.controller, business_unit=self.unit, month=self.last_month.strftime("%Y-%m"))

    def test_months_close_in_order_and_only_the_latest_reopens(self):
        self.controller.is_superuser = True
        self.controller.save(update_fields=["is_superuser"])
        self.post("fs-two-back", receipt("300.00"), day=self.two_back)
        self.post("fs-one-back", receipt("400.00"), day=self.last_month)
        with self.assertRaisesMessage(InvalidTransaction, f"Close {self.two_back:%B %Y} first"):
            close_month(actor=self.controller, business_unit=self.unit, month=self.last_month.strftime("%Y-%m"))
        close_month(actor=self.controller, business_unit=self.unit, month=self.two_back.strftime("%Y-%m"))
        close_month(actor=self.controller, business_unit=self.unit, month=self.last_month.strftime("%Y-%m"))
        with self.assertRaisesMessage(InvalidTransaction, f"Reopen {self.last_month:%B %Y} first"):
            reopen_month(actor=self.controller, business_unit=self.unit, month=self.two_back.strftime("%Y-%m"),
                         reason="A late supplier invoice arrived")
        with self.assertRaisesMessage(InvalidTransaction, "Say why"):
            reopen_month(actor=self.controller, business_unit=self.unit, month=self.last_month.strftime("%Y-%m"), reason="late")
        overview = reopen_month(actor=self.controller, business_unit=self.unit, month=self.last_month.strftime("%Y-%m"),
                                reason="A late supplier invoice arrived")
        self.assertEqual(overview["months"][1]["status"], "open")
        self.assertTrue(overview["months"][2]["can_reopen"])
        self.post("fs-after-reopen", receipt("50.00"), day=self.last_month)
        self.assertEqual([event["action"] for event in overview["history"]], ["reopen", "close", "close"])
        self.assertEqual(overview["history"][0]["reason"], "A late supplier invoice arrived")
        self.assertEqual(MonthCloseEvent.objects.count(), 3)
        self.assertEqual(ClosedMonth.objects.filter(business_unit=self.unit).count(), 1)

    def test_closing_needs_the_close_permission_and_warns_about_open_work(self):
        self.post("fs-perm", receipt("200.00"), day=self.last_month)
        overview = month_close_overview(actor=self.sales, business_unit=self.unit)
        self.assertFalse(overview["can_close"])
        self.assertFalse(overview["months"][1]["can_close"])
        with self.assertRaisesMessage(WorkflowPermissionDenied, "permission to close or reopen months"):
            close_month(actor=self.sales, business_unit=self.unit, month=self.last_month.strftime("%Y-%m"))
        with self.assertRaisesMessage(InvalidTransaction, "YYYY-MM"):
            close_month(actor=self.controller, business_unit=self.unit, month="last month")
        StockCount.objects.create(
            business_unit=self.unit, location=self.location, opened_by=self.controller,
            opened_at=timezone.make_aware(datetime.datetime.combine(self.last_month, datetime.time(10))),
        )
        month = month_close_overview(actor=self.controller, business_unit=self.unit)["months"][1]
        self.assertTrue(month["can_close"])
        self.assertEqual(len(month["warnings"]), 1)
        self.assertIn("1 stock count is not finished", month["warnings"][0])

    def test_the_checklist_needs_a_reason_to_close_with_checks_not_done(self):
        self.post("fs-check", receipt("200.00"), day=self.last_month)
        CashAccount.objects.create(business_unit=self.unit, name="TEST ONLY QNB current", kind="bank", account_type="current",
                                   opening_date=self.two_back, created_by=self.controller)
        month = month_close_overview(actor=self.controller, business_unit=self.unit)["months"][1]
        checks = {check["key"]: check for check in month["checklist"]}
        self.assertEqual(set(checks), {"bank_rec", "approvals", "stock_counts", "bills"},
                         "No depreciation or payroll check without assets or staff on the payroll")
        self.assertFalse(checks["bank_rec"]["done"])
        self.assertIn("Not approved yet: TEST ONLY QNB current.", checks["bank_rec"]["detail"])
        self.assertTrue(checks["approvals"]["done"])
        self.assertFalse(checks["stock_counts"]["required"])
        self.assertTrue(checks["bills"]["required"], "A delivery without its supplier's bill must be sorted before closing")
        label = self.last_month.strftime("%Y-%m")
        with self.assertRaisesMessage(InvalidTransaction, "Not done yet: bank and cash reconciled"):
            close_month(actor=self.controller, business_unit=self.unit, month=label)
        with self.assertRaisesMessage(InvalidTransaction, "at least 10 characters"):
            close_month(actor=self.controller, business_unit=self.unit, month=label, reason="late")
        closed = close_month(actor=self.controller, business_unit=self.unit, month=label,
                             reason="TEST ONLY bank statement arrives late, reconciling next week")
        event = MonthCloseEvent.objects.get(month=self.last_month, action="close")
        self.assertEqual(event.reason, "TEST ONLY bank statement arrives late, reconciling next week")
        self.assertEqual([check["label"] for check in event.checklist if not check["done"]], ["Bank and cash reconciled"])
        self.assertEqual(closed["history"][0]["checks_not_done"], ["Bank and cash reconciled"])

    def test_month_close_endpoints(self):
        self.post("fs-api", receipt("200.00"), day=self.last_month)
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.controller)
        token = client.get("/api/auth/csrf/").data["csrfToken"]
        listed = client.get("/api/finance/ledger/months/", {"unit": self.unit.pk})
        self.assertEqual(listed.status_code, 200, listed.data)
        month = self.last_month.strftime("%Y-%m")
        closed = client.post(f"/api/finance/ledger/months/close/?unit={self.unit.pk}", {"month": month}, format="json",
                             HTTP_X_CSRFTOKEN=token)
        self.assertEqual(closed.status_code, 200, closed.data)
        self.assertEqual(closed.data["months"][1]["status"], "closed")
        refused = client.post(f"/api/finance/ledger/months/reopen/?unit={self.unit.pk}", {"month": month, "reason": ""},
                              format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(refused.status_code, 403)  # Only the Master Admin reopens a month.
        self.controller.is_superuser = True
        self.controller.save(update_fields=["is_superuser"])
        refused = client.post(f"/api/finance/ledger/months/reopen/?unit={self.unit.pk}", {"month": month, "reason": ""},
                              format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(refused.status_code, 400)
        for path in ("income-statement", "balance-sheet"):
            self.assertEqual(client.get(f"/api/finance/ledger/{path}/", {"unit": self.unit.pk}).status_code, 200)
        client.force_login(self.roche_user)
        self.assertEqual(client.get("/api/finance/ledger/months/", {"unit": self.unit.pk}).status_code, 403)
