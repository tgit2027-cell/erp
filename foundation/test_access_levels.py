"""Preset access levels (Master Admin down to Sales) and custom users; synthetic only."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .access import active_grants
from .access_levels import FINANCE, PAYROLL, PRESETS, can_manage_users, level_of
from .models import AccessLevelAssignment, BusinessUnit, BusinessUnitRoleAssignment, DemoUserMarker, UserActionGrant
from .navigation_destinations import function_is_available
from .purchasing_services import WorkflowPermissionDenied
from .teams import teams_overview
from .test_stock_transfer_requests import TransferRequestFixtureMixin
from .transaction_services import InvalidTransaction
from .user_admin import add_person, change_person, copy_unit_access, people_overview, person_detail, reset_password

Action = UserActionGrant.Action
Level = AccessLevelAssignment.Level
STRONG = "Correct-Horse-Battery-71"


@override_settings(APP_ENV="demo")
class AccessLevelTests(TransferRequestFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_transfer_fixture()
        cls.master = get_user_model().objects.create_user(
            username="test_master", password="unused-test-password", is_staff=True, is_superuser=True)
        DemoUserMarker.objects.create(user=cls.master)
        cls.newcomer = cls._person("test_newcomer")
        cls.units = list(BusinessUnit.objects.filter(is_active=True, is_synthetic=True))

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)

    def set_level(self, person, level, actor=None, unit_ids=None):
        return change_person(actor=actor or self.master, user_id=person.pk,
                             change={"kind": "level", "level": level, "unit_ids": unit_ids or []})

    def actions(self, person, unit=None):
        grants = active_grants(person, personal_only=True)
        if unit is not None:
            grants = grants.filter(business_unit=unit)
        return set(grants.values_list("action", flat=True))

    def roles(self, person):
        return set(BusinessUnitRoleAssignment.objects.filter(user=person, revoked_at__isnull=True)
                   .values_list("business_unit__code", "role"))

    def general_admin(self):
        person = self._person("test_general_admin")
        self.set_level(person, Level.GENERAL_ADMIN)
        person.refresh_from_db()
        return person

    def test_every_existing_superuser_is_a_master_admin(self):
        self.assertEqual(level_of(self.master), Level.MASTER_ADMIN)
        self.assertEqual(level_of(self.newcomer), "")
        self.assertEqual(level_of(self.requester), Level.CUSTOM)  # given permissions one by one before levels existed

    def test_master_admin_gets_everything_everywhere(self):
        # Setup lock (Nov 2026): no screen makes a new Master Admin. IT adds one at the owner's request.
        with self.assertRaisesMessage(WorkflowPermissionDenied, "A new Master Admin can only be added by IT"):
            self.set_level(self.newcomer, Level.MASTER_ADMIN)
        self.assertNotIn("master_admin", person_detail(actor=self.master, user_id=self.newcomer.pk)["access_level"]["assignable"])
        get_user_model().objects.filter(pk=self.newcomer.pk).update(is_superuser=True, is_staff=True)
        self.set_level(self.newcomer, Level.MASTER_ADMIN)
        person = get_user_model().objects.get(pk=self.newcomer.pk)
        self.assertTrue(person.is_superuser and person.is_staff)
        for unit in self.units:
            self.assertTrue(set(PRESETS[Level.MASTER_ADMIN]["unit_actions"]) <= self.actions(person, unit))
        self.assertTrue({Action.OPEN_SUPPLIER, Action.APPROVE_SUPPLIER} <= self.actions(person))
        self.assertTrue(person.has_perm("chat.approve_chat_access") and person.has_perm("chat.request_chat_access"))
        self.assertEqual(len(self.roles(person)), len(self.units) * len(BusinessUnitRoleAssignment.Role))

    def test_general_admin_has_no_finance_or_payroll(self):
        person = self.general_admin()
        have = self.actions(person)
        self.assertFalse(have & (set(FINANCE) | set(PAYROLL) | {Action.VIEW_COSTS, Action.APPROVE_WRITE_OFF}))
        self.assertTrue({Action.READ_STOCK, Action.MANAGE_STAFF, Action.OPEN_SUPPLIER} <= have)
        self.assertFalse(person.is_superuser)
        self.assertTrue(can_manage_users(person))

    def test_finance_and_hr_admins_split_payroll(self):
        finance, hr = self._person("test_finance_admin"), self._person("test_hr_admin")
        self.set_level(finance, Level.FINANCE_ADMIN)
        self.set_level(hr, Level.HR_ADMIN)
        self.assertTrue(set(FINANCE) <= self.actions(finance, self.unit))
        self.assertIn(Action.APPROVE_PAY, self.actions(finance))
        self.assertNotIn(Action.MANAGE_PAY, self.actions(finance))
        self.assertEqual(self.actions(hr, self.unit), {Action.VIEW_STAFF, Action.MANAGE_STAFF, Action.VIEW_PAY, Action.MANAGE_PAY, Action.POST_PORTAL})
        self.assertTrue(get_user_model().objects.get(pk=hr.pk).has_perm("chat.request_chat_access"))
        self.assertFalse(can_manage_users(finance) or can_manage_users(hr))

    def test_unit_levels_apply_only_in_the_chosen_units_and_replace_what_was_there(self):
        self.set_level(self.requester, Level.SALES, unit_ids=[self.roche.pk])
        self.assertEqual(self.actions(self.requester, self.unit), set())  # old one-by-one permission replaced
        self.assertEqual(self.actions(self.requester, self.roche), {Action.READ_STOCK, Action.CREATE_ORDER, Action.REQUEST_TRANSFER})
        self.assertEqual(self.roles(self.requester), {(self.roche.code, "salesperson")})
        self.set_level(self.requester, Level.HEAD, unit_ids=[self.unit.pk, self.roche.pk])
        self.assertEqual(self.roles(self.requester), {(self.unit.code, "bu_gm"), (self.roche.code, "bu_gm")})
        self.assertIn(Action.APPROVE_EXPENSE, self.actions(self.requester, self.unit))
        detail = person_detail(actor=self.master, user_id=self.requester.pk)["access_level"]
        self.assertEqual((detail["level"], detail["unit_ids"]), ("head", sorted([self.unit.pk, self.roche.pk])))
        with self.assertRaisesMessage(InvalidTransaction, "at least one business unit"):
            self.set_level(self.requester, Level.MANAGER)

    def test_team_permissions_are_left_alone(self):
        from .teams import create_team, change_team
        team = create_team(actor=self.master, name="TEST Sales team", member_ids=[self.newcomer.pk])
        change_team(actor=self.master, team_id=team["team"]["id"],
                    change={"kind": "action", "unit_id": self.unit.pk, "action": Action.VIEW_COSTS, "enabled": True})
        self.set_level(self.newcomer, Level.SALES, unit_ids=[self.unit.pk])
        self.assertIn(Action.VIEW_COSTS, set(active_grants(self.newcomer).values_list("action", flat=True)))

    def test_changing_a_preset_person_one_by_one_makes_them_custom(self):
        self.set_level(self.newcomer, Level.MANAGER, unit_ids=[self.unit.pk])
        detail = change_person(actor=self.master, user_id=self.newcomer.pk,
                               change={"kind": "action", "unit_id": self.unit.pk, "action": Action.VIEW_COSTS, "enabled": True})
        self.assertEqual((detail["access_level"]["level"], detail["access_level"]["based_on"]), ("custom", "manager"))
        self.assertIn(Action.VIEW_COSTS, self.actions(self.newcomer, self.unit))
        # Choosing Custom directly keeps what the person has.
        self.set_level(self.requester, Level.CUSTOM)
        self.assertEqual(self.actions(self.requester), {Action.REQUEST_TRANSFER})

    def test_only_a_master_admin_makes_custom_users_or_admins(self):
        general = self.general_admin()
        for change in ({"kind": "action", "unit_id": self.unit.pk, "action": Action.READ_STOCK, "enabled": True},
                       {"kind": "role", "unit_id": self.unit.pk, "role": "manager", "enabled": True},
                       {"kind": "chat_role", "role": "approver", "enabled": True},
                       {"kind": "level", "level": Level.CUSTOM},
                       {"kind": "level", "level": Level.FINANCE_ADMIN},
                       {"kind": "level", "level": Level.MASTER_ADMIN}):
            with self.assertRaises(WorkflowPermissionDenied):
                change_person(actor=general, user_id=self.newcomer.pk, change=change)
        with self.assertRaises(WorkflowPermissionDenied):
            copy_unit_access(actor=general, user_id=self.newcomer.pk, from_user_id=self.requester.pk, unit_id=self.unit.pk)
        with self.assertRaises(WorkflowPermissionDenied):
            add_person(actor=general, username="test_sneaky", password=STRONG, level=Level.GENERAL_ADMIN)
        self.assertFalse(get_user_model().objects.filter(username="test_sneaky").exists())

    def test_general_admin_adds_people_onto_head_manager_or_sales(self):
        general = self.general_admin()
        self.assertTrue(people_overview(actor=general)["people"])
        detail = add_person(actor=general, username="test_new_seller", password=STRONG, level=Level.SALES,
                            unit_ids=[self.unit.pk])
        self.assertEqual(detail["person"]["level"], "sales")
        self.assertEqual(detail["access_level"]["assignable"], ["head", "manager", "sales"])
        self.assertFalse(detail["access_level"]["can_customize"])
        seller = get_user_model().objects.get(username="test_new_seller")
        self.set_level(seller, Level.MANAGER, actor=general, unit_ids=[self.unit.pk])
        reset_password(actor=general, user_id=seller.pk, password="Another-Long-Phrase-82")
        change_person(actor=general, user_id=seller.pk, change={"kind": "active", "enabled": False})
        # But not admins or custom users.
        for person in (self.master, self.requester):
            with self.assertRaises(WorkflowPermissionDenied):
                reset_password(actor=general, user_id=person.pk, password="Another-Long-Phrase-82")
            with self.assertRaises(WorkflowPermissionDenied):
                change_person(actor=general, user_id=person.pk, change={"kind": "active", "enabled": False})
        self.assertFalse(person_detail(actor=general, user_id=self.master.pk)["access_level"]["can_edit"])

    def test_the_last_master_admin_cannot_be_removed(self):
        with self.assertRaisesMessage(InvalidTransaction, "your own Master Admin"):
            self.set_level(self.master, Level.GENERAL_ADMIN)
        other = self._person("test_second_master")
        get_user_model().objects.filter(pk=other.pk).update(is_superuser=True, is_staff=True)
        other.refresh_from_db()
        self.set_level(other, Level.SALES, unit_ids=[self.unit.pk])
        other.refresh_from_db()
        self.assertFalse(other.is_superuser)

    def test_screen_visibility_and_api(self):
        general = self.general_admin()
        self.assertTrue(function_is_available("governance.users", is_staff=False, is_superuser=False,
                                              manages_panels=False, reads_stock=True, manages_users=True))
        client = APIClient()
        client.force_login(general)
        self.assertEqual(client.get("/api/governance/people/").status_code, 200)
        me = client.get("/api/auth/me/").data
        self.assertTrue(me["can_manage_users"])
        refused = client.post(f"/api/governance/people/{self.newcomer.pk}/",
                              {"kind": "level", "level": "custom"}, format="json")
        self.assertEqual(refused.status_code, 403)
        client.force_login(self.requester)
        self.assertEqual(client.get("/api/governance/people/").status_code, 403)
        self.assertFalse(client.get("/api/auth/me/").data["can_manage_users"])

    def test_general_admin_manages_teams_within_their_own_limits(self):
        from .teams import change_team, create_team
        general = self.general_admin()
        self.assertTrue(teams_overview(actor=general) is not None)
        team = create_team(actor=general, name="TEST Showroom team", member_ids=[self.newcomer.pk])
        team_id = team["team"]["id"]
        self.assertTrue(team["can_change"])
        self.assertIn(Action.READ_STOCK, team["allowed_actions"])
        detail = change_team(actor=general, team_id=team_id,
                             change={"kind": "action", "unit_id": self.unit.pk, "action": Action.READ_STOCK, "enabled": True})
        self.assertIn(Action.READ_STOCK, detail["units"][[row["id"] for row in detail["units"]].index(self.unit.pk)]["actions"])
        # Finance, payroll and company-wide permissions stay with the Master Admin.
        for action in (Action.PAY_SUPPLIER, Action.MANAGE_PAY, Action.CLOSE_MONTH):
            with self.assertRaisesMessage(WorkflowPermissionDenied, "Only the Master Admin can give"):
                change_team(actor=general, team_id=team_id,
                            change={"kind": "action", "unit_id": self.unit.pk, "action": action, "enabled": True})
        # Admins and custom users are not theirs to move.
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only the Master Admin can change"):
            change_team(actor=general, team_id=team_id, change={"kind": "member", "user_id": self.master.pk, "enabled": True})
        # Once the Master Admin gives the team a finance permission, the General Admin can no longer change it.
        change_team(actor=self.master, team_id=team_id,
                    change={"kind": "action", "unit_id": self.unit.pk, "action": Action.PAY_SUPPLIER, "enabled": True})
        from .teams import team_detail
        self.assertFalse(team_detail(actor=general, team_id=team_id)["can_change"])
        for change in ({"kind": "details", "name": "TEST Renamed"}, {"kind": "active", "enabled": False},
                       {"kind": "member", "user_id": self.newcomer.pk, "enabled": False}):
            with self.assertRaisesMessage(WorkflowPermissionDenied, "only the Master Admin can change it"):
                change_team(actor=general, team_id=team_id, change=change)

    def test_general_admin_changes_only_stock_transfer_permissions(self):
        from .transfer_permissions import set_transfer_permission, transfer_permission_overview
        general = self.general_admin()
        overview = transfer_permission_overview(actor=general, business_unit=self.unit)
        flags = {row["action"]: row["can_change"] for row in overview["actions"]}
        self.assertTrue(flags[Action.REQUEST_TRANSFER])
        self.assertFalse(flags[Action.PAY_SUPPLIER])
        people = {row["id"]: row["can_change"] for row in overview["users"]}
        self.assertFalse(people[self.master.pk])
        set_transfer_permission(actor=general, business_unit=self.unit, user_id=self.newcomer.pk,
                                action=Action.REQUEST_TRANSFER, enabled=True)
        self.assertIn(Action.REQUEST_TRANSFER, self.actions(self.newcomer, self.unit))
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only the Master Admin can change this permission."):
            set_transfer_permission(actor=general, business_unit=self.unit, user_id=self.newcomer.pk,
                                    action=Action.PAY_SUPPLIER, enabled=True)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "this person's permissions"):
            set_transfer_permission(actor=general, business_unit=self.unit, user_id=self.requester.pk,
                                    action=Action.APPROVE_TRANSFER, enabled=True)
        self.assertTrue(function_is_available("governance.transfer-permissions", is_staff=False, is_superuser=False,
                                              manages_panels=False, reads_stock=True, manages_users=True))

    def test_general_admin_sees_approval_limits_but_cannot_change_them(self):
        from .approval_limits import overview, set_discount
        general = self.general_admin()
        self.assertFalse(overview(actor=general, business_unit=self.unit)["can_change"])
        self.assertTrue(overview(actor=self.master, business_unit=self.unit)["can_change"])
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only the Master Admin can change approval limits"):
            set_discount(actor=general, business_unit=self.unit, role="manager", percent="5")
