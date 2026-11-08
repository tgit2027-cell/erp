"""Attendance: daily records, leave days and HR's monthly confirmation; synthetic only."""

import datetime
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .attendance import attendance_month, confirm_month, record_attendance
from .leave import decide_leave, request_leave
from .models import BusinessUnitWorkCalendar, Employee, EmployeeEvent, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import save_employee
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action
CLOSED = {4, 5}


def working_day_before(day, skip=1):
    """The skip-th working day before this one (Friday and Saturday are closed)."""
    while skip:
        day -= datetime.timedelta(days=1)
        if day.weekday() not in CLOSED:
            skip -= 1
    return day


def next_working_day(day):
    day += datetime.timedelta(days=1)
    while day.weekday() in CLOSED:
        day += datetime.timedelta(days=1)
    return day


@override_settings(APP_ENV="demo")
class AttendanceTests(PurchasingFixtureMixin, TestCase):
    """Admin is HR; Omar (demo_roche) manages Aisha (demo_multi)."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.get_or_create(user=cls.admin, business_unit=cls.unit, action=A.MANAGE_STAFF)
        BusinessUnitWorkCalendar.objects.update_or_create(business_unit=cls.unit, defaults={
            "closed_weekdays": sorted(CLOSED), "closed_dates": [], "is_configured": True})

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()
        add = lambda **data: save_employee(actor=self.admin, business_unit=self.unit, data={
            "job_title": "Staff", "department": "Sales", "nationality": "Jordanian", **data})
        self.omar = add(full_name="TEST ONLY Omar Haddad", joined_on="2015-01-01", user_id=self.roche_user.pk)
        self.aisha = add(full_name="TEST ONLY Aisha Rahman", joined_on="2023-01-01", user_id=self.sales.pk,
                         manager_id=self.omar["id"])

    def record(self, actor, day, *entries):
        return record_attendance(actor=actor, data={"day": day.isoformat(), "entries": list(entries)})

    def entry(self, person, status="present", **extra):
        return {"employee_id": person["id"], "status": status, **extra}

    def test_the_manager_records_the_team_and_corrections_are_kept(self):
        day = working_day_before(self.today)
        saved = self.record(self.roche_user, day, self.entry(self.aisha, "late", check_in="08:40", check_out="17:00"))
        self.assertEqual((saved["saved"], saved["changed"]), (1, 0))
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Nobody records their own attendance"):
            self.record(self.sales, day, self.entry(self.aisha))
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only the person's manager or HR"):
            self.record(self.sales, day, self.entry(self.omar))
        with self.assertRaisesMessage(InvalidTransaction, "give the time they arrived"):
            self.record(self.roche_user, day, self.entry(self.aisha, "late"))
        with self.assertRaisesMessage(InvalidTransaction, "leave the times empty"):
            self.record(self.roche_user, day, self.entry(self.aisha, "absent", check_in="08:00"))
        with self.assertRaisesMessage(InvalidTransaction, "leaving time must be after the arrival"):
            self.record(self.roche_user, day, self.entry(self.aisha, check_in="09:00", check_out="08:00"))
        with self.assertRaisesMessage(InvalidTransaction, "HH:MM"):
            self.record(self.roche_user, day, self.entry(self.aisha, check_in="9am"))
        corrected = self.record(self.roche_user, day, self.entry(self.aisha, "present", check_in="07:55"))
        self.assertEqual(corrected["changed"], 1)
        event = EmployeeEvent.objects.filter(employee_id=self.aisha["id"], change="attendance").get()
        self.assertIn("changed from late 08:40 to present 07:55", event.what)
        self.assertEqual(self.record(self.roche_user, day, self.entry(self.aisha, "present", check_in="07:55"))["saved"], 0,
                         "Saving the same values again changes nothing")
        with self.assertRaisesMessage(InvalidTransaction, "has not happened yet"):
            self.record(self.roche_user, self.today + datetime.timedelta(days=1), self.entry(self.aisha))
        friday = self.today - datetime.timedelta(days=(self.today.weekday() - 4) % 7 or 7)
        with self.assertRaisesMessage(InvalidTransaction, "is a day off"):
            self.record(self.admin, friday, self.entry(self.aisha))
        old = working_day_before(self.today, 8)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "up to 7 days back"):
            self.record(self.roche_user, old, self.entry(self.aisha))
        self.assertEqual(self.record(self.admin, old, self.entry(self.aisha), self.entry(self.omar, "absent"))["saved"], 2)

    def test_the_month_shows_leave_and_what_is_missing(self):
        start = next_working_day(self.today)
        leave = request_leave(actor=self.sales, data={"kind": "annual", "start": start.isoformat(), "end": start.isoformat()})
        decide_leave(actor=self.roche_user, request_id=leave["id"], action="approve")
        with self.assertRaisesMessage(InvalidTransaction, "has not happened yet"):
            self.record(self.admin, start, self.entry(self.aisha))
        month = attendance_month(actor=self.roche_user, business_unit=None, month=start.strftime("%Y-%m"))
        people = {person["name"]: person for person in month["people"]}
        self.assertEqual(set(people), {"TEST ONLY Omar Haddad", "TEST ONLY Aisha Rahman"}, "The manager and their team")
        aisha = people["TEST ONLY Aisha Rahman"]
        self.assertEqual(aisha["cells"][start.isoformat()]["code"], "AL")
        self.assertTrue(aisha["can_record"])
        self.assertFalse(people["TEST ONLY Omar Haddad"]["can_record"], "Not their own")
        self.assertEqual((month["can_confirm"], month["is_hr"]), (False, False))
        # Past leave cannot be overwritten either.
        past = working_day_before(self.today, 2)
        leave = request_leave(actor=self.admin, data={"employee_id": self.aisha["id"], "kind": "sick",
                                                      "start": past.isoformat(), "end": past.isoformat()})
        decide_leave(actor=self.roche_user, request_id=leave["id"], action="approve")
        with self.assertRaisesMessage(InvalidTransaction, "is on approved leave"):
            self.record(self.roche_user, past, self.entry(self.aisha))
        month = attendance_month(actor=self.admin, business_unit=self.unit, month=past.strftime("%Y-%m"))
        aisha = next(person for person in month["people"] if person["id"] == self.aisha["id"])
        self.assertEqual(aisha["cells"][past.isoformat()]["code"], "SL")
        self.assertGreaterEqual(aisha["totals"]["leave"], 1)

    def test_hr_confirms_a_finished_month_and_can_reopen_it(self):
        last_month = (self.today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        label = last_month.strftime("%Y-%m")
        with self.assertRaisesMessage(InvalidTransaction, "has not ended yet"):
            confirm_month(actor=self.admin, business_unit=self.unit, data={"month": self.today.strftime("%Y-%m"), "action": "confirm"})
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only HR"):
            confirm_month(actor=self.roche_user, business_unit=self.unit, data={"month": label, "action": "confirm"})
        with self.assertRaisesMessage(InvalidTransaction, "not recorded yet: TEST ONLY Aisha Rahman"):
            confirm_month(actor=self.admin, business_unit=self.unit, data={"month": label, "action": "confirm"})
        day = last_month
        while day.month == last_month.month:
            if day.weekday() not in CLOSED:
                self.record(self.admin, day, self.entry(self.aisha), self.entry(self.omar))
            day += datetime.timedelta(days=1)
        others = list(Employee.objects.filter(business_unit=self.unit).exclude(pk__in=[self.aisha["id"], self.omar["id"]]))
        self.assertEqual(others, [], "Only the two test people work in this unit")
        done = confirm_month(actor=self.admin, business_unit=self.unit, data={"month": label, "action": "confirm"})
        self.assertEqual((done["status"], done["people"]), ("confirmed", 2))
        month = attendance_month(actor=self.admin, business_unit=self.unit, month=label)
        self.assertEqual((month["locked"], month["can_confirm"], month["can_reopen"]), (True, False, True))
        with self.assertRaisesMessage(InvalidTransaction, "HR must reopen it first"):
            self.record(self.admin, working_day_before(last_month.replace(day=28)), self.entry(self.aisha, "absent"))
        with self.assertRaisesMessage(InvalidTransaction, "Give the reason"):
            confirm_month(actor=self.admin, business_unit=self.unit, data={"month": label, "action": "reopen"})
        confirm_month(actor=self.admin, business_unit=self.unit, data={"month": label, "action": "reopen", "reason": "TEST ONLY late sick note"})
        self.record(self.admin, working_day_before(last_month.replace(day=28)), self.entry(self.aisha, "absent"))
        again = confirm_month(actor=self.admin, business_unit=self.unit, data={"month": label, "action": "confirm"})
        self.assertEqual(again["status"], "confirmed")
        state = attendance_month(actor=self.admin, business_unit=self.unit, month=label)["confirmation"]
        self.assertEqual(state["reopen_reason"], "TEST ONLY late sick note", "The reopening stays on record")

    def test_the_api(self):
        client = APIClient()
        client.force_login(self.roche_user)
        day = working_day_before(self.today)
        response = client.post("/api/hr/attendance/", {"day": day.isoformat(), "entries": [self.entry(self.aisha)]}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        month = client.get(f"/api/hr/attendance/?month={day:%Y-%m}").json()
        aisha = next(person for person in month["people"] if person["id"] == self.aisha["id"])
        self.assertEqual(aisha["cells"][day.isoformat()]["code"], "P")
        response = client.post(f"/api/hr/attendance/confirm/?unit={self.unit.pk}", {"month": f"{day:%Y-%m}", "action": "confirm"}, format="json")
        self.assertEqual(response.status_code, 403)
        stranger = Employee.objects.get(pk=self.omar["id"])
        stranger.user = None
        stranger.save(update_fields=["user"])
        self.assertEqual(client.get("/api/hr/attendance/").status_code, 403, "No staff record and no HR permission")
