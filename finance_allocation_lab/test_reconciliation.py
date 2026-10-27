import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.conf import settings
from django.db import connection, connections, DatabaseError, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve

from foundation.test_finance_draft_workflow import Fixture, LAB
from foundation.finance_drafts import FinanceDraftConflict
from foundation.models import LegalEntity
from .models import ReconciliationProposal, ReconciliationRevision
from .reconciliation import save_reconciliation, snapshot


def source(identifier, amount, direction="increase", date="2028-02-29"):
    return {"row_id": identifier, "date": date, "amount": amount, "direction": direction,
            "reference": " Synthetic reference ", "explanation": " Source details — بيانات مقدمة "}


def pair(left="S1", right="B1"):
    return {"statement_row_id": left, "book_row_id": right, "explanation": " Reviewer proposed — مراجعة "}


class ReconciliationFixture(Fixture):
    def setup_reconciliation(self):
        self.setup_fixture()
        self.client.force_login(self.actor)
        self.before = self.business_counts()

    def body(self):
        return {"name": "Synthetic comparison", "currency": "QAR",
                "statement_date": "2028-02-29", "statement_balance": "100.30",
                "book_date": "2028-02-29", "book_balance": "100.10",
                "explanation": " Explicit review only — نص ",
                "statement_rows": [source("S1", "0.10"), source("S2", "0.20"), source("S3", "0.05", "decrease")],
                "book_rows": [source("B1", "0.10"), source("B2", "0.02", "decrease")],
                "matches": [pair()]}

    def post(self, body):
        return self.client.post("/lab/reconciliations/", json.dumps(body), content_type="application/json")

    def put(self, pk, body):
        return self.client.put(f"/lab/reconciliations/{pk}/", json.dumps(body), content_type="application/json")


