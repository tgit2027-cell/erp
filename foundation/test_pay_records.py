"""Pay records and working hours; synthetic only."""

import datetime
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .attendance import attendance_month, record_attendance
from .models import BusinessUnitWorkCalendar, UserActionGrant
from .pay_records import pay_overview, set_pay, set_working_hours
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import save_employee
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction
from .master_admin_testing import by_master_admin

A = UserActionGrant.Action
IBAN = "QA58DOHB00001234567890ABCDEFG"


@override_settings(APP_ENV="demo")
class PayRecordTests(PurchasingFixtureMixin, TestCase):
    """Admin sets pay and is HR; demo_roche may only look; demo_multi sets pay but is Aisha."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for user, action in ((cls.admin, A.MANAGE_PAY), (cls.admin, A.MANAGE_STAFF), (cls.roche_user, A.VIEW_PAY),
                             (cls.sales, A.MANAGE_PAY)):
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)
        BusinessUnitWorkCalendar.objects.update_or_create(business_unit=cls.unit, defaults={
            "closed_weekdays": [4], "closed_dates": [], "is_configured": True})

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()
        self.month = self.today.replace(day=1)
        self.aisha = save_employee(actor=self.admin, business_unit=self.unit, data={
            "full_name": "TEST ONLY Aisha Rahman", "job_title": "Consultant", "department": "Sales", "nationality": "Lebanese",
            "joined_on": "2024-03-10", "user_id": self.sales.pk})
        self.ravi = save_employee(actor=self.admin, business_unit=self.unit, data={
            "full_name": "TEST ONLY Ravi Kumar", "job_title": "Driver", "department": "Warehouse", "nationality": "Indian",
            "joined_on": "2025-06-01"})

    def pay(self, actor=None, person=None, month="2024-03", **extra):
        return set_pay(actor=actor or self.admin, business_unit=self.unit, data={
            "employee_id": (person or self.aisha)["id"], "effective_from": month, "basic": "6000", "housing": "2500",
            "transport": "500", "mobile": "150", "bank_name": "TEST ONLY Doha Bank", "iban": IBAN, "reason": "TEST ONLY offer letter",
            **extra})

    def test_first_pay_changes_and_history(self):
        first = self.pay()
        self.assertEqual((first["total"], first["iban"]), ("9150.00", "QA58 •••• DEFG"))
        with self.assertRaisesMessage(InvalidTransaction, "Pay cannot start before that month"):
            self.pay(person=self.ravi, month="2025-05")
        with self.assertRaisesMessage(InvalidTransaction, "Past months stay as they were paid"):
            self.pay(month="2025-01")
        nxt = (self.month + datetime.timedelta(days=32)).replace(day=1)
        self.pay(month=nxt.strftime("%Y-%m"), basic="6500", iban="", bank_name="", reason="TEST ONLY yearly raise")
        overview = pay_overview(actor=self.roche_user, business_unit=self.unit)
        aisha = next(row for row in overview["people"] if row["id"] == self.aisha["id"])
        self.assertEqual((aisha["pay"]["basic"], aisha["upcoming"]["basic"], aisha["upcoming"]["iban"]), ("6000.00", "6500.00", "QA58 •••• DEFG"),
                         "The raise is waiting for next month and keeps the bank account")
        self.assertEqual(len(aisha["history"]), 2)
        self.assertEqual((overview["missing"], overview["totals"]["total"], overview["can_manage"]), (1, "9150.00", False))

    def test_who_can_see_and_change_pay(self):
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Nobody changes their own pay"):
            self.pay(actor=self.sales)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only people who set salaries"):
            self.pay(actor=self.roche_user)
        UserActionGrant.objects.filter(user=self.roche_user, action=A.VIEW_PAY).delete()
        with self.assertRaisesMessage(WorkflowPermissionDenied, "payroll access"):
            pay_overview(actor=self.roche_user, business_unit=self.unit)
        with self.assertRaisesMessage(InvalidTransaction, "valid Qatar IBAN"):
            self.pay(iban="QA59DOHB00001234567890ABCDEFG")
        with self.assertRaisesMessage(InvalidTransaction, "Give the name of the other allowance"):
            self.pay(other="300")
        with self.assertRaisesMessage(InvalidTransaction, "basic pay must be more than zero"):
            self.pay(basic="0")
        self.assertEqual(self.pay(other="300", other_label="TEST ONLY food")["total"], "9450.00")

    def test_working_hours_count_minutes_late_and_early(self):
        hours = lambda **data: by_master_admin(set_working_hours)(actor=self.admin, business_unit=self.unit, data=data)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only HR"):
            set_working_hours(actor=self.roche_user, business_unit=self.unit, data={"action": "standard"})
        with self.assertRaisesMessage(InvalidTransaction, "finish after it starts"):
            hours(name="TEST ONLY night", starts_at="17:00", ends_at="08:00")
        added = hours(action="standard")["added"]
        self.assertEqual([(row["name"], row["starts_at"], row["ends_at"]) for row in added][-1], ("Showroom evening shift", "12:30", "21:00"))
        self.assertEqual(len(added), 7)
        self.assertEqual(hours(action="standard")["added"], [], "Adding the standard hours twice adds nothing")
        with self.assertRaisesMessage(InvalidTransaction, "already has working hours called"):
            hours(name="home delivery", starts_at="08:00", ends_at="17:00")
        office = next(row for row in added if row["name"].startswith("Back office"))
        warehouse = next(row for row in added if row["name"].startswith("Workshop"))
        hours(id=office["id"], name=office["name"], starts_at="08:00", ends_at="17:30", grace_minutes=10)
        hours(action="assign", employee_id=self.aisha["id"], hours_id=office["id"])
        hours(action="assign", employee_id=self.ravi["id"], hours_id=warehouse["id"])
        overview = pay_overview(actor=self.admin, business_unit=self.unit)
        self.assertEqual((overview["no_hours"], next(row for row in overview["working_hours"] if row["id"] == office["id"])["people"]), (0, 1))
        day = self.today - datetime.timedelta(days=1)
        while day.weekday() == 4:
            day -= datetime.timedelta(days=1)
        record_attendance(actor=self.admin, data={"day": day.isoformat(), "entries": [
            {"employee_id": self.aisha["id"], "status": "late", "check_in": "08:40", "check_out": "17:00"},
            {"employee_id": self.ravi["id"], "status": "present", "check_in": "07:00", "check_out": "16:05"}]})
        month = attendance_month(actor=self.admin, business_unit=self.unit, month=day.strftime("%Y-%m"))
        cells = {row["id"]: row for row in month["people"]}
        aisha = cells[self.aisha["id"]]["cells"][day.isoformat()]
        self.assertEqual((aisha["minutes_late"], aisha["minutes_early"]), (40, 30))
        self.assertEqual(cells[self.aisha["id"]]["hours"], "Back office, Admin and Interiors, 08:00 to 17:30")
        self.assertEqual(cells[self.ravi["id"]]["cells"][day.isoformat()]["minutes_late"], 0, "Ravi's workshop day starts at 07:00")
        self.assertEqual(cells[self.aisha["id"]]["totals"]["late_or_early"], 1)
        record_attendance(actor=self.admin, data={"day": day.isoformat(), "entries": [
            {"employee_id": self.aisha["id"], "status": "late", "check_in": "08:40", "check_out": "17:00", "excused": True,
             "note": "TEST ONLY doctor's appointment, approved"}]})
        month = attendance_month(actor=self.admin, business_unit=self.unit, month=day.strftime("%Y-%m"))
        self.assertEqual(next(row for row in month["people"] if row["id"] == self.aisha["id"])["totals"]["late_or_early"], 0,
                         "An approved late arrival does not count")
        with self.assertRaisesMessage(InvalidTransaction, "Record approved time off as leave"):
            record_attendance(actor=self.admin, data={"day": day.isoformat(), "entries": [
                {"employee_id": self.ravi["id"], "status": "absent", "excused": True}]})

    def test_the_api(self):
        client = APIClient()
        client.force_login(self.admin)
        response = client.post(f"/api/hr/pay/?unit={self.unit.pk}", {"employee_id": self.ravi["id"], "effective_from": "2025-06",
                                                                     "basic": "3200", "reason": "TEST ONLY offer letter"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        response = client.post(f"/api/hr/working-hours/?unit={self.unit.pk}", {"name": "TEST ONLY drivers", "starts_at": "07:30",
                                                                               "ends_at": "16:30"}, format="json")
        self.assertEqual(response.status_code, 403, response.content)  # Only the Master Admin adds sets of hours.
        self.admin.is_superuser = True
        self.admin.save(update_fields=["is_superuser"])
        response = client.post(f"/api/hr/working-hours/?unit={self.unit.pk}", {"name": "TEST ONLY drivers", "starts_at": "07:30",
                                                                               "ends_at": "16:30"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        response = client.post(f"/api/hr/working-hours/?unit={self.unit.pk}", {"action": "assign", "employee_id": self.ravi["id"],
                                                                               "hours_id": response.json()["id"]}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        overview = client.get(f"/api/hr/pay/?unit={self.unit.pk}").json()
        self.assertEqual((overview["totals"]["basic"], overview["working_hours"][0]["starts_at"], overview["no_hours"]), ("3200.00", "07:30", 1))
        client.force_login(self.roche_user)
        UserActionGrant.objects.filter(user=self.roche_user, action=A.VIEW_PAY).delete()
        self.assertEqual(client.get(f"/api/hr/pay/?unit={self.unit.pk}").status_code, 403)
