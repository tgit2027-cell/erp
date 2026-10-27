"""Subsection navigation preferences are not authority; disposable fixtures only."""
from django.test import TestCase
from django.utils import timezone

from .models import (
    ReportingRole, ReportingRoleMembership,
    UserActionGrant, UserFavorite,
)
from .navigation_destinations import FUNCTION_DESTINATIONS
from . import test_favorites


class FunctionFavoriteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_favorites.FavoriteDestinationTests.setUpTestData.__func__(cls)

    client_for = test_favorites.FavoriteDestinationTests.client_for

    def test_collection_function_pin_is_personal_navigation_not_collection_authority(self):
        owner = self.client_for(self.roche_user)
        other = self.client_for(self.multi_user)
        before = list(UserActionGrant.objects.order_by("pk").values())
        self.assertEqual(owner.post("/api/favorites/", {"destination": "retail"}, format="json").status_code, 201)
        self.assertEqual(owner.post("/api/favorites/", {"destination": "retail.collections"}, format="json").status_code, 201)
        self.assertEqual(
            {row["destination"] for row in owner.get("/api/favorites/").data["favorites"]},
            {"retail", "retail.collections"},
        )
        self.assertEqual(other.get("/api/favorites/").data["favorites"], [])
        self.assertEqual(owner.get(
            "/api/purchasing/collections/", {"business_unit_id": self.roche.pk},
        ).status_code, 404)
        self.assertEqual(owner.delete("/api/favorites/retail.collections/").status_code, 204)
        self.assertTrue(UserFavorite.objects.filter(user=self.roche_user, destination="retail").exists())
        self.assertEqual(list(UserActionGrant.objects.order_by("pk").values()), before)

    def test_function_pin_reload_isolation_and_unpin_preserve_section_favorites(self):
        owner = self.client_for(self.roche_user)
        before = list(UserActionGrant.objects.order_by("pk").values())
        for destination in ("purchasing", "purchasing.quote", "purchasing.transactions", "purchasing.registers"):
            for expected in (201, 200):
                response = owner.post("/api/favorites/", {"destination": destination}, format="json")
                self.assertEqual(response.status_code, expected)
        reloaded = self.client_for(self.roche_user).get(f"/api/favorites/?unit={self.roche.pk}")
        self.assertEqual(
            [row["destination"] for row in reloaded.data["favorites"]],
            ["purchasing", "purchasing.quote", "purchasing.transactions", "purchasing.registers"],
        )
        other = self.client_for(self.multi_user)
        self.assertEqual(other.get("/api/favorites/").data["favorites"], [])
        self.assertEqual(other.delete("/api/favorites/purchasing.quote/").status_code, 404)
        self.assertEqual(owner.delete("/api/favorites/purchasing.quote/").status_code, 204)
        self.assertEqual(list(UserActionGrant.objects.order_by("pk").values()), before)
        # Existing purchasing detail deliberately conceals unavailable records.
        self.assertEqual(owner.get(f"/api/purchasing/quotes/999999/?business_unit_id={self.roche.pk}").status_code, 404)
        self.assertEqual(owner.get(f"/api/reports/inventory/reservations/?unit={self.roche.pk}").status_code, 403)
        self.assertEqual(owner.get(f"/api/stock/?unit={self.tivoli.pk}").status_code, 403)

    def test_staff_superuser_and_panel_manager_choices_are_independent(self):
        ordinary = self.client_for(self.roche_user)
        restricted = (
            "procurement.suppliers", "governance.presentation", "governance.purchasing-config",
            "governance.reporting", "governance.report-panels",
            "governance.capacity-admin", "governance.capacity-audit", "governance.transfer-permissions",
        )
        for key in restricted:
            self.assertEqual(ordinary.post("/api/favorites/", {"destination": key}, format="json").status_code, 403)
        role = ReportingRole.objects.create(code="nav-test-manager", name="TEST ONLY", can_manage_report_panels=True, created_by=self.multi_user)
        membership = ReportingRoleMembership.objects.create(user=self.roche_user, role=role, assigned_by=self.multi_user)
        self.assertEqual(ordinary.post("/api/favorites/", {"destination": "governance.report-panels"}, format="json").status_code, 201)
        for key in ("governance.presentation", "governance.capacity-admin"):
            self.assertEqual(ordinary.post("/api/favorites/", {"destination": key}, format="json").status_code, 403)
        membership.revoked_at = timezone.now()
        membership.save(update_fields=["revoked_at"])
        self.assertNotIn("governance.report-panels", [r["destination"] for r in ordinary.get("/api/favorites/").data["favorites"]])
        self.assertTrue(UserFavorite.objects.filter(user=self.roche_user, destination="governance.report-panels").exists())
        self.roche_user.is_staff = True
        self.roche_user.save(update_fields=["is_staff"])
        for key in restricted[:-3]:
            self.assertEqual(ordinary.post("/api/favorites/", {"destination": key}, format="json").status_code, 201 if key != "governance.report-panels" else 200)
        self.assertEqual(ordinary.post("/api/favorites/", {"destination": "governance.capacity-admin"}, format="json").status_code, 403)
        self.roche_user.is_superuser = True
        self.roche_user.save(update_fields=["is_superuser"])
        self.assertEqual(ordinary.post("/api/favorites/", {"destination": "governance.capacity-admin"}, format="json").status_code, 201)

    def test_revoked_stock_hides_stock_functions_without_granting_capacity(self):
        owner = self.client_for(self.roche_user)
        self.assertEqual(owner.post("/api/favorites/", {"destination": "inventory.locations"}, format="json").status_code, 201)
        UserActionGrant.objects.filter(user=self.roche_user).update(revoked_at=timezone.now())
        # Context requires an existing grant; a test-only create_order preserves context,
        # without granting stock or capacity measurement access.
        UserActionGrant.objects.create(user=self.roche_user, business_unit=self.roche, action=UserActionGrant.Action.CREATE_ORDER)
        response = owner.get("/api/favorites/")
        keys = {row["destination"] for row in response.data["available_destinations"]}
        self.assertNotIn("inventory.locations", keys)
        self.assertIn("inventory.capacity", keys)
        self.assertEqual(response.data["favorites"], [])
        self.assertEqual(owner.get("/api/stock/").status_code, 403)
        self.assertEqual(owner.post("/api/favorites/", {"destination": "inventory.locations"}, format="json").status_code, 403)
        self.assertEqual(owner.post("/api/favorites/", {"destination": "inventory.capacity"}, format="json").status_code, 201)

    def test_function_key_allowlist_excludes_aliases_placeholders_and_arbitrary_targets(self):
        owner = self.client_for(self.roche_user)
        for key in ("governance.authority", "reports.panels", "finance.post", "hr.payroll", "inventory.receiving", "../purchasing.quote"):
            self.assertEqual(owner.post("/api/favorites/", {"destination": key}, format="json").status_code, 400)
        self.assertTrue(all(len(key) <= 32 for key, _ in FUNCTION_DESTINATIONS))
        self.assertEqual(len(FUNCTION_DESTINATIONS), len({key for key, _ in FUNCTION_DESTINATIONS}))
        for key, _ in FUNCTION_DESTINATIONS:
            UserFavorite(user=self.roche_user, destination=key).full_clean()

    def test_function_write_still_requires_session_csrf(self):
        from rest_framework.test import APIClient
        owner = APIClient(enforce_csrf_checks=True)
        owner.force_login(self.roche_user)
        token = owner.get("/api/auth/csrf/").data["csrfToken"]
        payload = {"destination": "delivery.returns"}
        self.assertEqual(owner.post("/api/favorites/", payload, format="json").status_code, 403)
        self.assertEqual(owner.post("/api/favorites/", payload, format="json", HTTP_X_CSRFTOKEN=token).status_code, 201)
        self.assertEqual(owner.delete("/api/favorites/delivery.returns/").status_code, 403)
        self.assertEqual(owner.delete("/api/favorites/delivery.returns/", HTTP_X_CSRFTOKEN=token).status_code, 204)

    def test_every_implemented_function_choice_persists_and_remains_navigation_only(self):
        self.roche_user.is_staff = True
        self.roche_user.is_superuser = True
        self.roche_user.save(update_fields=["is_staff", "is_superuser"])
        owner = self.client_for(self.roche_user)
        # Superuser context includes every BU; explicitly select the granted BU,
        # rather than assuming the default BU ordering implies stock authority.
        self.assertEqual(owner.post(f"/api/units/{self.roche.pk}/select/", {}, format="json").status_code, 200)
        before = list(UserActionGrant.objects.order_by("pk").values())
        for key, label in FUNCTION_DESTINATIONS:
            with self.subTest(destination=key):
                result = owner.post("/api/favorites/", {"destination": key}, format="json")
                self.assertEqual(result.status_code, 201)
                self.assertEqual(result.data["favorite"], {"destination": key, "label": label})
        reloaded = self.client_for(self.roche_user).get(f"/api/favorites/?unit={self.roche.pk}")
        self.assertEqual(
            [(r["destination"], r["label"]) for r in reloaded.data["favorites"]],
            FUNCTION_DESTINATIONS,
        )
        self.assertEqual(list(UserActionGrant.objects.order_by("pk").values()), before)
        for key, _ in FUNCTION_DESTINATIONS:
            self.assertEqual(owner.delete(f"/api/favorites/{key}/").status_code, 204)
