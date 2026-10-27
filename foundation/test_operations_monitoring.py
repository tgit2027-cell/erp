import json
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from unittest.mock import patch

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from .models import StockBalance
from .operations_monitoring import summarize_synthetic_integrity
from .stock_reconciliation import reconcile_stock_quantities
from .test_transactions import TransactionFixtureMixin
from .transaction_services import reserve_stock, transfer_stock


@override_settings(APP_ENV="demo")
class SyntheticOperationsMonitoringTests(TransactionFixtureMixin, TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.assertEqual(connection.settings_dict["NAME"], "test_tivoli_erp_demo")
        self.env = patch.dict("os.environ", {"APP_ENV": "demo"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.create_synthetic_catalog()

    @staticmethod
    def facts():
        return {
            model.__name__: list(model.objects.order_by("pk").values())
            for model in apps.get_app_config("foundation").get_models()
        }

    def command(self):
        output = StringIO()
        call_command("monitor_synthetic_integrity", stdout=output)
        return output.getvalue()

    def test_empty_checks_are_successfully_executed_not_failed_or_nonempty(self):
        result = json.loads(self.command())
        self.assertEqual(result["status"], "passed")
        self.assertIn("SYNTHETIC DATA ONLY", result["label"])
        self.assertFalse(result["shared_snapshot"])
        self.assertEqual([check["name"] for check in result["checks"]],
                         ["stock_quantities", "transfer_pairs", "posted_journals"])
        for check in result["checks"]:
            self.assertEqual(check["execution"], "passed")
            self.assertEqual(check["outcome"], "empty")
            self.assertEqual(check["result"]["discrepancy_count"], 0)
            self.assertIsNone(check["error"])

    def test_healthy_summary_is_deterministic_and_reads_without_writes(self):
        self.receive_fixture()
        reserve_stock(
            idempotency_key="monitor-reservation", business_unit=self.business_unit,
            variant=self.variant, location=self.locations["W41-01"], quantity="2.00",
        )
        transfer_stock(
            idempotency_key="monitor-transfer", business_unit=self.business_unit, variant=self.variant,
            source_location=self.locations["W41-01"],
            destination_location=self.locations["W41-02"], quantity="3.00",
        )
        before = self.facts()
        with CaptureQueriesContext(connection) as queries:
            first = self.command()
            second = self.command()
        self.assertEqual(first, second)
        result = json.loads(first)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["failed_check_count"], 0)
        self.assertEqual(result["completed_check_discrepancy_count"], 0)
        self.assertTrue(all(check["outcome"] == "no_discrepancies" for check in result["checks"]))
        self.assertEqual(result["checks"][0]["result"]["checked_buckets"], 2)
        self.assertEqual(result["checks"][1]["result"]["checked_transfers"], 1)
        self.assertGreater(result["checks"][2]["result"]["checked_entries"], 0)
        self.assertEqual(before, self.facts())
        allowed = ("SELECT", "SET TRANSACTION", "BEGIN", "COMMIT")
        for query in queries:
            self.assertTrue(query["sql"].lstrip().upper().startswith(allowed), query["sql"])

    def test_detected_discrepancies_are_not_execution_failures_or_repaired(self):
        self.receive_fixture()
        StockBalance.objects.filter(pk=self.balance_at().pk).update(on_hand_quantity="9.00")
        before = self.facts()
        output = StringIO()
        with self.assertRaisesMessage(CommandError, "Synthetic discrepancies found"):
            call_command("monitor_synthetic_integrity", stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "discrepancies")
        self.assertEqual(result["failed_check_count"], 0)
        self.assertEqual(result["completed_check_discrepancy_count"], 1)
        stock = result["checks"][0]
        self.assertEqual(stock["execution"], "passed")
        self.assertEqual(stock["outcome"], "discrepancies")
        self.assertEqual(stock["result"]["discrepancies"][0]["difference"], "-1.00")
        self.assertEqual(before, self.facts())

    def test_database_execution_failure_is_unavailable_redacted_and_other_checks_continue(self):
        self.receive_fixture()
        before = self.facts()

        def fail_transfer(execute, sql, params, many, context):
            if 'FROM "foundation_stocktransfer"' in sql:
                # Actual PostgreSQL error aborts this check's transaction.
                return execute("SELECT 1 / 0 /* TEST ONLY private-message */", None, False, context)
            return execute(sql, params, many, context)

        output = StringIO()
        with connection.execute_wrapper(fail_transfer):
            with self.assertRaisesMessage(CommandError, "checks failed to run"):
                call_command("monitor_synthetic_integrity", stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "failed_to_run")
        self.assertEqual(result["failed_check_count"], 1)
        self.assertEqual(result["checks"][0]["execution"], "passed")
        failed = result["checks"][1]
        self.assertEqual(failed["execution"], "failed_to_run")
        self.assertEqual(failed["outcome"], "unavailable")
        self.assertIsNone(failed["result"])
        self.assertEqual(failed["error"]["type"], "DataError")
        self.assertNotIn("private-message", output.getvalue())
        self.assertEqual(result["checks"][2]["outcome"], "no_discrepancies")
        self.assertEqual(before, self.facts())

    def test_all_three_transactions_enforce_repeatable_read_and_reject_writes(self):
        self.receive_fixture()
        seen = []
        before = self.facts()

        def verify_transaction(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if sql == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY":
                with connection.cursor() as cursor:
                    cursor.execute("SHOW transaction_isolation")
                    self.assertEqual(cursor.fetchone()[0], "repeatable read")
                    cursor.execute("SHOW transaction_read_only")
                    self.assertEqual(cursor.fetchone()[0], "on")
                # Savepoint rolls back only the deliberately forbidden statement.
                with self.assertRaisesMessage(DatabaseError, "read-only transaction"):
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "UPDATE foundation_stockbalance "
                                "SET on_hand_quantity = on_hand_quantity WHERE false"
                            )
                seen.append(True)
            return result

        with connection.execute_wrapper(verify_transaction):
            result = summarize_synthetic_integrity()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(len(seen), 3)
        self.assertEqual(before, self.facts())

    def test_safety_preflight_refuses_unsafe_targets_and_existing_transactions(self):
        with override_settings(APP_ENV="production"):
            with self.assertRaisesMessage(CommandError, "APP_ENV=demo"):
                self.command()
        settings = connection.settings_dict
        for change in ({"HOST": "remote.invalid"}, {"NAME": "not_a_demo_database"}):
            with patch.dict(settings, change):
                with self.assertRaises(CommandError):
                    self.command()
        with transaction.atomic():
            with self.assertRaisesMessage(CommandError, "outside an existing transaction"):
                self.command()

    def test_separate_snapshots_are_explicit_when_writer_commits_between_checks(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            reader_pid = cursor.fetchone()[0]

        def writer():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    writer_pid = cursor.fetchone()[0]
                self.receive_fixture(key="between-monitor-checks")
                return writer_pid
            finally:
                connection.close()

        def stock_then_commit():
            stock = reconcile_stock_quantities()
            self.assertFalse(connection.in_atomic_block)
            with ThreadPoolExecutor(max_workers=1) as pool:
                writer_pid = pool.submit(writer).result(timeout=15)
            self.assertNotEqual(reader_pid, writer_pid)
            return stock

        with patch("foundation.operations_monitoring.reconcile_stock_quantities", stock_then_commit):
            result = summarize_synthetic_integrity()
        self.assertFalse(result["shared_snapshot"])
        self.assertEqual(result["checks"][0]["result"]["checked_buckets"], 0)
        self.assertEqual(result["checks"][0]["outcome"], "empty")
        self.assertEqual(result["checks"][2]["result"]["checked_entries"], 1)
        refreshed = summarize_synthetic_integrity()
        self.assertEqual(refreshed["checks"][0]["result"]["checked_buckets"], 1)
        self.assertEqual(refreshed["status"], "passed")
