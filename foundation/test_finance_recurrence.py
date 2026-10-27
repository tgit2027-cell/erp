import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, Resolver404

from .finance_drafts import create_finance_draft_journal, update_finance_draft_journal, FinanceDraftConflict
from .models import FinanceDraftJournal, FinanceDraftJournalRevision, LegalEntity
from .test_finance_draft_workflow import Fixture, LAB


PROPOSAL = {"start": "2026-12-01", "end": "2028-03-01", "dates": ["2026-12-31", "2027-01-02", "2028-02-29"]}


@override_settings(**LAB)
@tag("finance_lab")
class FinanceRecurringTests(Fixture, TestCase):
    def setUp(self):
        self.setup_fixture()
        self.client.force_login(self.actor)

    def payload(self, amount="0.30", title="Synthetic recurring proposal"):
        return {**super().payload(amount, title), "kind": "recurring_template", "recurrence_proposal": copy.deepcopy(PROPOSAL)}

    def post(self, payload=None):
        return self.client.post("/lab/journals/", json.dumps(payload or self.payload()), content_type="application/json")

    def test_save_reopen_and_explicit_occurrence_preview_preserve_exact_dates_and_amounts(self):
        before = self.business_counts()
        saved = self.post()
        self.assertEqual(saved.status_code, 201, saved.content)
        row = saved.json()
        url = f"/lab/journals/{row['id']}/"
        reopened = self.client.get(url).json()
        self.assertEqual(reopened["recurrence_proposal"], PROPOSAL)
        self.assertEqual(reopened["kind"], "recurring_template")
        self.assertEqual(reopened["history"][0]["snapshot"]["recurrence_proposal"], PROPOSAL)
        self.assertEqual(self.client.get("/lab/journals/").json()["drafts"][0]["kind"], "recurring_template")
        preview = self.client.get(url + "preview/").json()
        self.assertEqual([r["date"] for r in preview["occurrences"]], PROPOSAL["dates"])
        self.assertEqual(preview["occurrence_count"], 3)
        self.assertEqual(preview["total_proposed_debits"], "0.90")
        self.assertEqual(preview["total_proposed_credits"], "0.90")
        self.assertEqual([r["debit"] for r in preview["entry_lines"]], ["0.30", "0.00"])
        self.assertFalse(preview["posted"])
        self.assertFalse(preview["scheduled"])
        self.assertEqual(before, self.business_counts())

    def test_invalid_or_ambiguous_dates_are_rejected_without_partial_writes(self):
        invalid = [
            None, {}, {**PROPOSAL, "frequency": "monthly"},
            {**PROPOSAL, "start": "2028-03-02"},
            {**PROPOSAL, "dates": []},
            {**PROPOSAL, "dates": ["2027-02-29"]},
            {**PROPOSAL, "dates": ["01/02/2027"]},
            {**PROPOSAL, "dates": ["2027-01-02T00:00:00Z"]},
            {**PROPOSAL, "dates": ["2027-1-2"]},
            {**PROPOSAL, "dates": ["2027-01-02", "2027-01-02"]},
            {**PROPOSAL, "dates": ["2027-01-03", "2027-01-02"]},
            {**PROPOSAL, "dates": ["2026-11-30"]},
            {**PROPOSAL, "dates": ["2028-03-02"]},
            {**PROPOSAL, "dates": [True]},
            {**PROPOSAL, "dates": ["2027-01-02"] * 101},
        ]
        for proposal in invalid:
            with self.subTest(proposal=proposal):
                payload = self.payload()
                payload["recurrence_proposal"] = proposal
                self.assertEqual(self.post(payload).status_code, 400)
                self.assertEqual(FinanceDraftJournal.objects.count(), 0)
                self.assertEqual(FinanceDraftJournalRevision.objects.count(), 0)

    def test_edit_revisions_and_stale_conflict_preserve_dates_and_entry_lines(self):
        row = self.post().json()
        url = f"/lab/journals/{row['id']}/"
        payload = self.payload("0.31", "Revised proposal")
        payload["recurrence_proposal"]["dates"] = ["2027-01-03"]
        payload["expected_revision"] = 1
        accepted = self.client.put(url, json.dumps(payload), content_type="application/json")
        self.assertEqual(accepted.status_code, 200, accepted.content)
        history = accepted.json()["history"]
        self.assertEqual(history[0]["snapshot"]["recurrence_proposal"], PROPOSAL)
        self.assertEqual(history[0]["snapshot"]["review"]["debit_total"], "0.30")
        self.assertEqual(history[1]["snapshot"]["recurrence_proposal"]["dates"], ["2027-01-03"])
        self.assertEqual(history[1]["snapshot"]["review"]["debit_total"], "0.31")
        self.assertEqual(self.client.put(url, json.dumps(payload), content_type="application/json").status_code, 409)
        self.assertEqual(self.client.get(url).json(), accepted.json())

    def test_foreign_choices_and_access_loss_cannot_expose_template_or_preview(self):
        payload = self.payload()
        payload["lines"][0]["account_id"] = 999999999
        self.assertEqual(self.post(payload).status_code, 400)
        row = self.post().json()
        url = f"/lab/journals/{row['id']}/"
        with self.settings(FINANCE_LAB_ENABLED=False):
            self.assertEqual(self.client.get(url + "preview/").status_code, 400)
        other = LegalEntity.objects.create(code="test-fin-rec-other", name="Synthetic other")
        foreign = FinanceDraftJournal.objects.create(legal_entity=other, title_en="Other")
        self.assertEqual(self.client.get(f"/lab/journals/{foreign.pk}/preview/").status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(url + "preview/").status_code, 403)
        staff = get_user_model().objects.create_user(username="test-rec-staff", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url + "preview/").status_code, 403)
        for path in (url, url + "preview/"):
            with self.assertRaises(Resolver404):
                resolve(path, urlconf="config.urls")

    def test_preview_is_deterministic_read_only_and_creates_no_draft_or_operational_rows(self):
        row = self.post().json()
        before = (self.business_counts(), FinanceDraftJournal.objects.count(), FinanceDraftJournalRevision.objects.count())
        url = f"/lab/journals/{row['id']}/preview/"
        with CaptureQueriesContext(connection) as queries:
            first = self.client.get(url)
            second = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.content, second.content)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")) for q in queries))
        self.assertEqual(before, (self.business_counts(), FinanceDraftJournal.objects.count(), FinanceDraftJournalRevision.objects.count()))

    def test_unbalanced_and_ordinary_journals_cannot_preview_occurrences(self):
        row = create_finance_draft_journal(legal_entity=self.entity, title_en="Unbalanced",
                                         kind="recurring_template", recurrence_proposal=PROPOSAL, lines=self.lines()[:1])
        self.assertEqual(self.client.get(f"/lab/journals/{row.pk}/preview/").status_code, 400)
        ordinary = create_finance_draft_journal(legal_entity=self.entity, title_en="Ordinary", lines=self.lines())
        self.assertEqual(self.client.get(f"/lab/journals/{ordinary.pk}/preview/").status_code, 400)
        payload = self.payload()
        payload["kind"] = "journal"
        self.assertEqual(self.post(payload).status_code, 400)

    def test_revision_failure_rolls_back_metadata_and_lines_and_kind_cannot_change(self):
        row = self.post().json()
        url = f"/lab/journals/{row['id']}/"
        payload = self.payload("0.31")
        payload["expected_revision"] = 1
        payload["recurrence_proposal"]["dates"] = ["2027-02-01"]
        with patch("foundation.finance_drafts._record_revision", side_effect=RuntimeError("synthetic forced history failure")):
            with self.assertRaises(RuntimeError):
                self.client.put(url, json.dumps(payload), content_type="application/json")
        self.assertEqual(self.client.get(url).json(), row)
        payload["kind"], payload["recurrence_proposal"] = "journal", {}
        self.assertEqual(self.client.put(url, json.dumps(payload), content_type="application/json").status_code, 400)
        self.assertEqual(self.client.get(url).json(), row)

    def test_large_exact_totals_and_invalid_amounts_never_use_float_arithmetic(self):
        row = self.post(self.payload("999999999999.99")).json()
        preview = self.client.get(f"/lab/journals/{row['id']}/preview/").json()
        self.assertEqual(preview["total_proposed_debits"], "2999999999999.97")
        for amount in ("0.001", "NaN", "-0.01", 0.1):
            self.assertEqual(self.post(self.payload(amount)).status_code, 400)
        self.assertEqual(FinanceDraftJournal.objects.count(), 1)


