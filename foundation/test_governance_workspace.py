"""Existing Governance API contracts; disposable synthetic fixtures only."""

import json

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    DemoUserMarker, Location, ReportDataCapability, ReportDataGrant,
    ReportingPanel, UserActionGrant,
)
from .reporting_services import (
    add_reporting_role_member, create_reporting_role, grant_report_data,
)
from .test_reporting import ReportingFixtureMixin


class GovernanceWorkspaceAPITests(ReportingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        User = get_user_model()
        cls.manager = User.objects.create_user("governance_manager_test")
        cls.system_admin = User.objects.create_user(
            "governance_system_admin_test", is_staff=True, is_superuser=True,
        )
        cls.capacity_user = User.objects.create_user("governance_capacity_test")
        for user in (cls.manager, cls.system_admin, cls.capacity_user):
            DemoUserMarker.objects.create(user=user)
        cls.reporting_role = create_reporting_role(
            actor=cls.admin, code="test-governance-manager",
            name="TEST ONLY panel manager",
        )
        cls.reporting_role.can_manage_report_panels = True
        cls.reporting_role.save(update_fields=["can_manage_report_panels"])
        cls.manager_membership, _ = add_reporting_role_member(
            actor=cls.admin, role=cls.reporting_role, target_user=cls.manager,
        )

    def client_for(self, user):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(user)
        csrf = client.get("/api/auth/csrf/").data["csrfToken"]
        client.credentials(HTTP_X_CSRFTOKEN=csrf)
        return client

    def snapshot(self):
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in apps.get_app_config("foundation").get_models()
        }

    def test_independent_staff_panel_manager_and_superuser_authorities(self):
        paths = [
            "/api/governance/business-unit-palettes/",
            "/api/governance/report-panels/",
            f"/api/governance/purchasing/{self.unit.pk}/",
            "/api/governance/warehouse-capacity/",
            "/api/governance/warehouse-capacity/audit/",
        ]
        cases = [
            (self.sales, [403, 403, 403, 403, 403]),
            (self.manager, [403, 200, 403, 403, 403]),
            (self.admin, [200, 200, 200, 403, 403]),
            (self.system_admin, [200, 200, 200, 200, 200]),
        ]
        before = self.snapshot()
        for user, expected in cases:
            client = self.client_for(user)
            with self.subTest(user=user.username):
                self.assertEqual(
                    [client.get(path).status_code for path in paths], expected,
                )
        self.assertEqual(self.snapshot(), before, "Navigation GETs must not write")
        self.assertFalse(self.manager.is_staff)
        self.assertFalse(self.admin.is_superuser)
        self.assertFalse(UserActionGrant.objects.filter(user=self.manager).exists())

    def test_panel_assignment_does_not_grant_data_and_revocation_is_rechecked(self):
        self.make_delivered_sale(key="governance-masked-sale")
        manager = self.client_for(self.manager)
        recipient = self.client_for(self.sales)
        action_grants = list(
            UserActionGrant.objects.order_by("pk").values(),
        )
        data_grants = list(ReportDataGrant.objects.order_by("pk").values())
        assignment = manager.post(
            "/api/governance/report-panels/",
            {"action": "assign_panel", "panel": ReportingPanel.SALES,
             "user_id": self.sales.pk},
            format="json",
        )
        self.assertEqual(assignment.status_code, 201, assignment.data)
        self.assertEqual(list(UserActionGrant.objects.order_by("pk").values()), action_grants)
        self.assertEqual(list(ReportDataGrant.objects.order_by("pk").values()), data_grants)
        query = {"unit": self.unit.pk, "period": "monthly"}
        self.assertEqual(recipient.get("/api/reports/sales/", query).status_code, 403)
        # This explicit grant is isolated fixture setup, never an effect of assignment.
        grant_report_data(
            actor=self.admin, target_user=self.sales,
            capability=ReportDataCapability.SALES, business_unit=self.unit,
        )
        report = recipient.get("/api/reports/sales/", query)
        self.assertEqual(report.status_code, 200, report.data)
        allowed = {
            "selling_price", "quantity", "description", "item_id",
            "warehouse_location", "brand_name",
        }
        self.assertEqual(set(report.data["fields"]), allowed)
        self.assertTrue(report.data["rows"])
        self.assertTrue(all(set(row) == allowed for row in report.data["rows"]))
        self.assertEqual(
            recipient.get("/api/reports/sales/", {"unit": self.roche.pk}).status_code, 403,
        )
        unsupported = manager.post(
            "/api/governance/report-panels/",
            {"action": "assign_data", "user_id": self.sales.pk},
            format="json",
        )
        self.assertEqual(unsupported.status_code, 400)
        revoked = manager.post(
            "/api/governance/report-panels/",
            {"action": "revoke_panel", "grant_id": assignment.data["grant_id"]},
            format="json",
        )
        self.assertEqual(revoked.status_code, 200, revoked.data)
        self.assertEqual(recipient.get("/api/reports/sales/", query).status_code, 403)
        self.manager_membership.revoked_at = timezone.now()
        self.manager_membership.save(update_fields=["revoked_at"])
        before = self.snapshot()
        self.assertEqual(manager.get("/api/governance/report-panels/").status_code, 403)
        self.assertEqual(
            manager.post(
                "/api/governance/report-panels/",
                {"action": "assign_panel", "panel": ReportingPanel.SALES,
                 "user_id": self.sales.pk},
                format="json",
            ).status_code, 403,
        )
        self.assertEqual(self.snapshot(), before)

    def test_capacity_assignment_is_location_only_and_audit_is_metadata_only(self):
        admin = self.client_for(self.system_admin)
        warehouse = self.client_for(self.capacity_user)
        other = Location.objects.get(code="W41-02")
        for location in (self.other_location, other):
            designated = admin.post(
                "/api/governance/warehouse-capacity/",
                {"location_id": location.pk}, format="json",
            )
            self.assertEqual(designated.status_code, 201, designated.data)
        grants_before = list(UserActionGrant.objects.order_by("pk").values())
        assigned = admin.post(
            "/api/governance/warehouse-capacity/assignments/",
            {"location_id": self.other_location.pk,
             "user_id": self.capacity_user.pk, "capability": "edit_capacity"},
            format="json",
        )
        self.assertEqual(assigned.status_code, 201, assigned.data)
        path = f"/api/warehouse-capacity/{self.other_location.pk}/"
        edited = warehouse.put(
            path, {"total_floor_area_m2": "250.000000",
                   "allocated_storage_floor_area_m2": "100.000000",
                   "received_goods_volume_m3": "14.250000"},
            format="json",
        )
        self.assertEqual(edited.status_code, 200, edited.data)
        self.assertEqual(warehouse.get(path).status_code, 200)
        self.assertEqual(
            warehouse.get(f"/api/warehouse-capacity/{other.pk}/").status_code, 403,
        )
        before_reads = self.snapshot()
        audit = admin.get("/api/governance/warehouse-capacity/audit/")
        config = admin.get("/api/governance/warehouse-capacity/")
        self.assertEqual(audit.status_code, 200, audit.data)
        self.assertEqual(config.status_code, 200, config.data)
        for content in (audit.data, config.data):
            serialized = json.dumps(content, default=str)
            for forbidden in (
                "before_values", "after_values", "total_floor_area_m2",
                "allocated_storage_floor_area_m2", "received_goods_volume_m3",
                "available_floor_area_m2",
            ):
                self.assertNotIn(forbidden, serialized)
        self.assertEqual(self.snapshot(), before_reads)
        removed = admin.delete(
            "/api/governance/warehouse-capacity/assignments/"
            f"{assigned.data['assignment']['id']}/",
        )
        self.assertEqual(removed.status_code, 200, removed.data)
        self.assertEqual(warehouse.get(path).status_code, 403)
        self.assertEqual(list(UserActionGrant.objects.order_by("pk").values()), grants_before)
        self.assertFalse(ReportDataGrant.objects.filter(user=self.capacity_user).exists())

    def test_purchasing_review_is_read_only_unit_specific_and_staff_is_rechecked(self):
        staff = self.client_for(self.admin)
        before = self.snapshot()
        tivoli = staff.get(f"/api/governance/purchasing/{self.unit.pk}/")
        roche = staff.get(f"/api/governance/purchasing/{self.roche.pk}/")
        self.assertEqual(tivoli.status_code, 200, tivoli.data)
        self.assertEqual(roche.status_code, 200, roche.data)
        self.assertEqual(tivoli.data["business_unit"]["id"], self.unit.pk)
        self.assertEqual(roche.data["business_unit"]["id"], self.roche.pk)
        self.assertTrue(tivoli.data["current_prices"])
        self.assertEqual(roche.data["current_prices"], [])
        self.assertEqual(roche.data["role_assignments"], [])
        self.assertEqual(roche.data["special_order_value_bands"], [])
        self.assertNotIn("unit_cost", json.dumps(tivoli.data))
        self.assertEqual(self.snapshot(), before)
        self.admin.is_staff = False
        self.admin.save(update_fields=["is_staff"])
        after_revocation = self.snapshot()
        self.assertEqual(
            staff.get(f"/api/governance/purchasing/{self.unit.pk}/").status_code, 403,
        )
        self.assertEqual(staff.get("/api/governance/business-unit-palettes/").status_code, 403)
        self.assertEqual(self.snapshot(), after_revocation)
