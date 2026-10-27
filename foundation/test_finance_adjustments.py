"""Explicit adjustment proposals, only in disposable synthetic Finance persistence."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections, DatabaseError, transaction
from django.test import TestCase, TransactionTestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, Resolver404

from .finance_drafts import (
    ADJUSTMENT_KINDS, FinanceDraftConflict, create_finance_draft_account,
    create_finance_draft_journal, update_finance_draft_journal,
)
from .models import (
    FinanceDraftJournal, FinanceDraftJournalRevision, FinanceDraftDimension,
    FinanceDraftDimensionValue, LegalEntity, BusinessUnit,
)
from .test_finance_draft_workflow import Fixture, LAB


@override_settings(**LAB)
@tag("finance_lab")
class FinanceAdjustmentTests(Fixture, TestCase):
    def setUp(self):
        self.setup_fixture()
        self.client.force_login(self.actor)
        self.before = self.business_counts()

    def payload(self, kind="accrual_adjustment", amount="0.30"):
        return {
            **super().payload(amount, "Synthetic explicit adjustment"),
            "kind": kind,
            "adjustment_proposal": {"date": "2028-02-29", "explanation": "Reviewer-entered synthetic adjustment; no recognition convention."},
        }

    def post(self, payload=None):
        return self.client.post("/lab/journals/", json.dumps(payload or self.payload()), content_type="application/json")

    def test_both_kinds_persist_reopen_edit_exact_inputs_dimensions_and_history(self):
        dimension = FinanceDraftDimension.objects.create(
            legal_entity=self.entity, code="TEST-DRAFT-DIM-ANALYSIS", label_en="Proposed analysis")
        value = FinanceDraftDimensionValue.objects.create(
            dimension=dimension, code="TEST-DRAFT-VAL-A", label_en="Synthetic A")
        for kind in ADJUSTMENT_KINDS:
            with self.subTest(kind=kind):
                payload = self.payload(kind, "999999999999.99")
                payload["lines"][0]["dimension_value_ids"] = [value.pk]
                response = self.post(payload)
                self.assertEqual(response.status_code, 201, response.content)
                row = response.json()
                url = f"/lab/journals/{row['id']}/"
                self.assertEqual(self.client.get(url).json(), row)
                self.assertEqual(row["adjustment_proposal"], payload["adjustment_proposal"])
                self.assertEqual(row["review"]["debit_total"], "999999999999.99")
                self.assertEqual(row["review"]["difference"], "0.00")
                self.assertEqual(row["lines"][0]["dimensions"][0]["id"], value.pk)
                self.assertEqual(row["lines"][0]["business_unit_id"], self.unit.pk)
                self.assertFalse(row["review"]["posted"])
                self.assertIsNone(row["as_of_date"])
                self.assertEqual(row["recurrence_proposal"], {})
                payload.update(expected_revision=1)
                payload["adjustment_proposal"] = {"date": "2028-03-01", "explanation": "Explicit revised explanation — توضيح مقدم"}
                edited = self.client.put(url, json.dumps(payload), content_type="application/json")
                self.assertEqual(edited.status_code, 200, edited.content)
                saved = edited.json()
                self.assertEqual(saved["revision"], 2)
                self.assertEqual(saved["history"][0]["snapshot"]["adjustment_proposal"], row["adjustment_proposal"])
                self.assertEqual(saved["history"][1]["snapshot"]["adjustment_proposal"], payload["adjustment_proposal"])
                self.assertEqual(self.client.get(url).json(), saved)
                self.assertEqual(self.client.put(url, json.dumps(payload), content_type="application/json").status_code, 409)
                self.assertEqual(self.client.get(url).json(), saved)
        self.assertEqual(self.before, self.business_counts())

    def test_missing_ambiguous_or_inferred_metadata_is_rejected_without_partial_writes(self):
        invalid = [
            None, {}, {"date": "2028-02-29"}, {"explanation": "Only explanation"},
            {"date": "2028-02-29", "explanation": ""},
            {"date": "2028-02-29", "explanation": " "},
            {"date": "2028-02-29", "explanation": "x" * 2001},
            {"date": "2028-02-29", "explanation": ["not text"]},
            {"date": "2028-02-29", "explanation": "test", "allocation": "monthly"},
        ]
        for value in (True, "", "2027-02-29", "2028-2-29", "29/02/2028", "2028-02-29T00:00:00Z"):
            invalid.append({"date": value, "explanation": "Explicit date required"})
        for kind in ADJUSTMENT_KINDS:
            for metadata in invalid:
                with self.subTest(kind=kind, metadata=metadata):
                    response = self.post({**self.payload(kind), "adjustment_proposal": metadata})
                    self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(FinanceDraftJournal.objects.count(), 0)
        self.assertEqual(FinanceDraftJournalRevision.objects.count(), 0)
        self.assertEqual(self.before, self.business_counts())

    def test_exact_line_validation_and_unbalanced_draft_review_never_invent_balancing_lines(self):
        for kind in ADJUSTMENT_KINDS:
            for amount in ("0.001", "-0.01", "NaN", "Infinity", "1000000000000.00", 0.1, True):
                self.assertEqual(self.post(self.payload(kind, amount)).status_code, 400)
            payload = self.payload(kind)
            payload["lines"][1]["credit"] = "0.29"
            row = self.post(payload).json()
            self.assertEqual(row["review"]["difference"], "0.01")
            self.assertFalse(row["review"]["balanced"])
            self.assertFalse(row["review"]["posted"])
            self.assertEqual(len(row["lines"]), 2)
            self.assertEqual(row["lines"][1]["credit"], "0.29")
        self.assertEqual(self.before, self.business_counts())

    def test_foreign_accounts_dimensions_and_business_units_are_not_accepted(self):
        other = LegalEntity.objects.create(code="test-adjustment-other", name="Synthetic other")
        account = create_finance_draft_account(
            legal_entity=other, code="TEST-DRAFT-ACCT-OTHER", name_en="Other proposal")
        unit = BusinessUnit.objects.create(legal_entity=other, code="TEST-OTHER", name="Synthetic")
        dimension = FinanceDraftDimension.objects.create(
            legal_entity=other, code="TEST-DRAFT-DIM-OTHER", label_en="Other")
        value = FinanceDraftDimensionValue.objects.create(
            dimension=dimension, code="TEST-DRAFT-VAL-OTHER", label_en="Other")
        for kind in ADJUSTMENT_KINDS:
            for fields in ({"account_id": account.pk}, {"business_unit_id": unit.pk}, {"dimension_value_ids": [value.pk]}):
                payload = self.payload(kind)
                payload["lines"][0].update(fields)
                self.assertEqual(self.post(payload).status_code, 400)
        self.assertEqual(FinanceDraftJournal.objects.count(), 0)
        self.assertEqual(self.before, self.business_counts())

    def test_lab_only_access_is_rechecked_and_no_shared_route_is_exposed(self):
        row = self.post().json()
        url = f"/lab/journals/{row['id']}/"
        payload = {**self.payload(), "expected_revision": 1}
        self.actor.is_active = False
        self.actor.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.put(url, json.dumps(payload), content_type="application/json").status_code, 403)
        for staff in (False, True):
            actor = get_user_model().objects.create_user(username=f"test-adjust-other-{staff}", password=None, is_staff=staff)
            self.client.force_login(actor)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.post().status_code, 403)
        with self.assertRaises(Resolver404):
            resolve(url, urlconf="config.urls")
        self.assertEqual(FinanceDraftJournal.objects.get(pk=row["id"]).revision, 1)
        self.assertEqual(self.before, self.business_counts())

    def test_failed_revision_or_validation_rolls_back_date_explanation_and_lines(self):
        row = self.post().json()
        url = f"/lab/journals/{row['id']}/"
        payload = {**self.payload(amount="0.31"), "expected_revision": 1}
        payload["adjustment_proposal"] = {"date": "2028-03-01", "explanation": "Changed proposal"}
        with patch("foundation.finance_drafts._record_revision", side_effect=RuntimeError("Synthetic history failure")):
            with self.assertRaises(RuntimeError):
                self.client.put(url, json.dumps(payload), content_type="application/json")
        self.assertEqual(self.client.get(url).json(), row)
        payload["adjustment_proposal"]["date"] = "2027-02-29"
        self.assertEqual(self.client.put(url, json.dumps(payload), content_type="application/json").status_code, 400)
        self.assertEqual(self.client.get(url).json(), row)
        with self.assertRaises(DatabaseError), transaction.atomic():
            FinanceDraftJournalRevision.objects.filter(journal_id=row["id"]).update(snapshot={})
        self.assertEqual(self.before, self.business_counts())

    def test_read_review_is_deterministic_and_performs_no_writes(self):
        row = self.post().json()
        url = f"/lab/journals/{row['id']}/"
        with CaptureQueriesContext(connection) as queries:
            first = self.client.get(url)
            second = self.client.get(url)
            self.client.get("/lab/journals/")
        self.assertEqual(first.json(), second.json())
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")) for q in queries))
        self.assertEqual(FinanceDraftJournalRevision.objects.count(), 1)
        self.assertEqual(self.before, self.business_counts())

    def test_proposal_kinds_and_metadata_cannot_be_silently_repurposed(self):
        row = self.post().json()
        url = f"/lab/journals/{row['id']}/"
        for changed in (
            {"kind": "prepayment_adjustment"},
            {"recurrence_proposal": {"start": "2028-02-29", "end": "2028-02-29", "dates": ["2028-02-29"]}},
            {"as_of_date": "2028-02-29"},
        ):
            payload = {**self.payload(), "expected_revision": 1, **changed}
            self.assertEqual(self.client.put(url, json.dumps(payload), content_type="application/json").status_code, 400)
            self.assertEqual(self.client.get(url).json(), row)
        self.assertEqual(self.post({**self.payload(), "kind": "journal"}).status_code, 400)
        with self.assertRaises(DatabaseError), transaction.atomic():
            FinanceDraftJournal.objects.filter(pk=row["id"]).update(adjustment_proposal={})
        self.assertEqual(self.before, self.business_counts())


@override_settings(**LAB)
@tag("finance_lab")
class FinanceAdjustmentConcurrencyTests(Fixture, TransactionTestCase):
    def setUp(self):
        self.setup_fixture()

    def test_two_independent_editors_preserve_one_revision_and_reject_stale_metadata(self):
        row = create_finance_draft_journal(
            legal_entity=self.entity, title_en="Synthetic adjustment", lines=self.lines(),
            kind="prepayment_adjustment",
            adjustment_proposal={"date": "2028-02-29", "explanation": "Explicit initial proposal"})
        before = self.business_counts()
        barrier = Barrier(2)

        def edit(day):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                entity = LegalEntity.objects.get(pk=self.entity.pk)
                barrier.wait(timeout=15)
                try:
                    update_finance_draft_journal(
                        journal_id=row.pk, legal_entity=entity, expected_revision=1,
                        title_en="Synthetic adjustment", lines=self.lines(), kind="prepayment_adjustment",
                        adjustment_proposal={"date": f"2028-03-{day:02d}", "explanation": f"Editor {day}"})
                    return pid, "saved", day
                except FinanceDraftConflict:
                    return pid, "conflict", day
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, [1, 2]))
        self.assertNotEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(r[1] for r in results), ["conflict", "saved"])
        winner = next(r[2] for r in results if r[1] == "saved")
        row.refresh_from_db()
        self.assertEqual(row.revision, 2)
        self.assertEqual(row.adjustment_proposal, {"date": f"2028-03-{winner:02d}", "explanation": f"Editor {winner}"})
        self.assertEqual(row.revisions.count(), 2)
        self.assertEqual(before, self.business_counts())
