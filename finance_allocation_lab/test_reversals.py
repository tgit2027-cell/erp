import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.db import connections, connection, transaction, DatabaseError
from django.db.models.deletion import ProtectedError
from django.test import TransactionTestCase, Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, Resolver404
from foundation.test_finance_draft_workflow import LAB
from foundation.models import FinanceDraftJournal, FinanceDraftJournalRevision, LegalEntity, BusinessUnit
from foundation.finance_drafts import create_finance_draft_account
from .test_budgets import BudgetFixture
from .models import ReversalProposal, ReversalRevision, DraftCreateReceipt
from .reversals import save_reversal


class ReversalFixture(BudgetFixture):
    def setup_reversal(self):
        self.setup_budget()
        payload = self.payload()
        payload["lines"][0]["dimension_value_ids"] = [self.value.pk]
        response = self.client.post("/lab/journals/", json.dumps(payload), content_type="application/json")
        assert response.status_code == 201, response.content
        self.source = response.json()

    def body(self):
        return {
            "source_journal_id": self.source["id"], "source_revision": 1,
            "proposed_date": "2028-02-29", "explanation": "Explicit synthetic reversal review",
            "lines": [
                {"account_id": self.account.pk, "business_unit_id": self.unit.pk,
                 "dimension_value_ids": [self.value.pk], "debit": "0.00", "credit": "0.30"},
                {"account_id": self.account.pk, "business_unit_id": None,
                 "dimension_value_ids": [], "debit": "0.30", "credit": "0.00"},
            ],
        }

    def post(self, data=None, key=None):
        headers = {"HTTP_X_FINANCE_REQUEST_ID": str(key)} if key else {}
        return self.client.post("/lab/reversals/", json.dumps(self.body() if data is None else data),
                                content_type="application/json", **headers)

    def put(self, pk, data):
        return self.client.put(f"/lab/reversals/{pk}/", json.dumps(data), content_type="application/json")

    def source_state(self):
        return (list(FinanceDraftJournal.objects.values()),
                list(FinanceDraftJournalRevision.objects.order_by("pk").values()))


