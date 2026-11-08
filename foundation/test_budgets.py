"""Budgets: prepared by one person, approved by another, compared with posted actual figures; synthetic only."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .budgets import LINE_KEYS, actuals, budget_view, decide_budget, save_budget
from .master_admin_testing import by_master_admin
from .cash_accounts import create_account
from .expenses import approve_expense, submit_expense
from .models import Budget, BudgetAmount, IdempotencyRecord, JournalEntry, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction, _post_journal


def months(each):
    return [each] * 12


@override_settings(APP_ENV="demo")
class BudgetTests(PurchasingFixtureMixin, TestCase):
    """Admin prepares budgets and enters expenses; sales approves both."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        A = UserActionGrant.Action
        for user, action in [(cls.admin, A.PREPARE_BUDGET), (cls.admin, A.RECORD_EXPENSE), (cls.admin, A.MANAGE_CASH),
                             (cls.sales, A.APPROVE_BUDGET), (cls.sales, A.APPROVE_EXPENSE)]:
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()
        self.year = self.today.year

    def save(self, amounts=None, actor=None, year=None):
        amounts = amounts or {"sales": months("50000"), "cost_of_sales": months("30000"), "rent": months("18000"),
                              "utilities": months("1000.00")}
        return save_budget(actor=actor or self.admin, business_unit=self.unit, year=year or self.year, amounts=amounts)

    def decide(self, action, actor=None, reason=""):
        return decide_budget(actor=actor or self.sales, business_unit=self.unit, year=self.year, action=action, reason=reason)

    def spend(self, category, amount):
        if not hasattr(self, "till"):
            self.till = by_master_admin(create_account)(actor=self.admin, business_unit=self.unit, name="TEST ONLY cash", kind="cash",
                                       opening_balance="0.00", opening_date=self.today.replace(day=1).isoformat())
        till = self.till
        expense = submit_expense(actor=self.admin, business_unit=self.unit, data={
            "date": self.today.isoformat(), "category": category, "amount": amount, "description": "TEST ONLY",
            "paid_by": "account", "account_id": till["id"]})
        approve_expense(actor=self.sales, business_unit=self.unit, expense_id=expense["id"])

    def post(self, key, day, lines):
        with transaction.atomic():
            operation = IdempotencyRecord.objects.create(
                key=key, operation="test_budgets", payload_hash="0" * 64,
                status=IdempotencyRecord.Status.COMPLETE, completed_at=timezone.now())
            _post_journal(legal_entity=self.unit.legal_entity, business_unit=self.unit, idempotency=operation,
                          kind=JournalEntry.Kind.RECEIPT, description=f"TEST ONLY {key}", document_date=day, lines=lines)

    def line(self, view, key):
        return next(line for line in view["lines"] if line["line"] == key)

    def test_a_budget_is_prepared_sent_approved_by_someone_else_and_locked(self):
        view = self.save()
        self.assertEqual(view["budget"]["status"], "draft")
        self.assertEqual(self.line(view, "rent")["year_budget"], "216000.00")
        self.assertEqual(view["net_profit"]["year_budget"], f"{(50000 - 30000 - 18000 - 1000) * 12}.00")
        self.assertTrue(view["can_edit"])

        submitted = self.decide("submit", actor=self.admin)
        self.assertEqual(submitted["budget"]["status"], "submitted")
        with self.assertRaisesMessage(InvalidTransaction, "cannot be changed now"):
            self.save()
        UserActionGrant.objects.create(user=self.admin, business_unit=self.unit, action=UserActionGrant.Action.APPROVE_BUDGET)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Someone other than the person who prepared"):
            self.decide("approve", actor=self.admin)
        self.assertFalse(budget_view(actor=self.admin, business_unit=self.unit, year=self.year)["can_decide"])

        approved = self.decide("approve")
        self.assertEqual((approved["budget"]["status"], approved["budget"]["approved_by"]), ("approved", self.sales.get_full_name()))
        with self.assertRaisesMessage(InvalidTransaction, "cannot be changed now"):
            self.save()
        with self.assertRaisesMessage(InvalidTransaction, "reason"):
            self.decide("reopen")
        reopened = self.decide("reopen", reason="TEST ONLY second showroom opens early")
        self.assertEqual(reopened["budget"]["status"], "draft")
        self.assertEqual([event["what"] for event in reopened["history"]],
                         ["reopened it", "approved it", "sent it for approval", "saved changes"])
        self.assertEqual(reopened["history"][0]["note"], "TEST ONLY second showroom opens early")

    def test_sending_back_needs_a_reason_and_returns_it_to_the_preparer(self):
        self.save()
        self.decide("submit", actor=self.admin)
        with self.assertRaisesMessage(InvalidTransaction, "reason"):
            self.decide("return")
        returned = self.decide("return", reason="TEST ONLY rent rises in July")
        self.assertEqual(returned["budget"]["status"], "draft")
        self.save({"rent": months("18000")[:6] + months("19500")[:6]})
        self.assertEqual(Budget.objects.get().amounts.count(), 12, "Saving replaces every amount")

    def test_actual_figures_come_from_the_ledger_and_overspending_is_flagged(self):
        self.save({"rent": months("18000"), "utilities": months("1000")})
        self.spend("utilities", "1250.00")
        self.spend("rent", "18000.00")
        self.spend("other", "40.00")
        view = budget_view(actor=self.admin, business_unit=self.unit, year=self.year, month=self.today.month)
        utilities = self.line(view, "utilities")["month"]
        self.assertEqual((utilities["budget"], utilities["actual"], utilities["difference"], utilities["flag"], utilities["used_percent"]),
                         ("1000.00", "1250.00", "250.00", "over", 125))
        self.assertEqual(self.line(view, "rent")["month"]["flag"], "")
        self.assertEqual(self.line(view, "other")["month"]["actual"], "40.00")
        self.assertEqual(self.line(view, "rent")["to_date"]["budget"], f"{18000 * self.today.month}.00")
        self.assertEqual(actuals(self.unit, self.year)["utilities"][self.today.month - 1], Decimal("1250.00"))
        last_year = budget_view(actor=self.admin, business_unit=self.unit, year=self.year + 1)
        self.assertEqual(self.line(last_year, "utilities")["last_year"][self.today.month - 1], "1250.00",
                         "Next year's budget can start from this year's actual figures")

    def test_provisions_bank_interest_other_income_and_previous_years(self):
        self.save({"bank_interest": months("100"), "provisions": months("500"), "other_income": months("0")})
        month = self.today.month
        for back, interest in ((0, "130.00"), (1, "100.00"), (2, "80.00")):
            day = self.today.replace(year=self.year - back, day=1)
            self.post(f"interest-{back}", day, [("TEST_CASH", interest, "0.00", "interest"), ("TEST_INTEREST_INCOME", "0.00", interest, "interest")])
        self.post("provision", self.today.replace(day=1), [("TEST_EXP_PROVISIONS", "600.00", "0.00", "end of service"),
                                                            ("TEST_CASH", "0.00", "600.00", "end of service")])
        self.post("fx", self.today.replace(day=1), [("TEST_CASH", "25.00", "0.00", "fx"), ("TEST_FX_GAIN", "0.00", "25.00", "fx")])
        later = self.today.replace(year=self.year - 1, month=12, day=31)
        self.post("interest-december", later, [("TEST_CASH", "40.00", "0.00", "interest"), ("TEST_INTEREST_INCOME", "0.00", "40.00", "interest")])
        view = budget_view(actor=self.admin, business_unit=self.unit, year=self.year, month=month)
        self.assertEqual([line["line"] for line in view["lines"]][-4:], ["provisions", "other", "bank_interest", "other_income"])
        interest = self.line(view, "bank_interest")
        self.assertEqual((interest["kind"], interest["month"]["actual"], interest["month"]["budget"]), ("income", "130.00", "100.00"))
        self.assertEqual(self.line(view, "provisions")["month"]["flag"], "over")
        self.assertEqual(self.line(view, "other_income")["month"]["actual"], "25.00")
        self.assertEqual(self.line(view, "other")["month"]["actual"], "0.00", "Provisions are not other running costs")
        last, before = interest["previous"]
        self.assertEqual((last["year"], last["month"]["actual"], last["month"]["change"], last["month"]["change_percent"]),
                         (self.year - 1, "100.00", "30.00", 30))
        self.assertEqual(last["full_year"], "140.00")
        self.assertEqual((before["year"], before["to_date"]["actual"], before["to_date"]["change_percent"]), (self.year - 2, "80.00", 62))
        # Net profit this month: interest 130 + other income 25 - provisions 600.
        self.assertEqual(view["net_profit"]["month"]["actual"], "-445.00")
        self.assertEqual(view["net_profit"]["previous"][0]["month"]["actual"], "100.00")
        self.assertEqual(view["net_profit"]["previous"][0]["full_year"], "140.00")

    def test_bad_input_and_permissions(self):
        with self.assertRaisesMessage(InvalidTransaction, "12 monthly amounts"):
            self.save({"rent": ["1"] * 11})
        with self.assertRaisesMessage(InvalidTransaction, "Send an amount per month"):
            self.save({"yachts": months("1")})
        with self.assertRaisesMessage(InvalidTransaction, "Rent for March is not a number"):
            self.save({"rent": ["1", "1", "lots"] + ["1"] * 9})
        with self.assertRaisesMessage(InvalidTransaction, "between 0"):
            self.save({"rent": ["-5"] + ["1"] * 11})
        with self.assertRaisesMessage(WorkflowPermissionDenied, "'prepare budgets'"):
            self.save(actor=self.sales)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "permission"):
            budget_view(actor=self.roche_user, business_unit=self.unit)
        self.save({key: months("") for key in LINE_KEYS})
        with self.assertRaisesMessage(InvalidTransaction, "at least one amount"):
            self.decide("submit", actor=self.admin)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BudgetAmount.objects.create(budget=Budget.objects.get(), line="rent", month=13, amount=Decimal("1"))

    def test_api_round_trip(self):
        preparer, approver = APIClient(), APIClient()
        preparer.force_authenticate(self.admin)
        approver.force_authenticate(self.sales)
        unit = f"?unit={self.unit.pk}"
        saved = preparer.post(f"/api/finance/budgets/{unit}", {"year": self.year, "amounts": {"rent": months("100")}}, format="json")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(preparer.post(f"/api/finance/budgets/{self.year}/submit/{unit}", {}, format="json").status_code, 200)
        self.assertEqual(preparer.post(f"/api/finance/budgets/{self.year}/approve/{unit}", {}, format="json").status_code, 403)
        approved = approver.post(f"/api/finance/budgets/{self.year}/approve/{unit}", {}, format="json")
        self.assertEqual(approved.data["budget"]["status"], "approved")
        shown = approver.get(f"/api/finance/budgets/{unit}&year={self.year}&month=3")
        self.assertEqual(shown.data["month"], 3)
        self.assertEqual(self.line(shown.data, "rent")["to_date"]["budget"], "300.00")
        self.assertEqual(approver.get(f"/api/finance/budgets/{unit}&year=1999").status_code, 400)
        self.assertEqual(preparer.post(f"/api/finance/budgets/{self.year}/fly/{unit}", {}, format="json").status_code, 400)
        self.assertIn(self.year - 1, shown.data["years"])
        self.assertEqual(shown.data["lines"][0]["line"], "sales")
        self.assertEqual(datetime.date(self.year, 1, 1).year, shown.data["year"])

    def test_months_that_have_not_happened_are_not_flagged_as_below_budget(self):
        self.save({"sales": months("50000")}, year=self.year + 1)
        future = budget_view(actor=self.admin, business_unit=self.unit, year=self.year + 1)
        self.assertEqual(future["month"], 1, "A future year opens on January")
        self.assertEqual(self.line(future, "sales")["month"]["flag"], "")
        self.assertEqual(self.line(future, "sales")["to_date"]["flag"], "")
        self.save({"sales": months("50000")})
        past = budget_view(actor=self.admin, business_unit=self.unit, year=self.year, month=max(self.today.month - 1, 1))
        if self.today.month > 1:
            self.assertEqual(self.line(past, "sales")["month"]["flag"], "below", "An ended month with no sales is flagged")
