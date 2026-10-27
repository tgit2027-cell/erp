import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.conf import settings
from django.db import connections, connection, DatabaseError, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, Resolver404

from foundation.test_finance_draft_workflow import Fixture, LAB
from foundation.finance_drafts import (
    FinanceDraftConflict, create_finance_draft_account, create_finance_draft_dimension,
    create_finance_draft_dimension_value,
)
from foundation.models import LegalEntity, BusinessUnit
from .models import BudgetScenario, BudgetRevision
from .budget import save_scenario, snapshot


class BudgetFixture(Fixture):
    def setup_budget(self):
        self.setup_fixture()
        dim = create_finance_draft_dimension(legal_entity=self.entity, code="TEST-DRAFT-DIM-B", label_en="Draft analysis")
        self.value = create_finance_draft_dimension_value(dimension=dim, code="TEST-DRAFT-VAL-B", label_en="Draft choice")
        self.client.force_login(self.actor)
        self.before = self.business_counts()

    def body(self):
        return {
            "name": "Explicit synthetic proposal — بيانات", "currency": "QAR",
            "start_date": "2028-02-01", "end_date": "2028-03-31",
            "entries": [{
                "start_date": first, "end_date": last, "amount": amount,
                "account_id": self.account.pk, "business_unit_id": self.unit.pk,
                "dimension_value_ids": [self.value.pk], "explanation": "Reviewer entered — نص مقدم",
            } for first, last, amount in [
                ("2028-02-01", "2028-02-29", "0.10"), ("2028-03-01", "2028-03-31", "0.20"),
            ]],
        }

    def post(self, body):
        return self.client.post("/lab/budgets/", json.dumps(body), content_type="application/json")

    def put(self, pk, body):
        return self.client.put(f"/lab/budgets/{pk}/", json.dumps(body), content_type="application/json")