@override_settings(**LAB)
class ReconciliationTests(ReconciliationFixture, TestCase):
    def setUp(self):
        self.setup_reconciliation()

    def test_exact_differences_unmatched_rows_and_sources_preserved(self):
        response = self.post(self.body())
        self.assertEqual(response.status_code, 201)
        saved = response.json()
        self.assertEqual(saved["review"], {
            "currency": "QAR", "balance_difference": "0.20",
            "statement_net": "0.25", "book_net": "0.08",
            "unmatched_statement_ids": ["S2", "S3"], "unmatched_book_ids": ["B2"],
            "unmatched_statement_net": "0.15", "unmatched_book_net": "-0.02",
            "unmatched_row_difference": "0.17", "comparison_residual": "0.03",
            "matched_count": 1, "warnings": [], "posted": False, "operational_reconciled": False})
        for key in ["statement_rows", "book_rows", "matches", "explanation"]:
            self.assertEqual(saved[key], self.body()[key])
        self.assertEqual(self.before, self.business_counts())

    def test_signed_balances_empty_zero_and_large_exact_inputs_never_complete_accounts(self):
        body = self.body()
        body.update(statement_balance="-1.00", book_balance="-1.10", statement_rows=[], book_rows=[], matches=[])
        result = self.post(body).json()["review"]
        self.assertEqual(result["balance_difference"], "0.10")
        self.assertEqual(result["comparison_residual"], "0.10")
        self.assertEqual(result["unmatched_statement_ids"], [])
        body.update(statement_balance="0.00", book_balance="-0.00")
        saved = self.post(body).json()
        self.assertEqual(saved["book_balance"], "0.00")
        self.assertEqual(saved["review"]["comparison_residual"], "0.00")
        self.assertFalse(saved["review"]["operational_reconciled"])
        self.assertFalse(saved["review"]["posted"])
        body.update(statement_balance="999999999999.99", book_balance="-999999999999.99")
        self.assertEqual(self.post(body).json()["review"]["balance_difference"], "1999999999999.98")
        body.update(statement_rows=[source("S0", "0.00")], book_rows=[source("B0", "0.00")], matches=[pair("S0", "B0")])
        self.assertEqual(self.post(body).json()["review"]["matched_count"], 1)
        self.assertEqual(self.before, self.business_counts())

    def test_double_missing_incompatible_and_duplicate_ids_rejected_without_partial_writes(self):
        invalid = []
        body = self.body()
        body["book_rows"].append(source("B3", "0.10"))
        body["matches"].append(pair("S1", "B3"))
        invalid.append(body)
        body = self.body()
        body["matches"].append(pair("S2", "B1"))
        invalid.append(body)
        body = self.body()
        body["matches"] = [pair("MISSING", "B1")]
        invalid.append(body)
        body = self.body()
        body["matches"] = [pair("S2", "B1")]
        invalid.append(body)
        body = self.body()
        body["book_rows"][0]["direction"] = "decrease"
        invalid.append(body)
        body = self.body()
        body["statement_rows"].append(copy.deepcopy(body["statement_rows"][0]))
        invalid.append(body)
        body = self.body()
        body["statement_rows"].pop(0)  # Matched source cannot silently disappear.
        invalid.append(body)
        for body in invalid:
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, 400)
        self.assertEqual((ReconciliationProposal.objects.count(), ReconciliationRevision.objects.count()), (0, 0))
        saved = self.post(self.body()).json()
        for body in invalid:
            self.assertEqual(self.put(saved["id"], {**body, "expected_revision": 1}).status_code, 400)
        self.assertEqual(self.client.get(f"/lab/reconciliations/{saved['id']}/").json(), saved)
        self.assertEqual(self.before, self.business_counts())

    def test_strict_dates_precision_row_limits_and_no_inferred_fields(self):
        for key, value in [
            ("name", ""), ("name", "x" * 201), ("currency", "USD"),
            ("statement_date", "0000-01-01"), ("book_date", "2027-02-29"),
            ("statement_balance", .1), ("book_balance", True),
            ("statement_balance", "NaN"), ("statement_balance", "0.001"),
            ("book_balance", "-1000000000000"), ("explanation", "x" * 2001),
            ("matches", "automatic"), ("statement_rows", None),
        ]:
            body = self.body()
            body[key] = value
            with self.subTest(key=key, value=str(value)[:20]):
                self.assertEqual(self.post(body).status_code, 400)
        for key, value in [
            ("row_id", []), ("row_id", "X" * 65), ("row_id", "not a local ID"),
            ("date", "2028-02-30"), ("amount", "-1.00"), ("amount", .1),
            ("amount", "1.001"), ("direction", "credit"), ("reference", "x" * 201),
            ("explanation", None),
        ]:
            body = self.body()
            body["statement_rows"][0][key] = value
            with self.subTest(key=key):
                self.assertEqual(self.post(body).status_code, 400)
        for field in ["automatic_match", "tolerance", "adjustment_account", "approve", "posted"]:
            body = self.body()
            body[field] = True
            self.assertEqual(self.post(body).status_code, 400)
        for field in ["statement_rows", "book_rows", "matches"]:
            body = self.body()
            body[field] *= 101
            self.assertEqual(self.post(body).status_code, 400)
        self.assertEqual(ReconciliationProposal.objects.count(), 0)

    def test_manual_match_does_not_infer_dates_references_cutoffs_or_row_order(self):
        body = self.body()
        body["book_date"] = "2028-03-02"
        body["book_rows"][0].update(date="2028-03-03", reference="Different reference", explanation=" Different source ")
        # Same exact amount/direction is structurally compatible even with different dates/references.
        response = self.post(body)
        self.assertEqual(response.status_code, 201)
        saved = response.json()
        self.assertEqual(saved["review"]["matched_count"], 1)
        self.assertEqual(saved["review"]["warnings"][0]["code"], "balance_dates_differ")
        self.assertEqual(saved["statement_rows"], body["statement_rows"])
        self.assertEqual(saved["book_rows"], body["book_rows"])
        self.assertEqual(saved["matches"], body["matches"])

    def test_save_list_reopen_edit_history_and_conflict(self):
        first = self.post(self.body()).json()
        self.assertEqual(self.client.get(f"/lab/reconciliations/{first['id']}/").json(), first)
        body = self.body()
        body.update(name="Edited explicit review", statement_balance="101.30", expected_revision=1)
        body["matches"] = []  # Explicit unmatch preserves both complete source lists.
        second = self.put(first["id"], body).json()
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["review"]["balance_difference"], "1.20")
        self.assertEqual(second["statement_rows"], first["statement_rows"])
        self.assertEqual(second["book_rows"], first["book_rows"])
        self.assertEqual(second["history"][0]["snapshot"]["matches"], first["matches"])
        self.assertEqual(second["history"][0]["snapshot"]["statement_balance"], "100.30")
        self.assertEqual(self.put(first["id"], body).status_code, 409)
        self.assertEqual(self.client.get(f"/lab/reconciliations/{first['id']}/").json(), second)
        newer = self.post(self.body()).json()
        self.assertEqual([row["id"] for row in self.client.get("/lab/reconciliations/").json()["proposals"]],
                         [newer["id"], first["id"]])
        self.assertEqual(self.before, self.business_counts())

    def test_access_entity_recheck_and_no_normal_routes_permissions_or_migrations(self):
        saved = self.post(self.body()).json()
        other = LegalEntity.objects.create(code="test-reconciliation-other", name="Synthetic other")
        row = ReconciliationProposal.objects.create(legal_entity=other, payload=self.body())
        self.assertEqual(self.client.get(f"/lab/reconciliations/{row.pk}/").status_code, 404)
        self.assertEqual(self.put(row.pk, {**self.body(), "expected_revision": 1}).status_code, 404)
        for flag in ["is_staff", "is_superuser", "is_active"]:
            old = getattr(self.actor, flag)
            setattr(self.actor, flag, False if flag == "is_active" else True)
            self.actor.save(update_fields=[flag])
            for response in [self.client.get("/lab/reconciliations/"),
                             self.client.get(f"/lab/reconciliations/{saved['id']}/"),
                             self.post(self.body()), self.put(saved["id"], {**self.body(), "expected_revision": 1})]:
                self.assertEqual(response.status_code, 403)
            setattr(self.actor, flag, old)
            self.actor.save(update_fields=[flag])
            self.client.force_login(self.actor)
        self.assertEqual(ReconciliationProposal.objects.get(pk=saved["id"]).revision, 1)
        with override_settings(ROOT_URLCONF="foundation.urls"):
            for path in ["/lab/reconciliations/", "/lab-reconciliation-ui/"]:
                with self.assertRaises(Resolver404):
                    resolve(path)
        with override_settings(INSTALLED_APPS=[app for app in settings.INSTALLED_APPS if app != "finance_allocation_lab"]):
            with CaptureQueriesContext(connection) as queries:
                graph = MigrationLoader(None).graph
            self.assertFalse(any(node[0] == "finance_allocation_lab" for node in graph.nodes))
            self.assertEqual(len(queries), 0)
        self.assertEqual(ReconciliationProposal._meta.default_permissions, ())
        self.assertEqual(ReconciliationRevision._meta.default_permissions, ())

    def test_atomic_revision_failure_and_database_immutable_history(self):
        with patch("finance_allocation_lab.reconciliation.record_revision", side_effect=RuntimeError("forced")):
            with self.assertRaises(RuntimeError):
                save_reconciliation(data=self.body(), entity=self.entity)
        self.assertEqual(ReconciliationProposal.objects.count(), 0)
        row = save_reconciliation(data=self.body(), entity=self.entity)
        original = snapshot(row)
        with patch("finance_allocation_lab.reconciliation.record_revision", side_effect=RuntimeError("forced")):
            with self.assertRaises(RuntimeError):
                save_reconciliation(data={**self.body(), "name": "Must roll back", "expected_revision": 1},
                                    entity=self.entity, proposal_id=row.pk)
        row.refresh_from_db()
        self.assertEqual(snapshot(row), original)
        for operation in [
            lambda: ReconciliationRevision.objects.filter(proposal=row).update(snapshot={}),
            lambda: ReconciliationRevision.objects.filter(proposal=row).delete(),
        ]:
            with self.assertRaises(DatabaseError), transaction.atomic():
                operation()
        self.assertEqual(ReconciliationRevision.objects.filter(proposal=row).count(), 1)
        self.assertEqual(self.before, self.business_counts())

    def test_reads_deterministic_and_create_no_business_records(self):
        self.assertEqual(self.client.get("/lab/reconciliations/").json(), {"proposals": []})
        saved = self.post(self.body()).json()
        with CaptureQueriesContext(connection) as queries:
            for _ in range(2):
                self.assertEqual(self.client.get(f"/lab/reconciliations/{saved['id']}/").json(), saved)
                self.assertEqual(len(self.client.get("/lab/reconciliations/").json()["proposals"]), 1)
        self.assertFalse(any(query["sql"].lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"} for query in queries))
        self.assertEqual(self.before, self.business_counts())


@override_settings(**LAB)
class ReconciliationConcurrencyTests(ReconciliationFixture, TransactionTestCase):
    def setUp(self):
        self.setup_reconciliation()

    def test_independent_editors_cannot_lose_matches_sources_or_revisions(self):
        saved = save_reconciliation(data=self.body(), entity=self.entity)
        barrier = Barrier(2)
        def worker(index):
            connections.close_all()
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                body = self.body()
                body.update(name=f"Editor {index}", statement_balance=f"{index}.30", expected_revision=1)
                body["matches"][0]["explanation"] = f"Match by editor {index}"
                barrier.wait(timeout=10)
                try:
                    save_reconciliation(data=body, entity=self.entity, proposal_id=saved.pk)
                    return pid, "saved", index
                except FinanceDraftConflict:
                    return pid, "conflict", index
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, (1, 2)))
        self.assertNotEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(row[1] for row in results), ["conflict", "saved"])
        winner = next(row[2] for row in results if row[1] == "saved")
        saved.refresh_from_db()
        self.assertEqual(saved.revision, 2)
        self.assertEqual(saved.payload["name"], f"Editor {winner}")
        self.assertEqual(saved.payload["statement_balance"], f"{winner}.30")
        self.assertEqual(saved.payload["matches"][0]["explanation"], f"Match by editor {winner}")
        self.assertEqual(saved.payload["statement_rows"], self.body()["statement_rows"])
        self.assertEqual(ReconciliationRevision.objects.filter(proposal=saved).count(), 2)
        self.assertEqual(self.before, self.business_counts())
