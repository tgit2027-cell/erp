import os
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils.crypto import get_random_string
from rest_framework.test import APIClient

from foundation.location_catalog import LOCATIONS
from foundation.models import (
    ApprovedSellingPrice,
    BusinessUnit,
    BusinessUnitRoleAssignment,
    BusinessUnitWorkCalendar,
    DemoUserMarker,
    Item,
    LegalEntity,
    Location,
    ProductBrand,
    ReportDataGrant,
    ReportPanelGrant,
    ReportingRole,
    StockExample,
    SyntheticCounterparty,
    UserActionGrant,
    Variant,
    WorkflowPolicyConfig,
)


class DemoAdminSeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            call_command("seed_demo", stdout=StringIO())
        cls.User = get_user_model()
        cls.admin = cls.User.objects.get(username="demo_admin")

    def test_seeded_demo_admin_has_exact_staff_role_and_read_stock_grants(self):
        admin = self.admin
        self.assertTrue(admin.is_staff)
        self.assertFalse(admin.is_superuser)
        self.assertFalse(admin.has_usable_password())
        self.assertTrue(DemoUserMarker.objects.filter(user=admin).exists())
        self.assertEqual(admin.groups.count(), 0)
        self.assertEqual(admin.user_permissions.count(), 0)

        grants = set(
            UserActionGrant.objects.filter(user=admin).values_list(
                "business_unit__code",
                "action",
            )
        )
        self.assertSetEqual(
            grants,
            {
                ("TVL", UserActionGrant.Action.READ_STOCK),
                ("RBB", UserActionGrant.Action.READ_STOCK),
                ("NAT", UserActionGrant.Action.READ_STOCK),
            },
        )

        client = APIClient()
        client.force_authenticate(user=admin)
        self.assertEqual(
            client.get("/api/governance/business-unit-palettes/").status_code,
            200,
        )

        for path in (
            "/admin/",
            "/api/users/",
            "/api/grants/",
            "/api/finance/post/",
            "/api/orders/",
        ):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 404)

    def test_private_password_command_enables_login_and_existing_grants_only(self):
        admin = self.admin
        self.assertFalse(admin.has_usable_password())

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
                username="demo_admin",
                stdout=output,
            )

        admin.refresh_from_db()
        self.assertTrue(admin.check_password(password))
        self.assertNotIn(password, output.getvalue())

        client = APIClient(enforce_csrf_checks=True)
        csrf = client.get("/api/auth/csrf/").data["csrfToken"]
        response = client.post(
            "/api/auth/login/",
            {"username": "demo_admin", "password": password},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_staff"])

        units = {
            code: unit_id
            for code, unit_id in BusinessUnit.objects.values_list("code", "id")
        }
        self.assertSetEqual(set(units), {"TVL", "RBB", "NAT"})
        for code, unit_id in units.items():
            with self.subTest(unit=code):
                self.assertEqual(
                    client.get(f"/api/stock/?unit={unit_id}").status_code,
                    200,
                )

        self.assertFalse(
            UserActionGrant.objects.filter(
                user=admin,
                action=UserActionGrant.Action.CREATE_ORDER,
            ).exists()
        )

    def test_seed_repeat_does_not_modify_other_demo_accounts(self):
        existing_accounts = {}
        for username in (
            "demo_tivoli",
            "demo_roche",
            "demo_natuzzi",
            "demo_multi",
        ):
            user = self.User.objects.get(username=username)
            existing_accounts[username] = {
                "password": user.password,
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
                "grants": set(
                    UserActionGrant.objects.filter(user=user).values_list(
                        "business_unit__code",
                        "action",
                    )
                ),
            }

        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            call_command("seed_demo", stdout=StringIO())

        for username, expected in existing_accounts.items():
            with self.subTest(username=username):
                user = self.User.objects.get(username=username)
                self.assertEqual(user.password, expected["password"])
                self.assertEqual(user.is_staff, expected["is_staff"])
                self.assertEqual(user.is_superuser, expected["is_superuser"])
                self.assertSetEqual(
                    set(
                        UserActionGrant.objects.filter(user=user).values_list(
                            "business_unit__code",
                            "action",
                        )
                    ),
                    expected["grants"],
                )

    def test_seed_refuses_unmarked_username_collision_without_modifying_account(self):
        User = self.User
        # Accounts with audit history cannot be deleted, so retire the seeded one by renaming it.
        User.objects.filter(username="demo_admin").update(username="demo_admin_retired")
        unrelated = User.objects.create_user(
            username="demo_admin",
            password=get_random_string(32),
            is_staff=False,
            is_superuser=False,
        )
        original_password = unrelated.password

        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            with self.assertRaises(CommandError):
                call_command("seed_demo", stdout=StringIO())

        unrelated.refresh_from_db()
        self.assertEqual(unrelated.password, original_password)
        self.assertFalse(unrelated.is_staff)
        self.assertFalse(unrelated.is_superuser)
        self.assertFalse(DemoUserMarker.objects.filter(user=unrelated).exists())


class DemoSeedIdempotencySnapshotTests(TestCase):
    seed_usernames = (
        "demo_tivoli",
        "demo_roche",
        "demo_natuzzi",
        "demo_multi",
        "demo_admin",
    )

    @classmethod
    def snapshot(cls):
        User = get_user_model()
        seeded_users = User.objects.filter(username__in=cls.seed_usernames).order_by("username")
        return {
            "counts": {
                "legal_entities": LegalEntity.objects.count(),
                "business_units": BusinessUnit.objects.count(),
                "work_calendars": BusinessUnitWorkCalendar.objects.count(),
                "workflow_policies": WorkflowPolicyConfig.objects.count(),
                "brands": ProductBrand.objects.count(),
                "locations": Location.objects.count(),
                "location_unit_links": Location.objects.values(
                    "pk", "business_units"
                ).distinct().count(),
                "items": Item.objects.count(),
                "variants": Variant.objects.count(),
                "stock_examples": StockExample.objects.count(),
                "seed_users": seeded_users.count(),
                "demo_markers": DemoUserMarker.objects.filter(
                    user__username__in=cls.seed_usernames
                ).count(),
                "user_action_grants": UserActionGrant.objects.filter(
                    user__username__in=cls.seed_usernames
                ).count(),
                "business_unit_role_assignments": BusinessUnitRoleAssignment.objects.count(),
                "approved_prices": ApprovedSellingPrice.objects.count(),
                "counterparties": SyntheticCounterparty.objects.count(),
                "reporting_roles": ReportingRole.objects.count(),
                "report_panel_grants": ReportPanelGrant.objects.count(),
                "report_data_grants": ReportDataGrant.objects.count(),
            },
            "entities": tuple(
                LegalEntity.objects.order_by("pk").values_list("code", "name")
            ),
            "units": tuple(
                BusinessUnit.objects.order_by("pk").values_list(
                    "code", "legal_entity__code", "name", "theme_palette"
                )
            ),
            "work_calendars": tuple(
                BusinessUnitWorkCalendar.objects.order_by("pk").values_list(
                    "business_unit__code", "closed_weekdays", "closed_dates", "is_configured"
                )
            ),
            "workflow_policies": tuple(
                WorkflowPolicyConfig.objects.order_by("pk").values_list(
                    "business_unit__code", "storage_exception_approver_roles"
                )
            ),
            "brands": tuple(ProductBrand.objects.order_by("pk").values_list("code", "name")),
            "locations": tuple(
                Location.objects.order_by("pk").values_list(
                    "code", "legal_entity__code", "name", "parent__code"
                )
            ),
            "location_unit_rows": tuple(
                sorted(Location.objects.values_list("code", "business_units__code"))
            ),
            "items": tuple(
                Item.objects.order_by("pk").values_list(
                    "item_code", "brand__code", "description"
                )
            ),
            "variants": tuple(
                Variant.objects.order_by("pk").values_list(
                    "sku", "item__item_code", "description"
                )
            ),
            "stock_examples": tuple(
                StockExample.objects.order_by("pk").values_list(
                    "item__item_code",
                    "variant__sku",
                    "location__code",
                    "responsible_business_unit__code",
                    "quantity",
                )
            ),
            "users": tuple(
                (
                    user.username,
                    user.first_name,
                    user.last_name,
                    user.is_active,
                    user.is_staff,
                    user.is_superuser,
                    user.has_usable_password(),
                    tuple(user.groups.order_by("pk").values_list("pk", flat=True)),
                    tuple(user.user_permissions.order_by("pk").values_list("pk", flat=True)),
                )
                for user in seeded_users
            ),
            "markers": tuple(
                DemoUserMarker.objects.filter(
                    user__username__in=cls.seed_usernames
                ).order_by("user__username").values_list("user__username", flat=True)
            ),
            "user_action_grants": tuple(
                UserActionGrant.objects.filter(
                    user__username__in=cls.seed_usernames
                ).order_by(
                    "user__username", "business_unit__code", "action", "pk"
                ).values_list(
                    "user__username",
                    "business_unit__code",
                    "action",
                    "revoked_at",
                )
            ),
            "business_unit_role_assignments": tuple(
                BusinessUnitRoleAssignment.objects.order_by("pk").values_list(
                    "user__username", "business_unit__code", "role", "revoked_at"
                )
            ),
            "approved_prices": tuple(
                ApprovedSellingPrice.objects.order_by("pk").values_list(
                    "business_unit__code",
                    "variant__sku",
                    "amount",
                    "revision",
                    "is_current",
                    "approved_by__username",
                )
            ),
            "counterparties": tuple(
                SyntheticCounterparty.objects.order_by("pk").values_list(
                    "party_type", "code", "name"
                )
            ),
            "reporting_roles": tuple(
                ReportingRole.objects.order_by("pk").values_list("code", "name")
            ),
            "report_panel_grants": tuple(
                ReportPanelGrant.objects.order_by("pk").values_list(
                    "user__username", "role_id", "panel", "revoked_at"
                )
            ),
            "report_data_grants": tuple(
                ReportDataGrant.objects.order_by("pk").values_list(
                    "user__username",
                    "role_id",
                    "capability",
                    "business_unit__code",
                    "legal_entity__code",
                    "revoked_at",
                )
            ),
            "auth_groups": tuple(Group.objects.order_by("pk").values_list("pk", "name")),
            "auth_permissions": tuple(
                Permission.objects.order_by("pk").values_list("pk", "content_type_id", "codename")
            ),
        }

    def test_seed_documents_expected_first_run_and_second_run_is_identical(self):
        before = self.snapshot()
        for key in before["counts"]:
            self.assertEqual(
                before["counts"][key],
                0,
                f"Disposable test database unexpectedly contains seeded table {key}.",
            )

        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            call_command("seed_demo", stdout=StringIO())
        after_first = self.snapshot()

        expected_first_run_counts = {
            "legal_entities": 1,
            "business_units": 3,
            "work_calendars": 3,
            "workflow_policies": 3,
            "brands": 3,
            "locations": 22,
            "location_unit_links": 54,
            "items": 6,
            "variants": 12,
            "stock_examples": 12,
            "seed_users": 5,
            "demo_markers": 5,
            "user_action_grants": 9,
            "business_unit_role_assignments": 0,
            "approved_prices": 0,
            "counterparties": 0,
            "reporting_roles": 0,
            "report_panel_grants": 0,
            "report_data_grants": 0,
        }
        self.assertEqual(after_first["counts"], expected_first_run_counts)
        self.assertEqual(after_first["auth_groups"], before["auth_groups"])
        self.assertEqual(after_first["auth_permissions"], before["auth_permissions"])
        self.assertEqual(
            {user[0] for user in after_first["users"] if user[4]},
            {"demo_admin"},
        )
        self.assertTrue(all(not user[6] for user in after_first["users"]))
        self.assertTrue(all(not user[7] and not user[8] for user in after_first["users"]))
        self.assertEqual(after_first["business_unit_role_assignments"], ())
        self.assertEqual(after_first["approved_prices"], ())
        self.assertEqual(after_first["counterparties"], ())
        self.assertEqual(after_first["reporting_roles"], ())
        self.assertEqual(after_first["report_panel_grants"], ())
        self.assertEqual(after_first["report_data_grants"], ())
        self.assertEqual(
            set(after_first["user_action_grants"]),
            {
                ("demo_tivoli", "TVL", UserActionGrant.Action.READ_STOCK, None),
                ("demo_roche", "RBB", UserActionGrant.Action.READ_STOCK, None),
                ("demo_natuzzi", "NAT", UserActionGrant.Action.READ_STOCK, None),
                ("demo_multi", "TVL", UserActionGrant.Action.READ_STOCK, None),
                ("demo_multi", "RBB", UserActionGrant.Action.READ_STOCK, None),
                ("demo_multi", "NAT", UserActionGrant.Action.READ_STOCK, None),
                ("demo_admin", "TVL", UserActionGrant.Action.READ_STOCK, None),
                ("demo_admin", "RBB", UserActionGrant.Action.READ_STOCK, None),
                ("demo_admin", "NAT", UserActionGrant.Action.READ_STOCK, None),
            },
        )
        self.assertEqual(
            after_first["location_unit_rows"],
            tuple(sorted(
                (code, unit_code)
                for code, _name, _kind, _area, _street, _sub, _parent, unit_codes in LOCATIONS
                for unit_code in unit_codes
            )),
        )

        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            call_command("seed_demo", stdout=StringIO())
        after_second = self.snapshot()
        self.assertEqual(after_second, after_first)
