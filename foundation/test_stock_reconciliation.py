import json
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Event
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from .management.commands.snapshot_integrity import Command as IntegritySnapshotCommand
from .models import StockBalance, StockReservation
from .stock_reconciliation import reconcile_stock_quantities
from .test_transactions import TransactionFixtureMixin
from .transaction_services import deliver_reserved_stock, reserve_stock, transfer_stock


@override_settings(APP_ENV="demo")
class StockQuantityReconciliationTests(TransactionFixtureMixin, TransactionTestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"APP_ENV": "demo"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.create_synthetic_catalog()

    def reserve(self, quantity="2.00", key="reconcile-reservation"):
        return reserve_stock(
            idempotency_key=key, business_unit=self.business_unit,
            variant=self.variant, location=self.locations["W41-01"],
            quantity=quantity,
        )

    def transfer(self, key="reconcile-transfer"):
        return transfer_stock(
            idempotency_key=key, business_unit=self.business_unit, variant=self.variant,
            source_location=self.locations["W41-01"],
            destination_location=self.locations["W41-02"],
            quantity="3.00",
        )

    def test_empty_ledger_is_explicit_and_never_uses_stock_examples(self):
        output = StringIO()
        call_command("reconcile_stock_quantities", stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["checked_buckets"], 0)
        self.assertEqual(result["discrepancies"], [])
        self.assertTrue(result["read_only"])

    def test_receipt_replay_reservation_delivery_and_transfer_reconcile_without_writes(self):
        self.receive_fixture()
        self.receive_fixture()  # Exact replay must not inflate the movement sum.
        reservation = self.reserve()
        deliver_reserved_stock(
            idempotency_key="reconcile-delivery",
            reservation_id=reservation["reservation_id"], unit_selling_price="2000.00",
        )
        self.transfer()
        self.reserve(quantity="1.25", key="reconcile-active-reservation")
        before = IntegritySnapshotCommand._manifest()
        balance_values = list(StockBalance.objects.values())
        with CaptureQueriesContext(connection) as captured:
            result = reconcile_stock_quantities()
        self.assertEqual(result["status"], "consistent")
        self.assertEqual(result["checked_buckets"], 2)
        self.assertEqual(result["discrepancy_count"], 0)
        self.assertEqual(before, IntegritySnapshotCommand._manifest())
        self.assertEqual(balance_values, list(StockBalance.objects.values()))
        statements = [query["sql"].upper() for query in captured]
        self.assertTrue(any("REPEATABLE READ, READ ONLY" in sql for sql in statements))
        self.assertFalse(any(sql.lstrip().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER")) for sql in statements))
        self.assertNotIn("on_hand_value", json.dumps(result))
        self.assertNotIn("cost", json.dumps(result))

    def test_released_state_fixture_and_zero_quantity_landed_cost_do_not_inflate_totals(self):
        self.receive_fixture()
        reservation = self.reserve()
        # Isolated state fixture only: no release/expiry policy or service is added.
        with transaction.atomic():
            StockReservation.objects.filter(pk=reservation["reservation_id"]).update(
                status=StockReservation.Status.RELEASED,
            )
            StockBalance.objects.update(reserved_quantity="0.00")
        self.assertEqual(reconcile_stock_quantities()["status"], "consistent")

    def test_drift_is_exact_deterministic_fails_command_and_is_not_repaired(self):
        self.receive_fixture()
        self.reserve()
        balance = self.balance_at()
        StockBalance.objects.filter(pk=balance.pk).update(
            on_hand_quantity="9.75", reserved_quantity="1.25",
        )
        before = IntegritySnapshotCommand._manifest()
        result = reconcile_stock_quantities()
        self.assertEqual(result, reconcile_stock_quantities())
        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["discrepancies"], [
            {"balance_id": balance.pk, "field": "on_hand_quantity",
             "stored_quantity": "9.75", "derived_quantity": "10.00", "difference": "-0.25"},
            {"balance_id": balance.pk, "field": "reserved_quantity",
             "stored_quantity": "1.25", "derived_quantity": "2.00", "difference": "-0.75"},
        ])
        output = StringIO()
        with self.assertRaisesMessage(CommandError, "Nothing was repaired"):
            call_command("reconcile_stock_quantities", stdout=output)
        self.assertEqual(json.loads(output.getvalue()), result)
        balance.refresh_from_db()
        self.assertEqual(str(balance.on_hand_quantity), "9.75")
        self.assertEqual(str(balance.reserved_quantity), "1.25")
        self.assertEqual(before, IntegritySnapshotCommand._manifest())

    def test_existing_local_demo_guards_and_standalone_snapshot_requirement_fail_closed(self):
        with override_settings(APP_ENV="production"):
            with self.assertRaisesMessage(CommandError, "APP_ENV=demo"):
                reconcile_stock_quantities()
        with patch.dict(connection.settings_dict, {"HOST": "synthetic-remote.invalid"}):
            with self.assertRaisesMessage(CommandError, "non-local"):
                reconcile_stock_quantities()
        with patch.dict(connection.settings_dict, {"NAME": "not_the_demo_database"}):
            with self.assertRaisesMessage(CommandError, "unrecognized database"):
                reconcile_stock_quantities()
        with transaction.atomic():
            with self.assertRaisesMessage(CommandError, "outside an existing transaction"):
                reconcile_stock_quantities()

    def test_concurrent_transfer_cannot_mix_snapshot_generations(self):
        self.receive_fixture()
        start_writer, writer_done = Event(), Event()
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            reader_pid = cursor.fetchone()[0]

        def writer():
            close_old_connections()
            try:
                if not start_writer.wait(15):
                    raise AssertionError("Reader did not reach the aggregate query.")
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    writer_pid = cursor.fetchone()[0]
                self.transfer(key="concurrent-reconcile-transfer")
                return writer_pid
            finally:
                writer_done.set()
                connection.close()

        def pause_before_aggregate(execute, sql, params, many, context):
            if 'FROM "foundation_stockmovement"' in sql and not start_writer.is_set():
                start_writer.set()
                if not writer_done.wait(15):
                    raise AssertionError("Concurrent transfer did not complete.")
            return execute(sql, params, many, context)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            with connection.execute_wrapper(pause_before_aggregate):
                snapshot = reconcile_stock_quantities()
            writer_pid = future.result(timeout=15)
        self.assertNotEqual(reader_pid, writer_pid)
        self.assertEqual(snapshot["status"], "consistent")
        self.assertEqual(snapshot["checked_buckets"], 1)
        refreshed = reconcile_stock_quantities()
        self.assertEqual(refreshed["status"], "consistent")
        self.assertEqual(refreshed["checked_buckets"], 2)
