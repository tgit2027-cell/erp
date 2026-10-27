import json
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Event
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .management.commands.snapshot_integrity import Command as IntegritySnapshotCommand
from .models import IdempotencyRecord, StockBalance, StockMovement, StockTransfer, Variant
from .test_transactions import TransactionFixtureMixin
from .transaction_services import transfer_stock
from .transfer_verification import HEADER_FIELDS, MOVEMENT_FIELDS, _verify_pair, verify_stock_transfer_pairs


@override_settings(APP_ENV="demo")
class TransferPairVerificationTests(TransactionFixtureMixin, TransactionTestCase):
    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.create_synthetic_catalog()

    def transfer(self, key="verify-transfer"):
        return transfer_stock(
            idempotency_key=key, business_unit=self.business_unit, variant=self.variant,
            source_location=self.locations["W41-01"],
            destination_location=self.locations["W41-02"], quantity="3.00",
        )

    def operation(self, key, operation="transfer"):
        return IdempotencyRecord.objects.create(
            key=key, operation=operation, payload_hash="0" * 64, status="complete",
            completed_at=timezone.now(), result={},
        )

    def raw_header(self, key="malformed", destination=None, operation=None):
        """Isolated malformed fixtures only; never alter posted records or constraints."""
        self.receive_fixture()
        source = self.balance_at()
        destination = destination or StockBalance.objects.get_or_create(
            legal_entity=self.legal_entity, business_unit=self.business_unit,
            variant=self.variant, location=self.locations["W41-02"],
        )[0]
        return StockTransfer.objects.create(
            idempotency=operation or self.operation(key), source_balance=source,
            destination_balance=destination, quantity="3.00", transferred_value="0.00",
        )

    def movement(self, header, outbound, quantity=None, balance=None, operation=None):
        return StockMovement.objects.create(
            transfer=header, idempotency=operation or header.idempotency,
            balance=balance or (header.source_balance if outbound else header.destination_balance),
            kind="transfer_out" if outbound else "transfer_in",
            quantity_delta=quantity or ("-3.00" if outbound else "3.00"), value_delta="0.00",
        )

    def codes(self, result):
        return {row["code"] for row in result["discrepancies"]}

    def test_empty_is_explicit_and_ignores_nontransfer_movements_and_examples(self):
        output = StringIO()
        call_command("verify_stock_transfer_pairs", stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["checked_transfers"], 0)
        self.assertEqual(result["checked_movements"], 0)
        self.receive_fixture()  # Receipt/landed-cost movements are not transfer pairs.
        self.assertEqual(verify_stock_transfer_pairs(), result)

    def test_valid_service_pairs_replay_without_writes_or_sensitive_fields(self):
        self.receive_fixture()
        self.transfer()
        self.transfer()  # Exact replay must still produce only one pair.
        before = IntegritySnapshotCommand._manifest()
        with CaptureQueriesContext(connection) as queries:
            result = verify_stock_transfer_pairs()
        self.assertEqual(result["status"], "consistent")
        self.assertEqual(result["checked_transfers"], 1)
        self.assertEqual(result["checked_movements"], 2)
        self.assertEqual(result["discrepancies"], [])
        self.assertEqual(before, IntegritySnapshotCommand._manifest())
        sql = "\n".join(query["sql"] for query in queries)
        self.assertIn("REPEATABLE READ, READ ONLY", sql)
        for query in queries:
            self.assertFalse(query["sql"].lstrip().upper().startswith(
                ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP")
            ))
        for sensitive in ("value_delta", "transferred_value", "on_hand_value", "payload_hash", "memo"):
            self.assertNotIn(sensitive, sql)
            self.assertNotIn(sensitive, json.dumps(result))

    def test_missing_both_or_one_side_is_reported_without_repair(self):
        header = self.raw_header()
        before = IntegritySnapshotCommand._manifest()
        missing = verify_stock_transfer_pairs()
        self.assertEqual(self.codes(missing), {"movement_count", "side_count"})
        self.assertEqual(before, IntegritySnapshotCommand._manifest())
        self.movement(header, True)
        output = StringIO()
        before = IntegritySnapshotCommand._manifest()
        with self.assertRaisesMessage(CommandError, "Nothing was repaired"):
            call_command("verify_stock_transfer_pairs", stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual(self.codes(result), {"movement_count", "side_count"})
        self.assertEqual(result, verify_stock_transfer_pairs())
        self.assertEqual(before, IntegritySnapshotCommand._manifest())

    def test_duplicate_outbound_is_not_hidden_by_a_balanced_quantity_sum(self):
        header = self.raw_header()
        self.movement(header, True, "-1.00")
        self.movement(header, True, "-2.00")
        self.movement(header, False)
        result = verify_stock_transfer_pairs()
        self.assertIn("movement_count", self.codes(result))
        self.assertIn("side_count", self.codes(result))
        self.assertEqual(result["checked_movements"], 3)

    def test_opposite_and_header_quantity_mismatches_are_exact_and_deterministic(self):
        header = self.raw_header()
        self.movement(header, True)
        inbound = self.movement(header, False, "2.25")
        result = verify_stock_transfer_pairs()
        self.assertEqual(self.codes(result), {"movement_quantity", "opposite_quantities"})
        self.assertIn({
            "transfer_id": header.pk, "movement_id": inbound.pk, "code": "movement_quantity",
            "expected_quantity": "3.00", "actual_quantity": "2.25",
        }, result["discrepancies"])
        self.assertEqual(result, verify_stock_transfer_pairs())

    def test_equal_opposite_pair_must_also_match_the_header_quantity(self):
        header = self.raw_header()
        self.movement(header, True, "-2.00")
        self.movement(header, False, "2.00")
        result = verify_stock_transfer_pairs()
        self.assertEqual(self.codes(result), {"movement_quantity"})
        self.assertEqual(result["discrepancy_count"], 2)

    def test_db_rejects_bad_movement_links_and_comparator_flags_memory_fixture(self):
        header = self.raw_header()
        unrelated_operation = self.operation("wrong-operation")
        for options in [
            {"balance": header.destination_balance},
            {"operation": unrelated_operation},
        ]:
            with self.assertRaisesMessage(DatabaseError, "does not match its transfer record"):
                with transaction.atomic():
                    self.movement(header, True, **options)
        self.movement(header, True)
        self.movement(header, False)
        self.assertEqual(verify_stock_transfer_pairs()["status"], "consistent")
        # The DB correctly prevents storing these broken links. Exercise the
        # defensive comparator with copies, without disabling any constraints.
        before = IntegritySnapshotCommand._manifest()
        copied_header = StockTransfer.objects.filter(pk=header.pk).values(*HEADER_FIELDS).get()
        copied_rows = list(StockMovement.objects.filter(transfer=header).order_by("pk").values(*MOVEMENT_FIELDS))
        copied_rows[0]["balance_id"] = header.destination_balance_id
        copied_rows[1]["idempotency_id"] = unrelated_operation.pk
        discrepancies = []
        _verify_pair(copied_header, copied_rows, discrepancies)
        self.assertEqual(
            {row["code"] for row in discrepancies}, {"balance_reference", "idempotency_reference"},
        )
        self.assertEqual(before, IntegritySnapshotCommand._manifest())

    def test_existing_transfer_bucket_dimensions_and_operation_are_checked(self):
        alternate = Variant.objects.create(item=self.variant.item, sku="TEST-VERIFY-OTHER")
        destination = StockBalance.objects.create(
            legal_entity=self.legal_entity, business_unit=self.business_unit,
            variant=alternate, location=self.locations["W41-03.1"],
        )
        self.receive_fixture()
        with self.assertRaisesMessage(DatabaseError, "must share business unit, legal entity, and variant"):
            with transaction.atomic():
                self.raw_header(destination=destination)
        header = self.raw_header(operation=self.operation("wrong-kind", "receipt"))
        self.movement(header, True)
        self.movement(header, False)
        # Isolated mutable-bucket drift fixture, not an edit to immutable posted
        # transfers or movements. The diagnostic must report it, never repair it.
        StockBalance.objects.filter(pk=header.destination_balance_id).update(variant=alternate)
        self.assertEqual(
            self.codes(verify_stock_transfer_pairs()), {"bucket_dimensions", "operation_reference"},
        )

    def test_local_demo_postgres_and_standalone_guards_fail_closed(self):
        with override_settings(APP_ENV="production"):
            with self.assertRaisesMessage(CommandError, "APP_ENV=demo"):
                verify_stock_transfer_pairs()
        for settings, message in [
            ({"HOST": "synthetic-remote.invalid"}, "non-local"),
            ({"NAME": "not_the_demo"}, "unrecognized database"),
        ]:
            with patch.dict(connection.settings_dict, settings):
                with self.assertRaisesMessage(CommandError, message):
                    verify_stock_transfer_pairs()
        with patch.object(connection, "vendor", "sqlite"):
            with self.assertRaisesMessage(CommandError, "PostgreSQL"):
                verify_stock_transfer_pairs()
        with transaction.atomic():
            with self.assertRaisesMessage(CommandError, "outside an existing transaction"):
                verify_stock_transfer_pairs()

    def test_concurrent_transfer_cannot_mix_header_and_movement_generations(self):
        self.receive_fixture()
        self.transfer()
        start_writer, writer_done = Event(), Event()
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            reader_pid = cursor.fetchone()[0]

        def writer():
            close_old_connections()
            try:
                if not start_writer.wait(15):
                    raise AssertionError("Reader did not reach movement read.")
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                self.transfer(key="verify-concurrent")
                return pid
            finally:
                writer_done.set()
                connection.close()

        def interleave(execute, sql, params, many, context):
            if 'FROM "foundation_stockmovement"' in sql and not start_writer.is_set():
                start_writer.set()
                if not writer_done.wait(15):
                    raise AssertionError("Concurrent transfer did not complete.")
            return execute(sql, params, many, context)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            with connection.execute_wrapper(interleave):
                first = verify_stock_transfer_pairs()
            self.assertNotEqual(reader_pid, future.result(timeout=15))
        self.assertEqual(first["status"], "consistent")
        self.assertEqual(first["checked_transfers"], 1)
        self.assertEqual(first["checked_movements"], 2)
        refreshed = verify_stock_transfer_pairs()
        self.assertEqual(refreshed["status"], "consistent")
        self.assertEqual(refreshed["checked_transfers"], 2)
        self.assertEqual(refreshed["checked_movements"], 4)
