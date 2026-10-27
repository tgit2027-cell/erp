import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections, DatabaseError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve
from django.conf import settings
from django.db.migrations.loader import MigrationLoader

from foundation.test_finance_draft_workflow import Fixture, LAB
from foundation.finance_drafts import (
    create_finance_draft_account, create_finance_draft_dimension, create_finance_draft_dimension_value,
    create_finance_draft_journal, finance_draft_snapshot, FinanceDraftConflict,
)
from foundation.models import LegalEntity, BusinessUnit
from .models import AllocationProposal, AllocationRevision
from .services import save_proposal, snapshot


class AllocationFixture(Fixture):
    def setup_allocation(self):
        self.setup_fixture()
        dim = create_finance_draft_dimension(
            legal_entity=self.entity, code="TEST-DRAFT-DIM-A", label_en="Test analysis")
        self.value = create_finance_draft_dimension_value(
            dimension=dim, code="TEST-DRAFT-VAL-A", label_en="Synthetic destination")
        self.client.force_login(self.actor)
        self.before = self.business_counts()

    def body(self, source="0.30", amounts=("0.10", "0.20")):
        return {**self.payload(), "allocation_proposal": {
            "source_amount": source, "currency": "QAR", "date": "2028-02-29",
            "explanation": "Explicit synthetic inputs — بيانات مقدمة",
            "destinations": [{
                "amount": amount, "account_id": self.account.pk,
                "business_unit_id": self.unit.pk, "dimension_value_ids": [self.value.pk],
            } for amount in amounts],
        }}

    def post(self, body):
        return self.client.post("/lab/allocations/", json.dumps(body), content_type="application/json")

    def put(self, pk, body):
        return self.client.put(f"/lab/allocations/{pk}/", json.dumps(body), content_type="application/json")


