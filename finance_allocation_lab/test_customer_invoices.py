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
from foundation.models import LegalEntity, BusinessUnit
from .test_invoices import InvoiceFixture
from .customer_invoices import save_invoice, snapshot
from .models import CustomerInvoiceDraft, CustomerInvoiceRevision, SupplierInvoiceDraft


class CustomerInvoiceFixture(InvoiceFixture):
    def body(self):
        body = InvoiceFixture.body(self)
        body["customer_reference"] = body.pop("supplier_reference")
        body["customer_details"] = " TEST customer details — بيانات "
        return body

    def post(self, body):
        return self.client.post("/lab/customer-invoices/", json.dumps(body), content_type="application/json")

    def put(self, pk, body):
        return self.client.put(f"/lab/customer-invoices/{pk}/", json.dumps(body), content_type="application/json")


@override_settings(**LAB)
class CustomerInvoiceTests(CustomerInvoiceFixture, TestCase):
    def setUp(self):
        self.setup_budget()
        self.assertEqual(connection.vendor, "postgresql")

    def test_exact_totals_explicit_dates_raw_customer_details_and_non_operational_review(self):
        response = self.post(self.body())
        self.assertEqual(response.status_code, 201, response.content)
        row = response.json()
        self.assertEqual(row["review"], {
            "currency": "QAR", "total": "0.30", "proposed": True,
            "issued_customer_invoice": False, "operational_receivable": False,
            "approved": False, "posted": False,
        })
        for key in ("customer_reference", "customer_details", "lines"):
            self.assertEqual(row[key], self.body()[key])
        self.assertIsNone(row["due_date"])
        for value, expected in (("0", "0.00"), ("999999999999.99", "1999999999999.98")):
            body = {**self.body(), "customer_details": "", "due_date": "2028-02-01"}
            for line in body["lines"]:
                line["amount"] = value
            saved = self.post(body)
            self.assertEqual(saved.status_code, 201, saved.content)
            self.assertEqual(saved.json()["review"]["total"], expected)
            self.assertEqual(saved.json()["customer_details"], "")
            # Optional due date is explicit, not a derived payment term.
            self.assertEqual(saved.json()["due_date"], "2028-02-01")
        self.assertEqual(self.business_counts(), self.before)
        self.assertEqual(SupplierInvoiceDraft.objects.count(), 0)

    def test_duplicate_advisory_is_scoped_case_sensitive_current_and_separate_from_supplier_drafts(self):
        # An AP proposal with equal references must not count as an AR example.
        SupplierInvoiceDraft.objects.create(legal_entity=self.entity, payload=InvoiceFixture.body(self))
        first = self.post(self.body()).json()
        self.assertFalse(first["duplicate_warning"]["possible_duplicate"])
        body = {**self.body(), "customer_reference": self.body()["customer_reference"].strip(),
                "invoice_reference": self.body()["invoice_reference"].strip()}
        second = self.post(body).json()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["duplicate_warning"], {
            "possible_duplicate": True, "matches": [{"id": first["id"], "revision": 1}]})
        first_again = self.client.get(f"/lab/customer-invoices/{first['id']}/").json()
        self.assertEqual(first_again["duplicate_warning"]["matches"], [{"id": second["id"], "revision": 1}])
        self.assertNotIn("duplicate_warning", first_again["history"][0]["snapshot"])
        body["customer_reference"] = body["customer_reference"].lower()
        lower = self.post(body).json()
        self.assertFalse(lower["duplicate_warning"]["possible_duplicate"])
        changed = self.put(second["id"], {**self.body(), "invoice_reference": "TEST-CHANGED", "expected_revision": 1})
        self.assertEqual(changed.status_code, 200)
        other = LegalEntity.objects.create(code="TEST-AR-DUP-OTHER", name="Synthetic other")
        CustomerInvoiceDraft.objects.create(legal_entity=other, payload=self.body())
        self.assertFalse(self.client.get(f"/lab/customer-invoices/{first['id']}/").json()["duplicate_warning"]["possible_duplicate"])
        self.assertEqual(CustomerInvoiceDraft.objects.count(), 4)

    def test_invalid_dates_money_customer_fields_and_unapproved_inputs_create_nothing(self):
        cases = []
        for key, values in {
            "customer_reference": ["", " ", None, "x" * 201],
            "customer_details": [None, True, "x" * 2001],
            "invoice_reference": ["", None, "x" * 201],
            "invoice_date": ["2027-02-29", "0000-01-01", "2028-2-1", ""],
            "due_date": ["", False, "2027-02-29"], "currency": ["USD", None],
            "lines": [[], {}, self.body()["lines"] * 101],
        }.items():
            cases.extend({**self.body(), key: value} for value in values)
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
        for field in ("customer_id", "supplier_reference", "credit_limit", "tax", "payment_terms",
                      "approved", "posted", "receivable_balance", "issued", "export", "review", "history"):
            cases.append({**self.body(), field: True})
        for body in cases:
            response = self.post(body)
            self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(CustomerInvoiceDraft.objects.count(), 0)
        self.assertEqual(CustomerInvoiceRevision.objects.count(), 0)
        self.assertEqual(self.business_counts(), self.before)

    def test_account_unit_dimension_entity_and_unavailable_record_boundaries(self):
        other = LegalEntity.objects.create(code="TEST-AR-OTHER", name="Synthetic other")
        account = create_finance_draft_account(legal_entity=other, code="TEST-DRAFT-ACCT-AR-OTHER", name_en="Other draft")
        unit = BusinessUnit.objects.create(legal_entity=other, code="TEST-AR-UNIT", name="Other unit")
        dimension = create_finance_draft_dimension(legal_entity=other, code="TEST-DRAFT-DIM-AR", label_en="Other")
        value = create_finance_draft_dimension_value(dimension=dimension, code="TEST-DRAFT-VAL-AR", label_en="Other")
        for key, val in [("account_id", account.pk), ("business_unit_id", unit.pk),
                         ("dimension_value_ids", [value.pk]), ("account_id", 99999999),
                         ("business_unit_id", 99999999), ("dimension_value_ids", [99999999])]:
            body = self.body()
            body["lines"][1][key] = val
            self.assertEqual(self.post(body).status_code, 400)
        foreign = CustomerInvoiceDraft.objects.create(legal_entity=other, payload=self.body())
        self.assertEqual(self.client.get(f"/lab/customer-invoices/{foreign.pk}/").status_code, 404)
        self.assertEqual(self.put(foreign.pk, {**self.body(), "expected_revision": 1}).status_code, 404)
        self.assertEqual(self.client.get("/lab/customer-invoices/").json()["customer_invoices"], [])

    def test_list_reopen_edit_revision_history_and_conflict_preserve_saved_work(self):
        first = self.post(self.body()).json()
        self.assertEqual(self.client.get(f"/lab/customer-invoices/{first['id']}/").json(), first)
        body = {**self.body(), "customer_details": "TEST revised details", "due_date": "2028-03-20", "expected_revision": 1}
        body["lines"][0]["amount"] = "1.01"
        second = self.put(first["id"], body).json()
        self.assertEqual(second["revision"], 2)
        self.assertEqual([h["snapshot"]["review"]["total"] for h in second["history"]], ["0.30", "1.21"])
        self.assertEqual(second["history"][0]["snapshot"]["customer_details"], self.body()["customer_details"])
        self.assertEqual(second["customer_details"], "TEST revised details")
        self.assertEqual(self.put(first["id"], body).status_code, 409)
        self.assertEqual(self.client.get(f"/lab/customer-invoices/{first['id']}/").json(), second)
        newest = self.post({**self.body(), "invoice_reference": "TEST-NEW"}).json()
        self.assertEqual([r["id"] for r in self.client.get("/lab/customer-invoices/").json()["customer_invoices"]],
                         [newest["id"], first["id"]])
        self.assertEqual(self.business_counts(), self.before)

    def test_invalid_revision_and_invalid_edit_preserve_whole_saved_state(self):
        self.assertEqual(self.post({**self.body(), "expected_revision": 1}).status_code, 400)
        first = self.post(self.body()).json()
        for expected in (None, True, "1", 0):
            self.assertEqual(self.put(first["id"], {**self.body(), "expected_revision": expected}).status_code, 400)
        body = {**self.body(), "expected_revision": 1}
        body["lines"][1]["amount"] = "0.001"
        self.assertEqual(self.put(first["id"], body).status_code, 400)
        self.assertEqual(self.client.get(f"/lab/customer-invoices/{first['id']}/").json(), first)

    def test_atomic_history_failures_and_database_immutable_revision_guards(self):
        with patch("finance_allocation_lab.customer_invoices.record_revision", side_effect=RuntimeError("forced")):
            with self.assertRaises(RuntimeError):
                save_invoice(data=self.body(), entity=self.entity)
        self.assertEqual(CustomerInvoiceDraft.objects.count(), 0)
        row = save_invoice(data=self.body(), entity=self.entity)
        original = snapshot(row)
        with patch("finance_allocation_lab.customer_invoices.record_revision", side_effect=RuntimeError("forced")):
            with self.assertRaises(RuntimeError):
                save_invoice(data={**self.body(), "expected_revision": 1, "invoice_reference": "TEST-FAIL"},
                             entity=self.entity, draft_id=row.pk)
        row.refresh_from_db()
        self.assertEqual(snapshot(row), original)
        for operation in ("UPDATE", "DELETE"):
            with self.assertRaises(DatabaseError), transaction.atomic():
                if operation == "UPDATE":
                    CustomerInvoiceRevision.objects.filter(draft=row).update(snapshot={})
                else:
                    CustomerInvoiceRevision.objects.filter(draft=row).delete()
        self.assertEqual(row.revisions.count(), 1)

    def test_lab_access_rechecked_no_shared_routes_schema_permissions_or_output_rights(self):
        first = self.post(self.body()).json()
        for flag in ("is_staff", "is_superuser"):
            setattr(self.actor, flag, True)
            self.actor.save(update_fields=[flag])
            self.assertEqual(self.client.get("/lab/customer-invoices/").status_code, 403)
            setattr(self.actor, flag, False)
            self.actor.save(update_fields=[flag])
        self.actor.is_active = False
        self.actor.save(update_fields=["is_active"])
        self.assertEqual(self.put(first["id"], {**self.body(), "expected_revision": 1}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.post(self.body()).status_code, 403)
        for path in ("/lab/customer-invoices/", "/lab-customer-invoices-ui/"):
            with override_settings(ROOT_URLCONF="foundation.urls"), self.assertRaises(Resolver404):
                resolve(path)
        for path in ("/lab/customer-invoices/export/", "/lab/customer-invoices/print/"):
            with self.assertRaises(Resolver404):
                resolve(path)
        from config import settings as normal
        with override_settings(INSTALLED_APPS=normal.INSTALLED_APPS):
            self.assertFalse(any(app == "finance_allocation_lab" for app, _ in MigrationLoader(connection).graph.nodes))
        self.assertEqual(CustomerInvoiceDraft._meta.default_permissions, ())
        self.assertEqual(CustomerInvoiceRevision._meta.default_permissions, ())

    def test_gets_deterministic_allowlisted_and_without_business_writes(self):
        first = self.post(self.body()).json()
        self.assertEqual(set(first), {*self.body(), "id", "revision", "review", "history", "duplicate_warning"})
        counts = (self.business_counts(), CustomerInvoiceDraft.objects.count(), CustomerInvoiceRevision.objects.count())
        with CaptureQueriesContext(connection) as queries:
            for _ in range(2):
                self.assertEqual(self.client.get(f"/lab/customer-invoices/{first['id']}/").json(), first)
                self.assertEqual(len(self.client.get("/lab/customer-invoices/").json()["customer_invoices"]), 1)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertEqual(counts, (self.business_counts(), CustomerInvoiceDraft.objects.count(), CustomerInvoiceRevision.objects.count()))


@override_settings(**LAB)
class CustomerInvoiceConcurrencyTests(CustomerInvoiceFixture, TransactionTestCase):
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
                body.update(expected_revision=1, customer_details=f"TEST-EDITOR-{index}")
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
        self.assertCountEqual([r[1] for r in results], ["saved", "conflict"])
        winner = next(r[2] for r in results if r[1] == "saved")
        saved.refresh_from_db()
        self.assertEqual(saved.payload["customer_details"], f"TEST-EDITOR-{winner}")
        self.assertEqual(saved.payload["lines"][0]["amount"], f"{winner}.01")
        self.assertEqual(saved.revision, 2)
        self.assertEqual(saved.revisions.count(), 2)
        self.assertEqual(self.business_counts(), self.before)
