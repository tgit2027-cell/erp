"""End-of-service settlement and its payment through payroll; synthetic only."""

import datetime
from decimal import ROUND_HALF_UP, Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .end_of_service import decide_settlement, prepare_settlement, settlements_overview
from .models import EndOfServiceSettlement, JournalLine, LeaveRequest, UserActionGrant
from .pay_records import set_pay
from .payroll import approve_payroll, periods, prepare_payroll
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import record_leaving, save_employee
from .test_payroll import RAVI_IBAN, valid
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action


@override_settings(APP_ENV="demo")
class EndOfServiceTests(PurchasingFixtureMixin, TestCase):
    """Admin is HR and prepares; demo_roche approves; demo_multi is Joseph, who is leaving."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for user, action in ((cls.admin, A.MANAGE_PAY), (cls.admin, A.MANAGE_STAFF), (cls.roche_user, A.APPROVE_PAY),
                             (cls.sales, A.APPROVE_PAY)):
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        today = timezone.localdate()
        self.month = ((today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        self.next_month = (self.month + datetime.timedelta(days=32)).replace(day=1)
        self.last_day = self.month.replace(day=15)
        self.joseph = save_employee(actor=self.admin, business_unit=self.unit, data={
            "full_name": "TEST ONLY Joseph Mendoza", "job_title": "Upholsterer", "department": "Workshop", "nationality": "Filipino",
            "joined_on": "2023-01-16", "user_id": self.sales.pk})
        set_pay(actor=self.admin, business_unit=self.unit, data={
            "employee_id": self.joseph["id"], "effective_from": "2023-01", "bank_name": "TEST ONLY bank", "iban": valid(RAVI_IBAN),
            "reason": "TEST ONLY offer letter", "basic": "3000", "housing": "1000"})
        self.ravi = save_employee(actor=self.admin, business_unit=self.unit, data={
            "full_name": "TEST ONLY Ravi Kumar", "job_title": "Driver", "department": "Delivery", "nationality": "Indian",
            "joined_on": "2020-01-01"})
        set_pay(actor=self.admin, business_unit=self.unit, data={
            "employee_id": self.ravi["id"], "effective_from": "2020-01", "bank_name": "TEST ONLY bank", "iban": valid(RAVI_IBAN),
            "reason": "TEST ONLY offer letter", "basic": "2000"})
        # Ten days of annual leave taken in this leave year (which started on the 2026 anniversary or the latest one).
        anniversary = datetime.date(self.last_day.year if self.last_day >= datetime.date(self.last_day.year, 1, 16)
                                    else self.last_day.year - 1, 1, 16)
        LeaveRequest.objects.create(employee_id=self.joseph["id"], kind="annual", start=anniversary + datetime.timedelta(days=30),
                                    end=anniversary + datetime.timedelta(days=39), days=10, status="approved",
                                    requested_by=self.admin, decided_by=self.roche_user)
        record_leaving(actor=self.admin, business_unit=self.unit, employee_id=self.joseph["id"],
                       last_day=self.last_day.isoformat(), reason="TEST ONLY resigned")
        since = (self.last_day - anniversary).days + 1
        earned = (Decimal(30) * since / 365 * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2
        self.leave_days = 35 + earned
        self.leave_pay = (100 * self.leave_days).quantize(Decimal("0.01"))
        served = Decimal((self.last_day - datetime.date(2023, 1, 16)).days + 1) / 365
        self.gratuity = (3000 * served * 21 / 30).quantize(Decimal("0.01"))

    def prepare(self, actor=None, **data):
        return prepare_settlement(actor=actor or self.admin, business_unit=self.unit, data={"employee_id": self.joseph["id"], **data})

    def decide(self, settlement, action, actor=None, reason=""):
        return decide_settlement(actor=actor or self.roche_user, business_unit=self.unit, settlement_id=settlement["id"],
                                 action=action, reason=reason)

    def payroll(self, month):
        return prepare_payroll(actor=self.admin, business_unit=self.unit, data={"month": month.strftime("%Y-%m")})

    def test_the_settlement_adds_unused_leave_and_extras_and_is_paid_in_that_months_payroll(self):
        preview = settlements_overview(actor=self.admin, business_unit=self.unit)["people"][0]["preview"]
        # 30 days credited at the anniversary plus 15 carried over, less 10 taken, plus what was earned since = at 3000 / 30.
        self.assertEqual((preview["gratuity"], preview["leave_days"], preview["leave_pay"]),
                         (str(self.gratuity), format(self.leave_days.normalize(), "f"), str(self.leave_pay)))
        self.assertIn("earned since then", preview["leave"])
        settlement = self.prepare(additions=[{"label": "TEST ONLY air ticket home", "amount": "1800"}],
                                  deductions=[{"label": "TEST ONLY laptop not returned", "amount": "500"}], note="TEST ONLY")
        total = self.gratuity + self.leave_pay + 1800 - 500
        self.assertEqual((settlement["status"], settlement["figures"]["total"], settlement["pay_month"]),
                         ("prepared", str(total), self.month.strftime("%Y-%m")))
        with self.assertRaisesMessage(WorkflowPermissionDenied, "pays you"):
            self.decide(settlement, "approve", actor=self.sales)
        UserActionGrant.objects.get_or_create(user=self.admin, business_unit=self.unit, action=A.APPROVE_PAY)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "You prepared this settlement"):
            self.decide(settlement, "approve", actor=self.admin)
        self.assertEqual(self.decide(settlement, "approve")["status"], "approved")

        run = self.payroll(self.month)
        line = next(row for row in run["lines"] if row["employee"]["id"] == self.joseph["id"])
        self.assertEqual((line["days_paid"], line["basic"], line["end_of_service"]), (15, "1500.00", str(total)))
        self.assertEqual(line["detail"]["end_of_service"]["leave_pay"], str(self.leave_pay))
        posted = approve_payroll(actor=self.roche_user, business_unit=self.unit, run_id=run["id"])
        lines = JournalLine.objects.filter(entry_id=posted["journal_entry"]).select_related("account")
        self.assertEqual(sum(row.debit for row in lines), sum(row.credit for row in lines))
        self.assertEqual(lines.get(account__code="TEST_EXP_END_OF_SERVICE").debit, self.gratuity)
        memos = {row.memo.split(": ", 1)[1]: (row.debit, row.credit) for row in lines}
        extras = self.leave_pay + 1800
        self.assertEqual(memos[f"{self.month:%B %Y} payroll: unused leave and other amounts in final settlements"],
                         (extras, Decimal("0.00")))
        self.assertEqual(memos[f"{self.month:%B %Y} payroll: deductions in final settlements"], (Decimal("0.00"), Decimal("500.00")))
        self.assertEqual(settlements_overview(actor=self.admin, business_unit=self.unit)["people"][0]["settlement"]["paid_in"],
                         f"{self.month:%B %Y}")
        with self.assertRaisesMessage(InvalidTransaction, "already paid"):
            self.decide(settlement, "cancel", actor=self.admin, reason="TEST ONLY")

    def test_after_the_payroll_is_posted_it_goes_in_the_next_month_without_paying_twice(self):
        first = self.payroll(self.month)
        self.assertTrue(any("no approved end-of-service settlement" in warning for warning in first["warnings"]))
        approve_payroll(actor=self.roche_user, business_unit=self.unit, run_id=first["id"])
        settlement = self.prepare()
        self.assertEqual((settlement["pay_month"], settlement["figures"]["already_paid"], settlement["figures"]["total"]),
                         (self.next_month.strftime("%Y-%m"), str(self.gratuity), str(self.leave_pay)), "Only the leave is still owed")
        later = self.payroll(self.next_month)
        self.decide(settlement, "approve")
        with self.assertRaisesMessage(InvalidTransaction, "Work the payroll out again"):
            approve_payroll(actor=self.roche_user, business_unit=self.unit, run_id=later["id"])
        later = self.payroll(self.next_month)
        line = next(row for row in later["lines"] if row["employee"]["id"] == self.joseph["id"])
        self.assertEqual((line["days_paid"], line["basic"], line["net"]), (0, "0.00", str(self.leave_pay)))
        posted = approve_payroll(actor=self.roche_user, business_unit=self.unit, run_id=later["id"])
        lines = JournalLine.objects.filter(entry_id=posted["journal_entry"])
        self.assertFalse(lines.filter(account__code="TEST_EXP_END_OF_SERVICE").exists())
        self.assertEqual(lines.get(memo__contains="unused leave").debit, self.leave_pay)

    def test_rules_permissions_and_the_api(self):
        with self.assertRaisesMessage(InvalidTransaction, "more than what is owed"):
            self.prepare(deductions=[{"label": "TEST ONLY loan", "amount": "900000"}])
        with self.assertRaisesMessage(InvalidTransaction, "Give a reason"):
            self.prepare(additions=[{"label": "", "amount": "10"}])
        with self.assertRaises(WorkflowPermissionDenied):
            prepare_settlement(actor=self.roche_user, business_unit=self.unit, data={"employee_id": self.joseph["id"]})
        with self.assertRaisesMessage(InvalidTransaction, "is not leaving"):
            prepare_settlement(actor=self.admin, business_unit=self.unit, data={"employee_id": self.ravi["id"]})
        settlement = self.prepare()
        again = self.prepare(additions=[{"label": "TEST ONLY notice pay", "amount": "4000"}])
        self.assertEqual((again["id"], EndOfServiceSettlement.objects.count()), (settlement["id"], 1), "Worked out again in place")
        with self.assertRaisesMessage(InvalidTransaction, "Say why"):
            self.decide(again, "cancel", actor=self.admin)
        self.assertEqual(self.decide(again, "cancel", actor=self.admin, reason="TEST ONLY stays on")["status"], "cancelled")
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.post(f"/api/hr/end-of-service/?unit={self.unit.pk}", {"employee_id": self.joseph["id"]}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        client.force_authenticate(self.roche_user)
        response = client.post(f"/api/hr/end-of-service/{response.json()['id']}/?unit={self.unit.pk}", {"action": "approve"}, format="json")
        self.assertEqual((response.status_code, response.json()["status"]), (200, "approved"))
        self.assertEqual(client.get(f"/api/hr/end-of-service/?unit={self.roche.pk}").status_code, 403)
