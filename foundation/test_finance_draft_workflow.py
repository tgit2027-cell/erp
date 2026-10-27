import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections, DatabaseError, IntegrityError, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, Resolver404

from .finance_drafts import (
    FinanceDraftConflict, create_finance_draft_account, create_finance_draft_journal,
    update_finance_draft_journal, finance_draft_snapshot,
)
from .models import (
    LegalEntity, BusinessUnit, FinanceDraftJournal, FinanceDraftJournalRevision,
    FinanceDraftDimension, FinanceDraftDimensionValue,
    JournalEntry, JournalLine, StockBalance, StockMovement, QuotePayment, QuoteReservation,
    UserActionGrant, BusinessUnitRoleAssignment,
)

LAB = dict(
    APP_ENV="demo", FINANCE_DRAFT_TEST_MODE=True, FINANCE_LAB_ENABLED=True,
    FINANCE_LAB_ACTOR="synthetic_finance_lab", FINANCE_LAB_ENTITY_CODE="test-finance-lab",
    FINANCE_LAB_DATA_DIRECTORY=os.environ.get("EXPECTED_DISPOSABLE_DATA", ""),
    ROOT_URLCONF="foundation.finance_lab_urls",
)


class Fixture:
    def setup_fixture(self):
        self.entity = LegalEntity.objects.create(code="test-finance-lab", name="TEST ONLY Finance")
        self.unit = BusinessUnit.objects.create(legal_entity=self.entity, code="TEST-FIN-A", name="Synthetic A")
        self.actor = get_user_model().objects.create_user(username="synthetic_finance_lab", password=None)
        self.account = create_finance_draft_account(legal_entity=self.entity, code="TEST-DRAFT-ACCT-A", name_en="Test proposal")

    def lines(self, amount="0.30"):
        return [
            {"account": self.account, "business_unit": self.unit, "debit": amount, "credit": "0.00"},
            {"account": self.account, "debit": "0.00", "credit": amount},
        ]

    def payload(self, amount="0.30", title="Synthetic draft"):
        return {"title_en": title, "title_ar": "مسودة تجريبية", "lines": [
            {"account_id": self.account.pk, "business_unit_id": self.unit.pk, "debit": amount, "credit": "0.00"},
            {"account_id": self.account.pk, "debit": "0.00", "credit": amount},
        ]}

    def business_counts(self):
        return {model.__name__: model.objects.count() for model in (
            JournalEntry, JournalLine, StockBalance, StockMovement, QuotePayment,
            QuoteReservation, UserActionGrant, BusinessUnitRoleAssignment,
        )}


