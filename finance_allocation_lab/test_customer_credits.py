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
from .test_customer_invoices import CustomerInvoiceFixture
from .customer_invoices import save_invoice, snapshot as invoice_snapshot
from .customer_credits import save_credit, snapshot
from .models import CustomerCreditDraft, CustomerCreditRevision, CustomerInvoiceDraft


class CreditFixture(CustomerInvoiceFixture):
    def body(self):
        invoice = CustomerInvoiceFixture.body(self)
        return {
            "customer_reference": invoice["customer_reference"],
            "document_reference": " TEST-AR-CREDIT — مرجع ", "proposal_date": "2028-02-29",
            "reason": " Explicit synthetic credit review reason — نص ",
            "linked_invoice_id": None, "currency": "QAR", "lines": invoice["lines"],
        }

    def post(self, body):
        return self.client.post("/lab/customer-credits/", json.dumps(body), content_type="application/json")

    def put(self, pk, body):
        return self.client.put(f"/lab/customer-credits/{pk}/", json.dumps(body), content_type="application/json")

    def invoice(self):
        return save_invoice(data=CustomerInvoiceFixture.body(self), entity=self.entity)


@override_settings(**LAB)
class CustomerCreditTests(CreditFixture, TestCase):
    def setUp(self):
        self.setup_budget()
        self.assertEqual(connection.vendor, "postgresql")

    def test_exact_unsigned_totals_raw_inputs_and_no_operational_effect(self):
        result = self.post(self.body())
        self.assertEqual(result.status_code, 201, result.content)
        row = result.json()
        self.assertEqual(row["review"]["total"], "0.30")
        self.assertEqual(row["lines"], self.body()["lines"])
        self.assertEqual(row["reason"], self.body()["reason"])
        self.assertEqual(row["review"]["amount_basis"], "explicit_non_negative_magnitudes")
        self.assertTrue(row["review"]["proposed"])
        for field in ("issued_credit_note", "refund", "settlement", "operational_receivable_adjustment",
                      "approved", "posted"):
            self.assertIs(row["review"][field], False)
        self.assertIn("no signed receivable effect", row["review"]["interpretation"])
        self.assertIsNone(row["linked_invoice"])
        for value, expected in (("0", "0.00"), ("999999999999.99", "1999999999999.98")):
            body = self.body()
            for line in body["lines"]:
                line["amount"] = value
            self.assertEqual(self.post(body).json()["review"]["total"], expected)
        self.assertEqual(self.business_counts(), self.before)
        self.assertEqual(CustomerInvoiceDraft.objects.count(), 0)

    def test_optional_link_captures_reference_revision_and_preserves_entire_source_and_history(self):
        invoice = self.invoice()
        before = CustomerInvoiceDraft.objects.values().get(pk=invoice.pk)
        history = list(invoice.revisions.values())
        response = self.post({**self.body(), "linked_invoice_id": invoice.pk,
                              "customer_reference": self.body()["customer_reference"].strip()})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["linked_invoice"], {
            "id": invoice.pk, "revision": 1, "customer_reference": invoice.payload["customer_reference"],
            "invoice_reference": invoice.payload["invoice_reference"],
        })
        self.assertEqual(CustomerInvoiceDraft.objects.values().get(pk=invoice.pk), before)
        self.assertEqual(list(invoice.revisions.values()), history)
        self.assertEqual(self.business_counts(), self.before)

    def test_customer_entity_missing_and_ambiguous_links_reject_without_partial_drafts(self):
        invoice = self.invoice()
        other = LegalEntity.objects.create(code="TEST-AR-CREDIT-OTHER", name="Synthetic other")
        foreign = CustomerInvoiceDraft.objects.create(legal_entity=other, payload=CustomerInvoiceFixture.body(self))
        cases = [
            {**self.body(), "linked_invoice_id": invoice.pk, "customer_reference": "TEST-DIFFERENT"},
            {**self.body(), "linked_invoice_id": invoice.pk, "customer_reference": self.body()["customer_reference"].swapcase()},
            {**self.body(), "linked_invoice_id": invoice.pk, "customer_reference": "\u00a0" + self.body()["customer_reference"]},
            {**self.body(), "linked_invoice_id": foreign.pk},
        ] + [{**self.body(), "linked_invoice_id": value}
             for value in (True, False, "1", 0, -1, 999999999, [invoice.pk])]
        for body in cases:
            response = self.post(body)
            self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(CustomerCreditDraft.objects.count(), 0)
        self.assertEqual(CustomerCreditRevision.objects.count(), 0)
        self.assertEqual(invoice.revisions.count(), 1)

    def test_explicit_dates_money_reason_and_forbidden_operational_inputs_are_atomic(self):
        cases = []
        for key, values in {
            "proposal_date": ["2027-02-29", "2028-2-1", "", None],
            "reason": ["", " ", None, "x" * 2001],
            "customer_reference": ["", None, "x" * 201],
            "document_reference": ["", None, "x" * 201],
            "currency": ["USD", None], "lines": [[], {}, self.body()["lines"] * 101],
        }.items():
            cases.extend({**self.body(), key: value} for value in values)
        for value in ("-0.01", "-0", "0.001", "NaN", "1e2", 0.1, True, "1000000000000"):
            body = self.body()
            body["lines"][1]["amount"] = value
            cases.append(body)
        for field in ("tax", "approved", "posted", "refund", "settlement", "signed_amount",
                      "customer_id", "review", "linked_invoice", "history", "proposal_type", "eligibility"):
            cases.append({**self.body(), field: True})
        for key in self.body():
            body = self.body()
            del body[key]
            cases.append(body)
        for body in cases:
            response = self.post(body)
            self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(CustomerCreditDraft.objects.count(), 0)
        self.assertEqual(CustomerCreditRevision.objects.count(), 0)
        self.assertEqual(self.business_counts(), self.before)

    def test_accounts_units_dimensions_and_foreign_references_remain_entity_scoped(self):
        other = LegalEntity.objects.create(code="TEST-CREDIT-REF-OTHER", name="Synthetic other")
        account = create_finance_draft_account(legal_entity=other, code="TEST-DRAFT-ACCT-CREDIT", name_en="Other")
        unit = BusinessUnit.objects.create(legal_entity=other, code="TEST-CREDIT-BU", name="Other")
        dimension = create_finance_draft_dimension(legal_entity=other, code="TEST-DRAFT-DIM-CREDIT", label_en="Other")
        value = create_finance_draft_dimension_value(dimension=dimension, code="TEST-DRAFT-VAL-CREDIT", label_en="Other")
        for key, values in {
            "account_id": [True, None, "1", -1, 999999999, account.pk],
            "business_unit_id": [True, "1", -1, 999999999, unit.pk],
            "dimension_value_ids": [[True], ["1"], [999999999], [value.pk], [self.value.pk, self.value.pk]],
        }.items():
            for value in values:
                body = self.body()
                body["lines"][1][key] = value
                self.assertEqual(self.post(body).status_code, 400)
        self.assertEqual(CustomerCreditDraft.objects.count(), 0)
        self.assertEqual(self.business_counts(), self.before)

    def test_list_reopen_revision_conflict_and_explicit_link_removal_preserve_history(self):
        invoice = self.invoice()
        original = self.post({**self.body(), "linked_invoice_id": invoice.pk}).json()
        changed = {**self.body(), "document_reference": "TEST-EDIT", "reason": "Explicit revised reason",
                   "expected_revision": 1}
        updated = self.put(original["id"], changed)
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["revision"], 2)
        self.assertIsNone(updated.json()["linked_invoice"])
        self.assertIsNone(updated.json()["linked_invoice_id"])
        self.assertEqual(updated.json()["history"][0]["snapshot"]["linked_invoice"]["revision"], 1)
        self.assertEqual(self.put(original["id"], changed).status_code, 409)
        current = self.client.get(f"/lab/customer-credits/{original['id']}/").json()
        self.assertEqual(current, updated.json())
        self.assertEqual([h["snapshot"]["reason"] for h in current["history"]],
                         [self.body()["reason"], "Explicit revised reason"])
        self.assertEqual(self.client.get("/lab/customer-credits/").json()["customer_credits"], [{
            "id": original["id"], "revision": 2, "document_reference": "TEST-EDIT",
            "customer_reference": self.body()["customer_reference"],
        }])
        for expected in (None, True, "2", 0):
            self.assertEqual(self.put(original["id"], {**self.body(), "expected_revision": expected}).status_code, 400)
        self.assertEqual(self.post({**self.body(), "expected_revision": 1}).status_code, 400)

    def test_saved_link_evidence_is_stable_and_current_source_revalidated_on_edit(self):
        invoice = self.invoice()
        saved = self.post({**self.body(), "linked_invoice_id": invoice.pk}).json()
        edited = {**CustomerInvoiceFixture.body(self), "customer_reference": "TEST-NEW-CUSTOMER", "expected_revision": 1}
        save_invoice(data=edited, entity=self.entity, draft_id=invoice.pk)
        source = invoice_snapshot(CustomerInvoiceDraft.objects.get(pk=invoice.pk))
        history = list(invoice.revisions.values())
        unchanged = self.client.get(f"/lab/customer-credits/{saved['id']}/").json()
        self.assertEqual(unchanged["linked_invoice"]["revision"], 1)
        self.assertEqual(unchanged["history"], saved["history"])
        self.assertEqual(self.put(saved["id"], {
            **self.body(), "linked_invoice_id": invoice.pk, "expected_revision": 1}).status_code, 400)
        self.assertEqual(self.client.get(f"/lab/customer-credits/{saved['id']}/").json(), unchanged)
        self.assertEqual(self.put(saved["id"], {**self.body(), "linked_invoice_id": invoice.pk,
            "customer_reference": "TEST-NEW-CUSTOMER", "expected_revision": 1}).status_code, 200)
        self.assertEqual(invoice_snapshot(CustomerInvoiceDraft.objects.get(pk=invoice.pk)), source)
        self.assertEqual(list(invoice.revisions.values()), history)

    def test_history_insert_failure_rolls_back_and_database_history_is_immutable(self):
        with patch("finance_allocation_lab.customer_credits.record_revision", side_effect=RuntimeError("forced")):
            with self.assertRaises(RuntimeError):
                save_credit(data=self.body(), entity=self.entity)
        self.assertEqual(CustomerCreditDraft.objects.count(), 0)
        saved = save_credit(data=self.body(), entity=self.entity)
        before = snapshot(saved)
        with patch("finance_allocation_lab.customer_credits.record_revision", side_effect=RuntimeError("forced")):
            with self.assertRaises(RuntimeError):
                save_credit(data={**self.body(), "reason": "TEST-NOT-COMMITTED", "expected_revision": 1},
                            entity=self.entity, draft_id=saved.pk)
        saved.refresh_from_db()
        self.assertEqual(snapshot(saved), before)
        for operation in ("UPDATE", "DELETE"):
            with self.assertRaises(DatabaseError), transaction.atomic():
                if operation == "UPDATE":
                    CustomerCreditRevision.objects.filter(draft=saved).update(snapshot={})
                else:
                    CustomerCreditRevision.objects.filter(draft=saved).delete()
        self.assertEqual(saved.revisions.count(), 1)

    def test_lab_guard_entity_isolation_shared_route_schema_and_output_boundaries(self):
        saved = self.post(self.body()).json()
        other = LegalEntity.objects.create(code="TEST-CREDIT-ACCESS-OTHER", name="Synthetic other")
        foreign = CustomerCreditDraft.objects.create(legal_entity=other, payload=self.body())
        self.assertEqual(self.client.get(f"/lab/customer-credits/{foreign.pk}/").status_code, 404)
        self.assertEqual(self.put(foreign.pk, {**self.body(), "expected_revision": 1}).status_code, 404)
        self.assertEqual(len(self.client.get("/lab/customer-credits/").json()["customer_credits"]), 1)
        for flag in ("is_staff", "is_superuser"):
            setattr(self.actor, flag, True)
            self.actor.save(update_fields=[flag])
            self.assertEqual(self.client.get("/lab/customer-credits/").status_code, 403)
            setattr(self.actor, flag, False)
            self.actor.save(update_fields=[flag])
        self.actor.is_active = False
        self.actor.save(update_fields=["is_active"])
        self.assertEqual(self.put(saved["id"], {**self.body(), "expected_revision": 1}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.post(self.body()).status_code, 403)
        for path in (f"/lab/customer-credits/{saved['id']}/print/",
                     f"/lab/customer-credits/{saved['id']}/export/"):
            self.assertEqual(self.client.get(path).status_code, 404)
        with override_settings(ROOT_URLCONF="foundation.urls"):
            for path in ("/lab/customer-credits/", "/lab-customer-credits-ui/"):
                with self.assertRaises(Resolver404):
                    resolve(path)
        from config import settings as normal
        with override_settings(INSTALLED_APPS=normal.INSTALLED_APPS):
            self.assertFalse(any(app == "finance_allocation_lab" for app, _ in MigrationLoader(connection).graph.nodes))
        self.assertEqual(CustomerCreditDraft._meta.default_permissions, ())
        self.assertEqual(CustomerCreditRevision._meta.default_permissions, ())

    def test_deterministic_allowlisted_gets_create_no_business_records(self):
        saved = self.post(self.body()).json()
        self.assertEqual(set(saved), {*self.body(), "id", "revision", "review", "linked_invoice", "history"})
        counts = (self.business_counts(), CustomerCreditDraft.objects.count(), CustomerCreditRevision.objects.count())
        with CaptureQueriesContext(connection) as queries:
            for _ in range(2):
                self.assertEqual(self.client.get(f"/lab/customer-credits/{saved['id']}/").json(), saved)
                self.assertEqual(len(self.client.get("/lab/customer-credits/").json()["customer_credits"]), 1)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertEqual(counts, (self.business_counts(), CustomerCreditDraft.objects.count(), CustomerCreditRevision.objects.count()))


@override_settings(**LAB)
class CustomerCreditConcurrencyTests(CreditFixture, TransactionTestCase):
    def setUp(self):
        self.setup_budget()

    def test_independent_connections_commit_one_complete_credit_revision_and_one_conflict(self):
        invoice = self.invoice()
        source = CustomerInvoiceDraft.objects.values().get(pk=invoice.pk)
        history = list(invoice.revisions.values())
        saved = save_credit(data={**self.body(), "linked_invoice_id": invoice.pk}, entity=self.entity)
        barrier = Barrier(2)

        def worker(index):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                body = copy.deepcopy(self.body())
                body.update(expected_revision=1, linked_invoice_id=invoice.pk, reason=f"TEST-CREDIT-EDITOR-{index}")
                body["lines"][0]["amount"] = f"{index}.01"
                barrier.wait(timeout=10)
                try:
                    save_credit(data=body, entity=self.entity, draft_id=saved.pk)
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
        self.assertEqual(saved.payload["reason"], f"TEST-CREDIT-EDITOR-{winner}")
        self.assertEqual(saved.payload["lines"][0]["amount"], f"{winner}.01")
        self.assertEqual(saved.revision, 2)
        self.assertEqual(saved.revisions.count(), 2)
        self.assertEqual(CustomerInvoiceDraft.objects.values().get(pk=invoice.pk), source)
        self.assertEqual(list(invoice.revisions.values()), history)
        self.assertEqual(self.business_counts(), self.before)