@override_settings(**LAB)
class ReversalTests(ReversalFixture, TransactionTestCase):
    def setUp(self):
        self.setup_reversal()

    def test_specific_historical_revision_remains_readonly_after_source_edit(self):
        original = self.source
        edited = self.client.put(f"/lab/journals/{original['id']}/", json.dumps({
            **self.payload("0.40"), "expected_revision": 1}), content_type="application/json")
        self.assertEqual(edited.status_code, 200, edited.content)
        before = self.source_state()
        result = self.post()
        self.assertEqual(result.status_code, 201, result.content)
        row = result.json()
        self.assertEqual(row["source"], {key: value for key, value in original.items() if key != "history"})
        self.assertEqual(row["source"]["revision"], 1)
        self.assertTrue(row["review"]["offsets_source"])
        self.assertFalse(row["review"]["posted"])
        self.assertFalse(row["review"]["approved"])
        self.assertEqual(self.client.get(f"/lab/reversal-sources/{original['id']}/1/").json()["source"], row["source"])
        self.assertEqual(self.client.get(f"/lab/reversal-sources/{original['id']}/2/").json()["source"]["review"]["debit_total"], "0.40")
        self.assertEqual(self.source_state(), before)
        self.assertEqual(self.business_counts(), self.before)

    def test_exact_split_lines_and_bucket_comparison_no_silent_correction(self):
        body = self.body()
        body["lines"][0]["credit"] = "0.10"
        body["lines"].append({**body["lines"][0], "credit": "0.20"})
        result = self.post(body)
        self.assertEqual(result.status_code, 201, result.content)
        review = result.json()["review"]
        self.assertEqual((review["debit_total"], review["credit_total"], review["difference"]), ("0.30", "0.30", "0.00"))
        self.assertTrue(review["balanced"])
        self.assertTrue(review["offsets_source"])
        self.assertEqual(review["bucket_differences"], [])
        body["lines"][0]["dimension_value_ids"] = []
        result = self.post(body).json()
        self.assertTrue(result["review"]["balanced"])
        self.assertFalse(result["review"]["offsets_source"])
        self.assertEqual([d["combined_net"] for d in result["review"]["bucket_differences"]], ["-0.10", "0.10"])
        self.assertEqual(result["lines"][0]["dimension_value_ids"], [])
        body["lines"][1]["debit"] = "0.29"
        result = self.post(body).json()
        self.assertFalse(result["review"]["balanced"])
        self.assertFalse(result["review"]["offsets_source"])
        self.assertEqual(result["review"]["difference"], "-0.01")
        self.assertEqual(result["lines"][1]["debit"], "0.29")
        body["lines"][1]["credit"] = "0.01"
        self.assertTrue(self.post(body).json()["review"]["issues"])

    def test_invalid_sources_exact_amounts_dates_and_cross_entity_inputs_no_partial_writes(self):
        foreign = LegalEntity.objects.create(code="test-foreign-reversal", name="Synthetic other")
        foreign_account = create_finance_draft_account(
            legal_entity=foreign, code="TEST-DRAFT-ACCT-FOREIGN", name_en="Other draft")
        foreign_unit = BusinessUnit.objects.create(legal_entity=foreign, code="TEST-OTHER", name="Other")
        cases = []
        for key, values in {
            "source_journal_id": [None, True, "1", 999999],
            "source_revision": [0, True, "1", 999999],
            "proposed_date": ["2027-02-29", "2028-2-1", "", None],
            "explanation": ["", "x" * 201],
        }.items():
            for value in values:
                cases.append({**self.body(), key: value})
        for amount in ("NaN", "Infinity", "-0.01", "0.001", "", 0.1, True):
            data = self.body(); data["lines"][0]["credit"] = amount; cases.append(data)
        for field, value in (("account_id", foreign_account.pk), ("account_id", True),
                             ("business_unit_id", foreign_unit.pk),
                             ("dimension_value_ids", [999999]),
                             ("dimension_value_ids", [self.value.pk, self.value.pk])):
            data = self.body(); data["lines"][0][field] = value; cases.append(data)
        data = self.body(); del data["lines"][0]["debit"]; cases.append(data)
        cases.extend([{**self.body(), "posted": True}, {**self.body(), "currency": "USD"}, {**self.body(), "lines": []}])
        source_before = self.source_state()
        for data in cases:
            with self.subTest(data=data):
                response = self.post(data, key=uuid4())
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(ReversalProposal.objects.count(), 0)
                self.assertEqual(ReversalRevision.objects.count(), 0)
                self.assertEqual(DraftCreateReceipt.objects.count(), 0)
        self.assertEqual(self.source_state(), source_before)
        self.assertEqual(self.business_counts(), self.before)

    def test_revisions_conflicting_edit_relink_and_protected_source_history(self):
        first = self.post().json()
        before = copy.deepcopy(first["history"])
        saved = self.put(first["id"], {**self.body(), "explanation": "Explicit second review", "expected_revision": 1})
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(saved.json()["revision"], 2)
        conflict = self.put(first["id"], {**self.body(), "explanation": "Obsolete input", "expected_revision": 1})
        self.assertEqual(conflict.status_code, 409)
        current = self.client.get(f"/lab/reversals/{first['id']}/").json()
        self.assertEqual(current["history"][:1], before)
        self.assertEqual(current["explanation"], "Explicit second review")
        edit = self.client.put(f"/lab/journals/{self.source['id']}/", json.dumps({
            **self.payload(), "expected_revision": 1}), content_type="application/json")
        self.assertEqual(edit.status_code, 200)
        result = self.put(first["id"], {**self.body(), "source_revision": 2, "expected_revision": 2})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual([r["snapshot"]["source_revision"] for r in result.json()["history"]], [1, 1, 2])
        with self.assertRaises(ProtectedError):
            FinanceDraftJournalRevision.objects.get(journal_id=self.source["id"], revision=1).delete()
        self.assertEqual(self.business_counts(), self.before)

    def test_scope_denial_normal_routes_and_metadata_only_recovery(self):
        key = uuid4()
        row = self.post(key=key).json()
        status = self.client.get(f"/lab/requests/{key}/").json()
        self.assertEqual(status["resource"], "reversals")
        self.assertEqual(status["draft_id"], row["id"])
        self.assertNotIn("source", status)
        self.assertNotIn("lines", status)
        self.assertEqual(self.client.get("/lab/reversals/").json()["reversals"][0]["id"], row["id"])
        for path in ("/lab-reversals-ui/", "/lab/reversals/", f"/lab/reversals/{row['id']}/",
                     f"/lab/reversal-sources/{self.source['id']}/1/"):
            with self.assertRaises(Resolver404):
                resolve(path, urlconf="config.urls")
            self.assertEqual(Client().get(path).status_code, 200 if path == "/lab-reversals-ui/" else 403)
        self.actor.is_staff = True; self.actor.save(update_fields=["is_staff"])
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.client.get(f"/lab/reversals/{row['id']}/").status_code, 403)
        self.actor.is_staff = False; self.actor.save(update_fields=["is_staff"])
        foreign = LegalEntity.objects.create(code="test-foreign-view", name="Synthetic foreign")
        with patch("foundation.finance_lab_views._target", return_value=foreign):
            self.assertEqual(self.client.get(f"/lab/reversals/{row['id']}/").status_code, 404)
            self.assertEqual(self.client.get(f"/lab/reversal-sources/{self.source['id']}/1/").status_code, 404)
            self.assertEqual(self.client.get("/lab/reversals/").json(), {"reversals": []})

    def test_reads_do_not_write_and_review_contains_no_combined_amounts(self):
        row = self.post().json()
        source_before = self.source_state()
        with CaptureQueriesContext(connection) as queries:
            for path in ("/lab/reversals/", f"/lab/reversals/{row['id']}/",
                         f"/lab/reversal-sources/{self.source['id']}/1/", "/lab/review/"):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        groups = self.client.get("/lab/review/").json()["groups"]
        group = next(group for group in groups if group["resource"] == "reversals")
        self.assertEqual(group["items"][0]["id"], row["id"])
        self.assertNotIn("review", group["items"][0])
        self.assertEqual(self.source_state(), source_before)
        self.assertEqual(self.business_counts(), self.before)

    def test_failed_history_or_receipt_insert_rolls_back_proposal_without_source_changes(self):
        before = self.source_state()
        with patch.object(ReversalRevision.objects, "create", side_effect=DatabaseError("Synthetic insert failure")):
            with self.assertRaises(DatabaseError):
                save_reversal(data=self.body(), entity=self.entity)
        with patch.object(DraftCreateReceipt, "save", side_effect=DatabaseError("Synthetic receipt failure")):
            with self.assertRaises(DatabaseError):
                self.post(key=uuid4())
        self.assertEqual(ReversalProposal.objects.count(), 0)
        self.assertEqual(ReversalRevision.objects.count(), 0)
        self.assertEqual(DraftCreateReceipt.objects.count(), 0)
        self.assertEqual(self.source_state(), before)

    def test_missing_or_inconsistent_revision_never_falls_back_to_current(self):
        record = FinanceDraftJournalRevision.objects.get(journal_id=self.source["id"], revision=1)
        with self.assertRaises(DatabaseError), transaction.atomic():
            FinanceDraftJournalRevision.objects.filter(pk=record.pk).update(
                snapshot={**record.snapshot, "legal_entity_id": 999999})
        self.assertEqual(self.client.get(f"/lab/reversal-sources/{self.source['id']}/99999/").status_code, 404)
        self.assertEqual(self.post({**self.body(), "source_revision": 99999}).status_code, 400)
        self.assertEqual(ReversalProposal.objects.count(), 0)

    def test_reversal_revision_update_and_delete_rejected_by_database(self):
        row = self.post().json()
        revision = ReversalRevision.objects.get(draft_id=row["id"])
        for action in (
            lambda: ReversalRevision.objects.filter(pk=revision.pk).update(snapshot={}),
            lambda: ReversalRevision.objects.filter(pk=revision.pk).delete(),
        ):
            with self.assertRaises(DatabaseError), transaction.atomic():
                action()
        self.assertEqual(ReversalRevision.objects.get(pk=revision.pk).snapshot, revision.snapshot)


