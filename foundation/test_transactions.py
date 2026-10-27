from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.core.management import call_command
from django.db import DatabaseError, close_old_connections, connections, transaction
from django.db.models import Sum
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .models import (
    IdempotencyRecord,
    Item,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Location,
    ProductBrand,
    StockBalance,
    StockMovement,
    StockReservation,
    StockTransfer,
    Variant,
)
from .transaction_services import (
    IdempotencyConflict,
    InsufficientStock,
    InvalidTransaction,
    ReceiptLine,
    UnsafeTransactionEnvironment,
    allocate_by_purchase_value,
    deliver_reserved_stock,
    receive_stock,
    reserve_stock,
    transfer_stock,
)


class TransactionFixtureMixin:
    @classmethod
    def create_synthetic_catalog(cls):
        with patch.dict("os.environ", {"APP_ENV": "demo"}):
            call_command("seed_demo", stdout=StringIO())
        cls.business_unit = cls._get_business_unit()
        cls.legal_entity = cls.business_unit.legal_entity
        cls.variant, cls.locations = cls._create_test_catalog()

    @classmethod
    def _get_business_unit(cls):
        from .models import BusinessUnit

        return BusinessUnit.objects.get(code="TVL")

    @classmethod
    def _create_test_catalog(cls):
        item = Item.objects.create(
            brand=ProductBrand.objects.get(code="TVF"),
            item_code="TEST-CHAIR-M2",
            description="TEST ONLY — synthetic chair",
        )
        variant = Variant.objects.create(
            item=item,
            sku="TEST-CHAIR-M2-STD",
            description="TEST ONLY — synthetic standard chair",
        )
        locations = {
            code: Location.objects.get(code=code)
            for code in (
                "W41-01",
                "W41-02",
                "W41-03.1",
            )
        }
        return variant, locations

    def receive_fixture(self, key="fixture-receipt", quantity="10.00", unit_cost="1000.00", charges="2000.00"):
        return receive_stock(
            idempotency_key=key,
            business_unit=self.business_unit,
            lines=[
                ReceiptLine(
                    variant=self.variant,
                    location=self.locations["W41-01"],
                    quantity=Decimal(quantity),
                    unit_cost=Decimal(unit_cost),
                )
            ],
            eligible_landed_charges=Decimal(charges),
        )

    def balance_at(self, location_code="W41-01"):
        return StockBalance.objects.get(
            business_unit=self.business_unit,
            variant=self.variant,
            location=self.locations[location_code],
        )


