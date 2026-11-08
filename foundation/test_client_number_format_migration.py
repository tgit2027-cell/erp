"""Real schema upgrade proof: issued client and project numbers gain the dash."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from .models import SyntheticCounterparty


class ClientNumberFormatMigrationTests(TransactionTestCase):
    def numbers(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT client_number, project_number FROM foundation_syntheticcounterparty WHERE code = %s",
                ["TEST-CUST-FORMAT"],
            )
            return cursor.fetchone()

    def test_issued_numbers_are_rewritten_both_ways_and_stay_permanent(self):
        SyntheticCounterparty.objects.create(
            party_type="customer", code="TEST-CUST-FORMAT", name="TEST ONLY format customer",
            qid_declined=True, client_number="C-000007", project_number="P-000003",
        )
        old = ("foundation", "0052_merge_customer_returns_and_report_share")
        new = ("foundation", "0053_client_number_dash_format")
        latest = MigrationExecutor(connection).loader.graph.leaf_nodes()
        try:
            MigrationExecutor(connection).migrate([old])
            self.assertEqual(self.numbers(), ("C000007", "P000003"))
            MigrationExecutor(connection).migrate([new])
            self.assertEqual(self.numbers(), ("C-000007", "P-000003"))
            with self.assertRaisesMessage(Exception, "cannot be changed"):
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE foundation_syntheticcounterparty SET client_number = 'C-000008' WHERE code = %s",
                        ["TEST-CUST-FORMAT"],
                    )
        finally:
            MigrationExecutor(connection).migrate(latest)
