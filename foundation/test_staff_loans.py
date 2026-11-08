"""Staff loans and salary advances, taken back through payroll and the end-of-service settlement; synthetic only."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .cash_accounts import create_account
from .master_admin_testing import by_master_admin
from .end_of_service import decide_settlement, prepare_settlement
from .models import JournalLine, StaffLoan, UserActionGrant
from .pay_records import set_pay
from .payroll import approve_payroll, prepare_payroll
from .purchasing_services import WorkflowPermissionDenied
from .staff_loans import decide_loan, loans_overview, request_loan
from .staff_records import record_leaving, save_employee
from .test_payroll import RAVI_IBAN, valid
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action


@override_settings(APP_ENV="demo")
class StaffLoanTests(PurchasingFixtureMixin, TestCase):
    """Admin is HR and finance; demo_roche approves; demo_multi is Joseph."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for user, action in ((cls.admin, A.MANAGE_PAY), (cls.admin, A.MANAGE_STAFF), (cls.admin, A.MANAGE_CASH),
                             (cls.roche_user, A.APPROVE_PAY), (cls.sales, A.MANAGE_CASH)):
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        today = timezone.localdate()
        self.next_month = (today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        self.month = (self.next_month - datetime.timedelta(days=1)).replace(day=1)
        self.ravi = save_employee(actor=self.admin, business_unit=self.unit, data={
            "full_name": "TEST ONLY Ravi Kumar", "job_title": "Driver", "department": "Delivery", "nationality": "Indian",
            "joined_on": "2020-01-01"})
        set_pay(actor=self.admin, business_unit=self.unit, data={
            "employee_id": self.ravi["id"], "effective_from": "2020-01", "bank_name": "TEST ONLY bank", "iban": valid(RAVI_IBAN),
            "reason": "TEST ONLY offer letter", "basic": "2000"})
        self.joseph = save_employee(actor=self.admin, business_unit=self.unit, data={
            "full_name": "TEST ONLY Joseph Mendoza", "job_title": "Upholsterer", "department": "Workshop", "nationality": "Filipino",
            "joined_on": "2023-01-16", "user_id": self.sales.pk})
        set_pay(actor=self.admin, business_unit=self.unit, data={
            "employee_id": self.joseph["id"], "effective_from": "2023-01", "bank_name": "TEST ONLY bank", "iban": valid(RAVI_IBAN),
            "reason": "TEST ONLY offer letter", "basic": "3000", "housing": "1000"})
        opened = (self.month - datetime.timedelta(days=30)).isoformat()
        self.bank = by_master_admin(create_account)(actor=self.admin, business_unit=self.unit, name="TEST ONLY QNB current", kind="bank",
                                   opening_balance="50000.00", opening_date=opened)

    def ask(self, person, kind="loan", **data):
        return request_loan(actor=self.admin, business_unit=self.unit, data={
            "employee_id": person["id"], "kind": kind, "reason": "TEST ONLY family need", **data})

    def act(self, loan, action, actor=None, **data):
        return decide_loan(actor=actor or self.roche_user, business_unit=self.unit, loan_id=loan["id"], action=action, **data)

    def paid_out(self, loan, first_month):
        self.act(loan, "approve")
        paid = self.act(loan, "pay", actor=self.admin, account_id=self.bank["id"], date=timezone.localdate().isoformat())
        # Instalments start in a past month here so the test can run that month's payroll.
        StaffLoan.objects.filter(pk=loan["id"]).update(first_month=first_month)
        return paid

    def payroll(self, month):
        return prepare_payroll(actor=self.admin, business_unit=self.unit, data={"month": month.strftime("%Y-%m")})

    def line(self, run, person):
        return next(row for row in run["lines"] if row["employee"]["id"] == person["id"])

    def journal(self, entry_id):
        return {row.memo.split(": ", 1)[-1]: (row.account.code, row.debit, row.credit)
                for row in JournalLine.objects.filter(entry_id=entry_id).select_related("account")}

    def test_a_loan_is_paid_out_and_taken_back_through_payroll_inside_the_half_pay_cap(self):
        loan = self.ask(self.ravi, amount="3000", instalment="500")
        self.assertEqual((loan["status"], loan["balance"], loan["first_month"]),
                         ("requested", "0.00", timezone.localdate().strftime("%Y-%m")), "Starts with the next payroll")
        paid = self.paid_out(loan, self.month)
        self.assertEqual((paid["status"], paid["balance"], paid["paid_from"]), ("repaying", "3000.00", "TEST ONLY QNB current"))
        self.assertEqual(JournalLine.objects.get(entry_id=paid["payout_entry"], account__code="TEST_CASH").credit, Decimal("3000.00"))
        self.assertEqual(JournalLine.objects.get(entry_id=paid["payout_entry"], account__code="TEST_STAFF_LOANS").debit,
                         Decimal("3000.00"))

        run = self.payroll(self.month)
        line = self.line(run, self.ravi)
        self.assertEqual((line["deductions"], line["net"]), ("500.00", "1500.00"))
        self.assertEqual(line["detail"]["loans"][0]["balance_after"], "2500.00")
        posted = approve_payroll(actor=self.roche_user, business_unit=self.unit, run_id=run["id"])
        entry = self.journal(posted["journal_entry"])
        self.assertEqual(entry[f"{self.month:%B %Y} payroll: staff loans and advances taken back"],
                         ("TEST_STAFF_LOANS", Decimal("0.00"), Decimal("500.00")))
        self.assertNotIn(f"{self.month:%B %Y} payroll: absence and lateness deductions", entry)
        lines = JournalLine.objects.filter(entry_id=posted["journal_entry"])
        self.assertEqual(sum(row.debit for row in lines), sum(row.credit for row in lines))

        # An advance of a full month's pay on top: both together are cut to half of Ravi's 2000 pay.
        advance = self.ask(self.ravi, kind="advance", amount="2000")
        self.paid_out(advance, self.next_month)
        line = self.line(self.payroll(self.next_month), self.ravi)
        self.assertEqual([row["amount"] for row in line["detail"]["loans"]], ["500.00", "500.00"])
        self.assertEqual((line["deductions"], line["net"]), ("1000.00", "1000.00"))
        self.assertTrue(any("half-pay cap" in note for note in line["detail"]["notes"]))
        approve_payroll(actor=self.roche_user, business_unit=self.unit, run_id=self.payroll(self.next_month)["id"])
        rows = {row["kind"]: row for row in loans_overview(actor=self.admin, business_unit=self.unit)["loans"]}
        self.assertEqual((rows["loan"]["balance"], rows["loan"]["months_left"], rows["advance"]["balance"]),
                         ("2000.00", 4, "1500.00"), "What the cap cut stays owed")
        self.assertEqual(len(rows["loan"]["repayments"]), 2)

    def test_what_is_still_owed_comes_off_the_end_of_service_settlement(self):
        loan = self.ask(self.joseph, amount="6000", instalment="1000")
        self.paid_out(loan, self.next_month)
        record_leaving(actor=self.admin, business_unit=self.unit, employee_id=self.joseph["id"],
                       last_day=self.month.replace(day=15).isoformat(), reason="TEST ONLY resigned")
        settlement = prepare_settlement(actor=self.admin, business_unit=self.unit, data={"employee_id": self.joseph["id"]})
        owed = [row for row in settlement["figures"]["deductions"] if row.get("loan")]
        self.assertEqual(owed, [{"label": "Staff loan still owed", "amount": "6000.00", "loan": loan["id"]}])
        decide_settlement(actor=self.roche_user, business_unit=self.unit, settlement_id=settlement["id"], action="approve")
        run = self.payroll(self.month)
        line = self.line(run, self.joseph)
        self.assertNotIn("loans", line["detail"], "No instalment on top of the settlement")
        posted = approve_payroll(actor=self.roche_user, business_unit=self.unit, run_id=run["id"])
        entry = self.journal(posted["journal_entry"])
        self.assertEqual(entry[f"{self.month:%B %Y} payroll: staff loans and advances taken back"],
                         ("TEST_STAFF_LOANS", Decimal("0.00"), Decimal("6000.00")))
        self.assertNotIn(f"{self.month:%B %Y} payroll: deductions in final settlements", entry)
        self.assertEqual(StaffLoan.objects.get(pk=loan["id"]).status, "repaid")

    def test_rules_permissions_and_the_api(self):
        # Ravi has served almost 7 years: about 9,500 QAR of end of service plus 2,000 QAR of pay.
        room = Decimal(next(row for row in loans_overview(actor=self.admin, business_unit=self.unit)["people"] if row["id"] == self.ravi["id"])["limits"]["room"])
        self.assertTrue(Decimal("9000") < room < Decimal("12000"), room)
        with self.assertRaisesMessage(InvalidTransaction, "can borrow at most"):
            self.ask(self.ravi, amount=str(room + 1), instalment="1000")
        with self.assertRaisesMessage(InvalidTransaction, "at the earliest"):
            self.ask(self.ravi, amount="1000", first_month=self.month.strftime("%Y-%m"))
        with self.assertRaisesMessage(InvalidTransaction, "Give the reason"):
            self.ask(self.ravi, amount="1000", reason="")
        with self.assertRaises(WorkflowPermissionDenied):
            request_loan(actor=self.roche_user, business_unit=self.unit, data={"employee_id": self.ravi["id"], "kind": "loan"})
        loan = self.ask(self.ravi, amount="1000", instalment="50")
        self.assertEqual(loan["months_left"], 0, "Any instalment HR and the employee agree, even over many months")
        after = next(row for row in loans_overview(actor=self.admin, business_unit=self.unit)["people"] if row["id"] == self.ravi["id"])["limits"]
        self.assertEqual(Decimal(after["room"]), room - 1000, "Counts what was asked for")
        with self.assertRaisesMessage(InvalidTransaction, "already has an open loan"):
            self.ask(self.ravi, amount="500")
        UserActionGrant.objects.get_or_create(user=self.admin, business_unit=self.unit, action=A.APPROVE_PAY)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "someone else must decide"):
            self.act(loan, "approve", actor=self.admin)
        with self.assertRaisesMessage(InvalidTransaction, "Give the note"):
            self.act(loan, "reject")
        with self.assertRaisesMessage(InvalidTransaction, "approved loan or advance can be paid out"):
            self.act(loan, "pay", actor=self.admin, account_id=self.bank["id"])
        self.assertEqual(self.act(loan, "reject", note="TEST ONLY not this month")["status"], "rejected")

        mine = self.ask(self.joseph, kind="advance", amount="1000")
        self.act(mine, "approve")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Someone else must pay out"):
            self.act(mine, "pay", actor=self.sales, account_id=self.bank["id"])
        with self.assertRaisesMessage(InvalidTransaction, "Give the reason"):
            self.act(mine, "cancel", actor=self.admin)
        self.assertEqual(self.act(mine, "cancel", actor=self.admin, note="TEST ONLY no longer needed")["status"], "cancelled")

        client = APIClient()
        client.force_login(self.admin)
        response = client.post(f"/api/hr/loans/?unit={self.unit.pk}", {
            "employee_id": self.ravi["id"], "kind": "advance", "amount": "800", "reason": "TEST ONLY school fees"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        overview = client.get(f"/api/hr/loans/?unit={self.unit.pk}").json()
        self.assertEqual((overview["tiles"]["waiting"], len(overview["people"]), overview["accounts"][0]["name"]),
                         (1, 2, "TEST ONLY QNB current"))
        client.force_login(self.roche_user)
        response = client.post(f"/api/hr/loans/{response.json()['id']}/?unit={self.unit.pk}", {"action": "approve"}, format="json")
        self.assertEqual(response.json()["status"], "approved")
        self.assertEqual(client.get(f"/api/hr/loans/?unit={self.roche.pk}").status_code, 403)
