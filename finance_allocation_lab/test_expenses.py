import copy
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
from .expenses import save_proposal, snapshot
from .models import ExpenseProposal, ExpenseRevision


class ExpenseFixture(BudgetFixture):
    def body(self):
        return {"name": "Synthetic proposed expenses", "proposal_type": "expense", "currency": "QAR",
                "explanation": " Explicit review — شرح ",
                "entries": [{
                    "expense_date": day, "party_reference": f" TEST-CLAIMANT-{i} ",
                    "description": f" Proposed expense {i} — وصف ", "amount": amount,
                    "account_id": self.account.pk, "business_unit_id": self.unit.pk,
                    "dimension_value_ids": [self.value.pk], "receipt_reference": f" TEST-RECEIPT-{i} ",
                } for i, (day, amount) in enumerate([("2028-02-29", "0.10"), ("2028-03-01", "0.20")], 1)]}

    def post(self, body):
        return self.client.post("/lab/expenses/", json.dumps(body), content_type="application/json")

    def put(self, pk, body):
        return self.client.put(f"/lab/expenses/{pk}/", json.dumps(body), content_type="application/json")


@override_settings(**LAB)
class ExpenseTests(ExpenseFixture, TestCase):
    def setUp(self):
        self.setup_budget()

    def test_types_exact_totals_and_original_inputs_never_approve_pay_or_post(self):
        for kind in ["expense", "petty_cash"]:
            body = self.body()
            body["proposal_type"] = kind
            body["entries"][1].update(receipt_reference="", business_unit_id=None, dimension_value_ids=[])
            response = self.post(body)
            self.assertEqual(response.status_code, 201)
            saved = response.json()
            self.assertEqual(saved["proposal_type"], kind)
            self.assertEqual(saved["entries"], body["entries"])
            self.assertEqual(saved["explanation"], body["explanation"])
            self.assertEqual(saved["review"], {
                "currency": "QAR", "proposed_total": "0.30", "entry_count": 2,
                "missing_receipt_rows": [2], "unproposed_business_unit_rows": [2],
                "approved": False, "paid": False, "posted": False,
            })
        self.assertEqual(self.business_counts(), self.before)

    def test_zero_large_amounts_and_repeated_rows_are_explicit_proposals_not_policy(self):
        for amount, total in [("0.00", "0.00"), ("999999999999.99", "1999999999999.98")]:
            body = self.body()
            for row in body["entries"]:
                row["amount"] = amount
            self.assertEqual(self.post(body).json()["review"]["proposed_total"], total)
        body = self.body()
        body["entries"].append(copy.deepcopy(body["entries"][0]))
        self.assertEqual(self.post(body).json()["review"]["proposed_total"], "0.40")
        self.assertEqual(self.business_counts(), self.before)

    def test_dates_text_types_amount_precision_and_unsupported_policy_rejected_atomically(self):
        invalid = []
        for key, value in [("name", ""), ("proposal_type", ""), ("proposal_type", "reimbursement"),
                           ("currency", "USD"), ("entries", []), ("explanation", "x" * 2001)]:
            invalid.append({**self.body(), key: value})
        for key, value in [
            ("expense_date", "2027-02-29"), ("expense_date", "0000-01-01"),
            ("party_reference", " "), ("party_reference", "x" * 201),
            ("description", ""), ("description", None),
            ("receipt_reference", {"file": "unsupported"}), ("receipt_reference", "x" * 201),
            ("amount", 0.10), ("amount", True), ("amount", "-0.01"), ("amount", "0.001"),
            ("amount", "NaN"), ("amount", "1e2"), ("amount", "1000000000000"),
            ("account_id", None), ("account_id", True), ("business_unit_id", []),
            ("dimension_value_ids", [True]), ("dimension_value_ids", [[1]]),
            ("dimension_value_ids", [self.value.pk, self.value.pk]),
        ]:
            body = self.body()
            body["entries"][1][key] = value  # A valid first row must not partially save.
            invalid.append(body)
        for field in ["approved", "paid", "posted", "tax", "cash_limit", "reimbursement_eligible", "category"]:
            invalid.append({**self.body(), field: True})
            body = self.body()
            body["entries"][1][field] = True
            invalid.append(body)
        body = self.body()
        body["entries"] *= 51
        invalid.append(body)
        for index, body in enumerate(invalid):
            with self.subTest(case=index):
                self.assertEqual(self.post(body).status_code, 400)
        self.assertEqual((ExpenseProposal.objects.count(), ExpenseRevision.objects.count()), (0, 0))
        saved = self.post(self.body()).json()
        for body in invalid:
            self.assertEqual(self.put(saved["id"], {**body, "expected_revision": 1}).status_code, 400)
        self.assertEqual(self.client.get(f"/lab/expenses/{saved['id']}/").json(), saved)
        self.assertEqual(self.business_counts(), self.before)

    def test_saved_accounts_bu_dimensions_and_entity_scope_are_not_inferred(self):
        other = LegalEntity.objects.create(code="test-expense-other", name="Synthetic other")
        account = create_finance_draft_account(legal_entity=other, code="TEST-DRAFT-ACCT-OTHER", name_en="Foreign")
        unit = BusinessUnit.objects.create(legal_entity=other, code="TEST-EXP-OTHER", name="Foreign")
        dim = create_finance_draft_dimension(legal_entity=other, code="TEST-DRAFT-DIM-OTHER", label_en="Foreign")
        value = create_finance_draft_dimension_value(dimension=dim, code="TEST-DRAFT-VAL-OTHER", label_en="Foreign")
        for key, choice in [("account_id", account.pk), ("business_unit_id", unit.pk), ("dimension_value_ids", [value.pk])]:
            body = self.body()
            body["entries"][0][key] = choice
            self.assertEqual(self.post(body).status_code, 400)
        # Existing draft validation allows distinct values from the same dimension.
        # Do not invent a one-value-per-dimension expense restriction.
        second = create_finance_draft_dimension_value(
            dimension=self.value.dimension, code="TEST-DRAFT-VAL-SECOND", label_en="Second")
        body = self.body()
        body["entries"][0]["dimension_value_ids"].append(second.pk)
        response = self.post(body)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["entries"][0]["dimension_value_ids"], sorted([self.value.pk, second.pk]))
        self.assertEqual(ExpenseProposal.objects.count(), 1)
        foreign = ExpenseProposal.objects.create(legal_entity=other, payload=self.body())
        self.assertEqual(self.client.get(f"/lab/expenses/{foreign.pk}/").status_code, 404)
        self.assertEqual(self.put(foreign.pk, {**self.body(), "expected_revision": 1}).status_code, 404)
        self.assertEqual([row["id"] for row in self.client.get("/lab/expenses/").json()["proposals"]],
                         [response.json()["id"]])

    def test_real_list_reopen_edit_history_and_stale_conflict_preserve_inputs(self):
        first = self.post(self.body()).json()
        self.assertEqual(self.client.get(f"/lab/expenses/{first['id']}/").json(), first)
        edited = self.body()
        edited.update(expected_revision=1, name="Reviewed explicit petty cash", proposal_type="petty_cash")
        edited["entries"][0].update(amount="1.01", party_reference="TEST-CUSTODIAN-A")
        second = self.put(first["id"], edited).json()
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["review"]["proposed_total"], "1.21")
        self.assertEqual(second["history"][0]["snapshot"]["proposal_type"], "expense")
        self.assertEqual(second["history"][0]["snapshot"]["entries"], first["entries"])
        self.assertEqual(self.put(first["id"], edited).status_code, 409)
        self.assertEqual(self.client.get(f"/lab/expenses/{first['id']}/").json(), second)
        newer = self.post(self.body()).json()
        self.assertEqual([row["id"] for row in self.client.get("/lab/expenses/").json()["proposals"]],
                         [newer["id"], first["id"]])
        self.assertEqual(self.business_counts(), self.before)

    def test_revision_insert_failure_rolls_back_create_and_update(self):
        with patch("finance_allocation_lab.expenses.record_revision", side_effect=RuntimeError("forced insert failure")):
            with self.assertRaises(RuntimeError):
                save_proposal(data=self.body(), entity=self.entity)
        self.assertEqual(ExpenseProposal.objects.count(), 0)
        proposal = save_proposal(data=self.body(), entity=self.entity)
        original = snapshot(proposal)
        with patch("finance_allocation_lab.expenses.record_revision", side_effect=RuntimeError("forced insert failure")):
            with self.assertRaises(RuntimeError):
                save_proposal(data={**self.body(), "expected_revision": 1, "name": "Roll back"},
                              entity=self.entity, proposal_id=proposal.pk)
        proposal.refresh_from_db()
        self.assertEqual(snapshot(proposal), original)
        self.assertEqual(proposal.revisions.count(), 1)
        self.assertEqual(self.business_counts(), self.before)

    def test_database_history_is_immutable_and_not_merely_hidden_in_ui(self):
        proposal = save_proposal(data=self.body(), entity=self.entity)
        for operation in [
            lambda: ExpenseRevision.objects.filter(proposal=proposal).update(snapshot={}),
            lambda: ExpenseRevision.objects.filter(proposal=proposal).delete(),
        ]:
            with self.assertRaises(DatabaseError), transaction.atomic():
                operation()
        self.assertEqual(ExpenseRevision.objects.filter(proposal=proposal).count(), 1)

    def test_access_rechecked_and_normal_app_has_no_routes_migrations_or_new_permissions(self):
        saved = self.post(self.body()).json()
        for flag in ["is_staff", "is_superuser", "is_active"]:
            before = getattr(self.actor, flag)
            setattr(self.actor, flag, False if flag == "is_active" else True)
            self.actor.save(update_fields=[flag])
            for response in [self.client.get("/lab/expenses/"),
                             self.client.get(f"/lab/expenses/{saved['id']}/"),
                             self.post(self.body()), self.put(saved["id"], {**self.body(), "expected_revision": 1})]:
                self.assertEqual(response.status_code, 403)
            setattr(self.actor, flag, before)
            self.actor.save(update_fields=[flag])
            self.client.force_login(self.actor)
        with override_settings(ROOT_URLCONF="foundation.urls"):
            for path in ["/lab/expenses/", "/lab-expense-ui/"]:
                with self.assertRaises(Resolver404):
                    resolve(path)
        with override_settings(INSTALLED_APPS=[app for app in settings.INSTALLED_APPS if app != "finance_allocation_lab"]):
            with CaptureQueriesContext(connection) as queries:
                graph = MigrationLoader(None).graph
            self.assertFalse(any(node[0] == "finance_allocation_lab" for node in graph.nodes))
            self.assertEqual(len(queries), 0)
        self.assertEqual(ExpenseProposal._meta.default_permissions, ())
        self.assertEqual(ExpenseRevision._meta.default_permissions, ())
        self.client.logout()
        self.assertEqual(self.post(self.body()).status_code, 403)
        self.assertEqual(self.client.get("/lab/expenses/").status_code, 403)
        self.assertEqual(ExpenseProposal.objects.get(pk=saved["id"]).revision, 1)

    def test_read_review_list_deterministic_without_business_writes(self):
        self.assertEqual(self.client.get("/lab/expenses/").json(), {"proposals": []})
        saved = self.post(self.body()).json()
        with CaptureQueriesContext(connection) as queries:
            for _ in range(2):
                self.assertEqual(self.client.get(f"/lab/expenses/{saved['id']}/").json(), saved)
                self.assertEqual(len(self.client.get("/lab/expenses/").json()["proposals"]), 1)
        self.assertFalse(any(query["sql"].lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"} for query in queries))
        self.assertEqual(self.business_counts(), self.before)


@override_settings(**LAB)
class ExpenseConcurrencyTests(ExpenseFixture, TransactionTestCase):
    def setUp(self):
        self.setup_budget()

    def test_independent_editors_commit_one_whole_revision_and_reject_the_stale_edit(self):
        saved = save_proposal(data=self.body(), entity=self.entity)
        barrier = Barrier(2)
        def worker(index):
            connections.close_all()
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                body = self.body()
                body.update(name=f"Reviewer {index}", expected_revision=1)
                body["entries"][0].update(amount=f"{index}.10", description=f"Explicit proposal by {index}")
                barrier.wait(timeout=10)
                try:
                    save_proposal(data=body, entity=self.entity, proposal_id=saved.pk)
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
        self.assertEqual(saved.payload["name"], f"Reviewer {winner}")
        self.assertEqual(saved.payload["entries"][0]["amount"], f"{winner}.10")
        self.assertEqual(saved.payload["entries"][0]["description"], f"Explicit proposal by {winner}")
        self.assertEqual(saved.revisions.count(), 2)
        self.assertEqual(self.business_counts(), self.before)
