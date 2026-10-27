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
from foundation.models import BusinessUnit, LegalEntity
from .test_budgets import BudgetFixture
from .invoices import save_invoice, snapshot
from .models import SupplierInvoiceDraft, SupplierInvoiceRevision


class InvoiceFixture(BudgetFixture):
    def body(self):
        return {
            "supplier_reference": " TEST-SUPPLIER — مورد ",
            "invoice_reference": " TEST-INVOICE — مرجع ",
            "invoice_date": "2028-02-29", "due_date": None, "currency": "QAR",
            "lines": [{
                "description": f" Synthetic proposal line {index} — نص ", "amount": value,
                "account_id": self.account.pk, "business_unit_id": self.unit.pk,
                "dimension_value_ids": [self.value.pk],
            } for index, value in enumerate(("0.10", "0.20"), 1)],
        }

    def post(self, body):
        return self.client.post("/lab/invoices/", json.dumps(body), content_type="application/json")

    def put(self, pk, body):
        return self.client.put(f"/lab/invoices/{pk}/", json.dumps(body), content_type="application/json")


@override_settings(**LAB)
class InvoiceTests(InvoiceFixture, TestCase):
    def setUp(self):
        self.setup_budget()
        self.assertEqual(connection.vendor, "postgresql")

    def test_exact_totals_optional_due_date_raw_references_and_no_payables(self):
        response = self.post(self.body())
        self.assertEqual(response.status_code, 201, response.content)
        saved = response.json()
        self.assertEqual(saved["review"], {
            "currency": "QAR", "total": "0.30", "proposed": True,
            "operational_payable": False, "approved": False, "posted": False,
        })
        self.assertIsNone(saved["due_date"])
        self.assertEqual(saved["supplier_reference"], self.body()["supplier_reference"])
        self.assertEqual(saved["lines"], self.body()["lines"])
        for value, expected in [("0", "0.00"), ("999999999999.99", "1999999999999.98")]:
            body = self.body()
            for line in body["lines"]:
                line["amount"] = value
            # Date is an explicit proposal, not an invented due-term restriction.
            body["due_date"] = "2028-02-01"
            result = self.post(body)
            self.assertEqual(result.status_code, 201, result.content)
            self.assertEqual(result.json()["review"]["total"], expected)
            self.assertEqual(result.json()["due_date"], "2028-02-01")
        self.assertEqual(self.business_counts(), self.before)

    def test_duplicate_reference_advisory_never_merges_or_rejects_and_is_entity_scoped(self):
        first = self.post(self.body()).json()
        self.assertFalse(first["duplicate_warning"]["possible_duplicate"])
        body = self.body()
        body["supplier_reference"] = body["supplier_reference"].strip()
        body["invoice_reference"] = body["invoice_reference"].strip()
        second = self.post(body).json()
        self.assertEqual(second["duplicate_warning"], {
            "possible_duplicate": True, "matches": [{"id": first["id"], "revision": 1}]})
        self.assertEqual(SupplierInvoiceDraft.objects.count(), 2)
        old = self.client.get(f"/lab/invoices/{first['id']}/").json()
        self.assertEqual(old["duplicate_warning"]["matches"], [{"id": second["id"], "revision": 1}])
        self.assertNotIn("duplicate_warning", old["history"][0]["snapshot"])
        changed = self.put(second["id"], {**body, "invoice_reference": "TEST-OTHER", "expected_revision": 1}).json()
        self.assertFalse(changed["duplicate_warning"]["possible_duplicate"])
        self.assertFalse(self.client.get(f"/lab/invoices/{first['id']}/").json()["duplicate_warning"]["possible_duplicate"])
        other = LegalEntity.objects.create(code="TEST-AP-DUP-OTHER", name="Synthetic other")
        SupplierInvoiceDraft.objects.create(legal_entity=other, payload=self.body())
        self.assertFalse(self.client.get(f"/lab/invoices/{first['id']}/").json()["duplicate_warning"]["possible_duplicate"])
        self.assertEqual(self.business_counts(), self.before)

    def test_invalid_dates_money_fields_and_unapproved_inputs_write_nothing(self):
        cases = []
        for key, values in {
            "supplier_reference": ["", " ", None, "x" * 201],
            "invoice_reference": ["", None, "x" * 201],
            "invoice_date": ["2027-02-29", "0000-01-01", "2028-2-1", ""],
            "due_date": ["", "2027-02-29", 123, False], "currency": ["USD", None],
            "lines": [[], {}, self.body()["lines"] * 101],
        }.items():
            for value in values:
                cases.append({**self.body(), key: value})
        for key, values in {
            "amount": ["-0.01", "0.001", "NaN", "1e2", True, 0.1, "1000000000000"],
            "description": ["", None, "x" * 2001],
            "account_id": [True, "1", None, -1],
            "business_unit_id": [True, "1", -1],
            "dimension_value_ids": [[True], ["1"], [self.value.pk, self.value.pk]],
        }.items():
            for value in values:
                body = self.body()
                body["lines"][1][key] = value
                cases.append(body)
        for field in ("bank_details", "tax", "payment_terms", "approved", "posted", "supplier_id",
                      "matching_tolerance", "review", "history", "duplicate_warning"):
            cases.append({**self.body(), field: True})
        for body in cases:
            with self.subTest(body=body):
                response = self.post(body)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("detail", response.json())
        self.assertEqual(SupplierInvoiceDraft.objects.count(), 0)
        self.assertEqual(SupplierInvoiceRevision.objects.count(), 0)
        self.assertEqual(self.business_counts(), self.before)

    def test_entity_account_unit_dimension_and_missing_reference_boundaries(self):
        other = LegalEntity.objects.create(code="TEST-AP-OTHER", name="Synthetic other")
        account = create_finance_draft_account(legal_entity=other, code="TEST-DRAFT-ACCT-AP-OTHER", name_en="Other draft")
        unit = BusinessUnit.objects.create(legal_entity=other, code="TEST-AP-OTHER", name="Other unit")
        dimension = create_finance_draft_dimension(legal_entity=other, code="TEST-DRAFT-DIM-AP", label_en="Other")
        value = create_finance_draft_dimension_value(dimension=dimension, code="TEST-DRAFT-VAL-AP", label_en="Other")
        for key, val in [("account_id", account.pk), ("business_unit_id", unit.pk), ("dimension_value_ids", [value.pk])]:
            body = self.body()
            body["lines"][1][key] = val
            self.assertEqual(self.post(body).status_code, 400)
        for key, val in [("account_id", 99999999), ("business_unit_id", 99999999),
                         ("dimension_value_ids", [99999999])]:
            body = self.body()
            body["lines"][0][key] = val
            self.assertEqual(self.post(body).status_code, 400)
        foreign = SupplierInvoiceDraft.objects.create(legal_entity=other, payload=self.body())
        self.assertEqual(self.client.get(f"/lab/invoices/{foreign.pk}/").status_code, 404)
        self.assertEqual(self.put(foreign.pk, {**self.body(), "expected_revision": 1}).status_code, 404)
        self.assertEqual(self.client.get("/lab/invoices/").json()["invoices"], [])

    def test_list_reopen_edit_revisions_and_stale_edit_do_not_lose_saved_work(self):
        first = self.post(self.body()).json()
        self.assertEqual(self.client.get(f"/lab/invoices/{first['id']}/").json(), first)
        body = self.body()
        body.update(expected_revision=1, due_date="2028-03-20")
        body["lines"][0]["amount"] = "1.01"
        second = self.put(first["id"], body).json()
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["review"]["total"], "1.21")
        self.assertEqual([h["snapshot"]["review"]["total"] for h in second["history"]], ["0.30", "1.21"])
        self.assertEqual(self.put(first["id"], body).status_code, 409)
        self.assertEqual(self.client.get(f"/lab/invoices/{first['id']}/").json(), second)
        newest = self.post({**self.body(), "invoice_reference": "TEST-NEW"}).json()
        self.assertEqual([row["id"] for row in self.client.get("/lab/invoices/").json()["invoices"]],
                         [newest["id"], first["id"]])
        self.assertEqual(self.business_counts(), self.before)

    def test_invalid_revision_and_invalid_edit_preserve_entire_saved_state(self):
        self.assertEqual(self.post({**self.body(), "expected_revision": 1}).status_code, 400)
        first = self.post(self.body()).json()
        for expected in (None, True, "1", 0):
            self.assertEqual(self.put(first["id"], {**self.body(), "expected_revision": expected}).status_code, 400)
        body = self.body()
        body["expected_revision"] = 1
        body["lines"][1]["amount"] = "0.001"
        self.assertEqual(self.put(first["id"], body).status_code, 400)
        self.assertEqual(self.client.get(f"/lab/invoices/{first['id']}/").json(), first)

    def test_atomic_history_failure_and_database_immutable_revision_guards(self):
        with patch("finance_allocation_lab.invoices.record_revision", side_effect=RuntimeError("forced")):
            with self.assertRaises(RuntimeError):
                save_invoice(data=self.body(), entity=self.entity)
        self.assertEqual(SupplierInvoiceDraft.objects.count(), 0)
        row = save_invoice(data=self.body(), entity=self.entity)
        original = snapshot(row)
        with patch("finance_allocation_lab.invoices.record_revision", side_effect=RuntimeError("forced")):
            with self.assertRaises(RuntimeError):
                save_invoice(data={**self.body(), "expected_revision": 1, "invoice_reference": "TEST-FAIL"},
                             entity=self.entity, draft_id=row.pk)
        row.refresh_from_db()
        self.assertEqual(snapshot(row), original)
        for operation in ("UPDATE", "DELETE"):
            with self.assertRaises(DatabaseError), transaction.atomic():
                if operation == "UPDATE":
                    SupplierInvoiceRevision.objects.filter(draft=row).update(snapshot={})
                else:
                    SupplierInvoiceRevision.objects.filter(draft=row).delete()
        self.assertEqual(row.revisions.count(), 1)

    def test_lab_access_rechecked_normal_routes_and_shared_migration_graph_unchanged(self):
        first = self.post(self.body()).json()
        for flag in ("is_staff", "is_superuser"):
            setattr(self.actor, flag, True)
            self.actor.save(update_fields=[flag])
            self.assertEqual(self.client.get("/lab/invoices/").status_code, 403)
            self.assertEqual(self.put(first["id"], {**self.body(), "expected_revision": 1}).status_code, 403)
            setattr(self.actor, flag, False)
            self.actor.save(update_fields=[flag])
        self.actor.is_active = False
        self.actor.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(f"/lab/invoices/{first['id']}/").status_code, 403)
        self.client.logout()
        self.assertEqual(self.post(self.body()).status_code, 403)
        for path in ("/lab/invoices/", "/lab-invoices-ui/"):
            with override_settings(ROOT_URLCONF="foundation.urls"), self.assertRaises(Resolver404):
                resolve(path)
        from config import settings as normal
        with override_settings(INSTALLED_APPS=normal.INSTALLED_APPS):
            self.assertFalse(any(app == "finance_allocation_lab" for app, _ in MigrationLoader(connection).graph.nodes))
        self.assertEqual(SupplierInvoiceDraft._meta.default_permissions, ())
        self.assertEqual(SupplierInvoiceRevision._meta.default_permissions, ())

    def test_gets_are_deterministic_no_writes_and_no_sensitive_response_fields(self):
        first = self.post(self.body()).json()
        self.assertEqual(set(first), {
            "id", "revision", "supplier_reference", "invoice_reference", "invoice_date", "due_date",
            "currency", "lines", "review", "history", "duplicate_warning",
        })
        counts = (self.business_counts(), SupplierInvoiceDraft.objects.count(), SupplierInvoiceRevision.objects.count())
        with CaptureQueriesContext(connection) as queries:
            for _ in range(2):
                self.assertEqual(self.client.get(f"/lab/invoices/{first['id']}/").json(), first)
                self.assertEqual(len(self.client.get("/lab/invoices/").json()["invoices"]), 1)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertEqual(counts, (self.business_counts(), SupplierInvoiceDraft.objects.count(), SupplierInvoiceRevision.objects.count()))


