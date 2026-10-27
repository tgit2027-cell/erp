import os
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.crypto import get_random_string
from rest_framework.test import APIClient

from .access import active_grants
from .models import (
    BusinessUnit,
    DemoUserMarker,
    Item,
    LegalEntity,
    Location,
    ProductBrand,
    StockExample,
    UserActionGrant,
)


class MilestoneOneTests(TestCase):
    PREVIEW_ORIGIN = "https://m1-preview.example.test:5000"

    @classmethod
    def setUpTestData(cls):
        cls.seed_output = StringIO()
        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            call_command("seed_demo", stdout=cls.seed_output)
        User = get_user_model()
        cls.tivoli = BusinessUnit.objects.get(code="TVL")
        cls.roche = BusinessUnit.objects.get(code="RBB")
        cls.natuzzi = BusinessUnit.objects.get(code="NAT")
        cls.company = LegalEntity.objects.get(code="tivoli")
        cls.rbb_user = User.objects.get(username="demo_roche")
        cls.tivoli_user = User.objects.get(username="demo_tivoli")
        cls.natuzzi_user = User.objects.get(username="demo_natuzzi")
        cls.multi_user = User.objects.get(username="demo_multi")
        cls.stock_rows = list(StockExample.objects.select_related("responsible_business_unit").order_by("id"))
        cls.nat_stock = next(
            row for row in cls.stock_rows if row.responsible_business_unit_id == cls.natuzzi.id
        )
        cls.roche_password = get_random_string(32)
        cls.rbb_user.set_password(cls.roche_password)
        cls.rbb_user.save(update_fields=["password"])

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_seed_created_one_company_three_units_and_synthetic_catalog(self):
        self.assertEqual(LegalEntity.objects.count(), 1)
        self.assertEqual(BusinessUnit.objects.count(), 3)
        self.assertEqual(ProductBrand.objects.count(), 3)
        self.assertEqual(Item.objects.count(), 6)
        self.assertEqual(sum(item.variants.count() for item in Item.objects.all()), 12)
        self.assertEqual(StockExample.objects.count(), 12)
        self.assertTrue(all(item.is_synthetic for item in Item.objects.all()))
        self.assertTrue(all(row.is_synthetic for row in StockExample.objects.all()))
        self.assertFalse(self.natuzzi_user.has_usable_password())

    def test_anonymous_requests_cannot_read_protected_data_or_export(self):
        client = APIClient()
        self.assertEqual(client.get("/api/auth/me/").data, {"authenticated": False})
        for url in ("/api/organization/", "/api/stock/", f"/api/stock/{self.nat_stock.id}/"):
            with self.subTest(url=url):
                self.assertIn(client.get(url).status_code, (401, 403))
        self.assertIn(client.post(f"/api/units/{self.roche.id}/select/", {}, format="json").status_code, (401, 403))
        self.assertEqual(client.get("/api/export/stock/").status_code, 404)

    def test_roche_lists_match_stock_independently_derived_from_active_read_grants(self):
        client = self.client_for(self.rbb_user)
        grant_unit_ids = set(
            active_grants(self.rbb_user, UserActionGrant.Action.READ_STOCK).values_list(
                "business_unit_id", flat=True
            )
        )
        expected = set(
            StockExample.objects.filter(
                responsible_business_unit_id__in=grant_unit_ids,
                location__business_units__id__in=grant_unit_ids,
            ).values_list("id", flat=True)
        )
        response = client.get("/api/stock/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["id"] for row in response.data["results"]}, expected)
        self.assertEqual(len(expected), 4)
        self.assertTrue(all(row["unit_code"] == "RBB" for row in response.data["results"]))
        self.assertEqual(response.data["pagination"]["total_count"], 4)

    def test_roche_cannot_retrieve_natuzzi_stock_by_direct_id(self):
        response = self.client_for(self.rbb_user).get(f"/api/stock/{self.nat_stock.id}/")
        self.assertEqual(response.status_code, 404)

    def test_query_parameter_and_forged_unit_header_cannot_expand_scope(self):
        client = self.client_for(self.rbb_user)
        query_response = client.get(f"/api/stock/?unit={self.natuzzi.id}")
        header_response = client.get("/api/stock/", HTTP_X_BUSINESS_UNIT=str(self.natuzzi.id))
        self.assertEqual(query_response.status_code, 403)
        self.assertEqual(header_response.status_code, 403)

    def test_search_does_not_reveal_other_units_records(self):
        response = self.client_for(self.rbb_user).get("/api/stock/?search=Natuzzi")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"], [])
        self.assertEqual(response.data["pagination"]["total_count"], 0)

    def test_stock_search_preserves_all_five_search_fields_with_business_unit_scope(self):
        client = self.client_for(self.rbb_user)
        row = next(
            stock
            for stock in self.stock_rows
            if stock.responsible_business_unit_id == self.roche.id
        )
        terms = (
            row.item.item_code,
            row.item.description,
            row.item.brand.name,
            row.variant.sku,
            row.location.name,
        )
        for term in terms:
            with self.subTest(search_field_value=term):
                response = client.get(
                    "/api/stock/",
                    {"search": term, "page": 1, "page_size": 100},
                )
                self.assertEqual(response.status_code, 200, response.data)
                self.assertIn(row.id, {result["id"] for result in response.data["results"]})
                self.assertTrue(
                    all(result["unit_code"] == "RBB" for result in response.data["results"])
                )

    def test_stock_paginates_after_business_unit_location_and_search_scope(self):
        client = self.client_for(self.rbb_user)
        expected_ids = list(
            StockExample.objects.filter(
                responsible_business_unit=self.roche,
                location__business_units=self.roche,
                is_synthetic=True,
            )
            .order_by("item__item_code", "variant__sku", "pk")
            .values_list("pk", flat=True)
        )
        first = client.get("/api/stock/?page=1&page_size=2")
        second = client.get("/api/stock/?page=2&page_size=2")
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data["pagination"], {
            "page": 1,
            "page_size": 2,
            "total_count": 4,
            "total_pages": 2,
            "previous_page": None,
            "next_page": 2,
        })
        self.assertEqual(
            [row["id"] for row in first.data["results"] + second.data["results"]],
            expected_ids,
        )

        location = Location.objects.get(code="SRBB")
        location_page = client.get(
            f"/api/stock/?location={location.pk}&page=1&page_size=1"
        )
        self.assertEqual(location_page.status_code, 200, location_page.data)
        expected_location_count = StockExample.objects.filter(
            responsible_business_unit=self.roche,
            location=location,
            location__business_units=self.roche,
            is_synthetic=True,
        ).count()
        self.assertEqual(location_page.data["pagination"]["total_count"], expected_location_count)
        self.assertEqual(len(location_page.data["results"]), min(1, expected_location_count))
        self.assertTrue(all(row["location_name"] == location.name for row in location_page.data["results"]))

        unauthorized_location = Location.objects.get(code="SNAT")
        denied_location = client.get(f"/api/stock/?location={unauthorized_location.pk}&page_size=1")
        denied_unit = client.get(f"/api/stock/?unit={self.natuzzi.pk}&page=2&page_size=1")
        self.assertEqual(denied_location.status_code, 403)
        self.assertEqual(denied_unit.status_code, 403)

    def test_narrow_stock_page_preserves_scope_order_and_serialized_fields(self):
        from .access import scoped_stock_queryset, stock_page_queryset
        from .serializers import StockExampleSerializer

        user = self.rbb_user
        records = scoped_stock_queryset(self.roche, user)
        expected = list(StockExampleSerializer(list(records), many=True).data)
        self.assertGreater(len(expected), 1)
        actual = []
        for page in range(1, len(expected) + 1):
            with self.assertNumQueries(1):
                result = list(StockExampleSerializer(
                    list(stock_page_queryset(
                        records, page=page, page_size=1, total_count=len(expected),
                    )), many=True,
                ).data)
            self.assertEqual(result, expected[page - 1:page])
            actual.extend(result)
        self.assertEqual(actual, expected)
        for location_id in records.values_list("location_id", flat=True).distinct():
            scoped = records.filter(location_id=location_id)
            count = scoped.count()
            self.assertEqual(
                list(stock_page_queryset(
                    scoped, page=1, page_size=1, total_count=count,
                ).values_list("pk", flat=True)),
                list(scoped.values_list("pk", flat=True)[:1]),
            )

    def test_stock_rejects_invalid_or_out_of_range_pages(self):
        client = self.client_for(self.rbb_user)
        for query in ("page=0", "page_size=101", "page=3&page_size=2"):
            with self.subTest(query=query):
                self.assertEqual(client.get(f"/api/stock/?{query}").status_code, 400)

    def test_export_endpoint_is_not_implemented(self):
        self.assertEqual(self.client_for(self.rbb_user).get("/api/export/stock/").status_code, 404)

    def test_location_and_organization_view_are_limited_to_authorized_unit(self):
        response = self.client_for(self.rbb_user).get("/api/organization/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([unit["code"] for unit in response.data["units"]], ["RBB"])
        location_codes = {location["code"] for location in response.data["locations"]}
        self.assertIn("SRBB", location_codes)
        self.assertIn("W41-01", location_codes)
        self.assertNotIn("STIV", location_codes)
        self.assertNotIn("SNAT", location_codes)

    def test_multi_unit_user_switches_only_within_active_grants(self):
        client = self.client_for(self.multi_user)
        response = client.post(f"/api/units/{self.natuzzi.id}/select/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        stock = client.get("/api/stock/")
        self.assertEqual(stock.status_code, 200)
        self.assertTrue(all(row["unit_code"] == "NAT" for row in stock.data["results"]))
        denied = client.post(f"/api/units/{self.roche.id + 10000}/select/", {}, format="json")
        self.assertEqual(denied.status_code, 403)

    def test_demo_multi_has_exactly_the_three_read_stock_grants(self):
        active_pairs = set(
            active_grants(self.multi_user).values_list("business_unit__code", "action")
        )
        self.assertEqual(
            active_pairs,
            {
                ("TVL", UserActionGrant.Action.READ_STOCK),
                ("RBB", UserActionGrant.Action.READ_STOCK),
                ("NAT", UserActionGrant.Action.READ_STOCK),
            },
        )
        self.assertFalse(
            active_grants(self.multi_user, UserActionGrant.Action.CREATE_ORDER).exists()
        )

    def test_revoked_grant_is_rechecked_and_removed_from_context(self):
        client = self.client_for(self.multi_user)
        self.assertEqual(client.post(f"/api/units/{self.roche.id}/select/", {}, format="json").status_code, 200)
        UserActionGrant.objects.filter(
            user=self.multi_user,
            business_unit=self.roche,
            action=UserActionGrant.Action.READ_STOCK,
        ).update(revoked_at=timezone.now())
        explicit_revoked_read = client.get(f"/api/stock/?unit={self.roche.id}")
        self.assertEqual(explicit_revoked_read.status_code, 403)
        context = client.get("/api/organization/")
        self.assertEqual(context.status_code, 200)
        self.assertNotIn("RBB", {unit["code"] for unit in context.data["units"]})
        self.assertNotEqual(context.data["selected_unit"]["id"], self.roche.id)

    def test_read_stock_and_create_order_are_separate_grants(self):
        User = get_user_model()
        order_only = User.objects.create_user(username="test_order_only", password=None)
        DemoUserMarker.objects.create(user=order_only)
        UserActionGrant.objects.create(
            user=order_only,
            business_unit=self.roche,
            action=UserActionGrant.Action.CREATE_ORDER,
        )
        self.assertTrue(active_grants(order_only, UserActionGrant.Action.CREATE_ORDER).exists())
        self.assertFalse(active_grants(order_only, UserActionGrant.Action.READ_STOCK).exists())
        client = self.client_for(order_only)

        organization = client.get("/api/organization/")
        self.assertEqual(organization.status_code, 200)
        self.assertEqual([unit["code"] for unit in organization.data["units"]], ["RBB"])
        location_codes = {location["code"] for location in organization.data["locations"]}
        self.assertIn("SRBB", location_codes)
        self.assertIn("W41-01", location_codes)
        self.assertNotIn("STIV", location_codes)
        self.assertNotIn("SNAT", location_codes)

        selected = client.post(f"/api/units/{self.roche.id}/select/", {}, format="json")
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.data["selected_unit"]["code"], "RBB")
        self.assertEqual(client.get("/api/stock/").status_code, 403)

        self.assertFalse(
            active_grants(self.rbb_user, UserActionGrant.Action.CREATE_ORDER).exists(),
            "The seeded Roche user is a view-only fixture.",
        )

    def test_changing_brand_prefix_does_not_change_responsible_unit_access(self):
        client = self.client_for(self.rbb_user)
        before = {row["id"] for row in client.get("/api/stock/").data["results"]}
        row = next(record for record in self.stock_rows if record.responsible_business_unit_id == self.roche.id)
        row.item.item_code = "NAT-900"
        row.item.save(update_fields=["item_code"])
        after = {result["id"] for result in client.get("/api/stock/").data["results"]}
        natuzzi_access = self.client_for(self.natuzzi_user).get("/api/stock/").data["results"]
        self.assertEqual(before, after)
        self.assertIn(row.id, after)
        self.assertNotIn(row.id, {result["id"] for result in natuzzi_access})

    def test_location_cycles_are_rejected(self):
        first = Location.objects.create(
            legal_entity=self.company,
            code="test-cycle-first",
            name="Synthetic cycle first",
        )
        second = Location.objects.create(
            legal_entity=self.company,
            parent=first,
            code="test-cycle-second",
            name="Synthetic cycle second",
        )
        first.parent = second
        with self.assertRaises(ValidationError):
            first.save()

    def test_cross_company_parent_is_rejected(self):
        other_company = LegalEntity.objects.create(code="other-demo", name="Other synthetic company")
        parent = Location.objects.create(
            legal_entity=other_company,
            code="other-parent",
            name="Other company location",
        )
        child = Location(
            legal_entity=self.company,
            parent=parent,
            code="invalid-cross-company-child",
            name="Invalid child",
        )
        with self.assertRaises(ValidationError):
            child.save()

    def test_seed_is_repeatable_and_does_not_delete_existing_records(self):
        initial_count = StockExample.objects.count()
        custom = Item.objects.create(
            brand=ProductBrand.objects.get(code="TVF"),
            item_code="TVF-999",
            description="Synthetic unrelated custom record",
        )
        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            call_command("seed_demo", stdout=StringIO())
        self.assertEqual(StockExample.objects.count(), initial_count)
        self.assertTrue(Item.objects.filter(pk=custom.pk).exists())
        self.assertEqual(Item.objects.filter(item_code="TVF-001").count(), 1)

    def test_seed_refuses_non_demo_environment_without_deleting_records(self):
        before = StockExample.objects.count()
        with patch.dict(os.environ, {"APP_ENV": "production"}):
            with self.assertRaises(CommandError):
                call_command("seed_demo", stdout=StringIO())
        self.assertEqual(StockExample.objects.count(), before)

    def test_demo_password_setup_reads_privately_and_does_not_echo_the_password(self):
        password = get_random_string(32)
        output = StringIO()
        with (
            patch.dict(os.environ, {"APP_ENV": "demo"}),
            patch(
                "foundation.management.commands.set_demo_password.getpass",
                side_effect=[password, password],
            ),
        ):
            call_command(
                "set_demo_password",
                username=self.natuzzi_user.username,
                stdout=output,
            )
        self.natuzzi_user.refresh_from_db()
        self.assertTrue(self.natuzzi_user.check_password(password))
        self.assertNotIn(password, output.getvalue())

    def test_login_logout_and_csrf_protection(self):
        client = APIClient(enforce_csrf_checks=True)
        csrf = client.get("/api/auth/csrf/").data["csrfToken"]
        missing_login_csrf = client.post(
            "/api/auth/login/",
            {"username": self.rbb_user.username, "password": self.roche_password},
            format="json",
        )
        self.assertEqual(missing_login_csrf.status_code, 403)

        login_response = client.post(
            "/api/auth/login/",
            {"username": self.rbb_user.username, "password": self.roche_password},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertEqual(login_response.data["username"], self.rbb_user.username)
        self.assertTrue(client.cookies["sessionid"]["httponly"])
        self.assertEqual(client.cookies["sessionid"]["samesite"], "Lax")
        self.assertEqual(client.get("/api/auth/me/").status_code, 200)

        no_csrf_logout = client.post("/api/auth/logout/", {}, format="json")
        self.assertEqual(no_csrf_logout.status_code, 403)
        logout_csrf = client.get("/api/auth/csrf/").data["csrfToken"]
        logout_response = client.post(
            "/api/auth/logout/",
            {},
            format="json",
            HTTP_X_CSRFTOKEN=logout_csrf,
        )
        self.assertEqual(logout_response.status_code, 204)
        self.assertEqual(client.get("/api/auth/me/").data, {"authenticated": False})

    @override_settings(LOGIN_FAILURE_LIMIT=3)
    def test_repeated_failed_logins_lock_the_username_even_for_the_right_password(self):
        cache.clear()
        self.addCleanup(cache.clear)
        client = APIClient()
        for _ in range(3):
            failed = client.post(
                "/api/auth/login/",
                {"username": self.rbb_user.username, "password": "wrong-password"},
                format="json",
            )
            self.assertEqual(failed.status_code, 400)
        locked = client.post(
            "/api/auth/login/",
            {"username": self.rbb_user.username.upper(), "password": self.roche_password},
            format="json",
        )
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(client.get("/api/auth/me/").data, {"authenticated": False})

        other_user = client.post(
            "/api/auth/login/",
            {"username": self.natuzzi_user.username, "password": "wrong-password"},
            format="json",
        )
        self.assertEqual(other_user.status_code, 400)

    def test_successful_login_clears_earlier_failed_attempts(self):
        cache.clear()
        self.addCleanup(cache.clear)
        client = APIClient()
        with override_settings(LOGIN_FAILURE_LIMIT=2):
            client.post(
                "/api/auth/login/",
                {"username": self.rbb_user.username, "password": "wrong-password"},
                format="json",
            )
            ok = client.post(
                "/api/auth/login/",
                {"username": self.rbb_user.username, "password": self.roche_password},
                format="json",
            )
            self.assertEqual(ok.status_code, 200)
            client.post(
                "/api/auth/login/",
                {"username": self.rbb_user.username, "password": "wrong-password"},
                format="json",
            )
            again = client.post(
                "/api/auth/login/",
                {"username": self.rbb_user.username, "password": self.roche_password},
                format="json",
            )
            self.assertEqual(again.status_code, 200)

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://m1-preview.example.test:5000"])
    def test_session_login_scopes_stock_and_logout_revokes_access(self):
        client = APIClient(enforce_csrf_checks=True)
        origin = self.PREVIEW_ORIGIN
        csrf = client.get("/api/auth/csrf/", HTTP_ORIGIN=origin).data["csrfToken"]
        self.assertIn("csrftoken", client.cookies)

        missing_csrf_login = client.post(
            "/api/auth/login/",
            {"username": self.rbb_user.username, "password": self.roche_password},
            format="json",
            HTTP_ORIGIN=origin,
        )
        self.assertEqual(missing_csrf_login.status_code, 403)

        login_response = client.post(
            "/api/auth/login/",
            {"username": self.rbb_user.username, "password": self.roche_password},
            format="json",
            HTTP_ORIGIN=origin,
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertEqual(login_response.data["username"], self.rbb_user.username)
        self.assertIn("sessionid", client.cookies)
        self.assertTrue(client.cookies["sessionid"]["httponly"])

        stock_response = client.get("/api/stock/")
        self.assertEqual(stock_response.status_code, 200)
        self.assertEqual(len(stock_response.data["results"]), 4)
        self.assertTrue(all(row["unit_code"] == "RBB" for row in stock_response.data["results"]))
        self.assertEqual(client.get(f"/api/stock/{self.nat_stock.id}/").status_code, 404)
        self.assertEqual(client.get(f"/api/stock/?unit={self.natuzzi.id}").status_code, 403)

        missing_csrf_logout = client.post(
            "/api/auth/logout/",
            {},
            format="json",
            HTTP_ORIGIN=origin,
        )
        self.assertEqual(missing_csrf_logout.status_code, 403)
        self.assertEqual(client.get("/api/auth/me/").data["authenticated"], True)

        logout_csrf = client.get("/api/auth/csrf/", HTTP_ORIGIN=origin).data["csrfToken"]
        logout_response = client.post(
            "/api/auth/logout/",
            {},
            format="json",
            HTTP_ORIGIN=origin,
            HTTP_X_CSRFTOKEN=logout_csrf,
        )
        self.assertEqual(logout_response.status_code, 204)
        self.assertEqual(client.get("/api/auth/me/").data, {"authenticated": False})
        self.assertEqual(client.get("/api/stock/").status_code, 403)

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://m1-preview.example.test:5000"])
    def test_configured_preview_origin_accepts_login_with_valid_csrf_token(self):
        client = APIClient(enforce_csrf_checks=True)
        csrf = client.get("/api/auth/csrf/").data["csrfToken"]

        response = client.post(
            "/api/auth/login/",
            {"username": self.rbb_user.username, "password": self.roche_password},
            format="json",
            HTTP_ORIGIN=self.PREVIEW_ORIGIN,
            HTTP_X_CSRFTOKEN=csrf,
        )

        self.assertEqual(response.status_code, 200)

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://m1-preview.example.test:5000"])
    def test_untrusted_origin_is_rejected_even_with_valid_csrf_token(self):
        client = APIClient(enforce_csrf_checks=True)
        csrf = client.get("/api/auth/csrf/").data["csrfToken"]

        response = client.post(
            "/api/auth/login/",
            {"username": self.rbb_user.username, "password": self.roche_password},
            format="json",
            HTTP_ORIGIN="https://untrusted.example.test:5000",
            HTTP_X_CSRFTOKEN=csrf,
        )

        self.assertEqual(response.status_code, 403)

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://m1-preview.example.test:5000"])
    def test_configured_preview_origin_still_rejects_missing_csrf_token(self):
        client = APIClient(enforce_csrf_checks=True)
        client.get("/api/auth/csrf/")

        response = client.post(
            "/api/auth/login/",
            {"username": self.rbb_user.username, "password": self.roche_password},
            format="json",
            HTTP_ORIGIN=self.PREVIEW_ORIGIN,
        )

        self.assertEqual(response.status_code, 403)

