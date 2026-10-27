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
from foundation.test_finance_draft_workflow import LAB
from foundation.finance_drafts import (
    FinanceDraftConflict, create_finance_draft_account,
    create_finance_draft_dimension, create_finance_draft_dimension_value,
)
from foundation.models import BusinessUnit, LegalEntity
from .test_budgets import BudgetFixture
from .assets import save_asset, snapshot
from .models import FixedAssetDraft, FixedAssetRevision


class AssetFixture(BudgetFixture):
    def body(self):
        return {"asset_identifier": " TEST-ASSET-01 ", "name": " Synthetic asset draft ",
                "description": " User supplied description — وصف ", "acquisition_date": "2028-02-29",
                "currency": "QAR", "cost": "0.10", "account_id": self.account.pk,
                "business_unit_id": self.unit.pk, "dimension_value_ids": [self.value.pk],
                "reference": " TEST-REFERENCE-01 ", "location_details": " Proposed text only — موقع "}

    def post(self, body):
        return self.client.post("/lab/assets/", json.dumps(body), content_type="application/json")

    def put(self, pk, body):
        return self.client.put(f"/lab/assets/{pk}/", json.dumps(body), content_type="application/json")


@override_settings(**LAB)
class AssetTests(AssetFixture, TestCase):
    def setUp(self):
        self.setup_budget()
        self.assertEqual(connection.vendor, "postgresql")

    def test_exact_cost_original_text_and_explicit_review_never_capitalize_or_post(self):
        body = self.body()
        body["cost"] = "0.1"
        response = self.post(body)
        self.assertEqual(response.status_code, 201)
        saved = response.json()
        for key in body:
            self.assertEqual(saved[key], "0.10" if key == "cost" else body[key])
        self.assertEqual(saved["review"], {
            "currency": "QAR", "proposed_cost": "0.10", "capitalized": False, "posted": False, "approved": False})
        self.assertEqual(self.business_counts(), self.before)

    def test_zero_large_cost_optional_fields_and_duplicate_draft_identifiers_infer_no_policy(self):
        ids = []
        for cost in ["0.00", "999999999999.99"]:
            body = {**self.body(), "cost": cost, "reference": "", "location_details": "",
                    "business_unit_id": None, "dimension_value_ids": []}
            response = self.post(body)
            self.assertEqual(response.status_code, 201)
            row = response.json()
            self.assertEqual(row["review"]["proposed_cost"], cost)
            self.assertIsNone(row["business_unit_id"])
            self.assertEqual(row["reference"], "")
            self.assertEqual(row["location_details"], "")
            ids.append(row["id"])
        self.assertNotEqual(*ids)  # User identifier is not an inferred unique operational asset key.
        self.assertEqual(self.post({**self.body(), "expected_revision": 1}).status_code, 400)
        self.assertEqual(FixedAssetDraft.objects.count(), 2)
        self.assertEqual(self.business_counts(), self.before)

    def test_invalid_dates_precision_ids_and_policy_fields_rejected_without_partial_writes(self):
        invalid = []
        for key, value in [
            ("asset_identifier", ""), ("asset_identifier", "x" * 101), ("name", " "), ("name", "x" * 201),
            ("description", None), ("description", ""), ("description", " "),
            ("description", "x" * 2001),
            ("acquisition_date", "2027-02-29"), ("acquisition_date", "0000-01-01"),
            ("acquisition_date", "2028-2-29"), ("acquisition_date", "2028-02-29T00:00:00Z"),
            ("currency", "USD"), ("cost", 0.1), ("cost", True), ("cost", "-1"), ("cost", "0.001"),
            ("cost", "NaN"), ("cost", "1e2"), ("cost", "1000000000000"),
            ("account_id", True), ("account_id", None), ("business_unit_id", []),
            ("dimension_value_ids", [True]), ("dimension_value_ids", [[1]]),
            ("dimension_value_ids", [self.value.pk, self.value.pk]),
            ("reference", {"file": "unsupported"}), ("reference", "x" * 201),
            ("location_details", {"location_id": 1}), ("location_details", "x" * 2001),
        ]:
            invalid.append({**self.body(), key: value})
        for key in ["depreciation", "useful_life", "residual_value", "capitalization_threshold",
                    "custodian", "location_id", "approved", "disposed", "posted", "tax", "review", "history", "id"]:
            invalid.append({**self.body(), key: True})
        for body in invalid:
            with self.subTest(body_keys=list(body)):
                self.assertEqual(self.post(body).status_code, 400)
        self.assertEqual((FixedAssetDraft.objects.count(), FixedAssetRevision.objects.count()), (0, 0))
        saved = self.post(self.body()).json()
        for body in invalid:
            self.assertEqual(self.put(saved["id"], {**body, "expected_revision": 1}).status_code, 400)
        for expected in [None, False, 0, "1"]:
            self.assertEqual(self.put(saved["id"], {**self.body(), "expected_revision": expected}).status_code, 400)
        self.assertEqual(self.client.get(f"/lab/assets/{saved['id']}/").json(), saved)
        self.assertEqual(self.business_counts(), self.before)

    def test_existing_account_bu_dimension_entity_checks_and_foreign_lookup_denial(self):
        other = LegalEntity.objects.create(code="test-asset-other", name="Synthetic other")
        account = create_finance_draft_account(legal_entity=other, code="TEST-DRAFT-ACCT-OTHER", name_en="Foreign")
        unit = BusinessUnit.objects.create(legal_entity=other, code="TEST-ASSET-OTHER", name="Foreign")
        dimension = create_finance_draft_dimension(legal_entity=other, code="TEST-DRAFT-DIM-OTHER", label_en="Foreign")
        value = create_finance_draft_dimension_value(dimension=dimension, code="TEST-DRAFT-VAL-OTHER", label_en="Foreign")
        for key, choice in [("account_id", account.pk), ("business_unit_id", unit.pk), ("dimension_value_ids", [value.pk])]:
            self.assertEqual(self.post({**self.body(), key: choice}).status_code, 400)
        second = create_finance_draft_dimension_value(
            dimension=self.value.dimension, code="TEST-DRAFT-VAL-SECOND", label_en="Second explicit value")
        response = self.post({**self.body(), "dimension_value_ids": [self.value.pk, second.pk]})
        self.assertEqual(response.status_code, 201)  # Keep existing multiple-values-per-dimension semantics.
        foreign = FixedAssetDraft.objects.create(legal_entity=other, payload=self.body())
        self.assertEqual(self.client.get(f"/lab/assets/{foreign.pk}/").status_code, 404)
        self.assertEqual(self.put(foreign.pk, {**self.body(), "expected_revision": 1}).status_code, 404)
        self.assertEqual([row["id"] for row in self.client.get("/lab/assets/").json()["assets"]],
                         [response.json()["id"]])

    def test_save_list_reopen_edit_immutable_snapshots_and_conflict(self):
        first = self.post(self.body()).json()
        self.assertEqual(self.client.get(f"/lab/assets/{first['id']}/").json(), first)
        changes = {**self.body(), "expected_revision": 1, "cost": "1.01", "reference": "TEST-REVISED-REF"}
        second = self.put(first["id"], changes).json()
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["review"]["proposed_cost"], "1.01")
        self.assertEqual(second["history"][0]["snapshot"]["reference"], first["reference"])
        self.assertEqual(second["history"][0]["snapshot"]["review"]["proposed_cost"], "0.10")
        self.assertEqual(self.put(first["id"], changes).status_code, 409)
        self.assertEqual(self.client.get(f"/lab/assets/{first['id']}/").json(), second)
        newer = self.post(self.body()).json()
        self.assertEqual([row["id"] for row in self.client.get("/lab/assets/").json()["assets"]],
                         [newer["id"], first["id"]])
        self.assertEqual(self.business_counts(), self.before)

    def test_failed_revision_insert_rolls_back_created_or_edited_header(self):
        with patch("finance_allocation_lab.assets.record_revision", side_effect=RuntimeError("forced insert failure")):
            with self.assertRaises(RuntimeError):
                save_asset(data=self.body(), entity=self.entity)
        self.assertEqual(FixedAssetDraft.objects.count(), 0)
        saved = save_asset(data=self.body(), entity=self.entity)
        original = snapshot(saved)
        with patch("finance_allocation_lab.assets.record_revision", side_effect=RuntimeError("forced insert failure")):
            with self.assertRaises(RuntimeError):
                save_asset(data={**self.body(), "expected_revision": 1, "cost": "1.23"},
                           entity=self.entity, draft_id=saved.pk)
        saved.refresh_from_db()
        self.assertEqual(snapshot(saved), original)
        self.assertEqual(saved.revisions.count(), 1)
        self.assertEqual(self.business_counts(), self.before)

    def test_database_enforces_history_immutability_and_synthetic_revision_constraints(self):
        saved = save_asset(data=self.body(), entity=self.entity)
        for operation in [
            lambda: FixedAssetRevision.objects.filter(draft=saved).update(snapshot={}),
            lambda: FixedAssetRevision.objects.filter(draft=saved).delete(),
            lambda: FixedAssetDraft.objects.filter(pk=saved.pk).update(is_synthetic=False),
            lambda: FixedAssetDraft.objects.filter(pk=saved.pk).update(revision=0),
        ]:
            with self.assertRaises(DatabaseError), transaction.atomic():
                operation()
        saved.refresh_from_db()
        self.assertTrue(saved.is_synthetic)
        self.assertEqual(saved.revision, 1)
        self.assertEqual(saved.revisions.count(), 1)

    def test_lab_access_rechecked_no_shared_routes_migrations_or_permissions(self):
        saved = self.post(self.body()).json()
        for flag in ["is_staff", "is_superuser", "is_active"]:
            before = getattr(self.actor, flag)
            setattr(self.actor, flag, False if flag == "is_active" else True)
            self.actor.save(update_fields=[flag])
            for response in [self.client.get("/lab/assets/"),
                             self.client.get(f"/lab/assets/{saved['id']}/"),
                             self.post(self.body()), self.put(saved["id"], {**self.body(), "expected_revision": 1})]:
                self.assertEqual(response.status_code, 403)
            setattr(self.actor, flag, before)
            self.actor.save(update_fields=[flag])
            self.client.force_login(self.actor)
        with override_settings(ROOT_URLCONF="foundation.urls"):
            for path in ["/lab/assets/", "/lab-assets-ui/"]:
                with self.assertRaises(Resolver404):
                    resolve(path)
        with override_settings(INSTALLED_APPS=[app for app in settings.INSTALLED_APPS if app != "finance_allocation_lab"]):
            with CaptureQueriesContext(connection) as queries:
                graph = MigrationLoader(None).graph
            self.assertFalse(any(node[0] == "finance_allocation_lab" for node in graph.nodes))
            self.assertEqual(len(queries), 0)
        self.assertEqual(FixedAssetDraft._meta.default_permissions, ())
        self.assertEqual(FixedAssetRevision._meta.default_permissions, ())
        self.client.logout()
        self.assertEqual(self.client.get("/lab/assets/").status_code, 403)
        self.assertEqual(self.post(self.body()).status_code, 403)

    def test_register_and_detail_reads_deterministic_and_no_business_writes(self):
        self.assertEqual(self.client.get("/lab/assets/").json(), {"assets": []})
        saved = self.post(self.body()).json()
        expected = {"assets": [{"id": saved["id"], "asset_identifier": saved["asset_identifier"],
                                "name": saved["name"], "revision": 1}]}
        with CaptureQueriesContext(connection) as queries:
            for _ in range(2):
                self.assertEqual(self.client.get("/lab/assets/").json(), expected)
                self.assertEqual(self.client.get(f"/lab/assets/{saved['id']}/").json(), saved)
        self.assertFalse(any(query["sql"].lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"} for query in queries))
        self.assertEqual(self.business_counts(), self.before)


@override_settings(**LAB)
class AssetConcurrencyTests(AssetFixture, TransactionTestCase):
    def setUp(self):
        self.setup_budget()

    def test_independent_connections_commit_one_complete_revision_without_lost_updates(self):
        saved = save_asset(data=self.body(), entity=self.entity)
        barrier = Barrier(2)
        def worker(index):
            connections.close_all()
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                data = {**self.body(), "expected_revision": 1, "cost": f"{index}.01",
                        "description": f"Explicit editor {index}", "location_details": f"Proposed text {index}"}
                barrier.wait(timeout=10)
                try:
                    save_asset(data=data, entity=self.entity, draft_id=saved.pk)
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
        self.assertEqual(saved.payload["cost"], f"{winner}.01")
        self.assertEqual(saved.payload["description"], f"Explicit editor {winner}")
        self.assertEqual(saved.payload["location_details"], f"Proposed text {winner}")
        self.assertEqual(saved.revisions.count(), 2)
        self.assertEqual(self.business_counts(), self.before)
