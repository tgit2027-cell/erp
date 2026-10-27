"""Real PostgreSQL contention; synthetic non-posting return acceptance only."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, Event
from time import monotonic

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    BusinessUnitRoleAssignment, IdempotencyRecord, SalesReturnEvent,
    SalesReturnIntake, UserActionGrant,
)
from .test_reporting import ReportingFixtureMixin
from . import test_sales_returns


class SalesReturnConcurrencyTests(ReportingFixtureMixin, TransactionTestCase):
    """Every worker owns a real connection and calls the actual inspection API."""

    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.assertTrue(connection.settings_dict["NAME"].startswith("test_"))
        self.create_fixture()
        test_sales_returns.SalesReturnIntakeTests.setUp(self)
        created = test_sales_returns.SalesReturnIntakeTests.post(self)
        self.assertEqual(created.status_code, 200, created.data)
        self.intake_id = created.data["id"]
        self.inspection_url = f"{self.url}{self.intake_id}/inspection/"
        self.payload = {
            "business_unit_id": self.unit.pk,
            "lines": [{
                "quote_line_id": self.line.pk, "condition": "good",
                "inspection_notes": "TEST ONLY first inspector's finding",
            }],
        }
        self.business_before = self.business_snapshot()
        self.intake_before = SalesReturnIntake.objects.get(pk=self.intake_id)

    def business_snapshot(self):
        return test_sales_returns.SalesReturnIntakeTests.operational_snapshot(self)

    def inspect(self, payload=None, key="inspection-first"):
        return self.client.post(
            self.inspection_url, payload or self.payload, format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )

    def race(self, requests, *, lock_table=None):
        """Hold a real first-worker lock until PostgreSQL proves worker 2 waits.

        An execute wrapper delays only the test schedule after the real SQL has
        acquired its lock. It does not mock SQL/results or application services.
        No timing-only assumption or shared Django connection proves contention.
        """
        ready = Barrier(3)
        locked, release = Event(), Event()
        workers = {}
        actor_id = self.sales.pk
        expected_database = connection.settings_dict["NAME"]
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            observer_pid = cursor.fetchone()[0]

        def worker(index, request):
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '12s'")
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SELECT pg_backend_pid(), current_database()")
                    pid, database = cursor.fetchone()
                self.assertEqual(database, expected_database)
                workers[index] = {"pid": pid, "lock_held": False}
                client = APIClient()
                client.force_authenticate(user=get_user_model().objects.get(pk=actor_id))
                ready.wait(timeout=10)
                if index == 1 and lock_table:
                    self.assertTrue(locked.wait(10), "First worker never acquired the expected lock")

                def schedule(execute, sql, params, many, context):
                    result = execute(sql, params, many, context)
                    if (
                        index == 0 and lock_table and not workers[index]["lock_held"]
                        and f'"{lock_table}"' in sql
                        and sql.lstrip().upper().startswith("SELECT")
                        and "FOR UPDATE" in sql.upper()
                    ):
                        self.assertTrue(db.in_atomic_block)
                        workers[index]["lock_held"] = True
                        locked.set()
                        self.assertTrue(release.wait(10), "Observer failed to release the held test lock")
                    return result

                key, payload = request
                with db.execute_wrapper(schedule):
                    response = client.post(
                        self.inspection_url, deepcopy(payload), format="json",
                        HTTP_IDEMPOTENCY_KEY=key,
                    )
                return {"pid": pid, "status": response.status_code, "data": deepcopy(response.data)}
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, index, request) for index, request in enumerate(requests)]
            blocked = False
            try:
                ready.wait(timeout=10)
                self.assertEqual(len({observer_pid, workers[0]["pid"], workers[1]["pid"]}), 3)
                if lock_table:
                    self.assertTrue(locked.wait(10), "First worker never reached its real row lock")
                    deadline = monotonic() + 8
                    while monotonic() < deadline:
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "SELECT pg_blocking_pids(%s), wait_event_type "
                                "FROM pg_stat_activity WHERE pid = %s",
                                [workers[1]["pid"], workers[1]["pid"]],
                            )
                            waiting = cursor.fetchone()
                        if waiting and workers[0]["pid"] in waiting[0] and waiting[1] == "Lock":
                            blocked = True
                            break
                        if futures[1].done():
                            break
                        release.wait(0.01)
                    self.assertTrue(blocked, "No real PostgreSQL lock contention was observed")
            finally:
                release.set()
            results = [future.result(timeout=15) for future in futures]
        print(
            f"{self._testMethodName}: PostgreSQL observer PID={observer_pid}; "
            f"worker PIDs={[result['pid'] for result in results]}; "
            f"observed_lock_wait={blocked}; HTTP={[result['status'] for result in results]}"
        )
        return results

    def assert_one_inspection_and_no_posting(self, result, *, expected_key):
        intake = SalesReturnIntake.objects.get(pk=self.intake_id)
        self.assertEqual(intake.status, "finance_pending")
        self.assertEqual(intake.lines, [{
            key: value for key, value in line.items()
            if key in {"quote_line_id", "quantity", "condition", "inspection_notes"}
        } for line in result["lines"]])
        self.assertEqual(intake.lines[0]["condition"], self.payload["lines"][0]["condition"])
        self.assertEqual(intake.lines[0]["inspection_notes"], self.payload["lines"][0]["inspection_notes"])
        self.assertEqual(intake.delivery_window, self.intake_before.delivery_window)
        self.assertEqual(intake.policy_snapshot, self.intake_before.policy_snapshot)
        self.assertEqual(intake.created_at, self.intake_before.created_at)
        self.assertTrue(intake.delivery_window["eligible"])
        self.assertEqual(self.business_before, self.business_snapshot())
        self.assertEqual(SalesReturnIntake.objects.count(), 1)
        self.assertEqual(SalesReturnEvent.objects.filter(intake=intake, kind="intake_recorded").count(), 1)
        events = SalesReturnEvent.objects.filter(intake=intake, kind="inspection_recorded")
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.get().details["lines"], intake.lines)
        self.assertEqual(events.get().details["status"], "finance_pending")
        records = IdempotencyRecord.objects.filter(operation="sales_return_inspection")
        self.assertEqual(records.count(), 1, "Rejected contender cannot leave a processing key")
        record = records.get()
        self.assertEqual(record.key, expected_key)
        self.assertEqual(record.status, IdempotencyRecord.Status.COMPLETE)
        self.assertEqual(record.result, result)

    def test_independent_inspection_keys_contend_once_and_retry_preserves_the_winner(self):
        other = deepcopy(self.payload)
        other["lines"][0].update(condition="not_good", inspection_notes="TEST ONLY competing finding")
        first, second = self.race([
            ("inspection-first", self.payload), ("inspection-contender", other),
        ], lock_table="foundation_salesreturnintake")
        self.assertEqual(first["status"], 200, first)
        self.assertEqual(second["status"], 400, second)
        self.assertIn("Inspection is already recorded", str(second["data"]))
        self.assert_one_inspection_and_no_posting(first["data"], expected_key="inspection-first")
        before = list(SalesReturnIntake.objects.values())
        events_before = list(SalesReturnEvent.objects.order_by("pk").values())
        replay = self.inspect()
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data, first["data"])
        conflict = self.inspect(other)
        self.assertEqual(conflict.status_code, 409)
        self.assertIn("different request", str(conflict.data))
        self.assertEqual(before, list(SalesReturnIntake.objects.values()))
        self.assertEqual(events_before, list(SalesReturnEvent.objects.order_by("pk").values()))
        self.assert_one_inspection_and_no_posting(first["data"], expected_key="inspection-first")

    def test_simultaneous_identical_key_and_payload_returns_one_original_outcome(self):
        first, second = self.race([
            ("inspection-same", self.payload), ("inspection-same", self.payload),
        ], lock_table="foundation_idempotencyrecord")
        self.assertEqual([first["status"], second["status"]], [200, 200])
        self.assertEqual(first["data"], second["data"])
        self.assert_one_inspection_and_no_posting(first["data"], expected_key="inspection-same")

    def test_simultaneous_conflicting_key_reuse_is_rejected_without_overwriting_outcome(self):
        other = deepcopy(self.payload)
        other["lines"][0]["inspection_notes"] = "TEST ONLY changed conflicting request"
        first, second = self.race([
            ("inspection-conflict", self.payload), ("inspection-conflict", other),
        ], lock_table="foundation_idempotencyrecord")
        self.assertEqual(first["status"], 200, first)
        self.assertEqual(second["status"], 409, second)
        self.assertIn("different request", str(second["data"]))
        self.assert_one_inspection_and_no_posting(first["data"], expected_key="inspection-conflict")

    def assert_denied_without_changes(self, results, expected_status):
        self.assertEqual([result["status"] for result in results], [expected_status, expected_status])
        for result in results:
            self.assertNotIn("lines", result["data"])
            self.assertNotIn("proposed_refund_amount", result["data"])
        self.assertEqual(self.business_before, self.business_snapshot())
        self.assertEqual(IdempotencyRecord.objects.filter(operation="sales_return_inspection").count(), 0)
        self.assertEqual(SalesReturnEvent.objects.filter(kind="inspection_recorded").count(), 0)
        intake = SalesReturnIntake.objects.get(pk=self.intake_id)
        self.assertEqual(intake.status, "awaiting_inspection")
        self.assertEqual(intake.lines, self.intake_before.lines)
        self.assertEqual(intake.updated_at, self.intake_before.updated_at)

    def test_grant_revoked_after_intake_creation_denies_both_independent_inspections(self):
        UserActionGrant.objects.filter(
            user=self.sales, business_unit=self.unit, action="create_order",
        ).update(revoked_at=timezone.now())
        self.business_before = self.business_snapshot()  # test-only revocation, not a service write
        results = self.race([("denied-grant-1", self.payload), ("denied-grant-2", self.payload)])
        self.assert_denied_without_changes(results, 404)

    def test_role_revoked_after_intake_creation_denies_both_independent_inspections(self):
        BusinessUnitRoleAssignment.objects.filter(
            user=self.sales, business_unit=self.unit,
        ).update(revoked_at=timezone.now())
        self.business_before = self.business_snapshot()
        results = self.race([("denied-role-1", self.payload), ("denied-role-2", self.payload)])
        self.assert_denied_without_changes(results, 403)

    def test_revoked_grant_prevents_completed_retry_from_disclosing_or_mutating_original_result(self):
        original = self.inspect()
        self.assertEqual(original.status_code, 200)
        UserActionGrant.objects.filter(
            user=self.sales, business_unit=self.unit, action="create_order",
        ).update(revoked_at=timezone.now())
        self.business_before = self.business_snapshot()
        intake_before = list(SalesReturnIntake.objects.values())
        events_before = list(SalesReturnEvent.objects.order_by("pk").values())
        keys_before = list(IdempotencyRecord.objects.order_by("pk").values())
        results = self.race([("inspection-first", self.payload), ("inspection-first", self.payload)])
        self.assertEqual([result["status"] for result in results], [404, 404])
        for result in results:
            self.assertNotIn("lines", result["data"])
            self.assertNotIn("proposed_refund_amount", result["data"])
        self.assertEqual(intake_before, list(SalesReturnIntake.objects.values()))
        self.assertEqual(events_before, list(SalesReturnEvent.objects.order_by("pk").values()))
        self.assertEqual(keys_before, list(IdempotencyRecord.objects.order_by("pk").values()))
        self.assert_one_inspection_and_no_posting(original.data, expected_key="inspection-first")


class SalesReturnCreationConcurrencyTests(ReportingFixtureMixin, TransactionTestCase):
    """Actual intake API requests, independent connections, no financial posting."""

    race = SalesReturnConcurrencyTests.race
    business_snapshot = SalesReturnConcurrencyTests.business_snapshot

    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.assertTrue(connection.settings_dict["NAME"].startswith("test_"))
        self.create_fixture()
        test_sales_returns.SalesReturnIntakeTests.setUp(self)
        # Reuse the real-lock harness, targeting creation rather than inspection.
        self.inspection_url = self.url
        self.business_before = self.business_snapshot()

    def post(self, payload=None, key="creation-same"):
        return test_sales_returns.SalesReturnIntakeTests.post(
            self, data=payload, key=key,
        )

    def return_snapshot(self):
        return {
            model.__name__: list(model.objects.order_by("pk").values())
            for model in (SalesReturnIntake, SalesReturnEvent, IdempotencyRecord)
        }

    def assert_one_intake_without_posting(self, result, key):
        self.assertEqual(self.business_before, self.business_snapshot())
        intake = SalesReturnIntake.objects.get()
        self.assertEqual(intake.pk, result["id"])
        self.assertEqual(intake.status, "awaiting_inspection")
        self.assertEqual(result["status"], "awaiting_inspection")
        self.assertTrue(intake.delivery_window["eligible"])
        self.assertTrue(intake.is_synthetic)
        self.assertEqual(format(intake.proposed_refund_amount, ".2f"), self.data["proposed_refund_amount"])
        self.assertEqual(intake.lines[0]["condition"], "uninspected")
        event = SalesReturnEvent.objects.get()
        self.assertEqual(event.intake_id, intake.pk)
        self.assertEqual(event.kind, "intake_recorded")
        self.assertEqual(event.details, {"status": intake.status, "lines": intake.lines})
        record = IdempotencyRecord.objects.get(operation="sales_return_intake")
        self.assertEqual(record.key, key)
        self.assertEqual(record.pk, intake.idempotency_id)
        self.assertEqual(record.status, IdempotencyRecord.Status.COMPLETE)
        self.assertEqual(record.result, result)

    def test_simultaneous_identical_intakes_commit_one_audit_and_replay_original(self):
        first, second = self.race([
            ("creation-same", self.data), ("creation-same", self.data),
        ], lock_table="foundation_idempotencyrecord")
        self.assertEqual([first["status"], second["status"]], [200, 200])
        self.assertEqual(first["data"], second["data"])
        self.assert_one_intake_without_posting(first["data"], "creation-same")
        before = self.return_snapshot()
        for _ in range(2):
            replay = self.post()
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(replay.data, first["data"])
        self.assertEqual(before, self.return_snapshot())
        self.assert_one_intake_without_posting(first["data"], "creation-same")

    def test_simultaneous_conflicting_intake_payload_rejects_loser_without_partial_writes(self):
        other = {**self.data, "proposed_refund_amount": "1999.99"}
        first, second = self.race([
            ("creation-conflict", self.data), ("creation-conflict", other),
        ], lock_table="foundation_idempotencyrecord")
        self.assertEqual([first["status"], second["status"]], [200, 409])
        self.assertIn("different request", str(second["data"]))
        self.assert_one_intake_without_posting(first["data"], "creation-conflict")
        before = self.return_snapshot()
        conflict = self.post(other, key="creation-conflict")
        self.assertEqual(conflict.status_code, 409)
        self.assertIn("different request", str(conflict.data))
        replay = self.post(key="creation-conflict")
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data, first["data"])
        self.assertEqual(before, self.return_snapshot())
        self.assert_one_intake_without_posting(first["data"], "creation-conflict")