@override_settings(**LAB)
class AllocationTests(AllocationFixture, TestCase):
    def setUp(self):
        self.setup_allocation()

    def test_exact_arithmetic_complete_remaining_and_large_amounts(self):
        for source, amounts, allocated, difference in (
            ("0.30", ("0.10", "0.20"), "0.30", "0.00"),
            ("1.00", ("0.10", "0.20"), "0.30", "0.70"),
            ("999999999999.99", ("999999999999.98", "0.01"), "999999999999.99", "0.00"),
        ):
            with self.subTest(source=source):
                response = self.post(self.body(source, amounts))
                self.assertEqual(response.status_code, 201, response.content)
                review = response.json()["review"]
                self.assertEqual(review["allocated_total"], allocated)
                self.assertEqual(review["difference"], difference)
                self.assertEqual(review["state"], "remaining" if difference != "0.00" else "fully_allocated")
                self.assertFalse(review["journal"]["posted"])
                self.assertEqual(review["journal"]["debit_total"], "0.30")
        self.assertEqual(self.before, self.business_counts())

    def test_persist_reopen_edit_revision_conflict_and_history(self):
        saved = self.post(self.body()).json()
        url = f"/lab/allocations/{saved['id']}/"
        reopened = self.client.get(url).json()
        self.assertEqual(reopened, saved)
        self.assertEqual(reopened["title_ar"], "مسودة تجريبية")
        edited = self.body("0.60")
        edited["allocation_proposal"].update(date="2028-03-01", explanation="Explicit edited proposal")
        edited["expected_revision"] = 1
        result = self.put(saved["id"], edited)
        self.assertEqual(result.status_code, 200, result.content)
        updated = result.json()
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["review"]["difference"], "0.30")
        self.assertEqual(updated["history"][0]["snapshot"], {k: v for k, v in saved.items() if k != "history"})
        stale = self.put(saved["id"], {**self.body("9.00"), "expected_revision": 1})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.get(url).json(), updated)
        self.assertEqual(self.before, self.business_counts())

    def test_overallocation_rejected_atomically_for_create_and_edit(self):
        invalid = self.body("0.29")
        response = self.post(invalid)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Overallocated by 0.01", response.json()["detail"])
        self.assertEqual(AllocationProposal.objects.count(), 0)
        self.assertEqual(AllocationRevision.objects.count(), 0)
        saved = self.post(self.body()).json()
        response = self.put(saved["id"], {**invalid, "expected_revision": 1})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(f"/lab/allocations/{saved['id']}/").json(), saved)
        self.assertEqual(self.before, self.business_counts())

    def test_invalid_currency_dates_amounts_and_implicit_conventions_rejected(self):
        changes = [
            ("currency", ""), ("currency", "USD"), ("currency", None), ("currency", "qar"),
            ("date", "2028-02-30"), ("date", "03/01/2028"), ("date", True),
            ("explanation", ""), ("explanation", "x" * 2001),
            ("source_amount", "0.00"), ("source_amount", "-1"), ("source_amount", "NaN"),
            ("source_amount", "0.301"), ("source_amount", "1000000000000.00"),
            ("source_amount", .3), ("source_amount", True), ("destinations", []),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=str(value)[:25]):
                body = self.body()
                body["allocation_proposal"][key] = value
                self.assertEqual(self.post(body).status_code, 400)
        for value in ("0.00", "-0.01", "0.001", "Infinity", True, .1):
            self.assertEqual(self.post(self.body(amounts=(value,))).status_code, 400)
        body = self.body()
        body["allocation_proposal"]["rounding_distribution"] = "automatic"
        self.assertEqual(self.post(body).status_code, 400)
        body = self.body()
        body["allocation_proposal"]["destinations"][0]["percentage"] = "50"
        self.assertEqual(self.post(body).status_code, 400)
        for field in ("account_id", "business_unit_id"):
            for value in (True, "1", {}, -1):
                for target in ("destination", "journal"):
                    body = self.body()
                    row = body["allocation_proposal"]["destinations"][0] if target == "destination" else body["lines"][0]
                    row[field] = value
                    self.assertEqual(self.post(body).status_code, 400)
        self.assertEqual(AllocationProposal.objects.count(), 0)
        self.assertEqual(AllocationRevision.objects.count(), 0)

    def test_foreign_account_bu_dimension_and_entity_are_denied(self):
        entity = LegalEntity.objects.create(code="test-other-finance", name="Synthetic other")
        unit = BusinessUnit.objects.create(legal_entity=entity, code="TEST-OTHER", name="Other")
        account = create_finance_draft_account(
            legal_entity=entity, code="TEST-DRAFT-ACCT-OTHER", name_en="Other proposed")
        dim = create_finance_draft_dimension(
            legal_entity=entity, code="TEST-DRAFT-DIM-OTHER", label_en="Other")
        value = create_finance_draft_dimension_value(
            dimension=dim, code="TEST-DRAFT-VAL-OTHER", label_en="Other")
        for field, val in (("account_id", account.pk), ("business_unit_id", unit.pk),
                           ("dimension_value_ids", [value.pk])):
            for target in ("destination", "journal"):
                body = self.body()
                row = body["allocation_proposal"]["destinations"][0] if target == "destination" else body["lines"][0]
                row[field] = val
                self.assertEqual(self.post(body).status_code, 400)
        saved = self.post(self.body()).json()
        AllocationProposal.objects.filter(pk=saved["id"]).update(legal_entity=entity)
        self.assertEqual(self.client.get(f"/lab/allocations/{saved['id']}/").status_code, 404)
        self.assertEqual(self.client.get("/lab/allocations/").json()["proposals"], [])

    def test_access_rechecked_no_shared_routes_or_default_permissions(self):
        saved = self.post(self.body()).json()
        for path in ("/lab/allocations/", "/lab-allocation-ui/"):
            with self.assertRaises(Resolver404):
                resolve(path, urlconf="config.urls")
        self.assertEqual(AllocationProposal._meta.default_permissions, ())
        self.assertEqual(AllocationRevision._meta.default_permissions, ())
        # MigrationLoader(None) reads the graph from disk without a DB connection.
        # Removing the lab-only app reproduces the normal installed-app boundary.
        with override_settings(INSTALLED_APPS=[
            app for app in settings.INSTALLED_APPS if app != "finance_allocation_lab"
        ]), CaptureQueriesContext(connection) as queries:
            self.assertFalse(any(node[0] == "finance_allocation_lab" for node in MigrationLoader(None).graph.nodes))
        self.assertEqual(len(queries), 0)
        for flags in ({"is_staff": True}, {"is_superuser": True}, {"is_active": False}):
            self.actor.refresh_from_db()
            self.actor.is_active, self.actor.is_staff, self.actor.is_superuser = True, False, False
            for key, val in flags.items():
                setattr(self.actor, key, val)
            self.actor.save()
            self.assertEqual(self.client.get(f"/lab/allocations/{saved['id']}/").status_code, 403)
            self.assertEqual(self.post(self.body()).status_code, 403)
        self.assertEqual(self.before, self.business_counts())
        with override_settings(APP_ENV="production"):
            self.assertEqual(self.client.get("/lab/allocations/").status_code, 400)

    def test_immutable_history_and_forced_revision_failure_roll_back(self):
        saved = self.post(self.body()).json()
        with patch("finance_allocation_lab.services.record_revision", side_effect=RuntimeError("Synthetic failure")):
            with self.assertRaises(RuntimeError):
                self.put(saved["id"], {**self.body("0.60"), "expected_revision": 1})
            with self.assertRaises(RuntimeError):
                self.post(self.body())
        self.assertEqual(self.client.get(f"/lab/allocations/{saved['id']}/").json(), saved)
        self.assertEqual(AllocationProposal.objects.count(), 1)
        with self.assertRaises(DatabaseError), transaction.atomic():
            AllocationRevision.objects.update(snapshot={})
        with self.assertRaises(DatabaseError), transaction.atomic():
            AllocationRevision.objects.all().delete()
        self.assertEqual(self.before, self.business_counts())

    def test_read_only_deterministic_and_journal_validation_matches_existing_drafts(self):
        saved = self.post(self.body()).json()
        with CaptureQueriesContext(connection) as queries:
            first = self.client.get(f"/lab/allocations/{saved['id']}/").json()
            second = self.client.get(f"/lab/allocations/{saved['id']}/").json()
            self.assertEqual(self.client.get("/lab/allocations/").json()["proposals"][0]["id"], saved["id"])
        self.assertEqual(first, second)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")) for q in queries))
        journal = create_finance_draft_journal(legal_entity=self.entity, title_en="Comparison", lines=self.lines())
        original = finance_draft_snapshot(journal)["review"]
        for key in ("debit_total", "credit_total", "difference", "balanced", "posted"):
            self.assertEqual(first["review"]["journal"][key], original[key])
        body = self.body()
        body["lines"][1]["credit"] = "0.29"
        unbalanced = self.post(body).json()["review"]["journal"]
        self.assertFalse(unbalanced["balanced"])
        self.assertEqual(unbalanced["difference"], "0.01")
        self.assertEqual(self.before, self.business_counts())


