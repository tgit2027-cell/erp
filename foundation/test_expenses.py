"""Expenses and petty cash: entered, approved by a second person, booked, repaid to staff; synthetic only."""

import datetime
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .cash_accounts import cash_overview, create_account
from .master_admin_testing import by_master_admin
from .expenses import approve_expense, expenses_overview, reimburse_expense, reject_expense, submit_expense
from .financial_statements import balance_sheet, income_statement
from .models import Expense, JournalEntry, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

MEDIA = tempfile.mkdtemp(prefix="expense-receipts-test-")
PDF = b"%PDF-1.4\n% TEST ONLY receipt\n"


@override_settings(APP_ENV="demo", MEDIA_ROOT=MEDIA)
class ExpenseTests(PurchasingFixtureMixin, TestCase):
    """Admin enters expenses and runs the cash; sales approves."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for user, action in [
            (cls.admin, UserActionGrant.Action.RECORD_EXPENSE), (cls.admin, UserActionGrant.Action.MANAGE_CASH),
            (cls.admin, UserActionGrant.Action.VIEW_LEDGER), (cls.sales, UserActionGrant.Action.APPROVE_EXPENSE),
            (cls.sales, UserActionGrant.Action.RECORD_EXPENSE),
        ]:
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()
        self.first = self.today.replace(day=1)
        self.opened = (self.first - datetime.timedelta(days=40)).isoformat()
        self.till = by_master_admin(create_account)(actor=self.admin, business_unit=self.unit, name="TEST ONLY Showroom till", kind="cash",
                                   opening_balance="2000.00", opening_date=self.opened)
        self.bank = by_master_admin(create_account)(actor=self.admin, business_unit=self.unit, name="TEST ONLY QNB current", kind="bank",
                                   opening_balance="50000.00", opening_date=self.opened)

    def enter(self, actor=None, **data):
        payload = {"date": self.today.isoformat(), "category": "utilities", "amount": "350.00",
                   "description": "TEST ONLY showroom electricity", "payee": "TEST ONLY Kahramaa",
                   "paid_by": "account", "account_id": self.till["id"], **data}
        return submit_expense(actor=actor or self.admin, business_unit=self.unit, data=payload)

    def balance(self, account):
        overview = cash_overview(actor=self.admin, business_unit=self.unit, account_id=account["id"])
        return next(row for row in overview["accounts"] if row["id"] == account["id"])["balance"]

    def test_petty_cash_expense_is_booked_only_after_someone_else_approves(self):
        expense = self.enter()
        self.assertRegex(expense["number"], rf"^EXP-{self.unit.code}-{self.today.year}-00001$")
        self.assertEqual(expense["status"], "submitted")
        self.assertIsNone(expense["journal_entry_id"])
        self.assertEqual(self.balance(self.till), "2000.00", "Nothing is booked before approval")

        with self.assertRaisesMessage(WorkflowPermissionDenied, "'approve expenses' permission"):
            approve_expense(actor=self.admin, business_unit=self.unit, expense_id=expense["id"])
        UserActionGrant.objects.create(user=self.admin, business_unit=self.unit, action=UserActionGrant.Action.APPROVE_EXPENSE)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Someone other than the person who entered"):
            approve_expense(actor=self.admin, business_unit=self.unit, expense_id=expense["id"])
        self.assertFalse(expenses_overview(actor=self.admin, business_unit=self.unit)["rows"][0]["can_decide"])

        approved = approve_expense(actor=self.sales, business_unit=self.unit, expense_id=expense["id"])
        self.assertEqual((approved["status"], approved["decided_by"]), ("approved", self.sales.username))
        entry = JournalEntry.objects.get(pk=approved["journal_entry_id"])
        self.assertEqual(entry.kind, JournalEntry.Kind.EXPENSE)
        self.assertEqual(sorted((line.account.code, line.debit, line.credit) for line in entry.lines.all()), [
            ("TEST_CASH", Decimal("0.00"), Decimal("350.00")), ("TEST_EXP_UTILITIES", Decimal("350.00"), Decimal("0.00")),
        ])
        self.assertEqual(self.balance(self.till), "1650.00", "The till goes down by the expense")
        with self.assertRaisesMessage(InvalidTransaction, "already approved"):
            approve_expense(actor=self.sales, business_unit=self.unit, expense_id=expense["id"])

        overview = expenses_overview(actor=self.admin, business_unit=self.unit)
        self.assertEqual(overview["totals"]["booked"], "350.00")
        self.assertEqual(overview["by_category"], [{"category": "utilities", "label": "Electricity and water", "total": "350.00", "count": 1}])

        statement = income_statement(actor=self.admin, business_unit=self.unit, start=self.first.isoformat(), end=self.today.isoformat())
        running = next(section for section in statement["sections"] if section["key"] == "other_expenses")
        self.assertIn(("TEST_EXP_UTILITIES", "350.00"), [(row["code"], row["period"]) for row in running["rows"]])

    def test_staff_paid_expense_is_owed_to_them_until_repaid(self):
        expense = self.enter(paid_by="staff", claimant_id=self.admin.pk, account_id=None, category="travel",
                             amount="120.00", description="TEST ONLY taxi to client site")
        self.assertEqual(expense["claimant"]["id"], self.admin.pk)
        approve_expense(actor=self.sales, business_unit=self.unit, expense_id=expense["id"])
        overview = expenses_overview(actor=self.admin, business_unit=self.unit)
        self.assertEqual((overview["totals"]["owed_to_staff"], overview["totals"]["owed_count"]), ("120.00", 1))
        sheet = balance_sheet(actor=self.admin, business_unit=self.unit)
        liabilities = next(section for section in sheet["sections"] if section["key"] == "liabilities")
        self.assertIn(("TEST_EXPENSE_CLAIMS", "120.00"), [(row["code"], row["balance"]) for row in liabilities["rows"]])

        with self.assertRaisesMessage(WorkflowPermissionDenied, "Someone else must repay"):
            reimburse_expense(actor=self.admin, business_unit=self.unit, expense_id=expense["id"], account_id=self.till["id"])

    def test_someone_cannot_approve_what_they_are_owed_and_repayment_needs_cash_permission(self):
        expense = self.enter(actor=self.admin, paid_by="staff", claimant_id=self.sales.pk, account_id=None, amount="80.00")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "cannot approve an expense you are owed for"):
            approve_expense(actor=self.sales, business_unit=self.unit, expense_id=expense["id"])

        other = self.enter(actor=self.sales, paid_by="staff", claimant_id=self.admin.pk, account_id=None, amount="60.00")
        UserActionGrant.objects.create(user=self.admin, business_unit=self.unit, action=UserActionGrant.Action.APPROVE_EXPENSE)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "cannot approve an expense you are owed for"):
            approve_expense(actor=self.admin, business_unit=self.unit, expense_id=other["id"])

        third = self.enter(actor=self.sales, paid_by="staff", claimant_id=self.sales.pk, account_id=None, amount="45.00")
        approve_expense(actor=self.admin, business_unit=self.unit, expense_id=third["id"])
        with self.assertRaisesMessage(WorkflowPermissionDenied, "record bank and cash"):
            reimburse_expense(actor=self.sales, business_unit=self.unit, expense_id=third["id"], account_id=self.till["id"])
        repaid = reimburse_expense(actor=self.admin, business_unit=self.unit, expense_id=third["id"], account_id=self.till["id"])
        self.assertEqual((repaid["status"], repaid["reimbursed_from"]), ("reimbursed", "TEST ONLY Showroom till"))
        self.assertEqual(self.balance(self.till), "1955.00")
        self.assertEqual(expenses_overview(actor=self.admin, business_unit=self.unit)["totals"]["owed_to_staff"], "0.00")
        with self.assertRaisesMessage(InvalidTransaction, "Only approved expenses paid by staff"):
            reimburse_expense(actor=self.admin, business_unit=self.unit, expense_id=third["id"], account_id=self.till["id"])

    def test_rejected_expense_keeps_its_reason_and_books_nothing(self):
        expense = self.enter()
        with self.assertRaises(InvalidTransaction):
            reject_expense(actor=self.sales, business_unit=self.unit, expense_id=expense["id"], reason="  ")
        rejected = reject_expense(actor=self.sales, business_unit=self.unit, expense_id=expense["id"], reason="TEST ONLY no receipt")
        self.assertEqual((rejected["status"], rejected["rejection_reason"]), ("rejected", "TEST ONLY no receipt"))
        self.assertIsNone(rejected["journal_entry_id"])
        self.assertEqual(self.balance(self.till), "2000.00")
        with self.assertRaisesMessage(InvalidTransaction, "already rejected"):
            approve_expense(actor=self.sales, business_unit=self.unit, expense_id=expense["id"])

    def test_bad_entries_are_refused(self):
        with self.assertRaisesMessage(InvalidTransaction, "more than zero"):
            self.enter(amount="0")
        with self.assertRaisesMessage(InvalidTransaction, "Choose a category"):
            self.enter(category="yachts")
        with self.assertRaises(InvalidTransaction):
            self.enter(date=(self.today + datetime.timedelta(days=2)).isoformat())
        with self.assertRaisesMessage(InvalidTransaction, "paid from a bank account or cash box, or by a member of staff"):
            self.enter(paid_by="")
        with self.assertRaisesMessage(InvalidTransaction, "Choose who paid it"):
            self.enter(paid_by="staff", claimant_id=self.roche_user.pk, account_id=None)
        with self.assertRaisesMessage(InvalidTransaction, "after this date"):
            self.enter(date=(self.first - datetime.timedelta(days=60)).isoformat())
        with self.assertRaisesMessage(WorkflowPermissionDenied, "permission"):
            self.enter(actor=self.roche_user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Expense.objects.create(business_unit=self.unit, number="EXP-X", expense_date=self.today, category="other",
                                   description="TEST ONLY", amount=Decimal("1.00"), submitted_by=self.admin)

    def test_receipts_upload_through_the_api_and_are_checked(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        url = f"/api/finance/expenses/?unit={self.unit.pk}"
        base = {"date": self.today.isoformat(), "category": "repairs", "amount": "900.00", "description": "TEST ONLY AC repair",
                "paid_by": "account", "account_id": self.bank["id"]}
        fake = client.post(url, {**base, "receipt": SimpleUploadedFile("receipt.pdf", b"not a pdf")}, format="multipart")
        self.assertEqual(fake.status_code, 400)
        self.assertIn("not the file type", str(fake.data))
        created = client.post(url, {**base, "receipt": SimpleUploadedFile("receipt.pdf", PDF)}, format="multipart")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertTrue(created.data["has_receipt"])
        download = client.get(f"{created.data['receipt_url']}?unit={self.unit.pk}")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b"".join(download.streaming_content), PDF)
        self.assertEqual(download["Content-Type"], "application/pdf")

        approver = APIClient()
        approver.force_authenticate(self.sales)
        decided = approver.post(f"/api/finance/expenses/{created.data['id']}/?unit={self.unit.pk}", {"decision": "approve"}, format="json")
        self.assertEqual(decided.status_code, 200, decided.data)
        self.assertEqual(self.balance(self.bank), "49100.00")
        listing = client.get(f"{url}&status=approved")
        self.assertEqual([row["number"] for row in listing.data["rows"]], [created.data["number"]])

        outsider = APIClient()
        outsider.force_authenticate(self.roche_user)
        self.assertEqual(outsider.get(url).status_code, 403)
        self.assertEqual(outsider.get(f"{created.data['receipt_url']}?unit={self.unit.pk}").status_code, 403)
