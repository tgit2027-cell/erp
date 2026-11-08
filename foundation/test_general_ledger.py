import datetime
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.db.models import Sum
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

import io
from zipfile import ZipFile

from django.db import transaction

from .general_ledger import account_ledger, export_ledger, journal_entries, journal_entry_detail, trial_balance
from .models import BusinessUnit, IdempotencyRecord, JournalEntry, JournalLine, LedgerAccount, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .test_reporting import ReportingFixtureMixin
from .transaction_services import InvalidTransaction, _post_journal
from .transfer_permissions import set_transfer_permission, transfer_permission_overview

VIEW = UserActionGrant.Action.VIEW_LEDGER


def backdate_entries(entries, day):
    """Move posted entries to an earlier accounting date; the immutability trigger is paused only for this."""
    when = timezone.make_aware(datetime.datetime.combine(day, datetime.time(12)))
    table = JournalEntry._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute(f'ALTER TABLE "{table}" DISABLE TRIGGER USER')
    JournalEntry.objects.filter(pk__in=[entry.pk for entry in entries]).update(posted_at=when, created_at=when, document_date=day)
    with connection.cursor() as cursor:
        cursor.execute(f'ALTER TABLE "{table}" ENABLE TRIGGER USER')
        cursor.execute("SET CONSTRAINTS ALL DEFERRED")


