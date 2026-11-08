"""Teams and groups: shared permissions and a team chat; synthetic only."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.test import APIClient

from chat import services as chat
from chat.models import Conversation

from .access import active_grants, context_units
from .models import DemoUserMarker, Team, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .teams import change_team, create_team, team_detail, teams_overview
from .test_stock_transfer_requests import TransferRequestFixtureMixin
from .transaction_services import InvalidTransaction
from .transfer_permissions import set_transfer_permission
from .user_admin import change_person, people_overview, person_detail

Action = UserActionGrant.Action


@override_settings(APP_ENV="demo")
class TeamTests(TransferRequestFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_transfer_fixture()
        cls.sysadmin = get_user_model().objects.create_user(
            username="test_sysadmin", password="unused-test-password", is_staff=True, is_superuser=True)
        DemoUserMarker.objects.create(user=cls.sysadmin)
        cls.newcomer = cls._person("test_newcomer")
        cls.second = cls._person("test_second_seller")

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)

    def sales_team(self, *members):
        detail = create_team(actor=self.sysadmin, name="TEST ONLY Sales", description="Showroom sales",
                             member_ids=[person.pk for person in members])
        team_id = detail["team"]["id"]
        for action in (Action.READ_STOCK, Action.CREATE_ORDER):
            change_team(actor=self.sysadmin, team_id=team_id,
                        change={"kind": "action", "unit_id": self.unit.pk, "action": action, "enabled": True})
        return team_id

    def change(self, team_id, **change):
        return change_team(actor=self.sysadmin, team_id=team_id, change=change)

    def can(self, person, action):
        return active_grants(person, action).filter(business_unit=self.unit).exists()

    def test_only_a_system_administrator_may_see_or_change_teams(self):
        team_id = self.sales_team()
        for call in (lambda: teams_overview(actor=self.requester),
                     lambda: team_detail(actor=self.requester, team_id=team_id),
                     lambda: create_team(actor=self.requester, name="Mine"),
                     lambda: change_team(actor=self.requester, team_id=team_id,
                                         change={"kind": "active", "enabled": False})):
            with self.assertRaises(WorkflowPermissionDenied):
                call()
        client = APIClient()
        client.force_login(self.requester)
        self.assertEqual(client.get("/api/governance/teams/").status_code, 403)
        client.force_login(self.sysadmin)
        response = client.get(f"/api/governance/teams/{team_id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["team"]["name"], "TEST ONLY Sales")

    def test_members_get_the_team_permissions_on_top_of_their_own(self):
        set_transfer_permission(actor=self.sysadmin, business_unit=self.unit, user_id=self.newcomer.pk,
                                action=Action.COUNT_STOCK, enabled=True)
        team_id = self.sales_team(self.newcomer)
        self.assertTrue(self.can(self.newcomer, Action.CREATE_ORDER))
        self.assertTrue(self.can(self.newcomer, Action.COUNT_STOCK))
        self.assertIn(self.unit, context_units(self.newcomer))
        self.assertFalse(self.can(self.second, Action.CREATE_ORDER))
        # Adding someone is enough to give them everything the team has.
        self.change(team_id, kind="member", user_id=self.second.pk, enabled=True)
        self.assertTrue(self.can(self.second, Action.READ_STOCK))
        # Taking them out takes the team's permissions away; their own stay.
        self.change(team_id, kind="member", user_id=self.newcomer.pk, enabled=False)
        self.assertFalse(self.can(self.newcomer, Action.CREATE_ORDER))
        self.assertTrue(self.can(self.newcomer, Action.COUNT_STOCK))
        # Switching a team permission off affects every member at once.
        self.change(team_id, kind="action", unit_id=self.unit.pk, action=Action.READ_STOCK, enabled=False)
        self.assertFalse(self.can(self.second, Action.READ_STOCK))
        overview = teams_overview(actor=self.sysadmin)["teams"][0]
        self.assertEqual((overview["members"], overview["permissions"]), (1, 1))
        history = [row["change"] + " " + row["what"] for row in team_detail(actor=self.sysadmin, team_id=team_id)["history"]]
        self.assertIn(f"revoked Read stock in {self.unit.code}", history)
        self.assertIn("removed test_newcomer", history)

    def test_switching_off_a_personal_permission_leaves_the_team_one(self):
        team_id = self.sales_team(self.newcomer)
        self.change(team_id, kind="action", action=Action.OPEN_SUPPLIER, enabled=True)
        set_transfer_permission(actor=self.sysadmin, business_unit=self.unit, user_id=self.newcomer.pk,
                                action=Action.OPEN_SUPPLIER, enabled=True)
        set_transfer_permission(actor=self.sysadmin, business_unit=self.unit, user_id=self.newcomer.pk,
                                action=Action.OPEN_SUPPLIER, enabled=False)
        self.assertTrue(active_grants(self.newcomer, Action.OPEN_SUPPLIER).exists())
        self.assertTrue(UserActionGrant.objects.filter(team_id=team_id, action=Action.OPEN_SUPPLIER,
                                                       revoked_at__isnull=True).exists())

    def test_a_closed_team_gives_nothing_and_its_chat_is_emptied(self):
        team_id = self.sales_team(self.newcomer, self.second)
        conversation = Conversation.objects.get(team_id=team_id)
        self.change(team_id, kind="active", enabled=False)
        self.assertFalse(self.can(self.newcomer, Action.CREATE_ORDER))
        self.assertFalse(conversation.participants.filter(left_at__isnull=True).exists())
        with self.assertRaises(InvalidTransaction):
            self.change(team_id, kind="member", user_id=self.requester.pk, enabled=True)
        self.change(team_id, kind="active", enabled=True)
        self.assertTrue(self.can(self.newcomer, Action.CREATE_ORDER))
        self.assertEqual(conversation.participants.filter(left_at__isnull=True).count(), 2)

    def test_the_team_chat_reaches_every_member_and_follows_the_team(self):
        team_id = self.sales_team(self.newcomer, self.second)
        conversation = Conversation.objects.get(team_id=team_id)
        self.assertEqual((conversation.kind, conversation.title), ("team", "TEST ONLY Sales"))
        message = chat.send_message(self.newcomer, conversation.pk, "TEST ONLY New price list is in.")
        rows = chat.conversation_list(self.second)
        self.assertEqual([(row["kind"], row["title"], row["unread"]) for row in rows], [("team", "TEST ONLY Sales", 1)])
        self.assertFalse(rows[0]["can_remove_members"])
        # Members are changed under Teams, not from the chat.
        for call in (lambda: chat.add_members(self.newcomer, conversation.pk, [self.requester.pk]),
                     lambda: chat.remove_member(self.newcomer, conversation.pk, self.newcomer.pk),
                     lambda: chat.rename_group(self.newcomer, conversation.pk, "Other")):
            with self.assertRaises(ValidationError):
                call()
        # Someone added later sees the team chat from when they joined; someone removed loses it.
        self.change(team_id, kind="member", user_id=self.requester.pk, enabled=True)
        self.assertEqual(chat.messages_page(self.requester, conversation.pk)["messages"], [])
        self.change(team_id, kind="member", user_id=self.second.pk, enabled=False)
        with self.assertRaises(NotFound):
            chat.messages_page(self.second, conversation.pk)
        # Renaming the team renames its chat.
        self.change(team_id, kind="details", name="TEST ONLY Showroom sales", description="")
        conversation.refresh_from_db()
        self.assertEqual(conversation.title, "TEST ONLY Showroom sales")
        self.assertTrue(message.pk)

    def test_the_person_screen_shows_and_changes_team_membership(self):
        team_id = self.sales_team()
        detail = change_person(actor=self.sysadmin, user_id=self.newcomer.pk,
                               change={"kind": "team", "team_id": team_id, "enabled": True})
        self.assertEqual([(row["name"], row["on"]) for row in detail["teams"]], [("TEST ONLY Sales", True)])
        unit = next(row for row in detail["units"] if row["id"] == self.unit.pk)
        self.assertEqual(unit["actions"], [])
        self.assertEqual(unit["from_teams"], {Action.CREATE_ORDER: ["TEST ONLY Sales"], Action.READ_STOCK: ["TEST ONLY Sales"]})
        self.assertIn(self.unit.code, detail["person"]["units"])
        self.assertEqual(detail["history"][0]["change"], "added to")
        summary = next(row for row in people_overview(actor=self.sysadmin)["people"] if row["id"] == self.newcomer.pk)
        self.assertEqual(summary["permission_count"], 2)
        detail = person_detail(actor=self.sysadmin, user_id=self.newcomer.pk)
        self.assertTrue(detail["teams"][0]["on"])

    def test_names_are_unique_and_a_team_can_start_from_a_person(self):
        team_id = self.sales_team()
        with self.assertRaises(InvalidTransaction):
            create_team(actor=self.sysadmin, name="test only sales")
        with self.assertRaises(InvalidTransaction):
            create_team(actor=self.sysadmin, name="   ")
        warehouse = create_team(actor=self.sysadmin, name="TEST ONLY Warehouse")["team"]["id"]
        detail = self.change(warehouse, kind="copy_person", user_id=self.manager.pk)
        unit = next(row for row in detail["units"] if row["id"] == self.unit.pk)
        self.assertEqual(unit["actions"], [Action.APPROVE_TRANSFER])
        self.assertEqual(Team.objects.count(), 2)
        self.assertTrue(team_id)
