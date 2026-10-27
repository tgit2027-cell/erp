import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import connection, connections, DatabaseError, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve
from foundation.test_finance_draft_workflow import LAB
from foundation.finance_drafts import (
    FinanceDraftConflict, create_finance_draft_dimension, create_finance_draft_dimension_value,
)
from foundation.models import BusinessUnit, LegalEntity
from .test_budgets import BudgetFixture
from .forecasts import save_forecast, snapshot
from .models import CashForecastDraft, CashForecastRevision


class ForecastFixture(BudgetFixture):
    def body(self):
        return {
            "name": " Explicit synthetic forecast — سيناريو ", "as_of_date": "2028-02-29",
            "opening_balance": "-0.10", "currency": "QAR",
            "entries": [{
                "date": day, "direction": direction, "amount": amount,
                "description": f" User supplied flow {index} — نص ",
                "business_unit_id": self.unit.pk, "dimension_value_ids": [self.value.pk],
            } for index, (day, direction, amount) in enumerate([
                ("2028-03-01", "inflow", "0.20"),
                ("2028-02-29", "outflow", "0.30"),
                ("2028-02-29", "inflow", "0.10"),
            ], 1)],
        }

    def post(self, body):
        return self.client.post("/lab/forecasts/", json.dumps(body), content_type="application/json")

    def put(self, pk, body):
        return self.client.put(f"/lab/forecasts/{pk}/", json.dumps(body), content_type="application/json")


