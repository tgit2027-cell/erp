"""Isolated PostgreSQL authorization and audit tests for synthetic capacity."""

import json
import os
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .access import context_units
from .models import (
    BusinessUnit,
    DemoUserMarker,
    Location,
    LocationCapacity,
    LocationCapacityAudit,
    LocationCapacityGrant,
    UserActionGrant,
)
from .warehouse_capacity_services import designate_capacity_location


class WarehouseCapacityAuthorizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            call_command("seed_demo", stdout=StringIO())
        User = get_user_model()
        cls.viewer = User.objects.create_user("capacity_viewer_test", password="test-only")
        cls.editor = User.objects.create_user("capacity_editor_test", password="test-only")
        cls.reader = User.objects.get(username="demo_tivoli")
        cls.staff = User.objects.create_user(
            "capacity_staff_test", password="test-only", is_staff=True,
        )
        cls.system_admin = User.objects.create_superuser(
            "capacity_system_admin_test", password="test-only", email="capacity-admin@example.invalid",
        )
        for user in (cls.viewer, cls.editor, cls.staff):
            DemoUserMarker.objects.create(user=user)
        cls.location = Location.objects.get(code="W41-01")
        cls.other_location = Location.objects.get(code="W41-02")
        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            cls.profile, _ = designate_capacity_location(
                actor=cls.system_admin,
                location_id=cls.location.pk,
            )
            cls.other_profile, _ = designate_capacity_location(
                actor=cls.system_admin,
                location_id=cls.other_location.pk,
            )
        cls.view_grant = LocationCapacityGrant.objects.create(
            user=cls.viewer,
            location=cls.profile,
            capability=LocationCapacityGrant.Capability.VIEW,
            assigned_by=cls.system_admin,
        )
        cls.edit_grant = LocationCapacityGrant.objects.create(
            user=cls.editor,
            location=cls.profile,
            capability=LocationCapacityGrant.Capability.EDIT,
            assigned_by=cls.system_admin,
        )

    def setUp(self):
        self.env = patch.dict(os.environ, {"APP_ENV": "demo"})
        self.env.start()
        self.addCleanup(self.env.stop)

    @staticmethod
    def values(total="125.250000", allocated="80.125000", volume="14.750000"):
        return {
            "total_floor_area_m2": total,
            "allocated_storage_floor_area_m2": allocated,
            "received_goods_volume_m3": volume,
        }

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_seeded_accounts_receive_no_capacity_assignment_by_default(self):
        self.assertFalse(LocationCapacityGrant.objects.filter(user__username__startswith="demo_").exists())
        self.assertFalse(LocationCapacity.objects.filter(location__code__startswith="demo_").exists())

    def test_viewer_sees_only_explicitly_assigned_location_and_no_edit_control(self):
        LocationCapacity.objects.filter(pk=self.profile.pk).update(
            total_floor_area_m2=Decimal("12.500000"),
            allocated_storage_floor_area_m2=Decimal("5.250000"),
            received_goods_volume_m3=Decimal("1.750000"),
        )
        client = self.client_for(self.viewer)
        response = client.get("/api/warehouse-capacity/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        record = response.data["results"][0]
        self.assertEqual(record["location"]["id"], self.location.id)
        self.assertEqual(record["capability"], LocationCapacityGrant.Capability.VIEW)
        self.assertTrue(record["configured"])
        self.assertEqual(
            set(record),
            {
                "location",
                "capability",
                "configured",
                "total_floor_area_m2",
                "allocated_storage_floor_area_m2",
                "received_goods_volume_m3",
                "available_floor_area_m2",
                "updated_at",
            },
        )
        self.assertEqual(record["available_floor_area_m2"], "7.250000")
        self.assertNotIn("supplier_cost", record)
        self.assertNotIn("on_hand_quantity", record)
        self.assertEqual(
            client.get(f"/api/warehouse-capacity/{self.other_location.id}/").status_code,
            403,
        )
        self.assertEqual(
            client.put(
                f"/api/warehouse-capacity/{self.location.id}/",
                self.values(),
                format="json",
            ).status_code,
            403,
        )

    def test_edit_updates_exact_decimal_measures_and_appends_audit(self):
        client = self.client_for(self.editor)
        response = client.put(
            f"/api/warehouse-capacity/{self.location.id}/",
            self.values(),
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total_floor_area_m2"], "125.250000")
        self.assertEqual(response.data["allocated_storage_floor_area_m2"], "80.125000")
        self.assertEqual(response.data["available_floor_area_m2"], "45.125000")
        self.assertEqual(response.data["received_goods_volume_m3"], "14.750000")
        self.assertNotIn("total_volume_capacity_m3", response.data)
        self.assertEqual(
            LocationCapacityAudit.objects.filter(
                kind=LocationCapacityAudit.Kind.CAPACITY_EDITED,
                actor=self.editor,
                location=self.location,
            ).count(),
            1,
        )
        audit = LocationCapacityAudit.objects.get(kind=LocationCapacityAudit.Kind.CAPACITY_EDITED)
        self.assertFalse(audit.before_values["configured"])
        self.assertEqual(audit.after_values["available_floor_area_m2"], "45.125000")
        with self.assertRaises(ValidationError):
            audit.save()
        with self.assertRaises(ValidationError):
            audit.delete()

    def test_session_write_requires_csrf_and_accepts_valid_token(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.editor)
        url = f"/api/warehouse-capacity/{self.location.id}/"
        missing_token = client.put(url, self.values(), format="json")
        self.assertEqual(missing_token.status_code, 403)

        csrf_token = client.get("/api/auth/csrf/").data["csrfToken"]
        allowed = client.put(
            url,
            self.values(),
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(allowed.status_code, 200, allowed.data)

    def test_validation_rejects_negative_excess_area_float_and_overprecision(self):
        client = self.client_for(self.editor)
        invalid_payloads = [
            self.values(total="-1"),
            self.values(total="20", allocated="20.000001"),
            self.values(volume="1.0000001"),
            self.values(volume=1.25),
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = client.put(
                    f"/api/warehouse-capacity/{self.location.id}/",
                    payload,
                    format="json",
                )
                self.assertEqual(response.status_code, 400, response.data)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.total_floor_area_m2)
        self.assertFalse(LocationCapacityAudit.objects.filter(kind=LocationCapacityAudit.Kind.CAPACITY_EDITED).exists())

    def test_database_constraints_reject_invalid_capacity_without_partial_write(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LocationCapacity.objects.filter(pk=self.profile.pk).update(
                    total_floor_area_m2=10,
                    allocated_storage_floor_area_m2=11,
                    received_goods_volume_m3=1,
                )
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.total_floor_area_m2)

    def test_staff_and_stock_reader_cannot_view_capacity_or_manage_assignments(self):
        staff_client = self.client_for(self.staff)
        self.assertEqual(staff_client.get("/api/governance/warehouse-capacity/").status_code, 403)
        self.assertEqual(
            staff_client.post(
                "/api/governance/warehouse-capacity/assignments/",
                {"location_id": self.location.pk, "user_id": self.viewer.pk, "capability": "edit_capacity"},
                format="json",
            ).status_code,
            403,
        )
        reader_client = self.client_for(self.reader)
        self.assertEqual(reader_client.get("/api/warehouse-capacity/").data, {"results": []})
        self.assertEqual(reader_client.get(f"/api/warehouse-capacity/{self.location.pk}/").status_code, 403)
        self.assertTrue(
            UserActionGrant.objects.filter(
                user=self.reader,
                business_unit__code="TVL",
                action=UserActionGrant.Action.READ_STOCK,
                revoked_at__isnull=True,
            ).exists()
        )

    def test_capacity_audit_register_lists_each_event_type_without_capacity_values(self):
        now = timezone.now()
        LocationCapacityAudit.objects.create(
            kind=LocationCapacityAudit.Kind.CAPACITY_EDITED,
            location=self.profile.location,
            actor=self.editor,
            before_values={"total_floor_area_m2": "12345.678901"},
            after_values={"received_goods_volume_m3": "9876.543210"},
            created_at=now,
        )
        LocationCapacityAudit.objects.create(
            kind=LocationCapacityAudit.Kind.ASSIGNED,
            location=self.profile.location,
            actor=self.system_admin,
            target_user=self.viewer,
            capability=LocationCapacityGrant.Capability.VIEW,
            created_at=now + timedelta(seconds=1),
        )
        LocationCapacityAudit.objects.create(
            kind=LocationCapacityAudit.Kind.REVOKED,
            location=self.profile.location,
            actor=self.system_admin,
            target_user=self.viewer,
            capability=LocationCapacityGrant.Capability.VIEW,
            created_at=now + timedelta(seconds=2),
        )

        response = self.client_for(self.system_admin).get(
            "/api/governance/warehouse-capacity/audit/",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            {event["kind"] for event in response.data["results"]},
            {
                LocationCapacityAudit.Kind.LOCATION_CONFIGURED,
                LocationCapacityAudit.Kind.CAPACITY_EDITED,
                LocationCapacityAudit.Kind.ASSIGNED,
                LocationCapacityAudit.Kind.REVOKED,
            },
        )
        event = next(
            item for item in response.data["results"]
            if item["kind"] == LocationCapacityAudit.Kind.CAPACITY_EDITED
        )
        self.assertEqual(
            set(event),
            {"id", "kind", "label", "location", "actor", "target_user", "capability", "created_at"},
        )
        self.assertEqual(
            set(event["location"]),
            {"id", "code", "name"},
        )
        self.assertEqual(event["actor"], self.editor.get_username())
        self.assertNotIn("before_values", event)
        self.assertNotIn("after_values", event)
        response_text = json.dumps(response.data)
        self.assertNotIn("12345.678901", response_text)
        self.assertNotIn("9876.543210", response_text)
        assigned = next(
            item for item in response.data["results"]
            if item["kind"] == LocationCapacityAudit.Kind.ASSIGNED
        )
        self.assertEqual(assigned["actor"], "System administrator")
        self.assertEqual(assigned["target_user"], self.viewer.get_username())
        self.assertEqual(assigned["capability"], LocationCapacityGrant.Capability.VIEW)

    def test_capacity_audit_register_is_deterministically_ordered_and_paginated(self):
        base = timezone.now() + timedelta(days=1)
        timestamps = [
            base,
            base,
            base - timedelta(minutes=1),
            base - timedelta(minutes=2),
            base - timedelta(minutes=3),
        ]
        created = [
            LocationCapacityAudit.objects.create(
                kind=LocationCapacityAudit.Kind.CAPACITY_EDITED,
                location=self.profile.location,
                actor=self.editor,
                created_at=timestamp,
            )
            for timestamp in timestamps
        ]
        client = self.client_for(self.system_admin)
        pages = [
            client.get(
                f"/api/governance/warehouse-capacity/audit/?page={page}&page_size=2",
            )
            for page in (1, 2, 3, 4)
        ]
        for page in pages:
            self.assertEqual(page.status_code, 200, page.data)
        self.assertEqual([len(page.data["results"]) for page in pages], [2, 2, 2, 1])
        self.assertEqual(pages[0].data["pagination"]["total_count"], 7)
        self.assertEqual(pages[0].data["pagination"]["total_pages"], 4)
        self.assertEqual(pages[0].data["pagination"]["next_page"], 2)
        self.assertEqual(pages[1].data["pagination"]["previous_page"], 1)
        self.assertIsNone(pages[3].data["pagination"]["next_page"])
        returned_ids = [
            event["id"]
            for page in pages
            for event in page.data["results"]
        ]
        expected_ids = list(
            LocationCapacityAudit.objects.filter(
                is_synthetic=True,
                location__is_synthetic=True,
                location__legal_entity__is_synthetic=True,
                location__capacity_profile__is_synthetic=True,
            ).order_by("-created_at", "-pk").values_list("pk", flat=True)
        )
        self.assertEqual(returned_ids, expected_ids)
        self.assertEqual(len(returned_ids), len(set(returned_ids)))
        self.assertEqual(returned_ids[:2], [created[1].pk, created[0].pk])
        self.assertEqual(
            client.get("/api/governance/warehouse-capacity/audit/?page=99&page_size=2").status_code,
            400,
        )

    def test_capacity_audit_register_is_superuser_only(self):
        url = "/api/governance/warehouse-capacity/audit/"
        self.assertEqual(self.client_for(self.system_admin).get(url).status_code, 200)
        self.assertEqual(self.client_for(self.staff).get(url).status_code, 403)
        self.assertEqual(self.client_for(self.reader).get(url).status_code, 403)

    def test_capacity_audit_register_is_synthetic_scoped_and_read_only(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LocationCapacityAudit.objects.create(
                    kind=LocationCapacityAudit.Kind.CAPACITY_EDITED,
                    location=self.profile.location,
                    actor=self.editor,
                    is_synthetic=False,
                )
        client = self.client_for(self.system_admin)
        before_count = LocationCapacityAudit.objects.count()
        response = client.get("/api/governance/warehouse-capacity/audit/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(LocationCapacityAudit.objects.count(), before_count)
        self.assertTrue(
            all(
                LocationCapacityAudit.objects.get(pk=event["id"]).is_synthetic
                and LocationCapacityAudit.objects.get(pk=event["id"]).location.is_synthetic
                for event in response.data["results"]
            )
        )
        self.assertEqual(
            client.post("/api/governance/warehouse-capacity/audit/", {}, format="json").status_code,
            405,
        )
        self.assertEqual(LocationCapacityAudit.objects.count(), before_count)

    def test_superuser_designation_assignment_and_revocation_are_audited(self):
        client = self.client_for(self.system_admin)
        designation = client.post(
            "/api/governance/warehouse-capacity/",
            {"location_id": self.other_location.pk},
            format="json",
        )
        self.assertEqual(designation.status_code, 200)  # idempotent profile designation
        assignment = client.post(
            "/api/governance/warehouse-capacity/assignments/",
            {
                "location_id": self.other_location.pk,
                "user_id": self.viewer.pk,
                "capability": LocationCapacityGrant.Capability.EDIT,
            },
            format="json",
        )
        self.assertEqual(assignment.status_code, 201, assignment.data)
        grant_id = assignment.data["assignment"]["id"]
        grants_before = UserActionGrant.objects.filter(user=self.viewer).count()
        revoked = client.delete(f"/api/governance/warehouse-capacity/assignments/{grant_id}/")
        self.assertEqual(revoked.status_code, 200)
        grant = LocationCapacityGrant.objects.get(pk=grant_id)
        self.assertIsNotNone(grant.revoked_at)
        self.assertEqual(UserActionGrant.objects.filter(user=self.viewer).count(), grants_before)
        self.assertEqual(
            LocationCapacityAudit.objects.filter(
                actor=self.system_admin,
                location=self.other_location,
                kind__in=[LocationCapacityAudit.Kind.ASSIGNED, LocationCapacityAudit.Kind.REVOKED],
            ).count(),
            2,
        )
        self.assertEqual(
            self.client_for(self.viewer).get(f"/api/warehouse-capacity/{self.other_location.pk}/").status_code,
            403,
        )

    def test_assignment_denies_non_synthetic_targets_and_unconfigured_locations(self):
        non_synthetic = get_user_model().objects.create_user("not_a_demo_target", password="test-only")
        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            response = self.client_for(self.system_admin).post(
                "/api/governance/warehouse-capacity/assignments/",
                {
                    "location_id": self.location.pk,
                    "user_id": non_synthetic.pk,
                    "capability": "view_capacity",
                },
                format="json",
            )
        self.assertEqual(response.status_code, 400)
        unconfigured = Location.objects.create(
            legal_entity=self.location.legal_entity,
            code="test-only-unconfigured-capacity",
            name="TEST ONLY unconfigured location",
        )
        unconfigured.business_units.add(BusinessUnit.objects.get(code="TVL"))
        response = self.client_for(self.system_admin).post(
            "/api/governance/warehouse-capacity/assignments/",
            {
                "location_id": unconfigured.pk,
                "user_id": self.viewer.pk,
                "capability": "view_capacity",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(LocationCapacityGrant.objects.filter(user=self.viewer, location__location=unconfigured).exists())

    def test_capacity_only_user_can_sign_in_and_context_is_location_scoped_without_stock_access(self):
        User = get_user_model()
        capacity_only = User.objects.create_user(
            "capacity_only_login_test",
            password="test-only-login-password",
        )
        DemoUserMarker.objects.create(user=capacity_only)
        LocationCapacityGrant.objects.create(
            user=capacity_only,
            location=self.profile,
            capability=LocationCapacityGrant.Capability.VIEW,
            assigned_by=self.system_admin,
        )
        login_client = APIClient(enforce_csrf_checks=True)
        csrf = login_client.get("/api/auth/csrf/").data["csrfToken"]
        response = login_client.post(
            "/api/auth/login/",
            {"username": capacity_only.username, "password": "test-only-login-password"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["authenticated"] if "authenticated" in response.data else True)
        organization = login_client.get("/api/organization/")
        self.assertEqual(organization.status_code, 200, organization.data)
        self.assertEqual(
            [item["code"] for item in organization.data["locations"]],
            [self.location.code],
        )
        self.assertEqual(
            login_client.get(f"/api/stock-balances/?unit={BusinessUnit.objects.get(code='TVL').pk}").status_code,
            403,
        )
        self.assertFalse(
            UserActionGrant.objects.filter(user=capacity_only).exists(),
            "Capacity assignment must not create stock/order grants.",
        )

    def test_assignment_changes_have_no_default_staff_or_business_unit_grant_effect(self):
        before = list(
            UserActionGrant.objects.filter(user=self.viewer).values_list(
                "business_unit_id", "action", "revoked_at",
            )
        )
        response = self.client_for(self.system_admin).post(
            "/api/governance/warehouse-capacity/assignments/",
            {
                "location_id": self.location.pk,
                "user_id": self.viewer.pk,
                "capability": "edit_capacity",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            list(
                UserActionGrant.objects.filter(user=self.viewer).values_list(
                    "business_unit_id", "action", "revoked_at",
                )
            ),
            before,
        )

    def test_capacity_api_fails_closed_outside_demo_environment(self):
        client = self.client_for(self.editor)
        with patch.dict(os.environ, {"APP_ENV": "production"}):
            self.assertEqual(client.get("/api/warehouse-capacity/").status_code, 403)
            self.assertEqual(
                client.put(
                    f"/api/warehouse-capacity/{self.location.pk}/",
                    self.values(),
                    format="json",
                ).status_code,
                403,
            )

    @override_settings(APP_ENV="production")
    def test_unassigned_superuser_login_and_global_context_exception_are_demo_only(self):
        self.assertEqual(list(context_units(self.system_admin)), [])
        client = APIClient(enforce_csrf_checks=True)
        csrf_token = client.get("/api/auth/csrf/").data["csrfToken"]
        response = client.post(
            "/api/auth/login/",
            {"username": self.system_admin.username, "password": "test-only"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(response.status_code, 400)
