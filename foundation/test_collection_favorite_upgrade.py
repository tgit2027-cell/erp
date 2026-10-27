"""Existing-data Favorites upgrade rehearsal, disposable PostgreSQL only."""
import hashlib
import json
import re
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from .models import UserFavorite
from .test_purchasing_workflows import PurchasingFixtureMixin


class CollectionFavoriteUpgradeTests(PurchasingFixtureMixin, TransactionTestCase):
    def snapshot(self, foundation_apps=None):
        models = [
            model for label in ("foundation", "auth", "sessions", "contenttypes")
            for model in (foundation_apps if label == "foundation" and foundation_apps else apps
                          ).get_app_config(label).get_models(include_auto_created=True)
        ]
        rows = {model._meta.label: list(model.objects.order_by("pk").values()) for model in models}
        return {
            "counts": {name: len(values) for name, values in rows.items()},
            # Never include credentials, payloads or row contents in test output.
            "digest": hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest(),
        }

    def test_existing_favorites_transactions_and_permissions_survive_0019_to_0020(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_setting('data_directory')")
            name, directory = cursor.fetchone()
        self.assertEqual(connection.vendor, "postgresql")
        self.assertTrue(name.startswith("test_"), "A disposable Django test database is required")
        self.assertFalse(Path(directory).resolve().is_relative_to(settings.BASE_DIR / ".runtime"),
                         "Shared preview cluster is forbidden")
        old = ("foundation", "0019_alter_userfavorite_destination")
        new = ("foundation", "0020_collection_function_favorite")
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            self.create_fixture()
            self.make_receipt(charges=[])
            quote = self.make_quote(key="TEST-ONLY-upgrade-quote")
            from .models import SalesQuote
            self.pay(SalesQuote.objects.get(pk=quote["quote_id"]),
                     key="TEST-ONLY-upgrade-collection", amount="2000.00")
            for user, destination in (
                (self.sales, "retail"), (self.sales, "retail.actions"),
                (self.roche_user, "inventory.locations"),
            ):
                UserFavorite.objects.create(user=user, destination=destination)
            # Generate fixtures with today's services, then compare the actual
            # historical schema using its own models, not today's ORM columns.
            MigrationExecutor(connection).migrate([old])
            historical = MigrationExecutor(connection).loader.project_state([old]).apps
            before = self.snapshot(historical)
            old_choices = MigrationExecutor(connection).loader.project_state([old]).apps.get_model(
                "foundation", "UserFavorite")._meta.get_field("destination").choices
            with CaptureQueriesContext(connection) as queries:
                executor = MigrationExecutor(connection)
                plan = executor.migration_plan([new])
                self.assertEqual([(migration.name, backwards) for migration, backwards in plan],
                                 [(new[1], False)])
                call_command("migrate", "foundation", new[1], interactive=False, verbosity=0)
            self.assertTrue(before == self.snapshot(historical), "Existing rows/counts/digest changed")
            new_choices = MigrationExecutor(connection).loader.project_state([new]).apps.get_model(
                "foundation", "UserFavorite")._meta.get_field("destination").choices
            self.assertEqual(set(new_choices) - set(old_choices),
                             {("retail.collections", "Retail · Collection follow-up")})
            self.assertFalse(set(old_choices) - set(new_choices))
            mutations = [
                query["sql"] for query in queries
                if re.match(r"^\s*(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE)\b",
                            query["sql"], flags=re.I)
            ]
            self.assertEqual(len(mutations), 1)
            self.assertTrue(mutations[0].startswith('INSERT INTO "django_migrations"'),
                            "Upgrade modified more than migration history")
            owner = APIClient()
            owner.force_login(self.sales)
            other = APIClient()
            other.force_login(self.roche_user)
            self.assertEqual({f["destination"] for f in owner.get("/api/favorites/").data["favorites"]},
                             {"retail", "retail.actions"})
            self.assertEqual({f["destination"] for f in other.get("/api/favorites/").data["favorites"]},
                             {"inventory.locations"})
            response = owner.post("/api/favorites/", {"destination": "retail.collections"}, format="json")
            self.assertEqual(response.status_code, 201)
            self.assertEqual({f["destination"] for f in owner.get("/api/favorites/").data["favorites"]},
                             {"retail", "retail.actions", "retail.collections"})
            print("Synthetic 0019→0020 rehearsal: existing favorites=3; quotes=1; "
                  "payments=1; all model row counts/digests unchanged; "
                  "only django_migrations INSERT; new personal function pin verified.")
            print("Preserved rehearsal counts: " + json.dumps({
                name: count for name, count in before["counts"].items()
                if name in {"auth.User", "foundation.UserFavorite", "foundation.UserActionGrant",
                            "foundation.BusinessUnitRoleAssignment", "foundation.SalesQuote",
                            "foundation.QuotePayment", "foundation.QuoteReservation",
                            "foundation.StockMovement", "foundation.StockBalance",
                            "foundation.JournalEntry", "foundation.JournalLine"}
            }, sort_keys=True))
        finally:
            MigrationExecutor(connection).migrate(latest)