@override_settings(APP_ENV="demo", FINANCE_DRAFT_TEST_MODE=True)
@tag("finance_lab")
class FinanceRecurringConcurrencyTests(Fixture, TransactionTestCase):
    def test_two_independent_editors_commit_one_complete_proposal_without_mixed_dates_or_amounts(self):
        self.setup_fixture()
        row = create_finance_draft_journal(legal_entity=self.entity, title_en="Original", lines=self.lines(),
                                          kind="recurring_template", recurrence_proposal=PROPOSAL)
        before = self.business_counts()
        barrier = Barrier(2)

        def edit(amount, when):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                barrier.wait(timeout=10)
                try:
                    update_finance_draft_journal(
                        journal_id=row.pk, legal_entity=self.entity, expected_revision=1,
                        title_en=amount, lines=self.lines(amount), kind="recurring_template",
                        recurrence_proposal={**PROPOSAL, "dates": [when]},
                    )
                    return "saved", pid, amount, when
                except FinanceDraftConflict:
                    return "conflict", pid, amount, when
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(edit, "0.31", "2027-01-03")
            b = pool.submit(edit, "0.32", "2027-02-01")
            results = [a.result(timeout=20), b.result(timeout=20)]
        self.assertEqual(sorted(r[0] for r in results), ["conflict", "saved"])
        self.assertEqual(len({r[1] for r in results}), 2)
        winner = next(r for r in results if r[0] == "saved")
        row.refresh_from_db()
        self.assertEqual(row.recurrence_proposal["dates"], [winner[3]])
        self.assertEqual(str(row.lines.first().debit), winner[2])
        self.assertEqual(row.revision, 2)
        self.assertEqual(row.revisions.count(), 2)
        self.assertEqual(before, self.business_counts())
        print(f"Independent recurring-template editor PostgreSQL PIDs: {[r[1] for r in results]}; one saved / one conflict.")
