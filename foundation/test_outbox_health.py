import json
import socket
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Event
from unittest.mock import patch

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from .models import NotificationOutboxIntent, SalesQuote, WorkflowEvent
from .outbox_health import summarize_notification_outbox_health
from .test_purchasing_workflows import PurchasingFixtureMixin


@override_settings(APP_ENV="demo")
class NotificationOutboxHealthTests(PurchasingFixtureMixin, TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.assertEqual(connection.settings_dict["NAME"], "test_tivoli_erp_demo")
        self.create_fixture()
        self.quote = SalesQuote.objects.get(pk=self.make_quote(key="health-quote")["quote_id"])

    def intent(self, *, status="pending", attempts=0, code="", kind=None):
        event = WorkflowEvent.objects.create(
            quote=self.quote, actor=self.sales,
            kind=kind or WorkflowEvent.Kind.READINESS_NOTIFICATION,
            message="PRIVATE_SYNTHETIC_MESSAGE",
            details={"recipient": "PRIVATE_SYNTHETIC_RECIPIENT", "amount": "PRIVATE_SYNTHETIC_COST"},
        )
        return NotificationOutboxIntent.objects.create(
            workflow_event=event, status=status, attempt_count=attempts, last_error_code=code,
        )

    @staticmethod
    def facts():
        return {
            model.__name__: list(model.objects.order_by("pk").values())
            for model in apps.get_app_config("foundation").get_models()
        }

    @staticmethod
    def command():
        output = StringIO()
        call_command("summarize_notification_outbox_health", stdout=output)
        return output.getvalue()

    def test_empty_explicit_zero_counts_and_no_external_delivery_claim(self):
        result = json.loads(self.command())
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["intent_count"], 0)
        self.assertEqual(result["counts_by_status"],
                         {"pending": 0, "retry": 0, "dry_run_recorded": 0, "dry_run_failed": 0})
        self.assertEqual(result["attempts"]["intent_counts_by_attempt_count"],
                         {"0": 0, "1": 0, "2": 0, "3": 0})
        self.assertEqual(result["failure_code_counts"], {})
        self.assertFalse(result["external_delivery_verified"])
        self.assertFalse(result["external_notification_sent_by_this_command"])
        self.assertIn("not externally delivered", result["label"])

    def test_mixed_counts_attempts_safe_codes_and_deterministic_redacted_output(self):
        self.intent()
        self.intent()
        self.intent(status="retry", attempts=1, code="FAKE_SINK_FAILURE")
        self.intent(status="retry", attempts=2, code="PRIVATE_SYNTHETIC_ERROR")
        self.intent(status="dry_run_recorded", attempts=2,
                    kind=WorkflowEvent.Kind.STORAGE_CHARGE_NOTIFICATION)
        self.intent(status="dry_run_failed", attempts=3, code="FAKE_SINK_FAILURE")
        first = self.command()
        self.assertEqual(first, self.command())
        result = json.loads(first)
        self.assertEqual(result["intent_count"], 6)
        self.assertEqual(result["counts_by_status"],
                         {"pending": 2, "retry": 2, "dry_run_recorded": 1, "dry_run_failed": 1})
        self.assertEqual(result["attempts"], {
            "total_recorded_attempts": 8, "existing_worker_attempt_limit": 3,
            "intent_counts_by_attempt_count": {"0": 2, "1": 1, "2": 2, "3": 1},
        })
        self.assertEqual(result["failure_code_counts"],
                         {"FAKE_SINK_FAILURE": 2, "REDACTED_UNRECOGNIZED": 1})
        self.assertEqual(result["status"], "recorded_metadata")
        self.assertNotIn("PRIVATE_SYNTHETIC", first)
        self.assertNotIn("delivered_count", result)
        self.assertEqual(set(result), {
            "check", "label", "read_only", "snapshot_scope", "status", "intent_count",
            "counts_by_status", "attempts", "failure_code_counts",
            "external_notification_sent_by_this_command", "external_delivery_verified",
        })

    def test_no_worker_network_payload_read_or_database_writes(self):
        self.intent(status="retry", attempts=1, code="FAKE_SINK_FAILURE")
        before = self.facts()
        with patch("foundation.notification_outbox.process_notification_outbox_dry_run",
                   side_effect=AssertionError("Worker must never run")), \
             patch("foundation.notification_outbox.InMemoryFakeNotificationSink.record_intent",
                   side_effect=AssertionError("Sink must never run")), \
             patch.object(socket.socket, "connect", side_effect=AssertionError("No network")), \
             CaptureQueriesContext(connection) as queries:
            self.command()
        self.assertEqual(before, self.facts())
        for query in queries:
            self.assertTrue(query["sql"].lstrip().upper().startswith(
                ("SELECT", "BEGIN", "SET TRANSACTION", "COMMIT")
            ), query["sql"])
            self.assertNotIn('"message"', query["sql"])
            self.assertNotIn('"details"', query["sql"])

    def test_read_only_repeatable_read_is_enforced_by_postgresql(self):
        self.intent()
        before = self.facts()
        seen = []

        def check_snapshot(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if sql == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY":
                with connection.cursor() as cursor:
                    cursor.execute("SHOW transaction_isolation")
                    self.assertEqual(cursor.fetchone()[0], "repeatable read")
                    cursor.execute("SHOW transaction_read_only")
                    self.assertEqual(cursor.fetchone()[0], "on")
                with self.assertRaisesMessage(DatabaseError, "read-only transaction"):
                    with transaction.atomic():
                        NotificationOutboxIntent.objects.update(attempt_count=0)
                seen.append(True)
            return result

        with connection.execute_wrapper(check_snapshot):
            self.command()
        self.assertEqual(seen, [True])
        self.assertEqual(before, self.facts())

    def test_scope_rejection_and_database_protection_of_synthetic_related_facts(self):
        intent = self.intent(kind=WorkflowEvent.Kind.STORAGE_EXCEPTION)
        before = self.facts()
        output = StringIO()
        with self.assertRaisesMessage(CommandError, "outside the supported synthetic"):
            call_command("summarize_notification_outbox_health", stdout=output)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(before, self.facts())
        # Do not disable database constraints to fabricate non-synthetic records.
        for instance in (intent, intent.workflow_event, self.quote, self.unit,
                         self.unit.legal_entity, self.customer):
            with self.subTest(model=type(instance).__name__):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        type(instance).objects.filter(pk=instance.pk).update(is_synthetic=False)
        self.assertEqual(before, self.facts())

    def test_unsafe_environment_target_or_existing_transaction_is_rejected(self):
        with override_settings(APP_ENV="production"):
            with self.assertRaisesMessage(CommandError, "APP_ENV=demo"):
                self.command()
        for change in ({"HOST": "remote.invalid"}, {"NAME": "not_synthetic"}):
            with patch.dict(connection.settings_dict, change):
                with self.assertRaises(CommandError):
                    self.command()
        with transaction.atomic():
            with self.assertRaisesMessage(CommandError, "outside an existing transaction"):
                self.command()

    def test_concurrent_commit_cannot_mix_scope_check_and_group_counts(self):
        intent = self.intent()
        start_writer, writer_done = Event(), Event()
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            reader_pid = cursor.fetchone()[0]

        def writer():
            close_old_connections()
            try:
                if not start_writer.wait(15):
                    raise AssertionError("Reader did not finish its scope-check snapshot")
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    writer_pid = cursor.fetchone()[0]
                with transaction.atomic():
                    NotificationOutboxIntent.objects.filter(pk=intent.pk).update(
                        status="retry", attempt_count=1, last_error_code="FAKE_SINK_FAILURE",
                    )
                    self.intent(status="dry_run_failed", attempts=3, code="FAKE_SINK_FAILURE")
                return writer_pid
            finally:
                writer_done.set()
                connection.close()

        def pause_after_scope_check(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if 'FROM "foundation_notificationoutboxintent"' in sql and "GROUP BY" not in sql \
                    and not start_writer.is_set():
                start_writer.set()
                if not writer_done.wait(15):
                    raise AssertionError("Writer did not finish its synthetic commit")
            return result

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            with connection.execute_wrapper(pause_after_scope_check):
                result = summarize_notification_outbox_health()
            writer_pid = future.result(timeout=15)
        self.assertNotEqual(reader_pid, writer_pid)
        self.assertEqual(result["intent_count"], 1)
        self.assertEqual(result["counts_by_status"]["pending"], 1)
        self.assertEqual(result["attempts"]["total_recorded_attempts"], 0)
        self.assertEqual(result["failure_code_counts"], {})
        fresh = summarize_notification_outbox_health()
        self.assertEqual(fresh["intent_count"], 2)
        self.assertEqual(fresh["counts_by_status"]["pending"], 0)
        self.assertEqual(fresh["counts_by_status"]["retry"], 1)
        self.assertEqual(fresh["counts_by_status"]["dry_run_failed"], 1)
        self.assertEqual(fresh["attempts"]["total_recorded_attempts"], 4)