@override_settings(**LAB)
class AllocationConcurrencyTests(AllocationFixture, TransactionTestCase):
    def setUp(self):
        self.setup_allocation()

    def test_independent_connections_reject_conflicting_edits_without_mixed_inputs(self):
        saved = save_proposal(data=self.body(), entity=self.entity)
        barrier = Barrier(2)
        def worker(index):
            connections.close_all()
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                body = copy.deepcopy(self.body())
                body["expected_revision"] = 1
                body["allocation_proposal"]["explanation"] = f"Explicit editor {index}"
                body["allocation_proposal"]["source_amount"] = f"{index}.00"
                body["allocation_proposal"]["date"] = f"2028-03-0{index}"
                barrier.wait(timeout=10)
                try:
                    result = save_proposal(data=body, entity=self.entity, proposal_id=saved.pk)
                    return pid, "saved", snapshot(result)["allocation_proposal"]["explanation"]
                except FinanceDraftConflict:
                    return pid, "conflict", None
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, (1, 2)))
        self.assertNotEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(row[1] for row in results), ["conflict", "saved"])
        saved.refresh_from_db()
        self.assertEqual(saved.revision, 2)
        self.assertEqual(saved.payload["allocation_proposal"]["explanation"],
                         next(row[2] for row in results if row[1] == "saved"))
        winner = int(saved.payload["allocation_proposal"]["explanation"].split()[-1])
        self.assertEqual(saved.payload["allocation_proposal"]["source_amount"], f"{winner}.00")
        self.assertEqual(saved.payload["allocation_proposal"]["date"], f"2028-03-0{winner}")
        self.assertEqual(AllocationRevision.objects.filter(proposal=saved).count(), 2)
        self.assertEqual(self.before, self.business_counts())
