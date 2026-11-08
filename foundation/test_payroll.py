"""Monthly payroll: the calculation, approval and posting, and the WPS file; synthetic only."""

import csv
import datetime
import io
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .attendance import record_attendance
from .models import (
    BusinessUnitWorkCalendar, EmployeeDocument, JournalLine, OvertimeLine, OvertimeRequest, PayrollRun, SalesQuote, UserActionGrant,
)
from .pay_records import set_pay, set_working_hours
from .payroll import approve_payroll, discard_payroll, payroll_overview, periods, prepare_payroll, set_wps_settings, wps_file
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import record_leaving, save_employee
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction
from .master_admin_testing import by_master_admin

A = UserActionGrant.Action
AISHA_IBAN = "QA58DOHB00001234567890ABCDEFG"
RAVI_IBAN = "QA57QNBA000000000123456789012"
PAYER_IBAN = "QA16CBQA000000000987654321098"


def valid(iban):
    """Put the right check digits into a synthetic Qatar IBAN."""
    body = iban[4:] + "QA00"
    check = 98 - int("".join(str(int(char, 36)) for char in body)) % 97
    return f"QA{check:02d}{iban[4:]}"


@override_settings(APP_ENV="demo")
class PayrollTests(PurchasingFixtureMixin, TestCase):
    """Admin is HR and prepares payroll; demo_roche approves it; demo_multi is Aisha, who is paid in it."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for user, action in ((cls.admin, A.MANAGE_PAY), (cls.admin, A.MANAGE_STAFF), (cls.roche_user, A.APPROVE_PAY),
                             (cls.sales, A.APPROVE_PAY)):
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)
        BusinessUnitWorkCalendar.objects.update_or_create(business_unit=cls.unit, defaults={
            "closed_weekdays": [4], "closed_dates": [], "is_configured": True})

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()
        self.month = ((self.today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        _, self.end, self.first, self.cut_off, self.pay_day = periods(self.month)
        add = lambda **data: save_employee(actor=self.admin, business_unit=self.unit, data={
            "job_title": "Staff", "department": "Workshop", "nationality": "Indian", "joined_on": "2020-01-01", **data})
        self.aisha = add(full_name="TEST ONLY Aisha Rahman", user_id=self.sales.pk)
        self.ravi = add(full_name="TEST ONLY Ravi Kumar")
        self.joseph = add(full_name="TEST ONLY Joseph Mendoza", joined_on="2023-01-16")
        pay = lambda person, iban, **amounts: set_pay(actor=self.admin, business_unit=self.unit, data={
            "employee_id": person["id"], "effective_from": "2020-01" if person is not self.joseph else "2023-01",
            "bank_name": "TEST ONLY bank", "iban": valid(iban), "reason": "TEST ONLY offer letter", **amounts})
        pay(self.aisha, AISHA_IBAN, basic="6000", housing="2500", transport="500", mobile="150")
        pay(self.ravi, RAVI_IBAN, basic="3000", housing="1000")
        pay(self.joseph, RAVI_IBAN, basic="3000")
        self.days = [day for day in (self.first + datetime.timedelta(days=n) for n in range((self.cut_off - self.first).days + 1))
                     if day.weekday() != 4]

    def record(self, day, *entries):
        record_attendance(actor=self.admin, data={"day": day.isoformat(), "entries": [
            {"employee_id": person["id"], "status": status, **times} for person, status, times in entries]})

    def overtime(self, day, hours, rate=125):
        self.make_receipt(charges=[])
        order = SalesQuote.objects.get(pk=self.make_quote(key=f"ot-{day}")["quote_id"])
        request = OvertimeRequest.objects.create(business_unit=self.unit, sales_order=order, work_date=day, rate_percent=rate,
                                                 rest_day=rate == 150, work="TEST ONLY fit-out", status="approved",
                                                 prepared_by=self.roche_user, decided_by=self.admin)
        OvertimeLine.objects.create(request=request, employee_id=self.ravi["id"], hours=Decimal(hours))
        return order

    def prepare(self, actor=None):
        return prepare_payroll(actor=actor or self.admin, business_unit=self.unit, data={"month": self.month.strftime("%Y-%m")})

    def test_each_rule_in_the_calculation(self):
        office = by_master_admin(set_working_hours)(actor=self.admin, business_unit=self.unit, data={
            "name": "TEST ONLY office", "starts_at": "08:00", "ends_at": "17:00"})
        set_working_hours(actor=self.admin, business_unit=self.unit, data={"action": "assign", "employee_id": self.aisha["id"],
                                                                          "hours_id": office["id"]})
        for number, day in enumerate(self.days[:7], start=1):
            self.record(day, (self.aisha, "late", {"check_in": "08:30", "check_out": "17:00"}),
                        (self.ravi, "absent" if number == 1 else "present", {} if number == 1 else {"check_in": "08:00", "check_out": "17:00"}))
        order = self.overtime(self.days[2], "2")
        record_leaving(actor=self.admin, business_unit=self.unit, employee_id=self.joseph["id"],
                       last_day=self.month.replace(day=15).isoformat(), reason="TEST ONLY resigned")
        run = self.prepare()
        lines = {line["employee"]["id"]: line for line in run["lines"]}
        aisha, ravi, joseph = lines[self.aisha["id"]], lines[self.ravi["id"]], lines[self.joseph["id"]]
        self.assertEqual([item["outcome"] for item in aisha["detail"]["lateness"]],
                         ["no effect"] * 3 + ["warning letter"] * 3 + ["deducted at double"])
        self.assertEqual((aisha["basic"], aisha["allowances"], aisha["deductions"], aisha["net"]),
                         ("6000.00", "3150.00", "38.13", "9111.87"), "30 minutes late counts as an hour of total pay")
        self.assertEqual((ravi["overtime"], ravi["overtime_hours"], ravi["deductions"], ravi["net"]),
                         ("31.25", "2", "133.33", "3897.92"), "3000 / 30 / 8 x 2 x 1.25; one absent day is 4000 / 30")
        self.assertEqual(ravi["detail"]["overtime"][0]["order_id"], order.pk)
        self.assertEqual((joseph["days_paid"], joseph["basic"], joseph["detail"]["end_of_service"]["years"]), (15, "1500.00", "3.67"))
        served = Decimal((self.month.replace(day=15) - datetime.date(2023, 1, 16)).days + 1) / 365
        self.assertEqual(joseph["end_of_service"], format((3000 * served * 21 / 30).quantize(Decimal("0.01")), "f"),
                         "basic x years of service x 21 / 30")
        self.assertTrue(any("without attendance" in warning for warning in run["warnings"]))
        self.assertEqual(run["totals"]["people"], 3)

    def test_approval_posts_the_journal_and_the_wps_file(self):
        order = self.overtime(self.days[0], "3", rate=150)
        run = self.prepare()
        with self.assertRaisesMessage(WorkflowPermissionDenied, "You prepared this payroll"):
            UserActionGrant.objects.get_or_create(user=self.admin, business_unit=self.unit, action=A.APPROVE_PAY)
            approve_payroll(actor=self.admin, business_unit=self.unit, run_id=run["id"])
        with self.assertRaisesMessage(WorkflowPermissionDenied, "This payroll pays you"):
            approve_payroll(actor=self.sales, business_unit=self.unit, run_id=run["id"])
        posted = approve_payroll(actor=self.roche_user, business_unit=self.unit, run_id=run["id"])
        self.assertEqual(posted["status"], "posted")
        lines = JournalLine.objects.filter(entry_id=posted["journal_entry"])
        by_account = {}
        for line in lines.select_related("account"):
            by_account.setdefault(line.account.code, []).append((line.debit, line.credit))
        self.assertEqual(by_account["TEST_EXP_OVERTIME"], [(Decimal("56.25"), Decimal("0.00"))], "3000 / 30 / 8 x 3 x 1.5")
        self.assertEqual(sum(line.debit for line in lines), sum(line.credit for line in lines))
        self.assertEqual(by_account["TEST_SALARIES_PAYABLE"][0][1], Decimal(posted["totals"]["net"]))
        self.assertIn(str(order.pk), lines.get(account__code="TEST_EXP_OVERTIME").memo)
        with self.assertRaisesMessage(InvalidTransaction, "already approved"):
            self.prepare()
        with self.assertRaisesMessage(InvalidTransaction, "Set the employer ID"):
            wps_file(actor=self.admin, business_unit=self.unit, run_id=run["id"])
        with self.assertRaisesMessage(InvalidTransaction, "does not belong to the chosen bank"):
            by_master_admin(set_wps_settings)(actor=self.admin, business_unit=self.unit, data={
                "employer_eid": "1234567", "payer_bank": "QNB", "payer_iban": valid(PAYER_IBAN)})
        by_master_admin(set_wps_settings)(actor=self.admin, business_unit=self.unit, data={
            "employer_eid": "1234567", "payer_bank": "CBQ", "payer_iban": valid(PAYER_IBAN)})
        with self.assertRaisesMessage(InvalidTransaction, "has no Qatar ID"):
            wps_file(actor=self.admin, business_unit=self.unit, run_id=run["id"])
        for number, person in enumerate((self.aisha, self.ravi, self.joseph)):
            EmployeeDocument.objects.create(employee_id=person["id"], kind="qid", number=f"2840000000{number}", added_by=self.admin)
        name, text = wps_file(actor=self.admin, business_unit=self.unit, run_id=run["id"])
        self.assertRegex(name, r"^SIF_1234567_CBQ_\d{8}_\d{4}\.csv$")
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual((rows[1][7], rows[1][8], rows[1][9]), (self.month.strftime("%Y%m"), posted["totals"]["net"], "3"))
        self.assertEqual((len(rows), len(rows[3]), rows[2][0]), (6, 22, "Record Sequence"))
        ravi = next(row for row in rows[3:] if row[3] == "Ravi Kumar")
        self.assertEqual((ravi[1], ravi[4], ravi[6], ravi[10], ravi[18]), ("28400000001", "QNB", "M", "3", "56.25"))

    def test_rules_before_preparing(self):
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only people who prepare payroll"):
            self.prepare(actor=self.roche_user)
        this_month = self.today.replace(day=1)
        if self.today <= periods(this_month)[3]:
            with self.assertRaisesMessage(InvalidTransaction, "after the cut-off"):
                prepare_payroll(actor=self.admin, business_unit=self.unit, data={"month": this_month.strftime("%Y-%m")})
        later = save_employee(actor=self.admin, business_unit=self.unit, data={
            "full_name": "TEST ONLY Maria Santos", "job_title": "Staff", "department": "Workshop", "nationality": "Filipino",
            "joined_on": "2021-01-01"})
        with self.assertRaisesMessage(InvalidTransaction, "Enter the pay for TEST ONLY Maria Santos"):
            self.prepare()
        set_pay(actor=self.admin, business_unit=self.unit, data={"employee_id": later["id"], "effective_from": "2021-01",
                                                                 "basic": "2500", "reason": "TEST ONLY offer letter"})
        run = self.prepare()
        self.assertTrue(any("Maria Santos" in warning and "no working hours" in warning for warning in run["warnings"]))
        self.assertTrue(any("Maria Santos has no IBAN" in warning for warning in run["warnings"]))
        overview = payroll_overview(actor=self.roche_user, business_unit=self.unit, month=self.month.strftime("%Y-%m"))
        self.assertEqual((overview["run"]["can_approve"], overview["run"]["can_recalculate"], overview["can_prepare"]), (True, False, False))
        self.assertEqual(self.prepare()["id"], run["id"], "Preparing again recalculates the same payroll")
        discard_payroll(actor=self.admin, business_unit=self.unit, run_id=run["id"])
        self.assertFalse(PayrollRun.objects.exists())
        UserActionGrant.objects.filter(user=self.roche_user).delete()
        with self.assertRaisesMessage(WorkflowPermissionDenied, "payroll access"):
            payroll_overview(actor=self.roche_user, business_unit=self.unit)

    def test_the_api(self):
        client = APIClient()
        client.force_login(self.admin)
        month = self.month.strftime("%Y-%m")
        response = client.post(f"/api/hr/payroll/?unit={self.unit.pk}", {"month": month}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        run_id = response.json()["id"]
        client.force_login(self.roche_user)
        response = client.post(f"/api/hr/payroll/{run_id}/?unit={self.unit.pk}", {"action": "approve"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        client.force_login(self.admin)
        response = client.post(f"/api/hr/wps-settings/?unit={self.unit.pk}", {
            "employer_eid": "1234567", "payer_bank": "CBQ", "payer_iban": valid(PAYER_IBAN)}, format="json")
        self.assertEqual(response.status_code, 403, response.content)  # Only the Master Admin sets the WPS details.
        self.admin.is_superuser = True
        self.admin.save(update_fields=["is_superuser"])
        response = client.post(f"/api/hr/wps-settings/?unit={self.unit.pk}", {
            "employer_eid": "1234567", "payer_bank": "CBQ", "payer_iban": valid(PAYER_IBAN)}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(client.get(f"/api/hr/payroll/{run_id}/wps-file/?unit={self.unit.pk}").status_code, 400, "No QIDs yet")
        for number, person in enumerate((self.aisha, self.ravi, self.joseph)):
            EmployeeDocument.objects.create(employee_id=person["id"], kind="qid", number=f"2840000000{number}", added_by=self.admin)
        response = client.get(f"/api/hr/payroll/{run_id}/wps-file/?unit={self.unit.pk}")
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "text/csv; charset=utf-8"))
        self.assertIn('filename="SIF_1234567_CBQ_', response["Content-Disposition"])
        self.assertEqual(client.get(f"/api/hr/payroll/?unit={self.unit.pk}&month={month}").json()["run"]["status"], "posted")
