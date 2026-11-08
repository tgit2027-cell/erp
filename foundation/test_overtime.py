"""Overtime: recorded before the work, approved by someone else, tied to a sales order; synthetic only."""

import datetime
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .leave import decide_leave, request_leave
from .models import BusinessUnitWorkCalendar, SalesQuote, UserActionGrant
from .overtime import decide_overtime, overtime_overview, prepare_overtime
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import save_employee
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action


def next_day(day, weekday_wanted):
    day += datetime.timedelta(days=1)
    while (day.weekday() == 4) != weekday_wanted:
        day += datetime.timedelta(days=1)
    return day


@override_settings(APP_ENV="demo")
class OvertimeTests(PurchasingFixtureMixin, TestCase):
    """Admin is HR; Omar (demo_roche) manages Aisha (demo_multi) and Ravi."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.get_or_create(user=cls.admin, business_unit=cls.unit, action=A.MANAGE_STAFF)
        BusinessUnitWorkCalendar.objects.update_or_create(business_unit=cls.unit, defaults={
            "closed_weekdays": [4], "closed_dates": [], "is_configured": True})

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()
        add = lambda **data: save_employee(actor=self.admin, business_unit=self.unit, data={
            "job_title": "Staff", "department": "Workshop", "nationality": "Indian", "joined_on": "2020-01-01", **data})
        self.omar = add(full_name="TEST ONLY Omar Haddad", user_id=self.roche_user.pk)
        self.aisha = add(full_name="TEST ONLY Aisha Rahman", user_id=self.sales.pk, manager_id=self.omar["id"])
        self.ravi = add(full_name="TEST ONLY Ravi Kumar", manager_id=self.omar["id"])
        self.stranger = add(full_name="TEST ONLY Joseph Mendoza")
        self.make_receipt(charges=[])
        self.order = SalesQuote.objects.get(pk=self.make_quote(key="ot-order")["quote_id"])
        SalesQuote.objects.filter(pk=self.order.pk).update(status=SalesQuote.Status.CONFIRMED)
        self.workday = next_day(self.today, weekday_wanted=False)
        self.friday = next_day(self.today, weekday_wanted=True)

    def prepare(self, actor=None, day=None, people=None, **extra):
        people = people or [(self.ravi, "2")]
        return prepare_overtime(actor=actor or self.roche_user, business_unit=self.unit, data={
            "sales_order_id": self.order.pk, "work_date": (day or self.workday).isoformat(),
            "work": "TEST ONLY install the wardrobe at the client's villa",
            "lines": [{"employee_id": person["id"], "hours": hours} for person, hours in people], **extra})

    def test_the_manager_records_it_and_hr_approves(self):
        request = self.prepare(people=[(self.ravi, "2"), (self.aisha, "1.5")])
        self.assertEqual((request["rate_percent"], request["rest_day"], request["total_hours"], request["order"]["customer"]),
                         (125, False, "3.5", self.order.customer.name))
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Someone other than the person who recorded"):
            decide_overtime(actor=self.roche_user, request_id=request["id"], action="approve")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "never someone working it"):
            decide_overtime(actor=self.sales, request_id=request["id"], action="approve")
        approved = decide_overtime(actor=self.admin, request_id=request["id"], action="approve")
        self.assertEqual(approved["status"], "approved")
        with self.assertRaisesMessage(InvalidTransaction, "already has overtime"):
            self.prepare()
        month = overtime_overview(actor=self.admin, business_unit=self.unit, month=self.workday.strftime("%Y-%m"))
        self.assertEqual([(row["id"], row["normal_hours"], row["people"]) for row in month["by_order"]],
                         [(self.order.pk, "3.5", 2)])

    def test_the_rules_before_the_work(self):
        with self.assertRaisesMessage(InvalidTransaction, "recorded before it is worked"):
            self.prepare(day=self.today - datetime.timedelta(days=1))
        with self.assertRaisesMessage(InvalidTransaction, "at most 2 hours of overtime on a working day"):
            self.prepare(people=[(self.ravi, "3")])
        rest = self.prepare(day=self.friday, people=[(self.ravi, "8")])
        self.assertEqual((rest["rate_percent"], rest["rest_day"]), (150, True))
        with self.assertRaisesMessage(WorkflowPermissionDenied, "is not in your team"):
            self.prepare(people=[(self.stranger, "1")])
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Nobody records overtime for themselves"):
            self.prepare(people=[(self.omar, "1")])
        with self.assertRaisesMessage(InvalidTransaction, "whole or half hours"):
            self.prepare(people=[(self.ravi, "0.3")])
        SalesQuote.objects.filter(pk=self.order.pk).update(status=SalesQuote.Status.PENDING_APPROVAL)
        with self.assertRaisesMessage(InvalidTransaction, "only be charged to a confirmed sales order"):
            self.prepare()
        SalesQuote.objects.filter(pk=self.order.pk).update(status=SalesQuote.Status.DELIVERED)
        leave = request_leave(actor=self.admin, data={"employee_id": self.ravi["id"], "kind": "unpaid",
                                                      "start": self.workday.isoformat(), "end": self.workday.isoformat()})
        decide_leave(actor=self.roche_user, request_id=leave["id"], action="approve")
        with self.assertRaisesMessage(InvalidTransaction, "is on approved leave"):
            self.prepare()

    def test_reject_needs_a_reason_and_cancelling(self):
        request = self.prepare()
        with self.assertRaisesMessage(InvalidTransaction, "Give the reason"):
            decide_overtime(actor=self.admin, request_id=request["id"], action="reject")
        rejected = decide_overtime(actor=self.admin, request_id=request["id"], action="reject", note="TEST ONLY not needed")
        self.assertEqual(rejected["status"], "rejected")
        again = self.prepare()
        self.assertTrue(again["can_cancel"])
        cancelled = decide_overtime(actor=self.roche_user, request_id=again["id"], action="cancel")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(overtime_overview(actor=self.admin, business_unit=self.unit, month=self.workday.strftime("%Y-%m"))["by_order"], [])

    def test_the_api(self):
        client = APIClient()
        client.force_login(self.roche_user)
        response = client.post("/api/hr/overtime/", {
            "sales_order_id": self.order.pk, "work_date": self.workday.isoformat(), "work": "TEST ONLY deliver and fit",
            "lines": [{"employee_id": self.ravi["id"], "hours": "2"}]}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        overview = client.get(f"/api/hr/overtime/?month={self.workday:%Y-%m}").json()
        self.assertEqual([person["name"] for person in overview["people"]], ["TEST ONLY Aisha Rahman", "TEST ONLY Ravi Kumar"],
                         "A manager picks from their own team")
        self.assertEqual(overview["orders"][0]["id"], self.order.pk)
        client.force_login(self.admin)
        response = client.post(f"/api/hr/overtime/{response.json()['id']}/", {"action": "approve"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
