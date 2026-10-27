import os
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils.crypto import get_random_string
from rest_framework.test import APIClient

from .models import BusinessUnit, DemoUserMarker, UserActionGrant, UserFavorite


class FavoriteDestinationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            call_command("seed_demo", stdout=StringIO())
        User = get_user_model()
        cls.roche_user = User.objects.get(username="demo_roche")
        cls.multi_user = User.objects.get(username="demo_multi")
        cls.tivoli = BusinessUnit.objects.get(code="TVL")
        cls.roche = BusinessUnit.objects.get(code="RBB")
        cls.roche_password = get_random_string(32)
        cls.roche_user.set_password(cls.roche_password)
        cls.roche_user.save(update_fields=["password"])

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_favorites_are_per_user_and_destinations_are_server_allowlisted(self):
        UserFavorite.objects.create(
            user=self.multi_user,
            destination=UserFavorite.Destination.DASHBOARD,
        )
        UserFavorite.objects.create(
            user=self.multi_user,
            destination=UserFavorite.Destination.INVENTORY,
        )
        UserFavorite.objects.create(
            user=self.roche_user,
            destination=UserFavorite.Destination.RETAIL,
        )

        roche_response = self.client_for(self.roche_user).get("/api/favorites/")
        multi_response = self.client_for(self.multi_user).get("/api/favorites/")

        self.assertEqual(roche_response.status_code, 200)
        self.assertEqual(multi_response.status_code, 200)
        self.assertEqual(
            roche_response.data["favorites"],
            [{"destination": "retail", "label": "Retail & Sales"}],
        )
        self.assertEqual(
            {item["destination"] for item in multi_response.data["favorites"]},
            {"dashboard", "inventory"},
        )
        self.assertEqual(
            {item["destination"] for item in roche_response.data["available_destinations"] if "." not in item["destination"]},
            {
                destination
                for destination, _ in UserFavorite.Destination.choices
                if destination != UserFavorite.Destination.REPORTS
            },
        )
        self.assertNotIn("dashboard", {item["destination"] for item in roche_response.data["favorites"]})

    def test_purchasing_favorite_session_pin_retry_reload_isolation_and_unpin(self):
        owner = APIClient(enforce_csrf_checks=True)
        owner.force_login(self.roche_user)
        token = owner.get("/api/auth/csrf/").data["csrfToken"]
        expected = {"destination": "purchasing", "label": "Purchasing Workbench"}
        before_grants = list(UserActionGrant.objects.order_by("pk").values())
        before_unit = owner.get("/api/organization/").data["selected_unit"]["id"]

        self.assertIn(expected, owner.get("/api/favorites/").data["available_destinations"])
        self.assertEqual(owner.post(
            "/api/favorites/", expected, format="json"
        ).status_code, 403)
        for expected_status in (201, 200):
            response = owner.post(
                "/api/favorites/", {"destination": "purchasing"}, format="json",
                HTTP_X_CSRFTOKEN=token,
            )
            self.assertEqual(response.status_code, expected_status)
            self.assertEqual(response.data["favorite"], expected)
        self.assertEqual(
            UserFavorite.objects.filter(user=self.roche_user, destination="purchasing").count(), 1
        )
        owner = APIClient(enforce_csrf_checks=True)
        owner.force_login(self.roche_user)
        token = owner.get("/api/auth/csrf/").data["csrfToken"]
        self.assertEqual(owner.get("/api/favorites/").data["favorites"], [expected])
        other = self.client_for(self.multi_user)
        self.assertEqual(other.get("/api/favorites/").data["favorites"], [])
        self.assertEqual(other.delete("/api/favorites/purchasing/").status_code, 404)
        self.assertEqual(owner.delete("/api/favorites/purchasing/").status_code, 403)
        self.assertEqual(owner.get("/api/organization/").data["selected_unit"]["id"], before_unit)
        self.assertEqual(list(UserActionGrant.objects.order_by("pk").values()), before_grants)
        self.assertEqual(owner.delete(
            "/api/favorites/purchasing/", HTTP_X_CSRFTOKEN=token
        ).status_code, 204)
        self.assertEqual(owner.get("/api/favorites/").data["favorites"], [])

    def test_purchasing_favorite_never_grants_transaction_report_staff_or_cross_unit_access(self):
        client = self.client_for(self.roche_user)
        grants_before = list(UserActionGrant.objects.order_by("pk").values())
        requests = [
            (f"/api/purchasing/quotes/?business_unit_id={self.roche.pk}", 404),
            (f"/api/purchasing/storage-charges/?business_unit_id={self.roche.pk}", 404),
            (f"/api/purchasing/quotes/?business_unit_id={self.tivoli.pk}", 404),
            (f"/api/reports/purchase_history/receipts/?unit={self.roche.pk}", 403),
            (f"/api/reports/inventory/reservations/?unit={self.roche.pk}", 403),
            (f"/api/reports/sales/deliveries/?unit={self.roche.pk}", 403),
            (f"/api/governance/purchasing/{self.roche.pk}/", 403),
            (f"/api/stock/?unit={self.tivoli.pk}", 403),
        ]
        for stage in ("before pin", "after pin"):
            for path, status in requests:
                with self.subTest(stage=stage, path=path):
                    self.assertEqual(client.get(path).status_code, status)
            self.assertEqual(
                client.post(f"/api/units/{self.tivoli.pk}/select/", {}, format="json").status_code, 403
            )
            if stage == "before pin":
                self.assertEqual(client.post(
                    "/api/favorites/", {"destination": "purchasing"}, format="json"
                ).status_code, 201)
        self.assertEqual(list(UserActionGrant.objects.order_by("pk").values()), grants_before)
        self.assertFalse(UserActionGrant.objects.filter(
            user=self.roche_user, action=UserActionGrant.Action.CREATE_ORDER
        ).exists())

    def test_current_unit_grant_filters_inventory_favorite_and_does_not_grant_stock_access(self):
        UserFavorite.objects.create(
            user=self.multi_user,
            destination=UserFavorite.Destination.INVENTORY,
        )
        UserActionGrant.objects.filter(
            user=self.multi_user,
            business_unit=self.roche,
            action=UserActionGrant.Action.READ_STOCK,
        ).delete()
        UserActionGrant.objects.create(
            user=self.multi_user,
            business_unit=self.roche,
            action=UserActionGrant.Action.CREATE_ORDER,
        )
        client = self.client_for(self.multi_user)

        self.assertEqual(
            client.post(f"/api/units/{self.roche.pk}/select/", {}, format="json").status_code,
            200,
        )
        roche_favorites = client.get("/api/favorites/")
        self.assertEqual(roche_favorites.status_code, 200)
        self.assertNotIn(
            "inventory",
            {item["destination"] for item in roche_favorites.data["available_destinations"]},
        )
        self.assertEqual(roche_favorites.data["favorites"], [])
        self.assertEqual(client.get("/api/stock/").status_code, 403)
        self.assertEqual(
            client.post(
                "/api/favorites/",
                {"destination": "inventory"},
                format="json",
            ).status_code,
            403,
        )
        self.assertEqual(
            UserFavorite.objects.filter(
                user=self.multi_user,
                destination=UserFavorite.Destination.INVENTORY,
            ).count(),
            1,
        )

        self.assertEqual(
            client.post(f"/api/units/{self.tivoli.pk}/select/", {}, format="json").status_code,
            200,
        )
        tivoli_favorites = client.get("/api/favorites/")
        self.assertIn(
            "inventory",
            {item["destination"] for item in tivoli_favorites.data["available_destinations"]},
        )
        self.assertEqual(
            tivoli_favorites.data["favorites"],
            [{"destination": "inventory", "label": "Inventory & Warehouses"}],
        )

    def test_pin_is_idempotent_persistent_and_only_owner_can_unpin(self):
        owner = self.client_for(self.roche_user)
        destination = UserFavorite.Destination.DASHBOARD

        first = owner.post("/api/favorites/", {"destination": destination}, format="json")
        repeated = owner.post("/api/favorites/", {"destination": destination}, format="json")
        refreshed_client = self.client_for(self.roche_user)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(UserFavorite.objects.filter(user=self.roche_user, destination=destination).count(), 1)
        self.assertEqual(
            refreshed_client.get("/api/favorites/").data["favorites"],
            [{"destination": "dashboard", "label": "Overview"}],
        )
        self.assertEqual(
            self.client_for(self.multi_user).delete(f"/api/favorites/{destination}/").status_code,
            404,
        )
        self.assertEqual(owner.delete(f"/api/favorites/{destination}/").status_code, 204)
        self.assertEqual(refreshed_client.get("/api/favorites/").data["favorites"], [])

    def test_invalid_destination_is_rejected(self):
        response = self.client_for(self.roche_user).post(
            "/api/favorites/",
            {"destination": "payroll-admin"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(UserFavorite.objects.filter(user=self.roche_user).count(), 0)

    def test_favorites_require_authentication_and_session_write_requires_csrf(self):
        anonymous = APIClient()
        self.assertIn(anonymous.get("/api/favorites/").status_code, (401, 403))
        self.assertIn(
            anonymous.post("/api/favorites/", {"destination": "dashboard"}, format="json").status_code,
            (401, 403),
        )

        client = APIClient(enforce_csrf_checks=True)
        csrf = client.get("/api/auth/csrf/").data["csrfToken"]
        login = client.post(
            "/api/auth/login/",
            {"username": self.roche_user.username, "password": self.roche_password},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(login.status_code, 200)

        missing_token = client.post(
            "/api/favorites/",
            {"destination": "dashboard"},
            format="json",
        )
        self.assertEqual(missing_token.status_code, 403)
        valid_token = login.data["csrfToken"]
        saved = client.post(
            "/api/favorites/",
            {"destination": "dashboard"},
            format="json",
            HTTP_X_CSRFTOKEN=valid_token,
        )
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(client.get("/api/favorites/").status_code, 200)
