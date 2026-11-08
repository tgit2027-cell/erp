"""Staff records: private, audited, never deleted; synthetic only."""

import csv
import datetime
import os
import tempfile
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Employee, EmployeeDocument, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import (
    add_document, import_existing_staff, record_leaving, save_employee, staff_detail, staff_overview,
)
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action


@override_settings(APP_ENV="demo")
class StaffRecordTests(PurchasingFixtureMixin, TestCase):
    """Admin keeps staff records; sales can only see them."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.get_or_create(user=cls.admin, business_unit=cls.unit, action=A.MANAGE_STAFF)
        UserActionGrant.objects.get_or_create(user=cls.sales, business_unit=cls.unit, action=A.VIEW_STAFF)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()

    def person(self, **data):
        payload = {"full_name": "TEST ONLY Aisha Rahman", "job_title": "Showroom consultant", "department": "Sales",
                   "nationality": "Jordanian", "joined_on": "2024-03-01", "grade": "S2", **data}
        return save_employee(actor=self.admin, business_unit=self.unit, data=payload)

    def test_records_are_private_and_every_change_is_recorded(self):
        aisha = self.person(work_email="Aisha@Tivoli.test", user_id=self.sales.pk)
        self.assertEqual((aisha["staff_number"], aisha["status"], aisha["work_email"], aisha["user"]["username"]),
                         ("EMP-00001", "active", "aisha@tivoli.test", self.sales.username))
        boss = self.person(full_name="TEST ONLY Omar Haddad", job_title="Showroom manager", user_id=None)
        aisha = save_employee(actor=self.admin, business_unit=self.unit, employee_id=aisha["id"],
                              data={**aisha, "user_id": self.sales.pk, "manager_id": boss["id"], "job_title": "Senior consultant"})
        self.assertEqual(aisha["history"][0]["change"], "changed")
        self.assertIn("job title: Showroom consultant → Senior consultant", aisha["history"][0]["what"])
        self.assertIn("manager: none → TEST ONLY Omar Haddad", aisha["history"][0]["what"])
        with self.assertRaisesMessage(InvalidTransaction, "report to themselves or to someone who reports to them"):
            save_employee(actor=self.admin, business_unit=self.unit, employee_id=boss["id"],
                          data={**boss, "manager_id": aisha["id"]})
        with self.assertRaisesMessage(InvalidTransaction, "Nothing was changed"):
            save_employee(actor=self.admin, business_unit=self.unit, employee_id=aisha["id"],
                          data={**aisha, "user_id": self.sales.pk, "manager_id": boss["id"]})
        # Sales can see but not change; the ledger permission does not open staff records.
        listed = staff_overview(actor=self.sales, business_unit=self.unit)
        self.assertEqual([row["full_name"] for row in listed["staff"]], ["TEST ONLY Aisha Rahman", "TEST ONLY Omar Haddad"])
        self.assertFalse(listed["can_manage"])
        self.assertEqual(listed["managers"], [])
        with self.assertRaisesMessage(WorkflowPermissionDenied, "'add and change staff records'"):
            save_employee(actor=self.sales, business_unit=self.unit, data={"full_name": "TEST ONLY Someone"})
        UserActionGrant.objects.create(user=self.sales, business_unit=self.roche, action=A.VIEW_LEDGER)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "permission to see staff records"):
            staff_overview(actor=self.sales, business_unit=self.roche)
        with self.assertRaisesMessage(InvalidTransaction, "not found in this business unit"):
            UserActionGrant.objects.create(user=self.sales, business_unit=self.roche, action=A.VIEW_STAFF)
            staff_detail(actor=self.sales, business_unit=self.roche, employee_id=aisha["id"])

    def test_documents_and_contracts_warn_before_they_end(self):
        soon = self.today + datetime.timedelta(days=20)
        aisha = self.person(contract="limited", contract_end=(self.today + datetime.timedelta(days=45)).isoformat(),
                            probation_end=(self.today + datetime.timedelta(days=10)).isoformat(), joined_on="2026-01-01")
        with self.assertRaisesMessage(InvalidTransaction, "11 digits and starts with 2 or 3"):
            add_document(actor=self.admin, business_unit=self.unit, employee_id=aisha["id"],
                         data={"kind": "qid", "number": "1234", "expires_on": soon.isoformat()})
        with self.assertRaisesMessage(InvalidTransaction, "Give the expiry date"):
            add_document(actor=self.admin, business_unit=self.unit, employee_id=aisha["id"], data={"kind": "passport", "number": "N1"})
        add_document(actor=self.admin, business_unit=self.unit, employee_id=aisha["id"],
                     data={"kind": "qid", "number": "28940012345", "expires_on": soon.isoformat()})
        aisha = add_document(actor=self.admin, business_unit=self.unit, employee_id=aisha["id"],
                             data={"kind": "passport", "number": "n 123456", "expires_on": "2020-01-01"})
        self.assertEqual([row["what"] for row in aisha["warnings"]],
                         ["Passport N123456", "Probation", "Qatar ID (QID) 28940012345", "Fixed-term contract"])
        self.assertTrue(aisha["warnings"][0]["expired"])
        # Renewing replaces the old document of the same kind.
        later = (self.today + datetime.timedelta(days=700)).isoformat()
        aisha = add_document(actor=self.admin, business_unit=self.unit, employee_id=aisha["id"],
                             data={"kind": "qid", "number": "28940012345", "expires_on": later})
        self.assertEqual([row["kind"] for row in aisha["documents"]], ["passport", "qid"])
        self.assertEqual(aisha["history"][0]["change"], "renewed document")
        self.assertEqual(EmployeeDocument.objects.filter(employee_id=aisha["id"]).count(), 3, "The old QID is kept")
        other = self.person(full_name="TEST ONLY Other")
        with self.assertRaisesMessage(InvalidTransaction, "already on another staff record"):
            add_document(actor=self.admin, business_unit=self.unit, employee_id=other["id"],
                         data={"kind": "qid", "number": "28940012345", "expires_on": later})
        totals = staff_overview(actor=self.admin, business_unit=self.unit)["totals"]
        self.assertEqual((totals["expiring"], totals["expired"]), (2, 1))
        with self.assertRaisesMessage(InvalidTransaction, "end date after the joining date"):
            self.person(contract="limited")

    def test_leaving_keeps_the_record_and_notice_can_be_withdrawn(self):
        boss = self.person(full_name="TEST ONLY Omar Haddad")
        aisha = self.person(manager_id=boss["id"])
        with self.assertRaisesMessage(InvalidTransaction, "team a new manager first"):
            record_leaving(actor=self.admin, business_unit=self.unit, employee_id=boss["id"],
                           last_day=(self.today - datetime.timedelta(days=1)).isoformat(), reason="TEST ONLY resigned")
        notice = record_leaving(actor=self.admin, business_unit=self.unit, employee_id=aisha["id"],
                                last_day=(self.today + datetime.timedelta(days=30)).isoformat(), reason="TEST ONLY resigned")
        self.assertEqual(notice["status"], "notice")
        back = record_leaving(actor=self.admin, business_unit=self.unit, employee_id=aisha["id"], cancel=True)
        self.assertEqual((back["status"], back["last_day"]), ("active", None))
        record_leaving(actor=self.admin, business_unit=self.unit, employee_id=aisha["id"],
                       last_day=(self.today + datetime.timedelta(days=2)).isoformat(), reason="TEST ONLY resigned")
        Employee.objects.filter(pk=aisha["id"]).update(last_day=self.today - datetime.timedelta(days=1))
        overview = staff_overview(actor=self.admin, business_unit=self.unit)
        self.assertEqual([row["full_name"] for row in overview["staff"]], ["TEST ONLY Omar Haddad"])
        left = staff_overview(actor=self.admin, business_unit=self.unit, status="left")["staff"]
        self.assertEqual((left[0]["full_name"], left[0]["status"]), ("TEST ONLY Aisha Rahman", "left"))
        self.assertEqual([row["change"] for row in staff_detail(actor=self.admin, business_unit=self.unit, employee_id=aisha["id"])["history"]],
                         ["notice", "notice withdrawn", "notice", "added"])

    def test_existing_staff_keep_their_numbers_and_new_people_continue_the_series(self):
        row = {"full_name": "TEST ONLY Aisha Rahman", "job_title": "Showroom consultant", "department": "Sales",
               "nationality": "Jordanian", "joined_on": "2019-03-01"}
        with self.assertRaisesMessage(InvalidTransaction, "Line 3: Employee code EMP-01234 is already used"):
            import_existing_staff(actor=self.admin, business_unit=self.unit, rows=[
                {**row, "employee_number": "01234"}, {**row, "employee_number": "EMP-01234"}])
        with self.assertRaisesMessage(InvalidTransaction, "Line 2: Give the employee code as a number of up to 5 digits"):
            import_existing_staff(actor=self.admin, business_unit=self.unit, rows=[{**row, "employee_number": "123456"}])
        self.assertFalse(Employee.objects.exists(), "A bad row stops the whole list")
        with self.assertRaises(WorkflowPermissionDenied):
            import_existing_staff(actor=self.sales, business_unit=self.unit, rows=[])
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "staff.csv")
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["employee_number", *row])
            writer.writeheader()
            writer.writerow({"employee_number": "01234", **row})
            writer.writerow({"employee_number": "875", **row, "full_name": "TEST ONLY Omar Haddad"})
        out = StringIO()
        call_command("import_staff", path, unit=self.unit.code, user=self.admin.username, stdout=out)
        self.assertIn("Imported 2 people. The next person added gets EMP-01235.", out.getvalue())
        self.assertEqual(sorted(Employee.objects.values_list("staff_number", flat=True)), ["EMP-00875", "EMP-01234"])
        added = self.person(full_name="TEST ONLY Ravi Kumar", staff_number="00001")
        self.assertEqual(added["staff_number"], "EMP-01235", "Always the next number; a number sent from the screen is ignored")
        with self.assertRaisesMessage(CommandError, "Nothing was imported. Line 2: Employee code EMP-01234 is already used"):
            call_command("import_staff", path, unit=self.unit.code, user=self.admin.username, stdout=StringIO())

    def test_the_api_adds_and_lists_staff(self):
        client = APIClient()
        client.force_login(self.admin)
        response = client.post(f"/api/hr/staff/?unit={self.unit.pk}", {
            "full_name": "TEST ONLY Aisha Rahman", "job_title": "Consultant", "department": "Sales", "nationality": "Jordanian",
            "joined_on": "2024-03-01"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        employee_id = response.json()["id"]
        response = client.post(f"/api/hr/staff/{employee_id}/?unit={self.unit.pk}",
                               {"action": "add_document", "kind": "qid", "number": "28940012345", "expires_on": "2030-01-01"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        response = client.get(f"/api/hr/staff/?unit={self.unit.pk}&search=2894001")
        self.assertEqual([row["id"] for row in response.json()["staff"]], [employee_id])
        client.force_login(self.sales)
        response = client.post(f"/api/hr/staff/{employee_id}/?unit={self.unit.pk}", {"action": "leaving"}, format="json")
        self.assertEqual(response.status_code, 403)