@override_settings(**LAB)
class InvoiceConcurrencyTests(InvoiceFixture, TransactionTestCase):
    def setUp(self):
        self.setup_budget()

    def test_independent_connections_commit_one_complete_revision_and_one_conflict(self):
        saved = save_invoice(data=self.body(), entity=self.entity)
        barrier = Barrier(2)

        def worker(index):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                body = copy.deepcopy(self.body())
                body.update(expected_revision=1, invoice_reference=f"TEST-EDITOR-{index}")
                body["lines"][0]["amount"] = f"{index}.01"
                barrier.wait(timeout=10)
                try:
                    save_invoice(data=body, entity=self.entity, draft_id=saved.pk)
                    return pid, "saved", index
                except FinanceDraftConflict:
                    return pid, "conflict", index
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, (1, 2)))
        self.assertNotEqual(results[0][0], results[1][0])
        self.assertCountEqual([row[1] for row in results], ["saved", "conflict"])
        winner = next(row[2] for row in results if row[1] == "saved")
        saved.refresh_from_db()
        self.assertEqual(saved.payload["invoice_reference"], f"TEST-EDITOR-{winner}")
        self.assertEqual(saved.payload["lines"][0]["amount"], f"{winner}.01")
        self.assertEqual(saved.revision, 2)
        self.assertEqual(saved.revisions.count(), 2)
        self.assertEqual(self.business_counts(), self.before)
