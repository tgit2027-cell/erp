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
from foundation.finance_drafts import FinanceDraftConflict
from foundation.models import LegalEntity
from .test_invoices import InvoiceFixture
from .invoices import save_invoice, snapshot as invoice_snapshot
from .supplier_adjustments import save_adjustment, snapshot
from .models import SupplierAdjustmentDraft, SupplierAdjustmentRevision, SupplierInvoiceDraft


class AdjustmentFixture(InvoiceFixture):
    def body(self):
        invoice = InvoiceFixture.body(self)
        return {
            "proposal_type": "credit", "supplier_reference": invoice["supplier_reference"],
            "document_reference": " TEST-CREDIT — مرجع ", "proposal_date": "2028-02-29",
            "reason": " Explicit synthetic review reason — نص ", "linked_invoice_id": None,
            "currency": "QAR", "lines": invoice["lines"],
        }

    def post(self, body):
        return self.client.post("/lab/adjustments/", json.dumps(body), content_type="application/json")

    def put(self, pk, body):
        return self.client.put(f"/lab/adjustments/{pk}/", json.dumps(body), content_type="application/json")

    def invoice(self):
        return save_invoice(data=InvoiceFixture.body(self), entity=self.entity)


@override_settings(**LAB)
class SupplierAdjustmentTests(AdjustmentFixture, TestCase):
    def setUp(self):
        self.setup_budget()
        self.assertEqual(connection.vendor, "postgresql")

    def test_credit_debit_exact_unsigned_totals_and_no_operational_effect(self):
        for kind in ("credit", "debit"):
            result = self.post({**self.body(), "proposal_type": kind})
            self.assertEqual(result.status_code, 201, result.content)
            row = result.json()
            self.assertEqual(row["review"]["total"], "0.30")
            self.assertEqual(row["lines"], self.body()["lines"])
            self.assertEqual(row["reason"], self.body()["reason"])
            self.assertEqual(row["review"]["amount_basis"], "explicit_non_negative_magnitudes")
            self.assertTrue(row["review"]["proposed"])
            for field in ("issued_credit_note", "settlement", "operational_payable_adjustment", "approved", "posted"):
                self.assertIs(row["review"][field], False)
            self.assertIn("no signed payable effect", row["review"]["interpretation"])
            self.assertIsNone(row["linked_invoice"])
        for value, expected in (("0", "0.00"), ("999999999999.99", "1999999999999.98")):
            body = self.body()
            for line in body["lines"]:
                line["amount"] = value
            self.assertEqual(self.post(body).json()["review"]["total"], expected)
        self.assertEqual(self.business_counts(), self.before)
        self.assertEqual(SupplierInvoiceDraft.objects.count(), 0)

    def test_optional_explicit_link_records_invoice_revision_without_modifying_invoice(self):
        invoice = self.invoice()
        before = invoice_snapshot(invoice)
        invoice_history = list(invoice.revisions.values())
        body = {**self.body(), "linked_invoice_id": invoice.pk,
                "supplier_reference": self.body()["supplier_reference"].strip()}
        saved = self.post(body)
        self.assertEqual(saved.status_code, 201, saved.content)
        self.assertEqual(saved.json()["linked_invoice"], {
            "id": invoice.pk, "revision": 1, "supplier_reference": invoice.payload["supplier_reference"],
            "invoice_reference": invoice.payload["invoice_reference"],
        })
        invoice.refresh_from_db()
        self.assertEqual(invoice_snapshot(invoice), before)
        self.assertEqual(list(invoice.revisions.values()), invoice_history)
        self.assertEqual(self.business_counts(), self.before)

    def test_supplier_entity_missing_and_ambiguous_link_ids_rejected_without_writes(self):
        invoice = self.invoice()
        other = LegalEntity.objects.create(code="TEST-AP-LINK-OTHER", name="Synthetic other entity")
        foreign = SupplierInvoiceDraft.objects.create(legal_entity=other, payload=InvoiceFixture.body(self))
        cases = [
            {**self.body(), "linked_invoice_id": invoice.pk, "supplier_reference": "TEST-DIFFERENT"},
            {**self.body(), "linked_invoice_id": foreign.pk},
        ] + [{**self.body(), "linked_invoice_id": value} for value in
             (True, False, "1", 0, -1, 999999999, [invoice.pk])]
        for body in cases:
            result = self.post(body)
            self.assertEqual(result.status_code, 400, result.content)
        self.assertEqual(SupplierAdjustmentDraft.objects.count(), 0)
        self.assertEqual(SupplierAdjustmentRevision.objects.count(), 0)
        self.assertEqual(invoice.revisions.count(), 1)

    def test_dates_types_money_reason_and_unapproved_fields_rejected_atomically(self):
        cases = []
        for key, values in {
            "proposal_type": ["invoice", "", None, True],
            "proposal_date": ["2027-02-29", "2028-2-1", "", None],
            "reason": ["", " ", None, "x" * 2001],
            "supplier_reference": ["", None], "document_reference": ["", None, "x" * 201],
            "currency": ["USD", None], "lines": [[], {}, self.body()["lines"] * 101],
        }.items():
            cases.extend({**self.body(), key: value} for value in values)
        for value in ("-0.01", "-0", "0.001", "NaN", "1e2", 0.1, True, "1000000000000"):
            body = self.body()
            body["lines"][1]["amount"] = value
            cases.append(body)
        for field in ("tax", "bank_details", "approved", "posted", "settlement", "signed_amount",
                      "supplier_id", "review", "linked_invoice", "history"):
            cases.append({**self.body(), field: True})
        for body in cases:
            response = self.post(body)
            self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(SupplierAdjustmentDraft.objects.count(), 0)
        self.assertEqual(SupplierAdjustmentRevision.objects.count(), 0)
        self.assertEqual(self.business_counts(), self.before)

    def test_saved_account_unit_dimension_validation_remains_entity_scoped(self):
        for key, values in {
            "account_id": [True, None, "1", -1, 999999999],
            "business_unit_id": [True, "1", -1, 999999999],
            "dimension_value_ids": [[True], ["1"], [999999999], [self.value.pk, self.value.pk]],
        }.items():
            for value in values:
                body = self.body()
                body["lines"][1][key] = value
                self.assertEqual(self.post(body).status_code, 400)
        # Existing invoice regression suite also exercises foreign-entity refs
        # through precisely the same reused resolver.
        self.assertEqual(SupplierAdjustmentDraft.objects.count(), 0)
        self.assertEqual(self.business_counts(), self.before)

    def test_list_reopen_revision_conflict_and_explicit_link_removal(self):
        invoice = self.invoice()
        original = self.post({**self.body(), "linked_invoice_id": invoice.pk}).json()
        changed = {**self.body(), "proposal_type": "debit", "document_reference": "TEST-EDIT",
                   "expected_revision": 1}
        updated = self.put(original["id"], changed)
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["revision"], 2)
        self.assertIsNone(updated.json()["linked_invoice"])
        self.assertIsNone(updated.json()["linked_invoice_id"])
        self.assertEqual(updated.json()["history"][0]["snapshot"]["linked_invoice"]["revision"], 1)
        self.assertEqual(self.put(original["id"], changed).status_code, 409)
        current = self.client.get(f"/lab/adjustments/{original['id']}/").json()
        self.assertEqual(current, updated.json())
        self.assertEqual([h["snapshot"]["proposal_type"] for h in current["history"]], ["credit", "debit"])
        self.assertEqual(self.client.get("/lab/adjustments/").json()["adjustments"], [{
            "id": original["id"], "revision": 2, "proposal_type": "debit",
            "document_reference": "TEST-EDIT", "supplier_reference": self.body()["supplier_reference"],
        }])
        for expected in (None, True, "2", 0):
            self.assertEqual(self.put(original["id"], {**self.body(), "expected_revision": expected}).status_code, 400)

    def test_link_is_saved_revision_evidence_and_revalidated_against_current_invoice_on_edit(self):
        invoice = self.invoice()
        saved = self.post({**self.body(), "linked_invoice_id": invoice.pk}).json()
        edited = {**InvoiceFixture.body(self), "supplier_reference": "TEST-NEW-SUPPLIER", "expected_revision": 1}
        save_invoice(data=edited, entity=self.entity, draft_id=invoice.pk)
        unchanged = self.client.get(f"/lab/adjustments/{saved['id']}/").json()
        self.assertEqual(unchanged["linked_invoice"]["revision"], 1)
        self.assertEqual(unchanged["history"], saved["history"])
        failed = self.put(saved["id"], {**self.body(), "linked_invoice_id": invoice.pk, "expected_revision": 1})
        self.assertEqual(failed.status_code, 400)
        self.assertEqual(self.client.get(f"/lab/adjustments/{saved['id']}/").json(), unchanged)
        self.assertEqual(self.put(saved["id"], {**self.body(), "linked_invoice_id": invoice.pk,
            "supplier_reference": "TEST-NEW-SUPPLIER", "expected_revision": 1}).status_code, 200)
        invoice.refresh_from_db()
        self.assertEqual(invoice.revision, 2)

    def test_history_insert_failures_rollback_and_revision_update_delete_are_blocked(self):
        with patch("finance_allocation_lab.supplier_adjustments.record_revision", side_effect=RuntimeError("forced")):
            with self.assertRaises(RuntimeError):
                save_adjustment(data=self.body(), entity=self.entity)
        self.assertEqual(SupplierAdjustmentDraft.objects.count(), 0)
        saved = save_adjustment(data=self.body(), entity=self.entity)
        before = snapshot(saved)
        with patch("finance_allocation_lab.supplier_adjustments.record_revision", side_effect=RuntimeError("forced")):
            with self.assertRaises(RuntimeError):
                save_adjustment(data={**self.body(), "reason": "TEST-NOT-COMMITTED", "expected_revision": 1},
                                entity=self.entity, draft_id=saved.pk)
        saved.refresh_from_db()
        self.assertEqual(snapshot(saved), before)
        for operation in ("UPDATE", "DELETE"):
            with self.assertRaises(DatabaseError), transaction.atomic():
                if operation == "UPDATE":
                    SupplierAdjustmentRevision.objects.filter(draft=saved).update(snapshot={})
                else:
                    SupplierAdjustmentRevision.objects.filter(draft=saved).delete()
        self.assertEqual(saved.revisions.count(), 1)

    def test_lab_guard_rechecked_and_shared_routes_schema_and_permissions_unchanged(self):
        saved = self.post(self.body()).json()
        other = LegalEntity.objects.create(code="TEST-AP-ADJ-OTHER", name="Synthetic other")
        foreign = SupplierAdjustmentDraft.objects.create(legal_entity=other, payload=self.body())
        self.assertEqual(self.client.get(f"/lab/adjustments/{foreign.pk}/").status_code, 404)
        self.assertEqual(len(self.client.get("/lab/adjustments/").json()["adjustments"]), 1)
        for flag in ("is_staff", "is_superuser"):
            setattr(self.actor, flag, True)
            self.actor.save(update_fields=[flag])
            self.assertEqual(self.client.get("/lab/adjustments/").status_code, 403)
            setattr(self.actor, flag, False)
            self.actor.save(update_fields=[flag])
        self.actor.is_active = False
        self.actor.save(update_fields=["is_active"])
        self.assertEqual(self.put(saved["id"], {**self.body(), "expected_revision": 1}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.post(self.body()).status_code, 403)
        with override_settings(ROOT_URLCONF="foundation.urls"):
            for path in ("/lab/adjustments/", "/lab-adjustments-ui/"):
                with self.assertRaises(Resolver404):
                    resolve(path)
        from config import settings as normal
        with override_settings(INSTALLED_APPS=normal.INSTALLED_APPS):
            self.assertFalse(any(app == "finance_allocation_lab" for app, _ in MigrationLoader(connection).graph.nodes))
        self.assertEqual(SupplierAdjustmentDraft._meta.default_permissions, ())
        self.assertEqual(SupplierAdjustmentRevision._meta.default_permissions, ())

    def test_deterministic_redacted_gets_no_business_writes(self):
        saved = self.post(self.body()).json()
        self.assertEqual(set(saved), {
            *self.body(), "id", "revision", "review", "linked_invoice", "history",
        })
        counts = (self.business_counts(), SupplierAdjustmentDraft.objects.count(), SupplierAdjustmentRevision.objects.count())
        with CaptureQueriesContext(connection) as queries:
            for _ in range(2):
                self.assertEqual(self.client.get(f"/lab/adjustments/{saved['id']}/").json(), saved)
                self.assertEqual(len(self.client.get("/lab/adjustments/").json()["adjustments"]), 1)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertEqual(counts, (self.business_counts(), SupplierAdjustmentDraft.objects.count(), SupplierAdjustmentRevision.objects.count()))


@override_settings(**LAB)
class SupplierAdjustmentConcurrencyTests(AdjustmentFixture, TransactionTestCase):
    def setUp(self):
        self.setup_budget()

    def test_independent_connections_commit_one_whole_revision_and_one_conflict(self):
        saved = save_adjustment(data=self.body(), entity=self.entity)
        barrier = Barrier(2)

        def worker(index):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                body = copy.deepcopy(self.body())
                body.update(expected_revision=1, reason=f"TEST-EDITOR-{index}")
                body["lines"][0]["amount"] = f"{index}.01"
                barrier.wait(timeout=10)
                try:
                    save_adjustment(data=body, entity=self.entity, draft_id=saved.pk)
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
        self.assertEqual(saved.payload["reason"], f"TEST-EDITOR-{winner}")
        self.assertEqual(saved.payload["lines"][0]["amount"], f"{winner}.01")
        self.assertEqual(saved.revision, 2)
        self.assertEqual(saved.revisions.count(), 2)
        self.assertEqual(self.business_counts(), self.before)
