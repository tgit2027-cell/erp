from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time
from io import StringIO
from threading import Event, Lock
from unittest.mock import patch

from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from .models import (
    IdempotencyRecord,
    JournalEntry,
    NotificationOutboxIntent,
    SalesQuote,
    StockBalance,
    StockMovement,
    StockReservation,
    StorageCharge,
    WorkflowEvent,
)
from .notification_outbox import (
    DRY_RUN_NOTICE,
    InMemoryFakeNotificationSink,
    NotificationOutboxError,
    process_notification_outbox_dry_run,
)
from .purchasing_services import (
    _add_calendar_month,
    accrue_storage_charges,
    notify_quote_ready,
)
from .test_purchasing_workflows import PurchasingFixtureMixin


class NotificationOutboxTests(PurchasingFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.create_fixture()

    def make_confirmed_quote(self, key):
        self.make_receipt(key=f"{key}-receipt", charges=[])
        quote_id = self.make_quote(key=f"{key}-quote")["quote_id"]
        quote = SalesQuote.objects.get(pk=quote_id)
        self.pay(quote, key=f"{key}-deposit", amount="2000.00")
        self.pay(quote, key=f"{key}-balance", amount="2000.00")
        quote.refresh_from_db()
        self.assertEqual(quote.status, SalesQuote.Status.CONFIRMED)
        return quote

    def make_ready_quote(self, key):
        quote = self.make_confirmed_quote(key)
        result = notify_quote_ready(
            actor=self.sales,
            quote=quote,
            idempotency_key=f"{key}-readiness",
        )
        quote.refresh_from_db()
        return quote, result

    def install_outbox_insert_failure_trigger(self):
        table = connection.ops.quote_name(NotificationOutboxIntent._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE OR REPLACE FUNCTION test_fail_notification_outbox_insert()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    RAISE EXCEPTION 'forced synthetic outbox insert failure'
                    USING ERRCODE = '23514';
                END;
                $$;
                """
            )
            cursor.execute(
                f"CREATE TRIGGER test_fail_notification_outbox_insert "
                f"BEFORE INSERT ON {table} FOR EACH ROW "
                "EXECUTE FUNCTION test_fail_notification_outbox_insert()"
            )

    def remove_outbox_insert_failure_trigger(self):
        table = connection.ops.quote_name(NotificationOutboxIntent._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TRIGGER IF EXISTS test_fail_notification_outbox_insert ON {table}")
            cursor.execute("DROP FUNCTION IF EXISTS test_fail_notification_outbox_insert()")

    def test_readiness_state_event_and_intent_commit_together_and_replay_is_deduplicated(self):
        quote = self.make_confirmed_quote("outbox-ready-atomic")
        event_count = WorkflowEvent.objects.filter(quote=quote).count()

        with self.assertRaisesRegex(RuntimeError, "force outer rollback"):
            with transaction.atomic():
                notify_quote_ready(
                    actor=self.sales,
                    quote=quote,
                    idempotency_key="outbox-ready-atomic-key",
                )
                self.assertEqual(
                    NotificationOutboxIntent.objects.filter(
                        workflow_event__quote=quote,
                        workflow_event__kind=WorkflowEvent.Kind.READINESS_NOTIFICATION,
                    ).count(),
                    1,
                )
                raise RuntimeError("force outer rollback")

        quote.refresh_from_db()
        self.assertEqual(quote.status, SalesQuote.Status.CONFIRMED)
        self.assertEqual(WorkflowEvent.objects.filter(quote=quote).count(), event_count)
        self.assertFalse(NotificationOutboxIntent.objects.filter(workflow_event__quote=quote).exists())
        self.assertFalse(IdempotencyRecord.objects.filter(key="outbox-ready-atomic-key").exists())

        result = notify_quote_ready(
            actor=self.sales,
            quote=quote,
            idempotency_key="outbox-ready-atomic-key",
        )
        self.assertEqual(result["status"], SalesQuote.Status.READY)
        intent = NotificationOutboxIntent.objects.get(
            workflow_event_id=result["event_id"],
        )
        self.assertEqual(intent.status, NotificationOutboxIntent.Status.PENDING)
        self.assertEqual(intent.attempt_count, 0)
        replay = notify_quote_ready(
            actor=self.sales,
            quote=quote,
            idempotency_key="outbox-ready-atomic-key",
        )
        self.assertEqual(replay, result)
        self.assertEqual(
            NotificationOutboxIntent.objects.filter(workflow_event_id=result["event_id"]).count(),
            1,
        )

    def test_storage_charge_event_and_intent_rollback_and_replay_together(self):
        quote, readiness = self.make_ready_quote("outbox-storage-atomic")
        ready_date = timezone.localtime(quote.ready_at).date()
        free_storage_end = _add_calendar_month(ready_date)
        at_boundary = timezone.make_aware(datetime.combine(free_storage_end, time.min))
        readiness_intents = NotificationOutboxIntent.objects.filter(
            workflow_event__quote=quote,
            workflow_event__kind=WorkflowEvent.Kind.READINESS_NOTIFICATION,
        ).count()
        self.assertEqual(readiness_intents, 1)

        with self.assertRaisesRegex(RuntimeError, "force storage rollback"):
            with transaction.atomic():
                accrue_storage_charges(
                    actor=self.sales,
                    quote=quote,
                    idempotency_prefix="outbox-storage-rollback",
                    as_of=at_boundary,
                )
                self.assertEqual(StorageCharge.objects.filter(quote=quote).count(), 1)
                self.assertEqual(
                    NotificationOutboxIntent.objects.filter(
                        workflow_event__quote=quote,
                        workflow_event__kind=WorkflowEvent.Kind.STORAGE_CHARGE_NOTIFICATION,
                    ).count(),
                    1,
                )
                raise RuntimeError("force storage rollback")

        self.assertEqual(StorageCharge.objects.filter(quote=quote).count(), 0)
        self.assertEqual(
            WorkflowEvent.objects.filter(
                quote=quote,
                kind=WorkflowEvent.Kind.STORAGE_CHARGE_NOTIFICATION,
            ).count(),
            0,
        )
        self.assertEqual(
            NotificationOutboxIntent.objects.filter(workflow_event__quote=quote).count(),
            readiness_intents,
        )

        result = accrue_storage_charges(
            actor=self.sales,
            quote=quote,
            idempotency_prefix="outbox-storage-rollback",
            as_of=at_boundary,
        )
        self.assertEqual(len(result["charges"]), 1)
        self.assertEqual(
            NotificationOutboxIntent.objects.filter(
                workflow_event__quote=quote,
                workflow_event__kind=WorkflowEvent.Kind.STORAGE_CHARGE_NOTIFICATION,
            ).count(),
            1,
        )
        replay = accrue_storage_charges(
            actor=self.sales,
            quote=quote,
            idempotency_prefix="outbox-storage-rollback",
            as_of=at_boundary,
        )
        self.assertEqual(replay, result)
        self.assertEqual(
            NotificationOutboxIntent.objects.filter(workflow_event__quote=quote).count(),
            readiness_intents + 1,
        )
        self.assertEqual(readiness["event_id"], quote.workflow_events.get(
            kind=WorkflowEvent.Kind.READINESS_NOTIFICATION
        ).pk)

    def test_readiness_outbox_insert_failure_rolls_back_state_event_idempotency_and_ledger(self):
        quote = self.make_confirmed_quote("outbox-ready-insert-failure")
        before = {
            "events": WorkflowEvent.objects.count(),
            "outbox": NotificationOutboxIntent.objects.count(),
            "idempotency": IdempotencyRecord.objects.count(),
            "stock_movements": StockMovement.objects.count(),
            "stock_balances": StockBalance.objects.count(),
            "stock_reservations": StockReservation.objects.count(),
            "journals": JournalEntry.objects.count(),
        }
        self.install_outbox_insert_failure_trigger()
        try:
            with self.assertRaisesRegex(IntegrityError, "forced synthetic outbox insert failure"):
                notify_quote_ready(
                    actor=self.sales,
                    quote=quote,
                    idempotency_key="outbox-ready-insert-failure-key",
                )
        finally:
            self.remove_outbox_insert_failure_trigger()

        quote.refresh_from_db()
        self.assertEqual(quote.status, SalesQuote.Status.CONFIRMED)
        self.assertIsNone(quote.ready_at)
        self.assertEqual(WorkflowEvent.objects.count(), before["events"])
        self.assertEqual(NotificationOutboxIntent.objects.count(), before["outbox"])
        self.assertEqual(IdempotencyRecord.objects.count(), before["idempotency"])
        self.assertFalse(IdempotencyRecord.objects.filter(key="outbox-ready-insert-failure-key").exists())
        self.assertEqual(StockMovement.objects.count(), before["stock_movements"])
        self.assertEqual(StockBalance.objects.count(), before["stock_balances"])
        self.assertEqual(StockReservation.objects.count(), before["stock_reservations"])
        self.assertEqual(JournalEntry.objects.count(), before["journals"])

    def test_storage_outbox_insert_failure_rolls_back_charge_event_idempotency_and_ledger(self):
        quote, _readiness = self.make_ready_quote("outbox-storage-insert-failure")
        ready_at = quote.ready_at
        free_storage_end = _add_calendar_month(timezone.localtime(ready_at).date())
        at_boundary = timezone.make_aware(datetime.combine(free_storage_end, time.min))
        before = {
            "events": WorkflowEvent.objects.count(),
            "outbox": NotificationOutboxIntent.objects.count(),
            "idempotency": IdempotencyRecord.objects.count(),
            "stock_movements": StockMovement.objects.count(),
            "stock_balances": StockBalance.objects.count(),
            "stock_reservations": StockReservation.objects.count(),
            "journals": JournalEntry.objects.count(),
            "charges": StorageCharge.objects.filter(quote=quote).count(),
            "idempotency_keys": set(IdempotencyRecord.objects.values_list("key", flat=True)),
        }
        self.install_outbox_insert_failure_trigger()
        try:
            with self.assertRaisesRegex(IntegrityError, "forced synthetic outbox insert failure"):
                accrue_storage_charges(
                    actor=self.sales,
                    quote=quote,
                    idempotency_prefix="outbox-storage-insert-failure",
                    as_of=at_boundary,
                )
        finally:
            self.remove_outbox_insert_failure_trigger()

        quote.refresh_from_db()
        self.assertEqual(quote.status, SalesQuote.Status.READY)
        self.assertEqual(quote.ready_at, ready_at)
        self.assertEqual(StorageCharge.objects.filter(quote=quote).count(), before["charges"])
        self.assertEqual(WorkflowEvent.objects.count(), before["events"])
        self.assertEqual(NotificationOutboxIntent.objects.count(), before["outbox"])
        self.assertEqual(IdempotencyRecord.objects.count(), before["idempotency"])
        self.assertEqual(
            set(IdempotencyRecord.objects.values_list("key", flat=True)),
            before["idempotency_keys"],
        )
        self.assertEqual(StockMovement.objects.count(), before["stock_movements"])
        self.assertEqual(StockBalance.objects.count(), before["stock_balances"])
        self.assertEqual(StockReservation.objects.count(), before["stock_reservations"])
        self.assertEqual(JournalEntry.objects.count(), before["journals"])

    def test_two_postgres_workers_claim_one_pending_intent_once(self):
        _quote, readiness = self.make_ready_quote("outbox-concurrent-worker")
        intent = NotificationOutboxIntent.objects.get(workflow_event_id=readiness["event_id"])
        sink = InMemoryFakeNotificationSink()
        first_sink_call = Event()
        release_first_sink_call = Event()
        call_guard = Lock()
        first_call = [True]
        original_record_intent = sink.record_intent

        def block_first_sink_call(payload):
            with call_guard:
                should_block = first_call[0]
                first_call[0] = False
            if should_block:
                first_sink_call.set()
                if not release_first_sink_call.wait(timeout=15):
                    raise RuntimeError("Timed out waiting for the concurrent worker test.")
            return original_record_intent(payload)

        def run_worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    backend_pid = cursor.fetchone()[0]
                outcomes = process_notification_outbox_dry_run(sink=sink, limit=1)
                return backend_pid, outcomes
            finally:
                close_old_connections()

        with patch.object(sink, "record_intent", side_effect=block_first_sink_call):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_worker = executor.submit(run_worker)
                second_worker = None
                try:
                    self.assertTrue(first_sink_call.wait(timeout=15), "First worker did not reach the fake sink.")
                    second_worker = executor.submit(run_worker)
                    second_backend_pid, second_outcomes = second_worker.result(timeout=15)
                finally:
                    release_first_sink_call.set()
                first_backend_pid, first_outcomes = first_worker.result(timeout=15)

        self.assertNotEqual(first_backend_pid, second_backend_pid)
        self.assertEqual(len(first_outcomes) + len(second_outcomes), 1)
        self.assertEqual(
            (first_outcomes + second_outcomes)[0]["intent_id"],
            intent.pk,
        )
        self.assertEqual(len(sink.attempts), 1)
        self.assertEqual(len(sink.recorded_by_intent_id), 1)
        self.assertFalse(sink.attempts[0]["external_notification_sent"])
        self.assertNotIn("delivered", sink.attempts[0])

        intent.refresh_from_db()
        self.assertEqual(intent.status, NotificationOutboxIntent.Status.DRY_RUN_RECORDED)
        self.assertEqual(intent.attempt_count, 1)
        self.assertEqual(intent.last_error_code, "")
        self.assertNotIn(
            "delivered",
            {value for value, _label in NotificationOutboxIntent.Status.choices},
        )

    def test_fake_worker_retries_are_bounded_labeled_and_never_use_network(self):
        _quote, readiness = self.make_ready_quote("outbox-worker-retry")
        intent = NotificationOutboxIntent.objects.get(workflow_event_id=readiness["event_id"])
        sink = InMemoryFakeNotificationSink(failures_remaining=1)
        with patch("socket.create_connection", side_effect=AssertionError("network forbidden")) as socket_connect:
            with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")) as urlopen:
                first = process_notification_outbox_dry_run(sink=sink, limit=1)
                intent.refresh_from_db()
                self.assertEqual(first[0]["status"], NotificationOutboxIntent.Status.RETRY)
                self.assertEqual(intent.attempt_count, 1)
                self.assertEqual(intent.last_error_code, "FAKE_SINK_FAILURE")
                self.assertEqual(sink.recorded_by_intent_id, {})

                second = process_notification_outbox_dry_run(sink=sink, limit=1)
                intent.refresh_from_db()
                self.assertEqual(second[0]["status"], NotificationOutboxIntent.Status.DRY_RUN_RECORDED)
                self.assertEqual(intent.attempt_count, 2)
                self.assertEqual(len(sink.attempts), 2)
                self.assertEqual(len(sink.recorded_by_intent_id), 1)
                recorded = sink.recorded_by_intent_id[intent.pk]
                self.assertEqual(recorded["label"], DRY_RUN_NOTICE)
                self.assertTrue(recorded["dry_run"])
                self.assertFalse(recorded["external_notification_sent"])
                self.assertNotIn("message", recorded)
                self.assertNotIn("recipient", recorded)
                self.assertNotIn("channel", recorded)
                self.assertEqual(process_notification_outbox_dry_run(sink=sink, limit=1), [])
                socket_connect.assert_not_called()
                urlopen.assert_not_called()

        self.assertNotIn(
            "delivered",
            {value for value, _label in NotificationOutboxIntent.Status.choices},
        )

        _exhausted_quote, exhausted_readiness = self.make_ready_quote("outbox-worker-exhausted")
        exhausted = NotificationOutboxIntent.objects.get(
            workflow_event_id=exhausted_readiness["event_id"],
        )
        failing_sink = InMemoryFakeNotificationSink(failures_remaining=10)
        expected_statuses = [
            NotificationOutboxIntent.Status.RETRY,
            NotificationOutboxIntent.Status.RETRY,
            NotificationOutboxIntent.Status.DRY_RUN_FAILED,
        ]
        for expected_status in expected_statuses:
            outcome = process_notification_outbox_dry_run(sink=failing_sink, limit=1)
            self.assertEqual(outcome[0]["status"], expected_status)
        exhausted.refresh_from_db()
        self.assertEqual(exhausted.attempt_count, 3)
        self.assertEqual(exhausted.status, NotificationOutboxIntent.Status.DRY_RUN_FAILED)
        self.assertEqual(process_notification_outbox_dry_run(sink=failing_sink, limit=1), [])

        with self.assertRaises(NotificationOutboxError):
            process_notification_outbox_dry_run(sink=object())

        output = StringIO()
        with patch("socket.create_connection", side_effect=AssertionError("network forbidden")) as socket_connect:
            with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")) as urlopen:
                call_command("process_notification_outbox_dry_run", stdout=output, limit=1)
                socket_connect.assert_not_called()
                urlopen.assert_not_called()
        self.assertIn("DRY-RUN: recorded intent only; no external notification sent.", output.getvalue())
        self.assertIn("No email, WhatsApp, SMS, or network delivery is configured.", output.getvalue())

        with transaction.atomic():
            with self.assertRaises(NotificationOutboxError):
                process_notification_outbox_dry_run(sink=InMemoryFakeNotificationSink())
        with override_settings(APP_ENV="production"):
            with self.assertRaises(NotificationOutboxError):
                process_notification_outbox_dry_run(sink=InMemoryFakeNotificationSink())
