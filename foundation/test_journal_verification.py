import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, localcontext
from io import StringIO
from threading import Event
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .journal_verification import HEADER_FIELDS, LINE_FIELDS, _journal_discrepancies, verify_posted_journal_balances
from .management.commands.snapshot_integrity import Command as IntegritySnapshotCommand
from .models import IdempotencyRecord, JournalEntry, JournalLine, LedgerAccount, LegalEntity
from .test_transactions import TransactionFixtureMixin
from .transaction_services import deliver_reserved_stock, reserve_stock


@override_settings(APP_ENV="demo")
class PostedJournalVerificationTests(TransactionFixtureMixin, TransactionTestCase):
    def setUp(self):
        self.create_synthetic_catalog()

    def entry_fixture(self, key, *, entity=None, debits=("100.00",), credits=("100.00",), posted=True):
        """Disposable fixture only: existing models/protections, no service mappings."""
        entity = entity or self.legal_entity
        with transaction.atomic():
            operation = IdempotencyRecord.objects.create(
                key=key, operation="test_journal_verification", payload_hash="0" * 64,
                status=IdempotencyRecord.Status.COMPLETE, completed_at=timezone.now(),
            )
            entry = JournalEntry.objects.create(
                idempotency=operation, legal_entity=entity, kind=JournalEntry.Kind.RECEIPT,
                description="TEST ONLY — journal verification fixture",
            )
            for side, values in (("debit", debits), ("credit", credits)):
                account, _ = LedgerAccount.objects.get_or_create(
                    legal_entity=entity, code=f"VERIFY_{side.upper()}",
                    defaults={"name": "TEST ONLY", "account_class": LedgerAccount.AccountClass.ASSET},
                )
                for value in values:
                    JournalLine.objects.create(
                        entry=entry, account=account, memo="TEST ONLY", **{side: Decimal(value)},
                    )
            if posted:
                entry.status = JournalEntry.Status.POSTED
                entry.posted_at = timezone.now()
                entry.save(update_fields=["status", "posted_at"])
            return entry

    @staticmethod
    def facts():
        return {
            model.__name__: list(model.objects.order_by("pk").values())
            for model in (JournalEntry, JournalLine, LedgerAccount, IdempotencyRecord)
        }

    @staticmethod
    def copied_posted_facts():
        entries = JournalEntry.objects.filter(status=JournalEntry.Status.POSTED)
        return (
            list(entries.order_by("pk").values(*HEADER_FIELDS)),
            list(JournalLine.objects.filter(entry__in=entries).order_by("pk").values(*LINE_FIELDS)),
        )

    def test_empty_and_database_rejection_of_uncommitted_drafts(self):
        output = StringIO()
        call_command("verify_posted_journal_balances", stdout=output)
        empty = json.loads(output.getvalue())
        self.assertEqual(empty["status"], "empty")
        self.assertEqual(empty["checked_entries"], 0)
        self.assertEqual(empty["checked_lines"], 0)
        self.assertEqual(empty["checked_legal_entities"], 0)
        before = self.facts()
        # The deferred trigger requires posting before commit, even though the
        # model exposes a transient draft state. Do not disable it for fixtures.
        with self.assertRaisesMessage(DatabaseError, "must be posted in QAR before commit"):
            self.entry_fixture("unbalanced-draft", credits=("90.00",), posted=False)
        self.assertEqual(verify_posted_journal_balances(), empty)
        self.assertEqual(before, self.facts())

    def test_existing_service_postings_and_replay_have_no_writes_or_extra_fields(self):
        self.receive_fixture()
        self.receive_fixture()
        reservation = reserve_stock(
            idempotency_key="verify-reservation", business_unit=self.business_unit, variant=self.variant,
            location=self.locations["W41-01"], quantity=Decimal("2.00"),
        )
        for _ in range(2):
            deliver_reserved_stock(
                idempotency_key="verify-delivery", reservation_id=reservation["reservation_id"],
                unit_selling_price=Decimal("2000.00"),
            )
        before, manifest = self.facts(), IntegritySnapshotCommand._manifest()
        with CaptureQueriesContext(connection) as queries:
            result = verify_posted_journal_balances()
        self.assertEqual(result["status"], "consistent")
        self.assertEqual(result["checked_entries"], 2)
        self.assertEqual(result["checked_lines"], JournalLine.objects.count())
        self.assertEqual(result["checked_legal_entities"], 1)
        self.assertEqual(result["discrepancies"], [])
        self.assertEqual(before, self.facts())
        self.assertEqual(manifest, IntegritySnapshotCommand._manifest())
        sql = "\n".join(query["sql"] for query in queries)
        self.assertIn("REPEATABLE READ, READ ONLY", sql)
        for query in queries:
            self.assertFalse(query["sql"].lstrip().upper().startswith(
                ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"),
            ))
        for excluded in ("description", "memo", "payload_hash", "policy_label", "account_class"):
            self.assertNotIn(excluded, sql)
            self.assertNotIn(excluded, json.dumps(result))
        self.assertEqual(set(result), {
            "check", "status", "read_only", "checked_entries", "checked_lines",
            "checked_legal_entities", "discrepancy_count", "discrepancies", "basis",
        })

    def test_valid_posted_journals_are_checked_separately_per_legal_entity(self):
        other = LegalEntity.objects.create(code="verify-other", name="TEST ONLY — second entity")
        self.entry_fixture("entity-one")
        self.entry_fixture("entity-two", entity=other, debits=("0.01", "0.02"), credits=("0.03",))
        result = verify_posted_journal_balances()
        self.assertEqual(result["status"], "consistent")
        self.assertEqual(result["checked_legal_entities"], 2)
        self.assertEqual(result["checked_entries"], 2)
        self.assertEqual(result["checked_lines"], 5)

    def test_postgres_rejects_unbalanced_empty_and_single_line_posting(self):
        before, manifest = self.facts(), IntegritySnapshotCommand._manifest()
        for key, debits, credits in [
            ("bad-total", ("100.00",), ("90.00",)),
            ("no-lines", (), ()),
            ("one-line", ("100.00",), ()),
        ]:
            with self.subTest(key=key):
                with self.assertRaisesMessage(DatabaseError, "not balanced for its legal entity"):
                    self.entry_fixture(key, debits=debits, credits=credits)
                self.assertEqual(before, self.facts())
                self.assertEqual(manifest, IntegritySnapshotCommand._manifest())

    def test_posted_entries_and_lines_remain_database_immutable(self):
        entry = self.entry_fixture("immutable")
        line = entry.lines.first()
        before = self.facts()
        for mutate in [
            lambda: JournalEntry.objects.filter(pk=entry.pk).update(description="TEST ONLY edit"),
            lambda: JournalEntry.objects.filter(pk=entry.pk).delete(),
            lambda: JournalLine.objects.filter(pk=line.pk).update(debit="110.00"),
            lambda: JournalLine.objects.filter(pk=line.pk).delete(),
            lambda: JournalLine.objects.bulk_create([
                JournalLine(entry=entry, account_id=line.account_id, debit="1.00", memo="TEST ONLY"),
            ]),
        ]:
            with self.assertRaises(DatabaseError):
                with transaction.atomic():
                    mutate()
        self.assertEqual(before, self.facts())
        self.assertEqual(verify_posted_journal_balances()["status"], "consistent")

    def test_mutable_account_entity_drift_is_reported_deterministically_without_repair(self):
        entry = self.entry_fixture("account-drift")
        other = LegalEntity.objects.create(code="drift-other", name="TEST ONLY — isolated drift")
        line = entry.lines.first()
        # Legal-account metadata is mutable. Only this disposable fixture changes;
        # immutable posted entries/lines and every constraint remain untouched.
        LedgerAccount.objects.filter(pk=line.account_id).update(legal_entity=other)
        before, manifest = self.facts(), IntegritySnapshotCommand._manifest()
        outputs = []
        for _ in range(2):
            output = StringIO()
            with self.assertRaisesMessage(CommandError, "Nothing was repaired"):
                call_command("verify_posted_journal_balances", stdout=output)
            outputs.append(output.getvalue())
        self.assertEqual(outputs[0], outputs[1])
        result = json.loads(outputs[0])
        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["discrepancies"], [{
            "entry_id": entry.pk, "legal_entity_id": self.legal_entity.pk,
            "code": "account_legal_entity", "line_id": line.pk,
            "expected_legal_entity_id": self.legal_entity.pk, "actual_legal_entity_id": other.pk,
        }])
        self.assertEqual(result["discrepancy_count"], 1)
        self.assertEqual(before, self.facts())
        self.assertEqual(manifest, IntegritySnapshotCommand._manifest())

    def test_cross_entity_lines_and_non_qar_records_are_rejected_by_postgres(self):
        other = LegalEntity.objects.create(code="reference-other", name="TEST ONLY")
        account = LedgerAccount.objects.create(
            legal_entity=other, code="VERIFY_OTHER", name="TEST ONLY",
            account_class=LedgerAccount.AccountClass.ASSET,
        )
        before = self.facts()
        for mutate, message in [
            (
                lambda draft: JournalLine.objects.create(
                    entry=draft, account=account, debit="1.00", memo="TEST ONLY",
                ),
                "must match its legal entity and QAR currency",
            ),
            (lambda draft: JournalEntry.objects.filter(pk=draft.pk).update(currency="USD"), "journal_qar_only"),
            (lambda draft: LedgerAccount.objects.filter(pk=account.pk).update(currency="USD"), "ledger_account_qar_only"),
        ]:
            with self.assertRaisesMessage(DatabaseError, message):
                with transaction.atomic():
                    # A draft exists only inside the enclosing transaction;
                    # every deliberately invalid insert/update rolls it back.
                    draft = self.entry_fixture("draft-reference", posted=False)
                    mutate(draft)
        self.assertEqual(before, self.facts())

    def test_comparator_flags_offsetting_entries_and_is_deterministic_without_sql(self):
        first = self.entry_fixture("offset-first")
        second = self.entry_fixture("offset-second")
        headers, lines = self.copied_posted_facts()
        for row in lines:
            if row["credit"]:
                row["credit"] = Decimal("90.00" if row["entry_id"] == first.pk else "110.00")
        before = self.facts()
        with CaptureQueriesContext(connection) as queries:
            result = _journal_discrepancies(headers, lines)
            reversed_result = _journal_discrepancies(list(reversed(headers)), list(reversed(lines)))
        self.assertEqual(len(queries), 0)
        self.assertEqual(result, reversed_result)
        self.assertEqual([row["entry_id"] for row in result], [first.pk, second.pk])
        self.assertEqual([row["difference"] for row in result], ["10.00", "-10.00"])
        self.assertEqual({row["code"] for row in result}, {"unbalanced"})
        self.assertEqual(before, self.facts())

    def test_exact_large_totals_ignore_callers_low_decimal_precision(self):
        largest = "99999999999999.99"
        self.entry_fixture("large", debits=(largest, largest), credits=(largest, largest))
        headers, lines = self.copied_posted_facts()
        with localcontext() as context:
            context.prec = 6
            self.assertEqual(verify_posted_journal_balances()["status"], "consistent")
            # An unpersistable imbalance is tested only on in-memory copies.
            lines[-1]["credit"] = Decimal("99999999999999.98")
            result = _journal_discrepancies(headers, lines)
        self.assertEqual(result[0]["debit_total"], "199999999999999.98")
        self.assertEqual(result[0]["credit_total"], "199999999999999.97")
        self.assertEqual(result[0]["difference"], "0.01")

    def test_local_demo_postgres_and_standalone_guards_fail_closed(self):
        with override_settings(APP_ENV="production"):
            with self.assertRaisesMessage(CommandError, "APP_ENV=demo"):
                verify_posted_journal_balances()
        for settings_patch, message in [
            ({"HOST": "db.invalid"}, "non-local"),
            ({"NAME": "unrecognized"}, "unrecognized database"),
        ]:
            with patch.dict(connection.settings_dict, settings_patch):
                with self.assertRaisesMessage(CommandError, message):
                    verify_posted_journal_balances()
        with patch.object(connection, "vendor", "sqlite"):
            with self.assertRaisesMessage(CommandError, "PostgreSQL"):
                verify_posted_journal_balances()
        with transaction.atomic():
            with self.assertRaisesMessage(CommandError, "outside an existing transaction"):
                verify_posted_journal_balances()

    def test_snapshot_read_only_mode_rejects_an_accidental_write(self):
        self.entry_fixture("read-only")
        before = self.facts()

        def attempted_write(execute, sql, params, many, context):
            if 'FROM "foundation_journalentry"' in sql:
                execute('UPDATE "foundation_journalentry" SET description = description', (), False, context)
            return execute(sql, params, many, context)

        with self.assertRaises(DatabaseError) as caught:
            with connection.execute_wrapper(attempted_write):
                verify_posted_journal_balances()
        self.assertEqual(caught.exception.__cause__.sqlstate, "25006")
        self.assertEqual(before, self.facts())

    def test_concurrent_posting_cannot_mix_header_and_line_generations(self):
        self.entry_fixture("snapshot-first")
        start_writer, writer_done = Event(), Event()
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            reader_pid = cursor.fetchone()[0]

        def writer():
            close_old_connections()
            try:
                if not start_writer.wait(15):
                    raise AssertionError("Reader did not reach journal-line read.")
                with connection.cursor() as cursor:
                    pid = cursor.execute("SELECT pg_backend_pid()").fetchone()[0]
                self.entry_fixture("snapshot-concurrent")
                return pid
            finally:
                writer_done.set()
                connection.close()

        def interleave(execute, sql, params, many, context):
            if 'FROM "foundation_journalline"' in sql and not start_writer.is_set():
                start_writer.set()
                if not writer_done.wait(15):
                    raise AssertionError("Concurrent posting did not complete.")
            return execute(sql, params, many, context)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            with connection.execute_wrapper(interleave):
                first = verify_posted_journal_balances()
            self.assertNotEqual(reader_pid, future.result(timeout=15))
        self.assertEqual(first["status"], "consistent")
        self.assertEqual((first["checked_entries"], first["checked_lines"]), (1, 2))
        fresh = verify_posted_journal_balances()
        self.assertEqual(fresh["status"], "consistent")
        self.assertEqual((fresh["checked_entries"], fresh["checked_lines"]), (2, 4))
