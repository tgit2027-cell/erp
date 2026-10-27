import os
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils.crypto import get_random_string
from rest_framework.test import APIClient

from .models import (
    BusinessUnit,
    CustomBusinessUnitPalette,
    LegalEntity,
    UserActionGrant,
)
from .palette_colors import build_custom_palette_colors


class BusinessUnitPaletteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            call_command("seed_demo", stdout=StringIO())

        cls.company = LegalEntity.objects.get(code="tivoli")
        cls.tivoli = BusinessUnit.objects.get(code="TVL")
        cls.roche = BusinessUnit.objects.get(code="RBB")
        cls.natuzzi = BusinessUnit.objects.get(code="NAT")

        User = get_user_model()
        cls.admin = User.objects.create_user(
            username="synthetic_palette_admin",
            password=get_random_string(32),
            is_staff=True,
        )
        cls.ordinary_user = User.objects.get(username="demo_roche")
        UserActionGrant.objects.create(
            user=cls.admin,
            business_unit=cls.tivoli,
            action=UserActionGrant.Action.READ_STOCK,
        )

    def session_client_for(self, user):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(user)
        csrf_token = client.get("/api/auth/csrf/").data["csrfToken"]
        return client, csrf_token

    def test_existing_seed_units_keep_their_palettes_and_new_units_default_to_slate(self):
        self.assertEqual(self.tivoli.theme_palette, BusinessUnit.Palette.TIVOLI)
        self.assertEqual(self.roche.theme_palette, BusinessUnit.Palette.ROCHE)
        self.assertEqual(self.natuzzi.theme_palette, BusinessUnit.Palette.NATUZZI)

        new_unit = BusinessUnit.objects.create(
            legal_entity=self.company,
            name="Synthetic New Unit",
            code="NEW",
        )
        self.assertEqual(new_unit.theme_palette, BusinessUnit.Palette.SLATE)

        client, csrf_token = self.session_client_for(self.admin)
        update = client.patch(
            f"/api/governance/business-unit-palettes/{new_unit.id}/",
            {"theme_palette": BusinessUnit.Palette.ROCHE},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(update.status_code, 200)
        new_unit.refresh_from_db()
        self.assertEqual(new_unit.theme_palette, BusinessUnit.Palette.ROCHE)

    def test_palette_choices_and_postgres_constraint_reject_unlisted_values(self):
        self.assertEqual(
            list(BusinessUnit.Palette.values),
            ["tivoli", "roche", "natuzzi", "slate"],
        )
        self.assertEqual(
            dict(BusinessUnit.Palette.choices),
            {
                "tivoli": "Tivoli orange",
                "roche": "Roche Bobois light blue",
                "natuzzi": "Natuzzi light brown",
                "slate": "Slate",
            },
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BusinessUnit.objects.filter(pk=self.tivoli.pk).update(theme_palette="neon")

        self.tivoli.refresh_from_db()
        self.assertEqual(self.tivoli.theme_palette, BusinessUnit.Palette.TIVOLI)

    def test_staff_can_assign_and_persist_palette_without_changing_data_grants(self):
        client, csrf_token = self.session_client_for(self.admin)
        grants_before = list(
            UserActionGrant.objects.filter(user=self.admin)
            .order_by("business_unit_id", "action")
            .values_list("business_unit_id", "action", "starts_at", "ends_at", "revoked_at")
        )

        listing = client.get("/api/governance/business-unit-palettes/")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(
            [palette["value"] for palette in listing.data["palettes"]],
            ["tivoli", "roche", "natuzzi", "slate"],
        )
        self.assertEqual(
            {unit["code"] for unit in listing.data["units"]},
            {"TVL", "RBB", "NAT"},
        )

        response = client.patch(
            f"/api/governance/business-unit-palettes/{self.tivoli.id}/",
            {"theme_palette": BusinessUnit.Palette.SLATE},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["unit"]["theme_palette"], BusinessUnit.Palette.SLATE)

        self.tivoli.refresh_from_db()
        self.assertEqual(self.tivoli.theme_palette, BusinessUnit.Palette.SLATE)
        self.assertEqual(
            list(
                UserActionGrant.objects.filter(user=self.admin)
                .order_by("business_unit_id", "action")
                .values_list("business_unit_id", "action", "starts_at", "ends_at", "revoked_at")
            ),
            grants_before,
        )
        organization = client.get("/api/organization/")
        self.assertEqual(organization.data["selected_unit"]["theme_palette"], BusinessUnit.Palette.SLATE)

    def test_palette_update_requires_csrf_and_rejects_invalid_values(self):
        client, csrf_token = self.session_client_for(self.admin)
        url = f"/api/governance/business-unit-palettes/{self.roche.id}/"

        missing_csrf = client.patch(
            url,
            {"theme_palette": BusinessUnit.Palette.TIVOLI},
            format="json",
        )
        self.assertEqual(missing_csrf.status_code, 403)

        invalid_palette = client.patch(
            url,
            {"theme_palette": "neon"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(invalid_palette.status_code, 400)
        self.roche.refresh_from_db()
        self.assertEqual(self.roche.theme_palette, BusinessUnit.Palette.ROCHE)

    def test_staff_can_create_and_retrieve_custom_palette(self):
        client, csrf_token = self.session_client_for(self.admin)
        response = client.post(
            "/api/governance/business-unit-palettes/",
            {"name": "Ocean Night", "accent_color": "#0050a0"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, 201)
        palette_data = response.data["palette"]
        palette = CustomBusinessUnitPalette.objects.get(pk=palette_data["id"])
        self.assertEqual(palette.name, "Ocean Night")
        self.assertEqual(palette.accent_color, "#0050A0")
        self.assertEqual(palette_data["value"], f"custom:{palette.id}")
        self.assertEqual(
            palette_data["colors"],
            build_custom_palette_colors("#0050A0"),
        )

        refreshed_listing = client.get("/api/governance/business-unit-palettes/")
        self.assertEqual(refreshed_listing.status_code, 200)
        persisted = next(
            item for item in refreshed_listing.data["palettes"]
            if item["value"] == f"custom:{palette.id}"
        )
        self.assertEqual(persisted["label"], "Ocean Night")
        self.assertEqual(persisted["accent_color"], "#0050A0")

    def test_custom_palette_validation_rejects_bad_hex_contrast_and_duplicate_names(self):
        client, csrf_token = self.session_client_for(self.admin)
        url = "/api/governance/business-unit-palettes/"
        headers = {"HTTP_X_CSRFTOKEN": csrf_token}

        invalid_hex = client.post(
            url,
            {"name": "Invalid Hex", "accent_color": "orange"},
            format="json",
            **headers,
        )
        self.assertEqual(invalid_hex.status_code, 400)
        self.assertIn("accent_color", invalid_hex.data)

        insufficient_contrast = client.post(
            url,
            {"name": "Too Light", "accent_color": "#FF0000"},
            format="json",
            **headers,
        )
        self.assertEqual(insufficient_contrast.status_code, 400)
        self.assertIn("accent_color", insufficient_contrast.data)

        missing_name = client.post(
            url,
            {"name": "  ", "accent_color": "#0050A0"},
            format="json",
            **headers,
        )
        self.assertEqual(missing_name.status_code, 400)
        self.assertIn("name", missing_name.data)

        created = client.post(
            url,
            {"name": "Ocean Night", "accent_color": "#0050A0"},
            format="json",
            **headers,
        )
        self.assertEqual(created.status_code, 201)
        duplicate = client.post(
            url,
            {"name": "ocean night", "accent_color": "#123456"},
            format="json",
            **headers,
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(CustomBusinessUnitPalette.objects.count(), 1)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CustomBusinessUnitPalette.objects.create(
                    name="Bad Persisted Color",
                    accent_color="orange",
                )

    def test_staff_can_edit_and_assign_custom_palette_without_changing_permissions(self):
        client, csrf_token = self.session_client_for(self.admin)
        created = client.post(
            "/api/governance/business-unit-palettes/",
            {"name": "Ocean Night", "accent_color": "#0050A0"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(created.status_code, 201)
        palette_id = created.data["palette"]["id"]
        palette_value = f"custom:{palette_id}"
        grants_before = list(
            UserActionGrant.objects.filter(user=self.admin)
            .order_by("business_unit_id", "action")
            .values_list("business_unit_id", "action", "starts_at", "ends_at", "revoked_at")
        )

        for unit in (self.tivoli, self.natuzzi):
            response = client.patch(
                f"/api/governance/business-unit-palettes/{unit.id}/",
                {"theme_palette": palette_value},
                format="json",
                HTTP_X_CSRFTOKEN=csrf_token,
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["unit"]["theme_palette"], "custom")
            self.assertEqual(response.data["unit"]["palette_value"], palette_value)
            unit.refresh_from_db()
            self.assertEqual(unit.custom_theme_palette_id, palette_id)

        organization = client.get("/api/organization/")
        self.assertEqual(organization.status_code, 200)
        selected_unit = organization.data["selected_unit"]
        self.assertEqual(selected_unit["theme_palette"], "custom")
        self.assertEqual(selected_unit["custom_palette"]["id"], palette_id)
        self.assertEqual(selected_unit["custom_palette"]["accent_color"], "#0050A0")

        edit = client.patch(
            f"/api/governance/business-unit-palettes/custom/{palette_id}/",
            {"name": "Autumn Ember", "accent_color": "#B24F00"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(edit.data["palette"]["label"], "Autumn Ember")
        self.assertEqual(edit.data["palette"]["accent_color"], "#B24F00")

        for unit in (self.tivoli, self.natuzzi):
            unit.refresh_from_db()
            self.assertEqual(unit.custom_theme_palette_id, palette_id)
        organization = client.get("/api/organization/")
        self.assertEqual(
            organization.data["selected_unit"]["custom_palette"]["accent_color"],
            "#B24F00",
        )
        grants_after = list(
            UserActionGrant.objects.filter(user=self.admin)
            .order_by("business_unit_id", "action")
            .values_list("business_unit_id", "action", "starts_at", "ends_at", "revoked_at")
        )
        self.assertEqual(grants_after, grants_before)

    def test_ordinary_user_cannot_create_edit_or_assign_custom_palettes(self):
        palette = CustomBusinessUnitPalette.objects.create(
            name="Synthetic Custom",
            accent_color="#0050A0",
        )
        client = APIClient()
        client.force_authenticate(user=self.ordinary_user)

        create = client.post(
            "/api/governance/business-unit-palettes/",
            {"name": "Unauthorized", "accent_color": "#0050A0"},
            format="json",
        )
        self.assertEqual(create.status_code, 403)
        edit = client.patch(
            f"/api/governance/business-unit-palettes/custom/{palette.id}/",
            {"name": "Changed", "accent_color": "#B24F00"},
            format="json",
        )
        self.assertEqual(edit.status_code, 403)
        assign = client.patch(
            f"/api/governance/business-unit-palettes/{self.roche.id}/",
            {"theme_palette": f"custom:{palette.id}"},
            format="json",
        )
        self.assertEqual(assign.status_code, 403)

        palette.refresh_from_db()
        self.roche.refresh_from_db()
        self.assertEqual(palette.name, "Synthetic Custom")
        self.assertEqual(palette.accent_color, "#0050A0")
        self.assertIsNone(self.roche.custom_theme_palette_id)

    def test_ordinary_users_and_anonymous_users_cannot_manage_palettes(self):
        ordinary_client = APIClient()
        ordinary_client.force_authenticate(user=self.ordinary_user)
        self.assertEqual(
            ordinary_client.get("/api/governance/business-unit-palettes/").status_code,
            403,
        )
        self.assertEqual(
            ordinary_client.patch(
                f"/api/governance/business-unit-palettes/{self.roche.id}/",
                {"theme_palette": BusinessUnit.Palette.TIVOLI},
                format="json",
            ).status_code,
            403,
        )

        anonymous = APIClient()
        self.assertIn(
            anonymous.get("/api/governance/business-unit-palettes/").status_code,
            (401, 403),
        )

    def test_current_user_reports_staff_flag_for_ui_gating(self):
        admin_client = APIClient()
        admin_client.force_authenticate(user=self.admin)
        ordinary_client = APIClient()
        ordinary_client.force_authenticate(user=self.ordinary_user)

        self.assertIs(admin_client.get("/api/auth/me/").data["is_staff"], True)
        self.assertIs(ordinary_client.get("/api/auth/me/").data["is_staff"], False)
