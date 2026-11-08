"""Leave: requests, manager approval and balances; synthetic only."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .leave import adjust_leave, balance, decide_leave, leave_overview, leave_year, request_leave
from .models import BusinessUnitWorkCalendar, Employee, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import save_employee
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action


def monday_after(day, weeks=1):
    return day + datetime.timedelta(days=(7 - day.weekday()) % 7 + 7 * weeks)


@override_settings(APP_ENV="demo")
class LeaveTests(PurchasingFixtureMixin, TestCase):
    """Admin is HR; Omar (demo_roche) manages Aisha (demo_multi)."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.get_or_create(user=cls.admin, business_unit=cls.unit, action=A.MANAGE_STAFF)
        BusinessUnitWorkCalendar.objects.update_or_create(business_unit=cls.unit, defaults={
            "closed_weekdays": [4, 5], "closed_dates": [], "is_configured": True})

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()
        self.year = self.today.year
        self.add = lambda **data: save_employee(actor=self.admin, business_unit=self.unit, data={
            "job_title": "Staff", "department": "Sales", "nationality": "Jordanian", **data})
        self.omar = self.add(full_name="TEST ONLY Omar Haddad", joined_on="2015-01-01", user_id=self.roche_user.pk)
        # Two years and two months in: the second leave year started about 60 days ago.
        joined = self.today.replace(year=self.today.year - 2) - datetime.timedelta(days=60)
        self.aisha = self.add(full_name="TEST ONLY Aisha Rahman", joined_on=joined.isoformat(), user_id=self.sales.pk,
                              manager_id=self.omar["id"])

    def ask(self, actor=None, **data):
        start = monday_after(self.today)
        payload = {"kind": "annual", "start": start.isoformat(), "end": (start + datetime.timedelta(days=4)).isoformat(),
                   "reason": "TEST ONLY family visit", **data}
        return request_leave(actor=actor or self.sales, data=payload)

    def employee(self, person):
        return Employee.objects.get(pk=person["id"])

    def test_the_manager_approves_and_the_balance_goes_down(self):
        request = self.ask()
        self.assertEqual((request["days"], request["status"]), (5, "requested"), "Annual leave counts calendar days")
        mine = balance(self.employee(self.aisha))
        self.assertEqual((mine["entitlement"], mine["carried"], mine["waiting"], mine["left"], mine["parts_used"]),
                         ("30", 15, 5, "40", 1), "Nothing was taken in the first leave year, so 15 days carried over")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Someone other than the person who asked"):
            decide_leave(actor=self.sales, request_id=request["id"], action="approve")
        manager_view = leave_overview(actor=self.roche_user)
        self.assertEqual([row["id"] for row in manager_view["to_decide"]], [request["id"]])
        self.assertEqual([person["name"] for person in manager_view["people"]], ["TEST ONLY Aisha Rahman"])
        approved = decide_leave(actor=self.roche_user, request_id=request["id"], action="approve")
        self.assertEqual(approved["status"], "approved")
        mine = balance(self.employee(self.aisha))
        self.assertEqual((mine["taken"], mine["waiting"], mine["left"]), (5, 0, "40"))
        with self.assertRaisesMessage(InvalidTransaction, "already has annual leave"):
            self.ask()
        adjust_leave(actor=self.admin, data={"employee_id": self.aisha["id"], "days": "-36", "reason": "TEST ONLY correction"})
        later = monday_after(self.today, 3)
        with self.assertRaisesMessage(InvalidTransaction, "4 days of annual leave left"):
            self.ask(start=later.isoformat(), end=(later + datetime.timedelta(days=5)).isoformat())
        self.ask(start=later.isoformat(), end=(later + datetime.timedelta(days=1)).isoformat())
        last = monday_after(self.today, 6)
        with self.assertRaisesMessage(InvalidTransaction, "It can be taken in at most 2 parts"):
            self.ask(start=last.isoformat(), end=last.isoformat())
        self.ask(kind="unpaid", start=last.isoformat(), end=last.isoformat())

    def test_the_first_year_carry_over_and_days_in_a_row(self):
        newcomer = self.add(full_name="TEST ONLY Lina Farouk", joined_on=(self.today - datetime.timedelta(days=100)).isoformat())
        self.assertEqual((balance(self.employee(newcomer))["entitlement"], balance(self.employee(newcomer))["leave_year"]), ("0", None))
        with self.assertRaisesMessage(InvalidTransaction, "completes 1 year of service on"):
            self.ask(actor=self.admin, employee_id=newcomer["id"])
        # Aisha took 20 days last leave year, in two parts, so only 10 carry over.
        aisha = self.employee(self.aisha)
        number, first, last = leave_year(aisha, self.today)
        self.assertEqual(number, 2)
        previous = first - datetime.timedelta(days=200)
        for start, days in ((previous, 12), (previous + datetime.timedelta(days=40), 8)):
            past = self.ask(actor=self.admin, employee_id=self.aisha["id"], start=start.isoformat(),
                            end=(start + datetime.timedelta(days=days - 1)).isoformat())
            decide_leave(actor=self.roche_user, request_id=past["id"], action="approve")
        mine = balance(aisha)
        self.assertEqual((mine["carried"], mine["left"], mine["leave_year"]["end"]), (10, "40", last.isoformat()))
        with self.assertRaisesMessage(InvalidTransaction, "cannot run past the end of the leave year"):
            self.ask(start=(last - datetime.timedelta(days=2)).isoformat(), end=(last + datetime.timedelta(days=2)).isoformat())
        start = monday_after(self.today)
        self.ask(start=start.isoformat(), end=(start + datetime.timedelta(days=13)).isoformat())
        with self.assertRaisesMessage(InvalidTransaction, "That would make 34 days away in a row"):
            self.ask(kind="unpaid", start=(start + datetime.timedelta(days=14)).isoformat(),
                     end=(start + datetime.timedelta(days=33)).isoformat())

    def test_sick_leave_bands_hr_adjustments_and_rejection(self):
        sick_start = self.today - datetime.timedelta(days=30)
        sick = self.ask(kind="sick", start=sick_start.isoformat(), end=(sick_start + datetime.timedelta(days=20)).isoformat())
        rejected = decide_leave(actor=self.roche_user, request_id=sick["id"], action="reject", note="TEST ONLY no certificate")
        self.assertEqual(rejected["status"], "rejected")
        with self.assertRaisesMessage(InvalidTransaction, "Give the reason"):
            decide_leave(actor=self.admin, request_id=self.ask(kind="unpaid")["id"], action="reject")
        sick = self.ask(kind="sick", start=sick_start.isoformat(), end=(sick_start + datetime.timedelta(days=20)).isoformat())
        decide_leave(actor=self.admin, request_id=sick["id"], action="approve")
        bands = balance(self.employee(self.aisha))["sick"]
        self.assertEqual([band["used"] for band in bands["bands"]], [14, sick["days"] - 14, 0])
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only HR can change leave balances"):
            adjust_leave(actor=self.roche_user, data={"employee_id": self.aisha["id"], "days": "5", "reason": "x"})
        person = adjust_leave(actor=self.admin, data={"employee_id": self.aisha["id"], "days": "4.5",
                                                      "reason": "TEST ONLY worked two public holidays"})
        self.assertEqual((person["balance"]["adjusted"], person["balance"]["left"]), ("4.5", "49.5"))
        with self.assertRaisesMessage(InvalidTransaction, "whole or half days"):
            adjust_leave(actor=self.admin, data={"employee_id": self.aisha["id"], "days": "0.3", "reason": "x"})
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only HR can ask for leave on someone else's behalf"):
            self.ask(actor=self.roche_user, employee_id=self.aisha["id"])

    def test_people_at_the_same_level_are_not_away_together(self):
        start = monday_after(self.today)
        first = self.ask()  # Aisha, Staff in Sales, away start..start+4
        sara = self.add(full_name="TEST ONLY Sara Nasser", joined_on="2016-01-01")
        hr = lambda person, begin, days, kind="annual": request_leave(actor=self.admin, data={
            "employee_id": person["id"], "kind": kind, "start": begin.isoformat(),
            "end": (begin + datetime.timedelta(days=days - 1)).isoformat()})
        back = start + datetime.timedelta(days=5)
        with self.assertRaisesMessage(InvalidTransaction, "TEST ONLY Aisha Rahman, at the same level in Sales, is away"):
            hr(sara, start + datetime.timedelta(days=2), 3)
        with self.assertRaisesMessage(InvalidTransaction, f"it can start from {back + datetime.timedelta(days=7):%d %b %Y}"):
            hr(sara, back + datetime.timedelta(days=6), 3)
        with self.assertRaisesMessage(InvalidTransaction, "a week apart"):
            hr(sara, start - datetime.timedelta(days=9), 3)  # back only six days before Aisha leaves
        self.assertEqual(hr(sara, back + datetime.timedelta(days=7), 3)["status"], "requested", "A full week after she is back")
        self.assertEqual(hr(sara, start - datetime.timedelta(days=12), 3)["status"], "requested", "A week before she leaves")
        # Sick leave is never held back, and other levels or departments are not affected.
        ravi = self.add(full_name="TEST ONLY Ravi Kumar", joined_on="2016-01-01")
        self.assertEqual(hr(ravi, start, 2, kind="sick")["status"], "requested")
        manager = self.add(full_name="TEST ONLY Lina Farouk", joined_on="2016-01-01", job_title="Showroom manager")
        driver = self.add(full_name="TEST ONLY Waleed Saeed", joined_on="2016-01-01", department="Warehouse")
        self.assertEqual(hr(manager, start, 3)["status"], "requested")
        self.assertEqual(hr(driver, start, 3)["status"], "requested")
        # A special approval lets it through, first from the line manager, then from HR.
        with self.assertRaisesMessage(InvalidTransaction, "Give the reason for the special approval"):
            request_leave(actor=self.admin, data={"employee_id": sara["id"], "kind": "annual", "start": start.isoformat(),
                                                  "end": (start + datetime.timedelta(days=1)).isoformat(), "special_approval": True})
        special = self.ask(start=(start + datetime.timedelta(days=14)).isoformat(), end=(start + datetime.timedelta(days=15)).isoformat(),
                           special_approval=True, reason="TEST ONLY brother's wedding")
        self.assertIn("TEST ONLY Sara Nasser, at the same level in Sales, is away", special["special_approval"]["why"])
        self.assertEqual(special["special_approval"]["waiting_for"], "line manager")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only the person's manager or HR"):
            decide_leave(actor=self.admin, request_id=special["id"], action="approve")
        step = decide_leave(actor=self.roche_user, request_id=special["id"], action="approve")
        self.assertEqual((step["status"], step["special_approval"]["waiting_for"]), ("requested", "HR"))
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only the person's manager or HR"):
            decide_leave(actor=self.roche_user, request_id=special["id"], action="approve")
        done = decide_leave(actor=self.admin, request_id=special["id"], action="approve")
        self.assertEqual((done["status"], done["decided_by"]), ("approved", "Synthetic Demo Admin"))
        decide_leave(actor=self.admin, request_id=first["id"], action="cancel")
        self.assertEqual(hr(ravi, start + datetime.timedelta(days=2), 2)["status"], "requested", "A cancelled leave no longer blocks")

    def test_cancelling_and_the_api(self):
        request = self.ask()
        cancelled = decide_leave(actor=self.sales, request_id=request["id"], action="cancel")
        self.assertEqual(cancelled["status"], "cancelled")
        client = APIClient()
        client.force_login(self.sales)
        start = monday_after(self.today, 2)
        response = client.post("/api/hr/leave/", {"kind": "unpaid", "start": start.isoformat(), "end": start.isoformat()}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        overview = client.get("/api/hr/leave/").json()
        self.assertEqual((overview["me"]["name"], overview["can_adjust"]), ("TEST ONLY Aisha Rahman", False))
        client.force_login(self.roche_user)
        response = client.post(f"/api/hr/leave/{response.json()['id']}/", {"action": "approve"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        stranger = Employee.objects.get(pk=self.omar["id"])
        stranger.user = None
        stranger.save(update_fields=["user"])
        response = client.get("/api/hr/leave/")
        self.assertEqual(response.status_code, 403, "No staff record and no HR permission")
