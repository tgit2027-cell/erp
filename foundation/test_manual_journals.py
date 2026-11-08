"""Manual journal entries: prepared, approved by a second person, posted, undone; synthetic only."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.db.models import Sum
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .manual_journals import decide_journal, journal_detail, journals_overview, reverse_journal, save_journal
from .models import ClosedMonth, JournalEntry, JournalLine, ManualJournal, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action


@override_settings(APP_ENV="demo")
class ManualJournalTests(PurchasingFixtureMixin, TestCase):
    """Admin prepares journals; sales approves them."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for user, action in [(cls.admin, A.PREPARE_JOURNAL), (cls.sales, A.APPROVE_JOURNAL), (cls.sales, A.PREPARE_JOURNAL)]:
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()

    def accrual(self, actor=None, submit=True, **data):
        payload = {"date": self.today.isoformat(), "description": "TEST ONLY showroom electricity not yet billed",
                   "lines": [{"account_code": "TEST_EXP_UTILITIES", "debit": "1200.00", "memo": "estimate"},
                             {"account_code": "TEST_ACCRUALS", "credit": "1200.00"},
                             {"account_code": "", "debit": "", "credit": ""}],
                   "submit": submit, **data}
        return save_journal(actor=actor or self.admin, business_unit=self.unit, data=payload)

    def balance(self, code, until=None):
        lines = JournalLine.objects.filter(entry__business_unit=self.unit, account__code=code,
                                           entry__status=JournalEntry.Status.POSTED)
        if until:
            lines = lines.filter(entry__document_date__lte=until)
        sums = lines.aggregate(debit=Sum("debit"), credit=Sum("credit"))
        return (sums["debit"] or Decimal("0")) - (sums["credit"] or Decimal("0"))

    def test_a_journal_is_posted_only_after_someone_else_approves_it(self):
        journal = self.accrual(submit=False)
        self.assertRegex(journal["number"], rf"^MJ-{self.unit.code}-{self.today.year}-00001$")
        self.assertEqual((journal["status"], journal["total"], journal["balanced"]), ("draft", "1200.00", True))
        self.assertEqual(len(journal["lines"]), 2, "Empty rows are dropped")
        journal = decide_journal(actor=self.admin, business_unit=self.unit, journal_id=journal["id"], action="submit")
        self.assertEqual(journal["status"], "submitted")
        self.assertEqual(self.balance("TEST_ACCRUALS"), 0, "Nothing is booked before approval")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "'approve journal entries' permission"):
            decide_journal(actor=self.admin, business_unit=self.unit, journal_id=journal["id"], action="approve")
        UserActionGrant.objects.create(user=self.admin, business_unit=self.unit, action=A.APPROVE_JOURNAL)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Someone other than the person who prepared"):
            decide_journal(actor=self.admin, business_unit=self.unit, journal_id=journal["id"], action="approve")
        journal = decide_journal(actor=self.sales, business_unit=self.unit, journal_id=journal["id"], action="approve")
        self.assertEqual(journal["status"], "posted")
        self.assertEqual(self.balance("TEST_ACCRUALS"), Decimal("-1200.00"))
        self.assertEqual(self.balance("TEST_EXP_UTILITIES"), Decimal("1200.00"))
        entry = JournalEntry.objects.get(pk=journal["journal_entry_id"])
        self.assertEqual((entry.kind, entry.document_date), (JournalEntry.Kind.MANUAL, self.today))
        self.assertEqual([row["change"] for row in journal["history"]], ["approved and posted", "sent for approval", "prepared"])
        with self.assertRaises(InvalidTransaction):
            decide_journal(actor=self.sales, business_unit=self.unit, journal_id=journal["id"], action="approve")

    def test_lines_must_balance_and_avoid_accounts_kept_by_their_own_screens(self):
        with self.assertRaisesMessage(InvalidTransaction, "does not balance"):
            self.accrual(lines=[{"account_code": "TEST_EXP_RENT", "debit": "100.00"},
                                {"account_code": "TEST_ACCRUALS", "credit": "90.00"}])
        with self.assertRaisesMessage(InvalidTransaction, "Bank and cash (changed under Bank and cash)"):
            self.accrual(lines=[{"account_code": "TEST_EXP_RENT", "debit": "100.00"},
                                {"account_code": "TEST_CASH", "credit": "100.00"}])
        with self.assertRaisesMessage(InvalidTransaction, "either debit or credit, not both"):
            self.accrual(lines=[{"account_code": "TEST_EXP_RENT", "debit": "100.00", "credit": "5.00"},
                                {"account_code": "TEST_ACCRUALS", "credit": "95.00"}])
        with self.assertRaisesMessage(InvalidTransaction, "at least two lines"):
            self.accrual(lines=[{"account_code": "TEST_EXP_RENT", "debit": "100.00"}])
        with self.assertRaisesMessage(InvalidTransaction, "cannot be in the future"):
            self.accrual(date=(self.today + datetime.timedelta(days=1)).isoformat())
        ClosedMonth.objects.create(legal_entity=self.unit.legal_entity, business_unit=self.unit, month=self.today.replace(day=1),
                                   closed_by=self.sales, entry_count=0, total_debit=0)
        with self.assertRaisesMessage(InvalidTransaction, "is closed"):
            self.accrual()
        self.assertFalse(ManualJournal.objects.exists())
        codes = {row["code"] for row in journals_overview(actor=self.admin, business_unit=self.unit)["accounts"]}
        self.assertIn("TEST_PREPAYMENTS", codes)
        self.assertNotIn("TEST_INVENTORY", codes)

    def test_an_accrual_can_undo_itself_next_month_and_a_posted_journal_is_reversed_by_a_new_one(self):
        first = self.today.replace(day=1)
        last_month_end = first - datetime.timedelta(days=1)
        journal = self.accrual(date=last_month_end.isoformat(), reverse_next_month=True)
        journal = decide_journal(actor=self.sales, business_unit=self.unit, journal_id=journal["id"], action="approve")
        undo = JournalEntry.objects.get(pk=journal["auto_reversal_entry_id"])
        self.assertEqual(undo.document_date, first)
        self.assertEqual(self.balance("TEST_ACCRUALS", until=last_month_end), Decimal("-1200.00"))
        self.assertEqual(self.balance("TEST_ACCRUALS"), 0, "Undone on the first of the next month")
        self.assertFalse(journal_detail(actor=self.admin, business_unit=self.unit, journal_id=journal["id"])["can_reverse"])

        prepaid = self.accrual(description="TEST ONLY yearly insurance paid ahead", lines=[
            {"account_code": "TEST_PREPAYMENTS", "debit": "6000.00"},
            {"account_code": "TEST_EXP_OTHER", "credit": "6000.00"}])
        prepaid = decide_journal(actor=self.sales, business_unit=self.unit, journal_id=prepaid["id"], action="approve")
        self.assertTrue(journal_detail(actor=self.admin, business_unit=self.unit, journal_id=prepaid["id"])["can_reverse"])
        reversal = reverse_journal(actor=self.admin, business_unit=self.unit, journal_id=prepaid["id"])
        self.assertEqual((reversal["status"], reversal["reversal_of"]), ("draft", prepaid["number"]))
        self.assertEqual([(row["account_code"], row["debit"], row["credit"]) for row in reversal["lines"]],
                         [("TEST_PREPAYMENTS", "0.00", "6000.00"), ("TEST_EXP_OTHER", "6000.00", "0.00")])
        with self.assertRaisesMessage(InvalidTransaction, "already reversed"):
            reverse_journal(actor=self.admin, business_unit=self.unit, journal_id=prepaid["id"])
        decide_journal(actor=self.admin, business_unit=self.unit, journal_id=reversal["id"], action="submit")
        decide_journal(actor=self.sales, business_unit=self.unit, journal_id=reversal["id"], action="approve")
        self.assertEqual(self.balance("TEST_PREPAYMENTS"), 0)
        self.assertEqual(journal_detail(actor=self.admin, business_unit=self.unit, journal_id=prepaid["id"])["reversed_by"],
                         reversal["number"])

    def test_a_journal_in_a_closed_month_is_reversed_in_the_current_open_month(self):
        last_month_end = self.today.replace(day=1) - datetime.timedelta(days=1)
        journal = self.accrual(date=last_month_end.isoformat())
        journal = decide_journal(actor=self.sales, business_unit=self.unit, journal_id=journal["id"], action="approve")
        ClosedMonth.objects.create(legal_entity=self.unit.legal_entity, business_unit=self.unit, month=last_month_end.replace(day=1),
                                   closed_by=self.sales, entry_count=1, total_debit=Decimal("1200.00"))
        self.assertTrue(journal_detail(actor=self.admin, business_unit=self.unit, journal_id=journal["id"])["can_reverse"])
        with self.assertRaisesMessage(InvalidTransaction, "is closed"):
            reverse_journal(actor=self.admin, business_unit=self.unit, journal_id=journal["id"], date=last_month_end.isoformat())
        reversal = reverse_journal(actor=self.admin, business_unit=self.unit, journal_id=journal["id"])
        self.assertEqual((reversal["status"], reversal["date"]), ("draft", self.today.isoformat()))
        decide_journal(actor=self.admin, business_unit=self.unit, journal_id=reversal["id"], action="submit")
        decide_journal(actor=self.sales, business_unit=self.unit, journal_id=reversal["id"], action="approve")
        self.assertEqual(self.balance("TEST_ACCRUALS", until=last_month_end), Decimal("-1200.00"), "The closed month is untouched")
        self.assertEqual(self.balance("TEST_ACCRUALS"), 0)

    def test_an_approver_can_send_a_journal_back_with_a_reason(self):
        journal = self.accrual()
        with self.assertRaisesMessage(InvalidTransaction, "Give the reason"):
            decide_journal(actor=self.sales, business_unit=self.unit, journal_id=journal["id"], action="return")
        journal = decide_journal(actor=self.sales, business_unit=self.unit, journal_id=journal["id"], action="return",
                                 reason="TEST ONLY use the March bill amount")
        self.assertEqual((journal["status"], journal["returned_reason"]), ("draft", "TEST ONLY use the March bill amount"))
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only the person who prepared"):
            save_journal(actor=self.sales, business_unit=self.unit, journal_id=journal["id"],
                         data={"date": self.today.isoformat(), "description": "x", "lines": []})
        journal = save_journal(actor=self.admin, business_unit=self.unit, journal_id=journal["id"], data={
            "date": self.today.isoformat(), "description": "TEST ONLY electricity, March bill", "submit": True,
            "lines": [{"account_code": "TEST_EXP_UTILITIES", "debit": "1350.00"},
                      {"account_code": "TEST_ACCRUALS", "credit": "1350.00"}]})
        self.assertEqual((journal["status"], journal["total"], journal["returned_reason"]), ("submitted", "1350.00", ""))
        overview = journals_overview(actor=self.sales, business_unit=self.unit)
        self.assertEqual((overview["waiting"], overview["waiting_for_me"]), (1, 1))
        self.assertTrue(overview["journals"][0]["can_decide"])

    def test_only_permitted_people_see_journals_and_the_api_works(self):
        with self.assertRaises(WorkflowPermissionDenied):
            journals_overview(actor=self.roche_user, business_unit=self.unit)
        client = APIClient()
        client.force_login(self.admin)
        response = client.post(f"/api/finance/journals/?unit={self.unit.pk}", {
            "date": self.today.isoformat(), "description": "TEST ONLY correction", "submit": True,
            "lines": [{"account_code": "TEST_EXP_TRAVEL", "debit": "75.50"},
                      {"account_code": "TEST_EXP_OFFICE", "credit": "75.50"}]}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        client.force_login(self.sales)
        journal_id = response.json()["id"]
        response = client.post(f"/api/finance/journals/{journal_id}/?unit={self.unit.pk}", {"action": "approve"}, format="json")
        self.assertEqual(response.json()["status"], "posted")
        listed = client.get(f"/api/finance/journals/?unit={self.unit.pk}&status=posted").json()["journals"]
        self.assertEqual([row["id"] for row in listed], [journal_id])
