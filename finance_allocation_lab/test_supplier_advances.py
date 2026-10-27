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
    FinanceDraftConflict, create_finance_draft_account, create_finance_draft_dimension,
    create_finance_draft_dimension_value,
)
from foundation.models import LegalEntity, BusinessUnit, FinanceDraftAccount
from .test_invoices import InvoiceFixture
from .supplier_advances import save_advance, snapshot
from .models import SupplierAdvanceDraft, SupplierAdvanceRevision


class AdvanceFixture(InvoiceFixture):
    def body(self):
        return {
            "supplier_reference": " TEST-SUPPLIER-ADV — مرجع ",
            "document_reference": " TEST-ADVANCE — مرجع ",
            "proposed_date": "2028-02-29", "amount": "0.10",
            "purpose": " Explicit synthetic purpose — نص ", "currency": "QAR",
            "account_id": None, "business_unit_id": None, "dimension_value_ids": [],
        }

    def post(self, body):
        return self.client.post("/lab/supplier-advances/", json.dumps(body), content_type="application/json")

    def put(self, pk, body):
        return self.client.put(f"/lab/supplier-advances/{pk}/", json.dumps(body), content_type="application/json")


@override_settings(**LAB)
class SupplierAdvanceTests(AdvanceFixture, TestCase):
    def setUp(self):
        self.setup_budget()
        self.assertEqual(connection.vendor, "postgresql")

    def test_exact_amount_raw_text_optional_choices_and_unpaid_unapplied_review(self):
        before = FinanceDraftAccount.objects.count()
        for amount, canonical in (("0", "0.00"), ("0.10", "0.10"), ("12.3", "12.30"),
                                  ("999999999999.99", "999999999999.99")):
            response = self.post({**self.body(), "amount": amount})
            self.assertEqual(response.status_code, 201, response.content)
            row = response.json()
            self.assertEqual(row["amount"], canonical)
            self.assertEqual(row["review"], {
                "currency": "QAR", "total": canonical, "proposed": True,
                "unpaid": True, "unapplied": True, "actual_payment": False,
                "invoice_application": False, "operational_payable_change": False,
                "approved": False, "posted": False,
            })
            self.assertIsNone(row["account_id"])
            self.assertIsNone(row["business_unit_id"])
            self.assertEqual(row["dimension_value_ids"], [])
            self.assertEqual(row["purpose"], self.body()["purpose"])
            self.assertEqual(row["supplier_reference"], self.body()["supplier_reference"])
        self.assertEqual(FinanceDraftAccount.objects.count(), before)
        self.assertEqual(self.business_counts(), self.before)

    def test_invalid_decimal_inputs_never_create_partial_drafts(self):
        for value in ("-0", "-0.01", "0.001", "NaN", "Infinity", "1e2", "01.00",
                      "", None, True, 0.1, "1000000000000", {}, []):
            response = self.post({**self.body(), "amount": value})
            self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(SupplierAdvanceDraft.objects.count(), 0)
        self.assertEqual(SupplierAdvanceRevision.objects.count(), 0)

    def test_dates_required_text_and_forbidden_payment_inputs_are_rejected(self):
        cases = []
        for key, values in {
            "proposed_date": ["2027-02-29", "2028-2-1", "2028-02-29T00:00:00Z", "", None],
            "supplier_reference": ["", " ", None, "x" * 201],
            "document_reference": ["", None, "x" * 201],
            "purpose": ["", " ", None, "x" * 2001], "currency": ["USD", None],
        }.items():
            cases.extend({**self.body(), key: value} for value in values)
        for key in self.body():
            body = self.body()
            del body[key]
            cases.append(body)
        for key in ("bank_details", "supplier_id", "deposit_percentage", "terms", "approved", "posted",
                    "invoice_id", "tax", "paid", "applied", "review", "history", "account_mapping"):
            cases.append({**self.body(), key: True})
        for body in cases:
            result = self.post(body)
            self.assertEqual(result.status_code, 400, result.content)
        self.assertEqual(SupplierAdvanceDraft.objects.count(), 0)
        self.assertEqual(SupplierAdvanceRevision.objects.count(), 0)
        self.assertEqual(self.business_counts(), self.before)

    def test_optional_account_unit_dimension_references_validate_actual_entity(self):
        other = LegalEntity.objects.create(code="TEST-ADV-OTHER", name="Synthetic other")
        account = create_finance_draft_account(legal_entity=other, code="TEST-DRAFT-ACCT-ADV", name_en="Other")
        unit = BusinessUnit.objects.create(legal_entity=other, code="TEST-ADV-UNIT", name="Other")
        dimension = create_finance_draft_dimension(legal_entity=other, code="TEST-DRAFT-DIM-ADV", label_en="Other")
        value = create_finance_draft_dimension_value(dimension=dimension, code="TEST-DRAFT-VAL-ADV", label_en="Other")
        for key, values in {
            "account_id": [True, "1", 0, -1, 99999999, account.pk],
            "business_unit_id": [True, "1", 0, -1, 99999999, unit.pk],
            "dimension_value_ids": [None, {}, [True], ["1"], [99999999], [value.pk], [self.value.pk, self.value.pk]],
        }.items():
            for invalid in values:
                result = self.post({**self.body(), key: invalid})
                self.assertEqual(result.status_code, 400, result.content)
        self.assertEqual(SupplierAdvanceDraft.objects.count(), 0)
        selected = self.post({**self.body(), "account_id": self.account.pk,
                              "business_unit_id": self.unit.pk, "dimension_value_ids": [self.value.pk]})
        self.assertEqual(selected.status_code, 201, selected.content)
        self.assertEqual(selected.json()["dimension_value_ids"], [self.value.pk])

    def test_list_reopen_edit_clear_optional_choices_and_conflict_preserve_history(self):
        original = self.post({**self.body(), "account_id": self.account.pk,
                              "business_unit_id": self.unit.pk, "dimension_value_ids": [self.value.pk]}).json()
        changed = {**self.body(), "purpose": "Explicit revised purpose", "amount": "1.01", "expected_revision": 1}
        result = self.put(original["id"], changed)
        self.assertEqual(result.status_code, 200, result.content)
        row = result.json()
        self.assertEqual(row["revision"], 2)
        self.assertIsNone(row["account_id"])
        self.assertIsNone(row["business_unit_id"])
        self.assertEqual(row["dimension_value_ids"], [])
        self.assertEqual([h["snapshot"]["amount"] for h in row["history"]], ["0.10", "1.01"])
        self.assertEqual(row["history"][0]["snapshot"]["account_id"], self.account.pk)
        self.assertEqual(self.put(original["id"], changed).status_code, 409)
        self.assertEqual(self.client.get(f"/lab/supplier-advances/{row['id']}/").json(), row)
        self.assertEqual(self.client.get("/lab/supplier-advances/").json()["advances"], [{
            "id": row["id"], "revision": 2,
            **{key: row[key] for key in ("supplier_reference", "document_reference", "proposed_date", "amount")},
        }])
        for expected in (None, True, "2", 0):
            self.assertEqual(self.put(row["id"], {**self.body(), "expected_revision": expected}).status_code, 400)
        self.assertEqual(self.post({**self.body(), "expected_revision": 1}).status_code, 400)

    def test_current_inactive_unit_and_invalid_edit_preserve_saved_state(self):
        row = self.post(self.body()).json()
        self.unit.is_active = False
        self.unit.save(update_fields=["is_active"])
        result = self.put(row["id"], {**self.body(), "business_unit_id": self.unit.pk, "expected_revision": 1})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.client.get(f"/lab/supplier-advances/{row['id']}/").json(), row)
        self.assertEqual(self.put(row["id"], {**self.body(), "amount": "0.001", "expected_revision": 1}).status_code, 400)
        self.assertEqual(self.client.get(f"/lab/supplier-advances/{row['id']}/").json(), row)

    def test_history_failure_rolls_back_and_direct_revision_update_delete_blocked(self):
        with patch("finance_allocation_lab.supplier_advances.record_revision", side_effect=RuntimeError("forced")):
            with self.assertRaises(RuntimeError):
                save_advance(data=self.body(), entity=self.entity)
        self.assertEqual(SupplierAdvanceDraft.objects.count(), 0)
        saved = save_advance(data=self.body(), entity=self.entity)
        before = snapshot(saved)
        with patch("finance_allocation_lab.supplier_advances.record_revision", side_effect=RuntimeError("forced")):
            with self.assertRaises(RuntimeError):
                save_advance(data={**self.body(), "purpose": "TEST-NOT-COMMITTED", "expected_revision": 1},
                             entity=self.entity, draft_id=saved.pk)
        saved.refresh_from_db()
        self.assertEqual(snapshot(saved), before)
        for operation in ("UPDATE", "DELETE"):
            with self.assertRaises(DatabaseError), transaction.atomic():
                if operation == "UPDATE":
                    SupplierAdvanceRevision.objects.filter(draft=saved).update(snapshot={})
                else:
                    SupplierAdvanceRevision.objects.filter(draft=saved).delete()
        self.assertEqual(saved.revisions.count(), 1)

    def test_guard_entity_exclusion_and_no_shared_routes_schema_or_permissions(self):
        row = self.post(self.body()).json()
        other = LegalEntity.objects.create(code="TEST-ADV-ACCESS-OTHER", name="Synthetic other")
        foreign = SupplierAdvanceDraft.objects.create(legal_entity=other, payload=self.body())
        self.assertEqual(self.client.get(f"/lab/supplier-advances/{foreign.pk}/").status_code, 404)
        self.assertEqual(self.put(foreign.pk, {**self.body(), "expected_revision": 1}).status_code, 404)
        self.assertEqual(len(self.client.get("/lab/supplier-advances/").json()["advances"]), 1)
        for flag in ("is_staff", "is_superuser"):
            setattr(self.actor, flag, True)
            self.actor.save(update_fields=[flag])
            self.assertEqual(self.client.get("/lab/supplier-advances/").status_code, 403)
            setattr(self.actor, flag, False)
            self.actor.save(update_fields=[flag])
        self.actor.is_active = False
        self.actor.save(update_fields=["is_active"])
        self.assertEqual(self.put(row["id"], {**self.body(), "expected_revision": 1}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.post(self.body()).status_code, 403)
        with override_settings(ROOT_URLCONF="foundation.urls"):
            for path in ("/lab/supplier-advances/", "/lab-supplier-advances-ui/"):
                with self.assertRaises(Resolver404):
                    resolve(path)
        from config import settings as normal
        with override_settings(INSTALLED_APPS=normal.INSTALLED_APPS):
            self.assertFalse(any(app == "finance_allocation_lab" for app, _ in MigrationLoader(connection).graph.nodes))
        self.assertEqual(SupplierAdvanceDraft._meta.default_permissions, ())
        self.assertEqual(SupplierAdvanceRevision._meta.default_permissions, ())

    def test_deterministic_allowlisted_gets_create_no_business_records(self):
        row = self.post(self.body()).json()
        self.assertEqual(set(row), {*self.body(), "id", "revision", "review", "history"})
        counts = (self.business_counts(), SupplierAdvanceDraft.objects.count(), SupplierAdvanceRevision.objects.count())
        with CaptureQueriesContext(connection) as queries:
            for _ in range(2):
                self.assertEqual(self.client.get(f"/lab/supplier-advances/{row['id']}/").json(), row)
                self.assertEqual(len(self.client.get("/lab/supplier-advances/").json()["advances"]), 1)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertEqual(counts, (self.business_counts(), SupplierAdvanceDraft.objects.count(), SupplierAdvanceRevision.objects.count()))


@override_settings(**LAB)
class SupplierAdvanceConcurrencyTests(AdvanceFixture, TransactionTestCase):
    def setUp(self):
        self.setup_budget()

    def test_independent_connections_commit_one_whole_revision_and_one_conflict(self):
        saved = save_advance(data=self.body(), entity=self.entity)
        barrier = Barrier(2)

        def worker(index):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                body = copy.deepcopy(self.body())
                body.update(expected_revision=1, purpose=f"TEST-ADV-EDITOR-{index}", amount=f"{index}.01")
                barrier.wait(timeout=10)
                try:
                    save_advance(data=body, entity=self.entity, draft_id=saved.pk)
                    return pid, "saved", index
                except FinanceDraftConflict:
                    return pid, "conflict", index
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, (1, 2)))
        self.assertNotEqual(results[0][0], results[1][0])
        self.assertCountEqual([item[1] for item in results], ["saved", "conflict"])
        winner = next(item[2] for item in results if item[1] == "saved")
        saved.refresh_from_db()
        self.assertEqual(saved.payload["purpose"], f"TEST-ADV-EDITOR-{winner}")
        self.assertEqual(saved.payload["amount"], f"{winner}.01")
        self.assertEqual(saved.revision, 2)
        self.assertEqual(saved.revisions.count(), 2)
        self.assertEqual(self.business_counts(), self.before)