@override_settings(APP_ENV="demo")
class GeneralLedgerTests(ReportingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.create(user=cls.sales, business_unit=cls.unit, action=VIEW)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()

    def unit_lines(self):
        return JournalLine.objects.filter(entry__business_unit=self.unit, entry__status=JournalEntry.Status.POSTED)

    def test_trial_balance_balances_and_matches_the_posted_lines(self):
        self.make_delivered_sale(key="gl-sale")
        report = trial_balance(actor=self.sales, business_unit=self.unit, start=self.today.isoformat())
        self.assertEqual(report["checks"], {"period_balanced": True, "closing_balanced": True, "unbalanced_entries": 0})
        self.assertEqual(report["totals"]["debit"], report["totals"]["credit"])
        self.assertEqual(report["entry_count"], JournalEntry.objects.filter(business_unit=self.unit).count())
        for row in report["rows"]:
            sums = self.unit_lines().filter(account_id=row["account_id"]).aggregate(d=Sum("debit"), c=Sum("credit"))
            self.assertEqual((Decimal(row["debit"]), Decimal(row["credit"])), (sums["d"], sums["c"]), row["code"])
        classes = [row["account_class"] for row in report["rows"]]
        self.assertEqual(classes, sorted(classes, key=["asset", "liability", "revenue", "expense"].index))
        inventory = next(row for row in report["rows"] if row["code"] == "TEST_INVENTORY")
        # 10 units at 1,000 plus 2,000 shipping came in; 2 of the 10 were delivered at their landed cost.
        self.assertEqual(inventory["closing_balance"], "9600.00")
        revenue = next(row for row in report["rows"] if row["code"] == "TEST_REVENUE")
        self.assertEqual((revenue["closing_credit"], revenue["closing_balance"]), ("4000.00", "4000.00"))

    def test_earlier_entries_become_the_opening_balance(self):
        self.make_receipt(key="gl-old-receipt")
        backdate_entries(JournalEntry.objects.filter(business_unit=self.unit), self.today - datetime.timedelta(days=40))
        self.make_receipt(key="gl-new-receipt", quantity="1.00", charges=[])
        start = (self.today - datetime.timedelta(days=5)).isoformat()
        report = trial_balance(actor=self.sales, business_unit=self.unit, start=start)
        inventory = next(row for row in report["rows"] if row["code"] == "TEST_INVENTORY")
        self.assertEqual(
            (inventory["opening_debit"], inventory["debit"], inventory["closing_debit"]),
            ("12000.00", "1000.00", "13000.00"),
        )
        self.assertEqual(report["entry_count"], 1)
        self.assertTrue(report["checks"]["closing_balanced"])

    def test_account_ledger_runs_a_balance_from_the_opening(self):
        self.make_receipt(key="gl-a")
        backdate_entries(JournalEntry.objects.filter(business_unit=self.unit), self.today - datetime.timedelta(days=40))
        self.make_receipt(key="gl-b", quantity="2.00", charges=[])
        self.make_receipt(key="gl-c", quantity="3.00", charges=[])
        account = LedgerAccount.objects.get(legal_entity=self.unit.legal_entity, code="TEST_INVENTORY")
        start = (self.today - datetime.timedelta(days=5)).isoformat()
        ledger = account_ledger(actor=self.sales, business_unit=self.unit, account_id=account.pk, start=start)
        self.assertEqual((ledger["opening_balance"], ledger["closing_balance"]), ("12000.00", "17000.00"))
        self.assertEqual([line["balance"] for line in ledger["lines"]], ["14000.00", "17000.00"])
        self.assertEqual(ledger["account"]["normal_side"], "debit")
        clearing = LedgerAccount.objects.get(legal_entity=self.unit.legal_entity, code="TEST_GRNI")
        credit_side = account_ledger(actor=self.sales, business_unit=self.unit, account_id=clearing.pk, start=start)
        self.assertEqual(credit_side["closing_balance"], "15000.00", "A liability shows its credit balance as positive")
        with self.assertRaisesMessage(InvalidTransaction, "Choose an account of this company"):
            account_ledger(actor=self.sales, business_unit=self.unit, account_id=999999)

    def test_journal_entries_filter_search_and_open_one(self):
        self.make_delivered_sale(key="gl-list")
        listed = journal_entries(actor=self.sales, business_unit=self.unit)
        self.assertEqual(listed["pagination"]["total_count"], JournalEntry.objects.filter(business_unit=self.unit).count())
        receipts = journal_entries(actor=self.sales, business_unit=self.unit, kind="receipt")
        self.assertTrue(receipts["entries"])
        self.assertEqual({entry["kind"] for entry in receipts["entries"]}, {"receipt"})
        first = receipts["entries"][0]
        found = journal_entries(actor=self.sales, business_unit=self.unit, search=str(first["id"]))
        self.assertIn(first["id"], [entry["id"] for entry in found["entries"]])
        detail = journal_entry_detail(actor=self.sales, business_unit=self.unit, entry_id=first["id"])
        self.assertTrue(detail["balanced"])
        self.assertEqual(detail["debit"], first["total"])
        with self.assertRaisesMessage(InvalidTransaction, "known entry type"):
            journal_entries(actor=self.sales, business_unit=self.unit, kind="bogus")
        UserActionGrant.objects.create(user=self.sales, business_unit=self.roche, action=VIEW)
        with self.assertRaisesMessage(InvalidTransaction, "not in the selected business unit"):
            journal_entry_detail(actor=self.sales, business_unit=self.roche, entry_id=first["id"])

    def test_ledger_needs_the_view_ledger_permission_in_that_unit(self):
        with self.assertRaisesMessage(WorkflowPermissionDenied, "permission to view the general ledger"):
            trial_balance(actor=self.roche_user, business_unit=self.unit)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "permission to view the general ledger"):
            trial_balance(actor=self.sales, business_unit=self.roche)
        client = APIClient()
        client.force_authenticate(user=self.sales)
        response = client.get("/api/finance/ledger/trial-balance/", {"unit": self.unit.pk})
        self.assertEqual(response.status_code, 200, response.data)
        UserActionGrant.objects.filter(user=self.sales, action=VIEW).update(revoked_at=timezone.now())
        self.assertEqual(client.get("/api/finance/ledger/trial-balance/", {"unit": self.unit.pk}).status_code, 403)
        self.assertEqual(client.get("/api/finance/ledger/entries/", {"unit": self.unit.pk}).status_code, 403)

    def test_bad_dates_are_rejected(self):
        with self.assertRaisesMessage(InvalidTransaction, "YYYY-MM-DD"):
            trial_balance(actor=self.sales, business_unit=self.unit, start="01/10/2026")
        with self.assertRaisesMessage(InvalidTransaction, "on or before"):
            trial_balance(actor=self.sales, business_unit=self.unit, start="2026-10-10", end="2026-10-01")

    def test_permission_screen_offers_view_ledger_per_unit(self):
        from django.contrib.auth import get_user_model

        sysadmin = get_user_model().objects.create_superuser("gl_sysadmin", password="unused-test-password")
        from .models import DemoUserMarker

        DemoUserMarker.objects.create(user=sysadmin)
        overview = transfer_permission_overview(actor=sysadmin, business_unit=self.unit)
        action = next(item for item in overview["actions"] if item["action"] == VIEW)
        self.assertEqual(action["scope"], "unit")
        set_transfer_permission(actor=sysadmin, business_unit=self.roche, user_id=self.sales.pk, action=VIEW, enabled=True)
        trial_balance(actor=self.sales, business_unit=self.roche)
        overview = transfer_permission_overview(actor=sysadmin, business_unit=self.roche)
        row = next(user for user in overview["users"] if user["id"] == self.sales.pk)
        self.assertIn(VIEW, row["actions"])

    def post_in(self, unit, key, amount, day=None):
        with transaction.atomic():
            operation = IdempotencyRecord.objects.create(
                key=key, operation="test_general_ledger", payload_hash="0" * 64,
                status=IdempotencyRecord.Status.COMPLETE, completed_at=timezone.now(),
            )
            return _post_journal(
                legal_entity=unit.legal_entity, business_unit=unit, idempotency=operation,
                kind=JournalEntry.Kind.RECEIPT, description=f"TEST ONLY {key}", document_date=day,
                lines=[("TEST_INVENTORY", amount, "0.00", key), ("TEST_GRNI", "0.00", amount, key)],
            )

    def test_the_document_date_not_the_posting_time_decides_the_period(self):
        last_month = self.today.replace(day=1) - datetime.timedelta(days=1)
        entry = self.post_in(self.unit, "gl-late", "500.00", day=last_month)
        self.assertEqual(timezone.localtime(entry.posted_at).date(), self.today)
        current = trial_balance(actor=self.sales, business_unit=self.unit)
        inventory = next(row for row in current["rows"] if row["code"] == "TEST_INVENTORY")
        self.assertEqual((inventory["opening_debit"], inventory["debit"]), ("500.00", "0.00"))
        earlier = journal_entries(actor=self.sales, business_unit=self.unit, start=last_month.isoformat(),
                                  end=last_month.isoformat())
        row = earlier["entries"][0]
        self.assertEqual((row["id"], row["document_date"], row["posted_on"]), (entry.pk, last_month.isoformat(), self.today.isoformat()))
        self.assertEqual((row["legal_entity_code"], row["unit_code"]), (self.unit.legal_entity.code, self.unit.code))

    def test_company_view_needs_every_unit_and_adds_them_up(self):
        units = BusinessUnit.objects.filter(legal_entity=self.unit.legal_entity, is_active=True)
        self.assertGreater(units.count(), 1)
        self.post_in(self.unit, "gl-tvl", "300.00")
        self.post_in(self.roche, "gl-rbb", "200.00")
        self.assertFalse(trial_balance(actor=self.sales, business_unit=self.unit)["can_view_company"])
        with self.assertRaisesMessage(WorkflowPermissionDenied, "every business unit of the company"):
            trial_balance(actor=self.sales, business_unit=self.unit, scope="company")
        for unit in units.exclude(pk=self.unit.pk):
            UserActionGrant.objects.create(user=self.sales, business_unit=unit, action=VIEW)
        company = trial_balance(actor=self.sales, business_unit=self.unit, scope="company")
        inventory = next(row for row in company["rows"] if row["code"] == "TEST_INVENTORY")
        self.assertEqual(inventory["debit"], "500.00")
        self.assertEqual((company["scope"], company["legal_entity"]["code"]), ("company", self.unit.legal_entity.code))
        listed = journal_entries(actor=self.sales, business_unit=self.unit, scope="company")
        self.assertEqual({row["unit_code"] for row in listed["entries"]}, {"TVL", "RBB"})
        other = next(row for row in listed["entries"] if row["unit_code"] == "RBB")
        self.assertEqual(journal_entry_detail(actor=self.sales, business_unit=self.unit, entry_id=other["id"], scope="company")["unit_code"], "RBB")
        with self.assertRaisesMessage(InvalidTransaction, "not in the selected business unit"):
            journal_entry_detail(actor=self.sales, business_unit=self.unit, entry_id=other["id"])
        with self.assertRaisesMessage(InvalidTransaction, "business unit or the whole company"):
            trial_balance(actor=self.sales, business_unit=self.unit, scope="group")

    def test_every_view_exports_to_excel(self):
        self.post_in(self.unit, "gl-xlsx", "750.00")
        account = LedgerAccount.objects.get(legal_entity=self.unit.legal_entity, code="TEST_INVENTORY")
        for view, expected in (("trial-balance", "Closing debit"), ("entries", "Document date"), ("account", "Balance")):
            name, content = export_ledger(actor=self.sales, business_unit=self.unit, view=view, account_id=account.pk)
            self.assertTrue(name.startswith(f"ledger-{view}-TVL-") and name.endswith(".xlsx"), name)
            sheet = ZipFile(io.BytesIO(content)).read("xl/worksheets/sheet1.xml").decode()
            self.assertIn(expected, sheet)
            self.assertIn('t="n"><v>750.00</v>', sheet, view)
        client = APIClient()
        client.force_authenticate(user=self.sales)
        response = client.get("/api/finance/ledger/export/entries/", {"unit": self.unit.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertEqual(client.get("/api/finance/ledger/export/everything/", {"unit": self.unit.pk}).status_code, 400)
        outsider = APIClient()
        outsider.force_authenticate(user=self.roche_user)
        self.assertEqual(outsider.get("/api/finance/ledger/export/entries/", {"unit": self.roche.pk}).status_code, 403)