@override_settings(**LAB)
class BudgetTests(BudgetFixture, TestCase):
    def setUp(self):
        self.setup_budget()

    def test_exact_totals_zero_and_large_values_no_operative_records(self):
        row = self.post(self.body())
        self.assertEqual(row.status_code, 201)
        self.assertEqual(row.json()["review"], {
            "currency": "QAR", "proposed_total": "0.30", "entry_count": 2, "warnings": [], "posted": False})
        for amount, expected in [("0.00", "0.00"), ("999999999999.99", "1999999999999.98")]:
            body = self.body()
            for entry in body["entries"]:
                entry["amount"] = amount
            self.assertEqual(self.post(body).json()["review"]["proposed_total"], expected)
        self.assertEqual(self.business_counts(), self.before)

    def test_duplicate_overlap_advisories_do_not_reject_merge_or_distribute(self):
        body = self.body()
        body["entries"].append(copy.deepcopy(body["entries"][0]))
        body["entries"][1]["start_date"] = "2028-02-29"
        body["entries"].append({**body["entries"][0], "business_unit_id": None})
        row = self.post(body)
        self.assertEqual(row.status_code, 201)
        saved = row.json()
        self.assertEqual(saved["review"]["proposed_total"], "0.50")
        self.assertEqual(saved["review"]["entry_count"], 4)
        self.assertEqual([(w["code"], w["rows"]) for w in saved["review"]["warnings"]],
                         [("overlapping_dates", [1, 2]), ("duplicate_entry", [1, 3]), ("overlapping_dates", [2, 3])])
        self.assertTrue(all("No company acceptance policy" in w["message"] for w in saved["review"]["warnings"]))
        self.assertEqual([entry["amount"] for entry in saved["entries"]], ["0.10", "0.20", "0.10", "0.10"])

    def test_persistence_list_reopen_revisions_and_conflict_preserve_original(self):
        first = self.post(self.body()).json()
        self.assertEqual(self.client.get(f"/lab/budgets/{first['id']}/").json(), first)
        changed = self.body()
        changed.update(name="Explicit edited scenario", expected_revision=1)
        changed["entries"][0]["amount"] = "1.01"
        second = self.put(first["id"], changed).json()
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["review"]["proposed_total"], "1.21")
        self.assertEqual(second["history"][0]["snapshot"]["name"], first["name"])
        self.assertEqual(second["history"][0]["snapshot"]["review"]["proposed_total"], "0.30")
        self.assertEqual(self.put(first["id"], changed).status_code, 409)
        self.assertEqual(self.client.get(f"/lab/budgets/{first['id']}/").json(), second)
        later = self.post(self.body()).json()
        listed = self.client.get("/lab/budgets/").json()["scenarios"]
        self.assertEqual([row["id"] for row in listed], [later["id"], first["id"]])
        self.assertEqual(self.business_counts(), self.before)

    def test_invalid_dates_ranges_amounts_and_inferred_inputs_rejected_without_writes(self):
        invalid_root = [
            ("name", ""), ("name", "x" * 201), ("currency", ""), ("currency", "USD"),
            ("currency", "qar"), ("start_date", "0000-01-01"), ("start_date", "2027-02-29"),
            ("start_date", "2028-04-01"), ("end_date", True), ("end_date", "01/03/2028"),
            ("entries", []), ("entries", "not rows"),
        ]
        for key, value in invalid_root:
            body = self.body()
            body[key] = value
            with self.subTest(key=key, value=str(value)[:30]):
                self.assertEqual(self.post(body).status_code, 400)
        for key, value in [
            ("amount", "-0.01"), ("amount", "NaN"), ("amount", "Infinity"), ("amount", True),
            ("amount", .1), ("amount", "0.001"), ("amount", "1000000000000"),
            ("start_date", "2028-01-31"), ("end_date", "2028-02-30"), ("end_date", "2028-01-01"),
            ("account_id", True), ("account_id", str(self.account.pk)), ("business_unit_id", -1),
            ("dimension_value_ids", [True]), ("dimension_value_ids", [self.value.pk, self.value.pk]),
            ("explanation", None), ("explanation", "x" * 2001),
        ]:
            body = self.body()
            body["entries"][0][key] = value
            with self.subTest(key=key, value=str(value)[:30]):
                self.assertEqual(self.post(body).status_code, 400)
        for field in ["approval", "fiscal_calendar", "actuals", "distribution", "posted"]:
            body = self.body()
            body[field] = "automatic"
            self.assertEqual(self.post(body).status_code, 400)
        body = self.body()
        body["entries"] *= 51
        self.assertEqual(self.post(body).status_code, 400)
        self.assertEqual((BudgetScenario.objects.count(), BudgetRevision.objects.count()), (0, 0))
        self.assertEqual(self.before, self.business_counts())

    def test_foreign_account_bu_dimensions_and_entity_lookup_denied(self):
        other = LegalEntity.objects.create(code="test-budget-other", name="Synthetic other")
        account = create_finance_draft_account(legal_entity=other, code="TEST-DRAFT-ACCT-OTHER", name_en="Foreign")
        unit = BusinessUnit.objects.create(legal_entity=other, code="TEST-BUD-OTHER", name="Foreign")
        dim = create_finance_draft_dimension(legal_entity=other, code="TEST-DRAFT-DIM-OTHER", label_en="Foreign")
        value = create_finance_draft_dimension_value(dimension=dim, code="TEST-DRAFT-VAL-OTHER", label_en="Foreign")
        for key, choice in [("account_id", account.pk), ("business_unit_id", unit.pk), ("dimension_value_ids", [value.pk])]:
            body = self.body()
            body["entries"][0][key] = choice
            self.assertEqual(self.post(body).status_code, 400)
        row = BudgetScenario.objects.create(legal_entity=other, payload=self.body())
        self.assertEqual(self.client.get(f"/lab/budgets/{row.pk}/").status_code, 404)
        self.assertEqual(self.put(row.pk, {**self.body(), "expected_revision": 1}).status_code, 404)

    def test_access_rechecked_normal_routes_and_migration_graph_excluded(self):
        saved = self.post(self.body()).json()
        self.actor.is_staff = True
        self.actor.save(update_fields=["is_staff"])
        self.assertEqual(self.client.get("/lab/budgets/").status_code, 403)
        self.assertEqual(self.client.get(f"/lab/budgets/{saved['id']}/").status_code, 403)
        self.assertEqual(self.put(saved["id"], {**self.body(), "expected_revision": 1}).status_code, 403)
        self.assertEqual(self.post(self.body()).status_code, 403)
        self.assertEqual(BudgetScenario.objects.get(pk=saved["id"]).revision, 1)
        with override_settings(ROOT_URLCONF="foundation.urls"):
            for path in ["/lab/budgets/", "/lab-budget-ui/"]:
                with self.assertRaises(Resolver404):
                    resolve(path)
        with override_settings(INSTALLED_APPS=[app for app in settings.INSTALLED_APPS if app != "finance_allocation_lab"]):
            with CaptureQueriesContext(connection) as queries:
                graph = MigrationLoader(None).graph
            self.assertFalse(any(node[0] == "finance_allocation_lab" for node in graph.nodes))
            self.assertEqual(len(queries), 0)
        self.assertEqual(BudgetScenario._meta.default_permissions, ())
        self.assertEqual(BudgetRevision._meta.default_permissions, ())

    def test_atomic_revision_failure_and_immutable_history(self):
        with patch("finance_allocation_lab.budget.record_revision", side_effect=RuntimeError("forced history failure")):
            with self.assertRaises(RuntimeError):
                save_scenario(data=self.body(), entity=self.entity)
        self.assertEqual(BudgetScenario.objects.count(), 0)
        row = save_scenario(data=self.body(), entity=self.entity)
        original = snapshot(row)
        with patch("finance_allocation_lab.budget.record_revision", side_effect=RuntimeError("forced history failure")):
            with self.assertRaises(RuntimeError):
                save_scenario(data={**self.body(), "name": "Must roll back", "expected_revision": 1},
                              entity=self.entity, scenario_id=row.pk)
        row.refresh_from_db()
        self.assertEqual(snapshot(row), original)
        for operation in [
            lambda: BudgetRevision.objects.filter(scenario=row).update(snapshot={}),
            lambda: BudgetRevision.objects.filter(scenario=row).delete(),
        ]:
            with self.assertRaises(DatabaseError), transaction.atomic():
                operation()
        self.assertEqual(BudgetRevision.objects.filter(scenario=row).count(), 1)

    def test_get_is_deterministic_without_writes_and_invalid_edit_keeps_saved_data(self):
        self.assertEqual(self.client.get("/lab/budgets/").json(), {"scenarios": []})
        first = self.post(self.body()).json()
        with CaptureQueriesContext(connection) as queries:
            for _ in range(2):
                self.assertEqual(self.client.get(f"/lab/budgets/{first['id']}/").json(), first)
                self.assertEqual(len(self.client.get("/lab/budgets/").json()["scenarios"]), 1)
        self.assertFalse(any(query["sql"].lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}
                             for query in queries))
        invalid = {**self.body(), "expected_revision": 1, "end_date": "2028-01-01"}
        self.assertEqual(self.put(first["id"], invalid).status_code, 400)
        self.assertEqual(self.client.get(f"/lab/budgets/{first['id']}/").json(), first)
        self.assertEqual(self.business_counts(), self.before)


@override_settings(**LAB)
class BudgetConcurrencyTests(BudgetFixture, TransactionTestCase):
    def setUp(self):
        self.setup_budget()

    def test_independent_connections_reject_conflict_without_mixed_rows(self):
        saved = save_scenario(data=self.body(), entity=self.entity)
        barrier = Barrier(2)
        def worker(index):
            connections.close_all()
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                body = self.body()
                body.update(expected_revision=1, name=f"Explicit editor {index}")
                body["entries"][0]["amount"] = f"{index}.01"
                body["entries"][0]["explanation"] = f"Editor {index} intact"
                barrier.wait(timeout=10)
                try:
                    save_scenario(data=body, entity=self.entity, scenario_id=saved.pk)
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
        self.assertEqual(saved.payload["name"], f"Explicit editor {winner}")
        self.assertEqual(saved.payload["entries"][0]["amount"], f"{winner}.01")
        self.assertEqual(saved.payload["entries"][0]["explanation"], f"Editor {winner} intact")
        self.assertEqual(BudgetRevision.objects.filter(scenario=saved).count(), 2)
        self.assertEqual(self.before, self.business_counts())
