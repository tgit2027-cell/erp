from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import BusinessUnit, DemoUserMarker, TransferPermissionAudit, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .stock_transfer_requests import submit_transfer_request
from .test_stock_transfer_requests import TransferRequestFixtureMixin
from .transaction_services import InvalidTransaction
from .transfer_permissions import set_transfer_permission, transfer_permission_overview


Action = UserActionGrant.Action


@override_settings(APP_ENV="demo")
class TransferPermissionTests(TransferRequestFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_transfer_fixture()
        cls.sysadmin = get_user_model().objects.create_user(
            username="test_sysadmin", password="unused-test-password", is_staff=True, is_superuser=True,
        )
        cls.newcomer = cls._person("test_newcomer")

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)

    def set(self, enabled, action=Action.REQUEST_TRANSFER, actor=None, user=None):
        return set_transfer_permission(
            actor=actor or self.sysadmin, business_unit=self.unit,
            user_id=(user or self.newcomer).pk, action=action, enabled=enabled,
        )

    def active(self, action=Action.REQUEST_TRANSFER):
        return UserActionGrant.objects.filter(
            user=self.newcomer, business_unit=self.unit, action=action, revoked_at__isnull=True,
        ).exists()

    def test_grant_revoke_and_regrant_are_audited_and_take_effect(self):
        self.assertTrue(self.set(True)["changed"])
        self.assertTrue(self.active())
        self.assertFalse(self.set(True)["changed"])
        self.assertTrue(self.set(False)["changed"])
        self.assertFalse(self.active())
        self.make_receipt(key="permission-stock", charges=[])
        with self.assertRaises(WorkflowPermissionDenied):
            submit_transfer_request(
                actor=self.newcomer, business_unit=self.unit, idempotency_key="revoked-newcomer",
                source_location=self.location, destination_location=self.destination,
                lines=[{"variant": self.variant, "quantity": "1.00"}],
            )
        self.assertTrue(self.set(True)["changed"])
        self.assertTrue(self.active())
        self.assertEqual(
            list(TransferPermissionAudit.objects.order_by("pk").values_list("change", flat=True)),
            ["granted", "revoked", "granted"],
        )
        event = TransferPermissionAudit.objects.first()
        with self.assertRaises(ValidationError):
            event.save()

    def test_only_a_system_administrator_may_change_permissions(self):
        for actor in (self.manager, self.admin):
            with self.assertRaises(WorkflowPermissionDenied):
                self.set(True, actor=actor)
            with self.assertRaises(WorkflowPermissionDenied):
                transfer_permission_overview(actor=actor, business_unit=self.unit)
        self.assertFalse(self.active())
        self.assertFalse(TransferPermissionAudit.objects.exists())

    def test_only_transfer_permissions_and_demo_accounts_can_be_set(self):
        with self.assertRaises(InvalidTransaction):
            self.set(True, action=Action.CREATE_ORDER)
        with self.assertRaises(InvalidTransaction):
            self.set(True, user=self.sysadmin)

    def test_overview_lists_people_and_their_unit_permissions(self):
        overview = transfer_permission_overview(actor=self.sysadmin, business_unit=self.unit)
        people = {row["username"]: row["actions"] for row in overview["users"]}
        self.assertEqual(people["test_showroom_manager"], [Action.APPROVE_TRANSFER])
        self.assertEqual(people["test_newcomer"], [])
        self.assertNotIn("test_sysadmin", people)
        other = transfer_permission_overview(actor=self.sysadmin, business_unit=BusinessUnit.objects.get(code="RBB"))
        self.assertEqual({row["username"]: row["actions"] for row in other["users"]}["test_showroom_manager"], [])

    def test_api_round_trip(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.sysadmin)
        csrf = client.get("/api/auth/csrf/").data["csrfToken"]
        listing = client.get("/api/governance/transfer-permissions/", {"unit": self.unit.pk})
        self.assertEqual(listing.status_code, 200)
        changed = client.post("/api/governance/transfer-permissions/", {
            "unit_id": self.unit.pk, "user_id": self.newcomer.pk,
            "action": Action.EXECUTE_TRANSFER, "enabled": True,
        }, format="json", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertTrue(self.active(Action.EXECUTE_TRANSFER))
        ordinary = APIClient()
        ordinary.force_login(self.manager)
        self.assertEqual(ordinary.get("/api/governance/transfer-permissions/", {"unit": self.unit.pk}).status_code, 403)