@override_settings(**LAB)
class ForecastTests(ForecastFixture, TestCase):
    def setUp(self):
        self.setup_budget()
        self.assertEqual(connection.vendor, "postgresql")

    def test_exact_negative_balances_and_same_date_source_order_are_presentation_only(self):
        response = self.post(self.body())
        self.assertEqual(response.status_code, 201)
        saved = response.json()
        self.assertEqual(saved["entries"], self.body()["entries"])
        self.assertEqual(saved["name"], self.body()["name"])
        review = saved["review"]
        self.assertEqual([row["line_number"] for row in review["entries"]], [2, 3, 1])
        self.assertEqual([row["running_balance"] for row in review["entries"]], ["-0.40", "-0.30", "-0.10"])
        self.assertEqual([review[key] for key in ("total_inflows", "total_outflows", "net_change", "closing_balance")],
                         ["0.30", "0.30", "0.00", "-0.10"])
        self.assertEqual(review["ordering"], "date_ascending_then_input_order")
        self.assertEqual([review[key] for key in ("proposed", "actual_cash", "posted", "approved")],
                         [True, False, False, False])
        self.assertEqual(self.business_counts(), self.before)

    def test_zero_large_values_optional_references_and_aggregate_precision(self):
        body = self.body()
        body["opening_balance"] = "-0.00"
        body["entries"] = [{**body["entries"][0], "amount": "0", "business_unit_id": None, "dimension_value_ids": []}]
        saved = self.post(body).json()
        self.assertEqual(saved["review"]["closing_balance"], "0.00")
        self.assertEqual(saved["entries"][0]["amount"], "0.00")
        body["entries"] *= 2
        for row in body["entries"]:
            row["amount"] = "999999999999.99"
        self.assertEqual(self.post(body).json()["review"]["closing_balance"], "1999999999999.98")
        self.assertEqual(self.business_counts(), self.before)

    def test_invalid_dates_amounts_descriptions_and_unsupplied_policy_inputs_no_partial_write(self):
        invalid = []
        for key, values in {
            "name": ["", " ", "x" * 201, None], "as_of_date": ["2027-02-29", "0000-01-01", "", "2028-3-1"],
            "opening_balance": [0.1, True, "-0.001", "1e2", "NaN", "1000000000000", "--1"],
            "currency": ["USD", None], "entries": [[], {}, self.body()["entries"] * 67],
        }.items():
            for value in values:
                invalid.append({**self.body(), key: value})
        for key, values in {
            "date": ["2028-02-28", "2027-02-29", ""], "amount": ["-0.01", "0.001", 1, False, "1e2"],
            "description": ["", " ", None, "x" * 2001], "direction": ["", "transfer", None],
            "dimension_value_ids": [[True], ["1"], [self.value.pk, self.value.pk]],
            "business_unit_id": [True, "1", -1],
        }.items():
            for value in values:
                body = self.body()
                body["entries"][0][key] = value
                invalid.append(body)
        for key in ("approved", "actual_cash", "bank_account_id", "exchange_rate", "schedule", "post", "review", "history"):
            invalid.append({**self.body(), key: True})
        body = self.body()
        body["entries"][0]["account_id"] = self.account.pk
        invalid.append(body)
        for body in invalid:
            with self.subTest(body=body):
                result = self.post(body)
                self.assertEqual(result.status_code, 400, result.content)
                self.assertIn("detail", result.json())
        self.assertEqual(CashForecastDraft.objects.count(), 0)
        self.assertEqual(CashForecastRevision.objects.count(), 0)
        self.assertEqual(self.business_counts(), self.before)

    def test_entity_active_unit_dimension_scope_and_foreign_draft_denial(self):
        other = LegalEntity.objects.create(name="Synthetic other", code="TEST-FORECAST-OTHER")
        unit = BusinessUnit.objects.create(legal_entity=other, name="Synthetic other unit", code="TEST-FOR-OTHER")
        dim = create_finance_draft_dimension(legal_entity=other, code="TEST-DRAFT-DIM-FORECAST", label_en="Other")
        val = create_finance_draft_dimension_value(dimension=dim, code="TEST-DRAFT-VAL-FORECAST", label_en="Other")
        for key, value in [("business_unit_id", unit.pk), ("dimension_value_ids", [val.pk])]:
            body = self.body()
            body["entries"][0][key] = value
            self.assertEqual(self.post(body).status_code, 400)
        self.unit.is_active = False
        self.unit.save(update_fields=["is_active"])
        self.assertEqual(self.post(self.body()).status_code, 400)
        foreign = CashForecastDraft.objects.create(legal_entity=other, payload=self.body())
        self.assertEqual(self.client.get(f"/lab/forecasts/{foreign.pk}/").status_code, 404)
        self.assertEqual(self.put(foreign.pk, {**self.body(), "expected_revision": 1}).status_code, 404)
        self.assertEqual(self.client.get("/lab/forecasts/").json()["forecasts"], [])

    def test_saved_list_reopen_edit_and_immutable_original_with_conflict(self):
        first = self.post(self.body()).json()
        self.assertEqual(self.client.get(f"/lab/forecasts/{first['id']}/").json(), first)
        changes = {**self.body(), "expected_revision": 1, "opening_balance": "-1.01"}
        second = self.put(first["id"], changes).json()
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["history"][0]["snapshot"]["opening_balance"], "-0.10")
        self.assertEqual(second["history"][1]["snapshot"]["opening_balance"], "-1.01")
        self.assertEqual(self.put(first["id"], changes).status_code, 409)
        self.assertEqual(self.client.get(f"/lab/forecasts/{first['id']}/").json(), second)
        newer = self.post(self.body()).json()
        self.assertEqual([row["id"] for row in self.client.get("/lab/forecasts/").json()["forecasts"]],
                         [newer["id"], first["id"]])
        self.assertEqual(self.business_counts(), self.before)

    def test_revision_metadata_and_invalid_edit_cannot_change_saved_state(self):
        self.assertEqual(self.post({**self.body(), "expected_revision": 1}).status_code, 400)
        saved = self.post(self.body()).json()
        for expected in (None, True, "1", 0):
            self.assertEqual(self.put(saved["id"], {**self.body(), "expected_revision": expected}).status_code, 400)
        bad = {**self.body(), "expected_revision": 1}
        bad["entries"][1]["amount"] = "0.123"
        self.assertEqual(self.put(saved["id"], bad).status_code, 400)
        self.assertEqual(self.client.get(f"/lab/forecasts/{saved['id']}/").json(), saved)

    def test_atomic_revision_failure_and_database_history_protection(self):
        with patch("finance_allocation_lab.forecasts.record_revision", side_effect=RuntimeError("forced")):
            with self.assertRaises(RuntimeError):
                save_forecast(data=self.body(), entity=self.entity)
        self.assertEqual(CashForecastDraft.objects.count(), 0)
        saved = save_forecast(data=self.body(), entity=self.entity)
        original = snapshot(saved)
        with patch("finance_allocation_lab.forecasts.record_revision", side_effect=RuntimeError("forced")):
            with self.assertRaises(RuntimeError):
                save_forecast(data={**self.body(), "expected_revision": 1, "opening_balance": "2"}, entity=self.entity, draft_id=saved.pk)
        saved.refresh_from_db()
        self.assertEqual(snapshot(saved), original)
        table = CashForecastRevision._meta.db_table
        for sql in (f"UPDATE {table} SET revision=9", f"DELETE FROM {table}"):
            with self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(sql)
        self.assertEqual(saved.revisions.count(), 1)
        for overrides in ({"is_synthetic": False}, {"revision": 0}):
            with self.assertRaises(DatabaseError), transaction.atomic():
                CashForecastDraft.objects.create(legal_entity=self.entity, payload=self.body(), **overrides)

    def test_access_rechecked_no_normal_routes_or_shared_migration_permissions(self):
        saved = self.post(self.body()).json()
        for flag in ("is_staff", "is_superuser"):
            setattr(self.actor, flag, True)
            self.actor.save(update_fields=[flag])
            self.assertEqual(self.client.get("/lab/forecasts/").status_code, 403)
            self.assertEqual(self.put(saved["id"], {**self.body(), "expected_revision": 1}).status_code, 403)
            setattr(self.actor, flag, False)
            self.actor.save(update_fields=[flag])
        self.actor.is_active = False
        self.actor.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(f"/lab/forecasts/{saved['id']}/").status_code, 403)
        self.client.logout()
        self.assertEqual(self.post(self.body()).status_code, 403)
        with override_settings(ROOT_URLCONF="foundation.urls"):
            with self.assertRaises(Resolver404):
                resolve("/lab/forecasts/")
        from config import settings as normal_settings
        with override_settings(INSTALLED_APPS=normal_settings.INSTALLED_APPS):
            self.assertFalse(any(app == "finance_allocation_lab" for app, _ in MigrationLoader(connection).graph.nodes))
        self.assertEqual(CashForecastDraft._meta.default_permissions, ())
        self.assertEqual(CashForecastRevision._meta.default_permissions, ())

    def test_register_detail_reads_are_deterministic_and_do_not_write(self):
        saved = self.post(self.body()).json()
        before = (self.business_counts(), CashForecastDraft.objects.count(), CashForecastRevision.objects.count())
        with CaptureQueriesContext(connection) as queries:
            for _ in range(2):
                self.assertEqual(self.client.get(f"/lab/forecasts/{saved['id']}/").json(), saved)
                self.assertEqual(len(self.client.get("/lab/forecasts/").json()["forecasts"]), 1)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertEqual(before, (self.business_counts(), CashForecastDraft.objects.count(), CashForecastRevision.objects.count()))


@override_settings(**LAB)
class ForecastConcurrencyTests(ForecastFixture, TransactionTestCase):
    def setUp(self):
        self.setup_budget()

    def test_independent_connections_preserve_one_complete_winning_revision(self):
        saved = save_forecast(data=self.body(), entity=self.entity)
        barrier = Barrier(2)

        def worker(index):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                body = copy.deepcopy(self.body())
                body.update(expected_revision=1, name=f"Explicit editor {index}", opening_balance=f"-{index}.01")
                body["entries"][0]["description"] = f"Whole edit {index}"
                barrier.wait(timeout=10)
                try:
                    save_forecast(data=body, entity=self.entity, draft_id=saved.pk)
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
        self.assertEqual(saved.payload["opening_balance"], f"-{winner}.01")
        self.assertEqual(saved.payload["entries"][0]["description"], f"Whole edit {winner}")
        self.assertEqual(saved.revisions.count(), 2)
        self.assertEqual(self.business_counts(), self.before)