@override_settings(**LAB)
class ReversalTransactionTests(ReversalFixture, TransactionTestCase):
    def setUp(self):
        self.setup_reversal()

    def test_committed_create_lost_ack_replay_and_current_revision_are_distinct(self):
        key = uuid4(); committed = []
        original_create = ReversalRevision.objects.create
        def observe(*args, **kwargs):
            result = original_create(*args, **kwargs)
            transaction.on_commit(lambda: committed.append(result.pk))
            return result
        with patch.object(ReversalRevision.objects, "create", side_effect=observe):
            acknowledgement = self.post(key=key)
        self.assertEqual(acknowledgement.status_code, 201)
        self.assertEqual(len(committed), 1)  # Committed before caller discards delivery.
        row = acknowledgement.json()
        self.assertEqual(self.put(row["id"], {**self.body(), "explanation": "Current edit", "expected_revision": 1}).status_code, 200)
        replay = self.post(key=key)
        self.assertEqual(replay.json(), row)
        self.assertEqual(replay["X-Finance-Replayed"], "true")
        self.assertEqual(self.client.get(f"/lab/requests/{key}/").json()["original_revision"], 1)
        self.assertEqual(self.client.get(f"/lab/reversals/{row['id']}/").json()["revision"], 2)
        self.assertEqual(self.post({**self.body(), "explanation": "Conflicting reuse"}, key=key).status_code, 409)
        self.assertEqual((ReversalProposal.objects.count(), ReversalRevision.objects.count(), DraftCreateReceipt.objects.count()), (1, 2, 1))
        self.assertEqual(self.business_counts(), self.before)

    def concurrent(self, *, different=False, edit=False):
        key = uuid4(); barrier = Barrier(2)
        row = self.post().json() if edit else None
        def worker(index):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()"); pid = cursor.fetchone()[0]
                client = Client(); client.force_login(self.actor)
                body = self.body()
                if different or edit:
                    body["explanation"] = f"Explicit competing input {index}"
                if edit:
                    body["expected_revision"] = 1
                barrier.wait(timeout=15)
                if edit:
                    response = client.put(f"/lab/reversals/{row['id']}/", json.dumps(body), content_type="application/json")
                else:
                    response = client.post("/lab/reversals/", json.dumps(body), content_type="application/json",
                                           HTTP_X_FINANCE_REQUEST_ID=str(key))
                return pid, response.status_code, response.json()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, (0, 1)))
        self.assertEqual(len({value[0] for value in results}), 2)
        print("Independent reversal PostgreSQL PIDs:", sorted(value[0] for value in results))
        self.assertEqual(ReversalProposal.objects.count(), 1)
        self.assertEqual(ReversalRevision.objects.count(), 2 if edit else 1)
        self.assertEqual(self.business_counts(), self.before)
        return results

    def test_independent_concurrent_identical_create_one_receipt_and_revision(self):
        results = self.concurrent()
        self.assertEqual([value[1] for value in results], [201, 201])
        self.assertEqual(results[0][2], results[1][2])
        self.assertEqual(DraftCreateReceipt.objects.count(), 1)

    def test_independent_concurrent_conflicting_identity_rejects_without_overwrite(self):
        self.assertEqual(sorted(value[1] for value in self.concurrent(different=True)), [201, 409])
        self.assertEqual(DraftCreateReceipt.objects.count(), 1)

    def test_independent_concurrent_revision_edits_no_lost_updates(self):
        self.assertEqual(sorted(value[1] for value in self.concurrent(edit=True)), [200, 409])