class TransactionServiceTests(TransactionFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_synthetic_catalog()

    def test_test_only_receipt_landed_cost_delivery_fixture_and_balanced_qar_journals(self):
        receipt = self.receive_fixture()
        balance = self.balance_at()
        self.assertEqual(receipt["purchase_value"], "10000.00")
        self.assertEqual(receipt["landed_charges"], "2000.00")
        self.assertEqual(receipt["landed_allocations"], ["2000.00"])
        self.assertEqual(receipt["inventory_value"], "12000.00")
        self.assertEqual(balance.on_hand_quantity, Decimal("10.00"))
        self.assertEqual(balance.reserved_quantity, Decimal("0.00"))
        self.assertEqual(balance.on_hand_value, Decimal("12000.00"))

        reservation = reserve_stock(
            idempotency_key="fixture-reservation",
            business_unit=self.business_unit,
            variant=self.variant,
            location=self.locations["W41-01"],
            quantity=Decimal("2.00"),
        )
        balance.refresh_from_db()
        self.assertEqual(balance.on_hand_quantity, Decimal("10.00"))
        self.assertEqual(balance.reserved_quantity, Decimal("2.00"))
        self.assertEqual(reservation["available_quantity"], "8.00")

        delivery = deliver_reserved_stock(
            idempotency_key="fixture-delivery",
            reservation_id=reservation["reservation_id"],
            unit_selling_price=Decimal("2000.00"),
        )
        balance.refresh_from_db()
        self.assertEqual(delivery["revenue"], "4000.00")
        self.assertEqual(delivery["cost_of_goods_sold"], "2400.00")
        self.assertEqual(delivery["remaining_inventory_value"], "9600.00")
        self.assertEqual(balance.on_hand_quantity, Decimal("8.00"))
        self.assertEqual(balance.reserved_quantity, Decimal("0.00"))
        self.assertEqual(balance.on_hand_value, Decimal("9600.00"))
        self.assertEqual(StockReservation.objects.get(pk=reservation["reservation_id"]).status, "consumed")

        movement_totals = balance.movements.aggregate(
            quantity=Sum("quantity_delta"),
            value=Sum("value_delta"),
        )
        self.assertEqual(movement_totals["quantity"], balance.on_hand_quantity)
        self.assertEqual(movement_totals["value"], balance.on_hand_value)

        entries = list(JournalEntry.objects.order_by("pk"))
        self.assertEqual(len(entries), 2)
        for entry in entries:
            self.assertEqual(entry.status, JournalEntry.Status.POSTED)
            self.assertEqual(entry.currency, "QAR")
            self.assertEqual(entry.legal_entity_id, self.legal_entity.pk)
            self.assertEqual(entry.policy_label, "TEST_ONLY_NOT_TIVOLI_APPROVED")
            self.assertGreaterEqual(entry.lines.count(), 2)
            self.assertEqual(
                sum((line.debit for line in entry.lines.all()), Decimal("0.00")),
                sum((line.credit for line in entry.lines.all()), Decimal("0.00")),
            )
            self.assertTrue(all(line.account.legal_entity_id == self.legal_entity.pk for line in entry.lines.all()))

        delivery_lines = {
            line.account.code: (line.debit, line.credit)
            for line in JournalEntry.objects.get(kind=JournalEntry.Kind.DELIVERY).lines.select_related("account")
        }
        self.assertEqual(delivery_lines["TEST_RECEIVABLE"], (Decimal("4000.00"), Decimal("0.00")))
        self.assertEqual(delivery_lines["TEST_REVENUE"], (Decimal("0.00"), Decimal("4000.00")))
        self.assertEqual(delivery_lines["TEST_COGS"], (Decimal("2400.00"), Decimal("0.00")))
        self.assertEqual(delivery_lines["TEST_INVENTORY"], (Decimal("0.00"), Decimal("2400.00")))

    def test_landed_cost_rounding_is_deterministic_and_reconciles_exactly(self):
        allocations = allocate_by_purchase_value(
            Decimal("100.00"),
            [Decimal("1.00"), Decimal("1.00"), Decimal("1.00")],
        )
        self.assertEqual(allocations, [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")])
        self.assertEqual(sum(allocations, Decimal("0.00")), Decimal("100.00"))
        with self.assertRaises(InvalidTransaction):
            allocate_by_purchase_value(0.3, [Decimal("0.10"), Decimal("0.10"), Decimal("0.10")])

    @override_settings(APP_ENV="production")
    def test_services_refuse_to_run_outside_the_demo_environment(self):
        with self.assertRaises(UnsafeTransactionEnvironment):
            self.receive_fixture(key="must-not-run-outside-demo")
        self.assertFalse(IdempotencyRecord.objects.filter(key="must-not-run-outside-demo").exists())

    def test_same_idempotency_key_and_payload_replays_result_without_duplicate_records(self):
        first = self.receive_fixture(key="receipt-replay")
        movement_count = StockMovement.objects.count()
        journal_count = JournalEntry.objects.count()
        balance_count = StockBalance.objects.count()

        replay = self.receive_fixture(key="receipt-replay")

        self.assertEqual(replay, first)
        self.assertEqual(StockMovement.objects.count(), movement_count)
        self.assertEqual(JournalEntry.objects.count(), journal_count)
        self.assertEqual(StockBalance.objects.count(), balance_count)

    def test_same_idempotency_key_with_different_payload_is_rejected(self):
        self.receive_fixture(key="receipt-conflict")
        with self.assertRaises(IdempotencyConflict):
            self.receive_fixture(key="receipt-conflict", quantity="9.00")
        self.assertEqual(StockMovement.objects.filter(idempotency__key="receipt-conflict").count(), 2)
        self.assertEqual(JournalEntry.objects.filter(idempotency__key="receipt-conflict").count(), 1)

    def test_failure_during_delivery_rolls_back_stock_journal_reservation_and_key(self):
        self.receive_fixture(key="receipt-before-rollback")
        reservation = reserve_stock(
            idempotency_key="reservation-before-rollback",
            business_unit=self.business_unit,
            variant=self.variant,
            location=self.locations["W41-01"],
            quantity=Decimal("2.00"),
        )
        balance = self.balance_at()
        original = (balance.on_hand_quantity, balance.reserved_quantity, balance.on_hand_value)
        movement_count = StockMovement.objects.count()
        journal_count = JournalEntry.objects.count()

        with patch(
            "foundation.transaction_services._post_journal",
            side_effect=RuntimeError("injected synthetic test failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected synthetic test failure"):
                deliver_reserved_stock(
                    idempotency_key="delivery-rollback",
                    reservation_id=reservation["reservation_id"],
                    unit_selling_price=Decimal("2000.00"),
                )

        balance.refresh_from_db()
        self.assertEqual(
            (balance.on_hand_quantity, balance.reserved_quantity, balance.on_hand_value),
            original,
        )
        self.assertEqual(StockReservation.objects.get(pk=reservation["reservation_id"]).status, "active")
        self.assertEqual(StockMovement.objects.count(), movement_count)
        self.assertEqual(JournalEntry.objects.count(), journal_count)
        self.assertFalse(IdempotencyRecord.objects.filter(key="delivery-rollback").exists())

    def test_posted_journals_and_stock_movements_are_immutable(self):
        self.receive_fixture(key="receipt-immutable")
        entry = JournalEntry.objects.get(idempotency__key="receipt-immutable")
        line = entry.lines.first()
        movement = StockMovement.objects.filter(idempotency__key="receipt-immutable").first()

        attempts = (
            lambda: StockMovement.objects.filter(pk=movement.pk).update(value_delta=Decimal("1.00")),
            lambda: StockMovement.objects.filter(pk=movement.pk).delete(),
            lambda: JournalEntry.objects.filter(pk=entry.pk).update(description="changed"),
            lambda: JournalEntry.objects.filter(pk=entry.pk).delete(),
            lambda: JournalLine.objects.filter(pk=line.pk).update(memo="changed"),
            lambda: JournalLine.objects.filter(pk=line.pk).delete(),
        )
        for attempt in attempts:
            with self.subTest(attempt=attempt):
                with self.assertRaises(DatabaseError):
                    with transaction.atomic():
                        attempt()


class TransactionPostgresConcurrencyTests(TransactionFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.create_synthetic_catalog()

    @staticmethod
    def _run_on_separate_connection(barrier, callback):
        close_old_connections()
        try:
            db = connections["default"]
            db.ensure_connection()
            with db.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                backend_pid = cursor.fetchone()[0]
            barrier.wait(timeout=10)
            try:
                return ("success", callback(), backend_pid)
            except InsufficientStock as error:
                return ("unavailable", str(error), backend_pid)
        finally:
            connections.close_all()

    def _run_two_workers(self, first, second):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._run_on_separate_connection, barrier, callback)
                for callback in (first, second)
            ]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual(len({result[2] for result in results}), 2)
        return results

    def test_two_postgres_connections_cannot_over_reserve_last_available_stock(self):
        self.receive_fixture(key="reserve-race-receipt", quantity="10.00", charges="0.00")
        location = self.locations["W41-01"]
        callbacks = [
            lambda key=key: reserve_stock(
                idempotency_key=key,
                business_unit=self.business_unit,
                variant=self.variant,
                location=location,
                quantity=Decimal("7.00"),
            )
            for key in ("reserve-race-a", "reserve-race-b")
        ]

        results = self._run_two_workers(*callbacks)

        self.assertEqual(sorted(result[0] for result in results), ["success", "unavailable"])
        balance = self.balance_at()
        self.assertEqual(balance.on_hand_quantity, Decimal("10.00"))
        self.assertEqual(balance.reserved_quantity, Decimal("7.00"))
        self.assertEqual(
            StockReservation.objects.filter(balance=balance, status=StockReservation.Status.ACTIVE).aggregate(
                total=Sum("quantity")
            )["total"],
            Decimal("7.00"),
        )

    def test_two_postgres_connections_cannot_overdraw_concurrent_transfers(self):
        self.receive_fixture(key="transfer-race-receipt", quantity="10.00", unit_cost="1000.00", charges="0.00")
        source = self.locations["W41-01"]
        destinations = (
            self.locations["W41-02"],
            self.locations["W41-03.1"],
        )
        callbacks = [
            lambda key=key, destination=destination: transfer_stock(
                idempotency_key=key,
                business_unit=self.business_unit,
                variant=self.variant,
                source_location=source,
                destination_location=destination,
                quantity=Decimal("7.00"),
            )
            for key, destination in zip(("transfer-race-a", "transfer-race-b"), destinations)
        ]

        results = self._run_two_workers(*callbacks)

        self.assertEqual(sorted(result[0] for result in results), ["success", "unavailable"])
        balances = list(
            StockBalance.objects.filter(
                business_unit=self.business_unit,
                variant=self.variant,
                location__in=[source, *destinations],
            )
        )
        self.assertEqual(sum((row.on_hand_quantity for row in balances), Decimal("0.00")), Decimal("10.00"))
        self.assertEqual(sum((row.on_hand_value for row in balances), Decimal("0.00")), Decimal("10000.00"))
        self.assertEqual(StockTransfer.objects.count(), 1)
        self.assertEqual(StockMovement.objects.filter(kind=StockMovement.Kind.TRANSFER_OUT).count(), 1)
        self.assertEqual(StockMovement.objects.filter(kind=StockMovement.Kind.TRANSFER_IN).count(), 1)

    def test_postgres_rejects_unbalanced_posted_journal_at_commit(self):
        from .models import LegalEntity

        entity = LegalEntity.objects.get(code="tivoli")
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                request = IdempotencyRecord.objects.create(
                    key="unbalanced-journal-test",
                    operation="test",
                    payload_hash="0" * 64,
                    status=IdempotencyRecord.Status.COMPLETE,
                    result={},
                    completed_at=timezone.now(),
                )
                debit_account = LedgerAccount.objects.create(
                    legal_entity=entity,
                    code="TEST_UNBALANCED_DEBIT",
                    name="TEST ONLY — unbalanced debit",
                    account_class=LedgerAccount.AccountClass.ASSET,
                )
                credit_account = LedgerAccount.objects.create(
                    legal_entity=entity,
                    code="TEST_UNBALANCED_CREDIT",
                    name="TEST ONLY — unbalanced credit",
                    account_class=LedgerAccount.AccountClass.LIABILITY,
                )
                entry = JournalEntry.objects.create(
                    idempotency=request,
                    legal_entity=entity,
                    kind=JournalEntry.Kind.RECEIPT,
                    description="TEST ONLY — deliberately unbalanced journal",
                )
                JournalLine.objects.create(
                    entry=entry,
                    account=debit_account,
                    debit=Decimal("100.00"),
                    credit=Decimal("0.00"),
                    memo="TEST ONLY",
                )
                JournalLine.objects.create(
                    entry=entry,
                    account=credit_account,
                    debit=Decimal("0.00"),
                    credit=Decimal("90.00"),
                    memo="TEST ONLY",
                )
                entry.status = JournalEntry.Status.POSTED
                entry.posted_at = timezone.now()
                entry.save(update_fields=["status", "posted_at"])
