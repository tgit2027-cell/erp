"""Users and permissions in one place; synthetic only."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from audit.models import AuditEvent

from .models import (
    BusinessUnitRoleAssignment, DemoUserMarker, ReportingRole, ReportingRoleMembership, TransferPermissionAudit,
    UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied
from .test_stock_transfer_requests import TransferRequestFixtureMixin
from .transaction_services import InvalidTransaction
from .user_admin import (
    COMPANY_ACTIONS, UNIT_GROUPS, add_person, change_person, copy_unit_access, people_overview, person_detail,
    reset_password,
)

Action = UserActionGrant.Action
STRONG = "Correct-Horse-Battery-71"


@override_settings(APP_ENV="demo")
class UserAdminTests(TransferRequestFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_transfer_fixture()
        cls.sysadmin = get_user_model().objects.create_user(
            username="test_sysadmin", password="unused-test-password", is_staff=True, is_superuser=True)
        DemoUserMarker.objects.create(user=cls.sysadmin)
        cls.newcomer = cls._person("test_newcomer")

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)

    def change(self, **change):
        return change_person(actor=self.sysadmin, user_id=self.newcomer.pk, change=change)

    def unit_row(self, detail, unit=None):
        return next(row for row in detail["units"] if row["id"] == (unit or self.unit).pk)

    def test_every_permission_appears_once_on_the_screen(self):
        listed = [action for _, actions in UNIT_GROUPS for action in actions] + COMPANY_ACTIONS
        self.assertEqual(sorted(listed), sorted(Action.values))

    def test_only_a_system_administrator_may_see_or_change_people(self):
        for call in (lambda: people_overview(actor=self.requester),
                     lambda: person_detail(actor=self.requester, user_id=self.newcomer.pk),
                     lambda: change_person(actor=self.requester, user_id=self.newcomer.pk,
                                           change={"kind": "active", "enabled": False})):
            with self.assertRaises(WorkflowPermissionDenied):
                call()
        client = APIClient()
        client.force_login(self.requester)
        self.assertEqual(client.get("/api/governance/people/").status_code, 403)

    def test_list_shows_each_person_and_where_they_have_access(self):
        people = {row["username"]: row for row in people_overview(actor=self.sysadmin)["people"]}
        self.assertEqual((people["test_showroom_admin"]["units"], people["test_showroom_admin"]["permission_count"]),
                         ([self.unit.code], 1))
        self.assertEqual(people["test_newcomer"]["units"], [])
        self.assertEqual([row["username"] for row in people_overview(actor=self.sysadmin, search="manager")["people"]],
                         ["test_showroom_manager"])

    def test_unit_permissions_and_approval_roles_are_changed_and_recorded(self):
        detail = self.change(kind="action", unit_id=self.unit.pk, action=Action.READ_STOCK, enabled=True)
        detail = self.change(kind="action", unit_id=self.unit.pk, action=Action.MANAGE_CASH, enabled=True)
        self.assertEqual(self.unit_row(detail)["actions"], ["manage_cash", "read_stock"])
        detail = self.change(kind="role", unit_id=self.unit.pk, role="manager", enabled=True)
        self.assertEqual(self.unit_row(detail)["roles"], ["manager"])
        self.change(kind="role", unit_id=self.unit.pk, role="manager", enabled=True)  # no duplicate
        self.assertEqual(BusinessUnitRoleAssignment.objects.filter(user=self.newcomer, revoked_at__isnull=True).count(), 1)
        detail = self.change(kind="role", unit_id=self.unit.pk, role="manager", enabled=False)
        self.assertEqual(self.unit_row(detail)["roles"], [])
        detail = self.change(kind="action", action=Action.OPEN_SUPPLIER, enabled=True)
        self.assertTrue(next(row for row in detail["company"] if row["action"] == "open_supplier")["on"])
        self.assertEqual(TransferPermissionAudit.objects.filter(target_user=self.newcomer).count(), 3)
        whats = [row["what"] for row in detail["history"]]
        self.assertIn(f"Manager in {self.unit.code}", whats)
        self.assertIn(f"Record bank and cash in {self.unit.code}", whats)
        self.assertEqual(AuditEvent.objects.filter(user=self.newcomer, detail__by="test_sysadmin").count(), 2)
        with self.assertRaisesMessage(InvalidTransaction, "approval role"):
            self.change(kind="role", unit_id=self.unit.pk, role="king", enabled=True)

    def test_reporting_and_chat_roles_and_switching_an_account_off(self):
        role = ReportingRole.objects.create(code="test-finance", name="TEST ONLY Finance", created_by=self.sysadmin)
        detail = self.change(kind="reporting_role", role_id=role.pk, enabled=True)
        self.assertTrue(next(row for row in detail["reporting_roles"] if row["id"] == role.pk)["on"])
        self.assertTrue(ReportingRoleMembership.objects.filter(user=self.newcomer, role=role, revoked_at__isnull=True).exists())
        detail = self.change(kind="chat_role", role="approver", enabled=True)
        self.assertEqual({row["role"]: row["on"] for row in detail["chat_roles"]}, {"requester": False, "approver": True})
        self.assertTrue(get_user_model().objects.get(pk=self.newcomer.pk).has_perm("chat.approve_chat_access"))
        detail = self.change(kind="active", enabled=False)
        self.assertFalse(detail["person"]["is_active"])
        self.assertTrue(AuditEvent.objects.filter(user=self.newcomer, detail__changed=["is_active"]).exists())
        with self.assertRaisesMessage(InvalidTransaction, "your own account"):
            change_person(actor=self.sysadmin, user_id=self.sysadmin.pk, change={"kind": "active", "enabled": False})

    def test_copying_a_colleagues_access_in_a_unit(self):
        BusinessUnitRoleAssignment.objects.create(user=self.manager, business_unit=self.unit, role="manager", assigned_by=self.sysadmin)
        detail = copy_unit_access(actor=self.sysadmin, user_id=self.newcomer.pk, from_user_id=self.manager.pk, unit_id=self.unit.pk)
        self.assertEqual((self.unit_row(detail)["actions"], self.unit_row(detail)["roles"]), (["approve_transfer"], ["manager"]))

    def test_adding_a_person_and_setting_a_temporary_password(self):
        with self.assertRaises(InvalidTransaction):
            add_person(actor=self.sysadmin, username="test_new_buyer", password="short")
        detail = add_person(actor=self.sysadmin, username="test_new_buyer", first_name="Test", last_name="Buyer",
                            email="buyer@example.invalid", password=STRONG)
        person = get_user_model().objects.get(username="test_new_buyer")
        self.assertTrue(person.check_password(STRONG) and DemoUserMarker.objects.filter(user=person).exists())
        self.assertEqual((detail["person"]["name"], detail["person"]["units"]), ("Test Buyer", []))
        with self.assertRaisesMessage(InvalidTransaction, "already taken"):
            add_person(actor=self.sysadmin, username="TEST_NEW_BUYER", password=STRONG)
        reset_password(actor=self.sysadmin, user_id=person.pk, password="Another-Long-Phrase-82")
        person.refresh_from_db()
        self.assertTrue(person.check_password("Another-Long-Phrase-82"))

    def test_api_round_trip(self):
        client = APIClient()
        client.force_login(self.sysadmin)
        listing = client.get("/api/governance/people/")
        self.assertEqual(listing.status_code, 200)
        changed = client.post(f"/api/governance/people/{self.newcomer.pk}/", {
            "kind": "action", "unit_id": self.unit.pk, "action": "read_stock", "enabled": True}, format="json")
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(self.unit_row(changed.data)["actions"], ["read_stock"])
        copied = client.post(f"/api/governance/people/{self.newcomer.pk}/", {"copy_from": self.requester.pk, "unit_id": self.unit.pk}, format="json")
        self.assertEqual(self.unit_row(copied.data)["actions"], ["read_stock", "request_transfer"])
        created = client.post("/api/governance/people/", {"username": "test_api_person", "password": STRONG}, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        reset = client.post(f"/api/governance/people/{created.data['person']['id']}/", {"password": "weak"}, format="json")
        self.assertEqual(reset.status_code, 400)