@override_settings(**LAB)
@tag("finance_lab")
class FinanceDraftWorkflowTests(Fixture, TestCase):
    def setUp(self):
        self.setup_fixture()
        self.client.force_login(self.actor)

    def post(self, path, data):
        return self.client.post(path, json.dumps(data), content_type="application/json")

    def test_lab_routes_are_absent_from_normal_application_and_existing_roles_are_not_authority(self):
        for path in ("/lab/session/", "/lab/journals/", "/lab-ui/"):
            with self.assertRaises(Resolver404):
                resolve(path, urlconf="config.urls")
        self.actor.is_staff = True
        self.actor.save(update_fields=["is_staff"])
        self.assertEqual(self.client.get("/lab/context/").status_code, 403)

    def test_http_save_list_reopen_edit_and_preserved_revision_without_operational_writes(self):
        before = self.business_counts()
        saved = self.post("/lab/journals/", self.payload()).json()
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(saved["review"]["debit_total"], "0.30")
        self.assertEqual(len(self.client.get("/lab/journals/").json()["drafts"]), 1)
        with CaptureQueriesContext(connection) as queries:
            reopened = self.client.get(f"/lab/journals/{saved['id']}/").json()
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")) for q in queries))
        edited = self.client.put(f"/lab/journals/{saved['id']}/", json.dumps({
            **self.payload("0.31", "Edited draft"), "expected_revision": 1,
        }), content_type="application/json")
        self.assertEqual(edited.status_code, 200, edited.content)
        data = edited.json()
        self.assertEqual([r["revision"] for r in data["history"]], [1, 2])
        self.assertEqual(data["history"][0]["snapshot"], {k: v for k, v in reopened.items() if k != "history"})
        self.assertEqual(data["review"]["debit_total"], "0.31")
        self.assertEqual(data["state"], "draft")
        self.assertFalse(data["review"]["posted"])
        self.assertEqual(before, self.business_counts())

    def test_stale_update_returns_409_without_lost_edits_or_extra_revision(self):
        saved = self.post("/lab/journals/", self.payload()).json()
        url = f"/lab/journals/{saved['id']}/"
        self.client.put(url, json.dumps({**self.payload("2.00", "Winner"), "expected_revision": 1}), content_type="application/json")
        result = self.client.put(url, json.dumps({**self.payload("3.00", "Loser"), "expected_revision": 1}), content_type="application/json")
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["current_revision"], 2)
        self.assertEqual(self.client.get(url).json()["title_en"], "Winner")
        self.assertEqual(FinanceDraftJournalRevision.objects.count(), 2)

    def test_invalid_amount_and_foreign_entity_choices_rollback_and_recover(self):
        foreign = LegalEntity.objects.create(code="test-foreign-fin", name="TEST ONLY other")
        other_unit = BusinessUnit.objects.create(legal_entity=foreign, code="TEST-FIN-B", name="Other")
        for amount in ["-1.00", "0.001", "NaN", 0.1]:
            self.assertEqual(self.post("/lab/journals/", self.payload(amount)).status_code, 400)
        self.assertEqual(self.post("/lab/journals/", {"title_en": "Malformed", "lines": [None]}).status_code, 400)
        payload = self.payload()
        payload["lines"][0]["business_unit_id"] = other_unit.pk
        self.assertEqual(self.post("/lab/journals/", payload).status_code, 400)
        foreign_account = create_finance_draft_account(legal_entity=foreign, code="TEST-DRAFT-ACCT-FOREIGN", name_en="Other entity")
        payload = self.payload()
        payload["lines"][0]["account_id"] = foreign_account.pk
        self.assertEqual(self.post("/lab/journals/", payload).status_code, 400)
        dimension = FinanceDraftDimension.objects.create(legal_entity=foreign, code="TEST-DRAFT-DIM-OTHER", label_en="Other")
        value = FinanceDraftDimensionValue.objects.create(dimension=dimension, code="TEST-DRAFT-VAL-OTHER", label_en="Other")
        payload = self.payload()
        payload["lines"][0]["dimension_value_ids"] = [value.pk]
        self.assertEqual(self.post("/lab/journals/", payload).status_code, 400)
        self.assertEqual(FinanceDraftJournal.objects.count(), 0)
        self.assertEqual(self.post("/lab/journals/", self.payload()).status_code, 201)

    def test_revision_insert_failure_rolls_back_edit_and_history_is_database_immutable(self):
        journal = create_finance_draft_journal(legal_entity=self.entity, title_en="Before", lines=self.lines())
        before = finance_draft_snapshot(journal)
        with patch("foundation.finance_drafts._record_revision", side_effect=IntegrityError("forced test failure")):
            with self.assertRaises(IntegrityError):
                update_finance_draft_journal(journal_id=journal.pk, legal_entity=self.entity, expected_revision=1,
                                             title_en="Not committed", lines=self.lines("5.00"))
        journal.refresh_from_db()
        self.assertEqual(finance_draft_snapshot(journal), before)
        with self.assertRaises(DatabaseError), transaction.atomic():
            FinanceDraftJournalRevision.objects.filter(journal=journal).update(snapshot={})
        with self.assertRaises(DatabaseError), transaction.atomic():
            FinanceDraftJournalRevision.objects.filter(journal=journal).delete()

    def test_explicit_session_csrf_nonstaff_actor_and_disabled_lab_boundaries(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get("/lab/context/").status_code, 403)
        token = client.get("/lab/session/").json()["csrf_token"]
        self.assertEqual(client.post("/lab/session/", "{}", content_type="application/json").status_code, 403)
        opened = client.post("/lab/session/", "{}", content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(opened.status_code, 200)
        self.assertTrue(opened.json()["authenticated"])
        self.assertEqual(client.get("/lab/context/").status_code, 200)
        staff = get_user_model().objects.create_user(username="test-other-staff", is_staff=True)
        client.force_login(staff)
        self.assertEqual(client.get("/lab/context/").status_code, 403)
        with self.settings(FINANCE_LAB_ENABLED=False):
            self.assertEqual(client.get("/lab/session/").status_code, 400)
        with self.settings(FINANCE_LAB_DATA_DIRECTORY="/tmp/not-the-database/data"):
            self.assertEqual(self.client.get("/lab/context/").status_code, 400)
        self.assertEqual(UserActionGrant.objects.count(), 0)
        self.assertEqual(BusinessUnitRoleAssignment.objects.count(), 0)

    def test_foreign_draft_detail_denied_and_legacy_baseline_preserved_on_first_edit(self):
        foreign = LegalEntity.objects.create(code="test-foreign-fin", name="Other")
        legacy = FinanceDraftJournal.objects.create(legal_entity=self.entity, title_en="Actual old draft")
        updated = update_finance_draft_journal(journal_id=legacy.pk, legal_entity=self.entity, expected_revision=0,
                                              title_en="Now revised", lines=self.lines())
        self.assertEqual(list(updated.revisions.values_list("revision", flat=True)), [0, 1])
        self.assertEqual(updated.revisions.first().snapshot["title_en"], "Actual old draft")
        other = FinanceDraftJournal.objects.create(legal_entity=foreign, title_en="Foreign")
        self.assertEqual(self.client.get(f"/lab/journals/{other.pk}/").status_code, 404)
        ids = [row["id"] for row in self.client.get("/lab/journals/").json()["drafts"]]
        self.assertNotIn(other.pk, ids)


@override_settings(APP_ENV="demo", FINANCE_DRAFT_TEST_MODE=True)
@tag("finance_lab")
class FinanceDraftConcurrentEditTests(Fixture, TransactionTestCase):
    def setUp(self):
        self.setup_fixture()

    def test_independent_connections_reject_one_conflicting_edit_without_lost_updates(self):
        journal = create_finance_draft_journal(legal_entity=self.entity, title_en="Original", lines=self.lines())
        before = self.business_counts()
        barrier = Barrier(2)

        def edit(title):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                barrier.wait(timeout=10)
                try:
                    row = update_finance_draft_journal(journal_id=journal.pk, legal_entity=self.entity,
                                                      expected_revision=1, title_en=title, lines=self.lines("0.40"))
                    return pid, "saved", row.title_en
                except FinanceDraftConflict:
                    return pid, "conflict", title
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, ["Editor A", "Editor B"]))
        self.assertEqual(len({row[0] for row in results}), 2)
        self.assertEqual(sorted(row[1] for row in results), ["conflict", "saved"])
        journal.refresh_from_db()
        winner = next(row[2] for row in results if row[1] == "saved")
        self.assertEqual(journal.title_en, winner)
        self.assertEqual(journal.revision, 2)
        self.assertEqual(journal.revisions.count(), 2)
        self.assertEqual(journal.revisions.first().snapshot["title_en"], "Original")
        self.assertEqual(self.business_counts(), before)
        print(f"Independent Finance editor PostgreSQL PIDs: {[row[0] for row in results]}; one saved / one conflict.")
