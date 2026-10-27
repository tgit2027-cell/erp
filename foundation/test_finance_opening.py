"""Disposable-only opening worksheet proposals; never operational balances."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import connection, connections, DatabaseError, transaction
from django.test import TestCase, TransactionTestCase, override_settings, Client, tag
from django.urls import resolve, Resolver404
from django.test.utils import CaptureQueriesContext

from .finance_drafts import update_finance_draft_journal, FinanceDraftConflict
from .models import (
    FinanceDraftJournal, FinanceDraftJournalRevision, LegalEntity, BusinessUnit,
    FinanceDraftDimension, FinanceDraftDimensionValue,
)
from .test_finance_draft_workflow import Fixture, LAB


@override_settings(**LAB)
@tag("finance_lab")
class FinanceOpeningTests(Fixture, TestCase):
    def setUp(self):
        self.setup_fixture()
        self.client.force_login(self.actor)
        self.before = self.business_counts()

    def payload(self, amount="0.30", title="Synthetic opening worksheet"):
        return {**super().payload(amount, title), "kind": "opening_balance",
                "as_of_date": "2028-02-29"}

    def post(self, data=None):
        return self.client.post("/lab/journals/", json.dumps(data or self.payload()), content_type="application/json")

    def test_persist_reopen_edit_exact_date_dimensions_and_immutable_history(self):
        dimension = FinanceDraftDimension.objects.create(
            legal_entity=self.entity, code="TEST-DRAFT-DIM-PROJECT", label_en="Proposed project")
        value = FinanceDraftDimensionValue.objects.create(
            dimension=dimension, code="TEST-DRAFT-VAL-A", label_en="Synthetic A")
        data = self.payload("999999999999.99")
        data["lines"][0]["dimension_value_ids"] = [value.pk]
        response = self.post(data)
        self.assertEqual(response.status_code, 201, response.content)
        row = response.json()
        self.assertEqual(row["as_of_date"], "2028-02-29")
        self.assertEqual(row["review"]["debit_total"], "999999999999.99")
        self.assertEqual(row["review"]["difference"], "0.00")
        self.assertTrue(row["review"]["balanced"])
        self.assertFalse(row["review"]["posted"])
        self.assertEqual(row["lines"][0]["dimensions"][0]["id"], value.pk)
        url = f"/lab/journals/{row['id']}/"
        self.assertEqual(self.client.get(url).json(), row)
        listed = self.client.get("/lab/journals/").json()["drafts"][0]
        self.assertEqual((listed["kind"], listed["as_of_date"]), ("opening_balance", "2028-02-29"))
        changed = self.payload("0.31")
        changed.update(as_of_date="2028-03-01", expected_revision=1)
        saved = self.client.put(url, json.dumps(changed), content_type="application/json")
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(saved.json()["revision"], 2)
        history = saved.json()["history"]
        self.assertEqual([h["snapshot"]["as_of_date"] for h in history], ["2028-02-29", "2028-03-01"])
        conflict = self.client.put(url, json.dumps(changed), content_type="application/json")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["current_revision"], 2)
        self.assertEqual(self.client.get(url).json(), saved.json())
        with self.assertRaises(DatabaseError), transaction.atomic():
            # Existing immutable history trigger raises a PostgreSQL exception.
            FinanceDraftJournalRevision.objects.filter(journal_id=row["id"]).update(is_synthetic=False)
        self.assertEqual(self.before, self.business_counts())

    def test_explicit_valid_date_required_and_invalid_requests_make_no_drafts(self):
        for invalid in (None, "", "2027-02-29", "2028-2-29", "29/02/2028", "2028-02-29T00:00:00Z", True):
            with self.subTest(date=invalid):
                self.assertEqual(self.post({**self.payload(), "as_of_date": invalid}).status_code, 400)
        for amount in ("0.001", "-1.00", "NaN", "Infinity", "1000000000000.00", 0.1):
            self.assertEqual(self.post(self.payload(amount)).status_code, 400)
        self.assertEqual(self.post({**self.payload(), "recurrence_proposal": {"dates": ["2028-02-29"]}}).status_code, 400)
        self.assertEqual(FinanceDraftJournal.objects.count(), 0)
        self.assertEqual(FinanceDraftJournalRevision.objects.count(), 0)
        self.assertEqual(self.before, self.business_counts())

    def test_unbalanced_proposal_is_saved_for_review_without_automatic_balancing(self):
        data = self.payload()
        data["lines"][1]["credit"] = "0.10"
        row = self.post(data).json()
        self.assertEqual(row["review"]["difference"], "0.20")
        self.assertFalse(row["review"]["balanced"])
        self.assertFalse(row["review"]["posted"])
        self.assertEqual(len(row["lines"]), 2)
        self.assertEqual(self.before, self.business_counts())

    def test_cross_entity_account_bu_dimension_and_lab_access_are_rejected(self):
        other = LegalEntity.objects.create(code="test-opening-other", name="Synthetic other")
        unit = BusinessUnit.objects.create(legal_entity=other, code="TEST-OTHER", name="Synthetic")
        dimension = FinanceDraftDimension.objects.create(
            legal_entity=other, code="TEST-DRAFT-DIM-OTHER", label_en="Other")
        value = FinanceDraftDimensionValue.objects.create(
            dimension=dimension, code="TEST-DRAFT-VAL-OTHER", label_en="Other")
        for fields in ({"account_id": 999999}, {"business_unit_id": unit.pk}, {"dimension_value_ids": [value.pk]}):
            data = self.payload()
            data["lines"][0].update(fields)
            self.assertEqual(self.post(data).status_code, 400)
        row = self.post().json()
        url = f"/lab/journals/{row['id']}/"
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.post().status_code, 403)
        with self.assertRaises(Resolver404):
            resolve(url, urlconf="config.urls")
        self.assertEqual(self.before, self.business_counts())

    def test_reads_have_no_writes_and_invalid_edit_preserves_saved_proposal(self):
        row = self.post().json()
        url = f"/lab/journals/{row['id']}/"
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(url).json(), row)
            self.client.get("/lab/journals/")
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")) for q in queries))
        bad = {**self.payload(), "as_of_date": "", "expected_revision": 1}
        self.assertEqual(self.client.put(url, json.dumps(bad), content_type="application/json").status_code, 400)
        self.assertEqual(self.client.get(url).json(), row)
        self.assertEqual(self.before, self.business_counts())


@override_settings(**LAB)
@tag("finance_lab")
class FinanceOpeningConcurrencyTests(Fixture, TransactionTestCase):
    def setUp(self):
        self.setup_fixture()

    def test_independent_connection_edits_have_one_winner_without_lost_update(self):
        client = Client()
        client.force_login(self.actor)
        payload = {**self.payload(), "kind": "opening_balance", "as_of_date": "2028-02-29"}
        row = client.post("/lab/journals/", json.dumps(payload), content_type="application/json").json()
        before = self.business_counts()
        barrier = Barrier(2)
        def edit(day):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                entity = LegalEntity.objects.get(pk=self.entity.pk)
                lines = self.lines()
                barrier.wait(timeout=15)
                try:
                    saved = update_finance_draft_journal(
                        journal_id=row["id"], legal_entity=entity, expected_revision=1,
                        title_en="Concurrent opening", lines=lines, kind="opening_balance",
                        as_of_date=f"2028-03-{day:02d}")
                    return pid, saved.revision, day
                except FinanceDraftConflict as error:
                    return pid, "conflict", error.current_revision
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(edit, [1, 2]))
        self.assertNotEqual(outcomes[0][0], outcomes[1][0])
        self.assertEqual(sum(o[1] == 2 for o in outcomes), 1)
        self.assertEqual(sum(o[1] == "conflict" for o in outcomes), 1)
        saved = FinanceDraftJournal.objects.get(pk=row["id"])
        self.assertEqual(saved.as_of_date.day, next(o[2] for o in outcomes if o[1] == 2))
        self.assertEqual(saved.revisions.count(), 2)
        self.assertEqual(before, self.business_counts())
