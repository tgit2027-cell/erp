"""Additive quotation schema upgrade proof on disposable synthetic data."""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from .models import Item, SalesQuote, UserFavorite
from .test_collection_favorite_upgrade import CollectionFavoriteUpgradeTests
from .test_purchasing_workflows import PurchasingFixtureMixin


class QuotationPresentationMigrationTests(PurchasingFixtureMixin, TransactionTestCase):
    def test_0021_to_0022_preserves_legacy_records_favorites_and_permissions_without_invented_content(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertTrue(cursor.fetchone()[0].startswith("test_"))
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        old = ("foundation", "0021_delivery_preparation_favorite")
        new = ("foundation", "0022_quotation_presentation")
        try:
            self.create_fixture()
            quote_id = self.make_quote(key="TEST-ONLY-quotation-upgrade")["quote_id"]
            UserFavorite.objects.create(user=self.sales, destination="retail")
            UserFavorite.objects.create(user=self.sales, destination="delivery.preparation")
            MigrationExecutor(connection).migrate([old])
            historical = MigrationExecutor(connection).loader.project_state([old]).apps
            before = CollectionFavoriteUpgradeTests.snapshot(self, historical)
            executor = MigrationExecutor(connection)
            self.assertEqual(
                [(migration.name, backwards) for migration, backwards in executor.migration_plan([new])],
                [("0022_quotation_presentation", False)],
            )
            executor.migrate([new])
            self.assertEqual(before, CollectionFavoriteUpgradeTests.snapshot(self, historical))
            quote = SalesQuote.objects.get(pk=quote_id)
            self.assertEqual(quote.presentation_document, {})
            self.variant.refresh_from_db()
            self.customer.refresh_from_db(fields=["contact_ar"])  # later columns (such as qid) do not exist yet
            item = Item.objects.only("name_ar", "image_path").get(pk=self.variant.item_id)  # later item columns do not exist yet
            self.assertEqual(item.name_ar, "")
            self.assertEqual(item.image_path, "")
            self.assertEqual(self.customer.contact_ar, "")
        finally:
            MigrationExecutor(connection).migrate(latest)
