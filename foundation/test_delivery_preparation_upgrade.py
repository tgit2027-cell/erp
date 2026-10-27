"""0021 existing-data upgrade proof; disposable PostgreSQL only."""
import json
import re
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from .models import SalesQuote, UserFavorite
from . import test_collection_favorite_upgrade as collection_upgrade
from .test_purchasing_workflows import PurchasingFixtureMixin


class DeliveryPreparationUpgradeTests(PurchasingFixtureMixin, TransactionTestCase):
    def test_0020_to_0021_preserves_existing_favorites_business_and_permission_rows(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_setting('data_directory')")
            database, directory = cursor.fetchone()
        self.assertEqual(connection.vendor, "postgresql")
        self.assertTrue(database.startswith("test_"))
        self.assertFalse(Path(directory).resolve().is_relative_to(settings.BASE_DIR / ".runtime"))
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        old = ("foundation", "0020_collection_function_favorite")
        new = ("foundation", "0021_delivery_preparation_favorite")
        try:
            self.create_fixture()
            self.make_receipt(charges=[])
            result = self.make_quote(key="TEST-ONLY-preparation-upgrade-quote")
            self.pay(SalesQuote.objects.get(pk=result["quote_id"]),
                     key="TEST-ONLY-preparation-upgrade-collection", amount="2000.00")
            for user, destination in (
                (self.sales, "retail"), (self.sales, "retail.collections"),
                (self.sales, "retail.actions"), (self.sales, "delivery.quote"),
                (self.roche_user, "inventory.locations"), (self.roche_user, "delivery"),
            ):
                UserFavorite.objects.create(user=user, destination=destination)
            MigrationExecutor(connection).migrate([old])
            historical = MigrationExecutor(connection).loader.project_state([old]).apps
            before = collection_upgrade.CollectionFavoriteUpgradeTests.snapshot(self, historical)
            old_choices = MigrationExecutor(connection).loader.project_state([old]).apps.get_model(
                "foundation", "UserFavorite")._meta.get_field("destination").choices
            with CaptureQueriesContext(connection) as queries:
                plan = MigrationExecutor(connection).migration_plan([new])
                self.assertEqual([(migration.name, backwards) for migration, backwards in plan],
                                 [(new[1], False)])
                call_command("migrate", "foundation", new[1], interactive=False, verbosity=0)
            self.assertTrue(before == collection_upgrade.CollectionFavoriteUpgradeTests.snapshot(self, historical),
                            "An existing model count or row digest changed")
            new_choices = MigrationExecutor(connection).loader.project_state([new]).apps.get_model(
                "foundation", "UserFavorite")._meta.get_field("destination").choices
            addition = ("delivery.preparation", "Delivery · Picking preparation")
            self.assertEqual(set(new_choices) - set(old_choices), {addition})
            self.assertEqual([choice for choice in new_choices if choice != addition], list(old_choices))
            mutations = [query["sql"] for query in queries if re.match(
                r"^\s*(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE)\b", query["sql"], re.I)]
            self.assertEqual(len(mutations), 1)
            self.assertTrue(mutations[0].startswith('INSERT INTO "django_migrations"'))
            owner, other = APIClient(), APIClient()
            owner.force_login(self.sales)
            other.force_login(self.roche_user)
            self.assertEqual({row["destination"] for row in owner.get("/api/favorites/").data["favorites"]},
                             {"retail", "retail.collections", "retail.actions", "delivery.quote"})
            self.assertEqual({row["destination"] for row in other.get("/api/favorites/").data["favorites"]},
                             {"inventory.locations", "delivery"})
            self.assertEqual(owner.post("/api/favorites/",
                                        {"destination": "delivery.preparation"}, format="json").status_code, 201)
            self.assertEqual(UserFavorite.objects.count(), 7)
            self.assertNotIn("delivery.preparation", {
                row["destination"] for row in other.get("/api/favorites/").data["favorites"]})
            relevant = ("auth.User", "foundation.UserFavorite", "foundation.UserActionGrant",
                        "foundation.BusinessUnitRoleAssignment", "foundation.SalesQuote",
                        "foundation.QuotePayment", "foundation.QuoteReservation",
                        "foundation.StockMovement", "foundation.StockBalance",
                        "foundation.JournalEntry", "foundation.JournalLine")
            print("Synthetic 0020→0021 upgrade: all existing rows/counts/digests preserved; "
                  "only django_migrations INSERT; existing personal Favorites and new pin verified.")
            print("Preserved fixture counts: " + json.dumps({
                key: value for key, value in before["counts"].items() if key in relevant}, sort_keys=True))
        finally:
            MigrationExecutor(connection).migrate(latest)
