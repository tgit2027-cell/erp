import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction
from django.test import Client, TransactionTestCase, override_settings
from django.urls import Resolver404, resolve
from django.db.migrations.loader import MigrationLoader
from foundation.models import LegalEntity
from foundation.test_finance_draft_workflow import LAB
from .models import DraftCreateReceipt, SupplierAdvanceDraft, SupplierAdvanceRevision, SupplierInvoiceDraft
from .test_supplier_advances import AdvanceFixture
from .test_invoices import InvoiceFixture
from .recovery import RESOURCES


@override_settings(**LAB)
class RecoveryTests(AdvanceFixture, TransactionTestCase):
    def setUp(self):
        self.setup_budget()
        self.assertEqual(connection.vendor, "postgresql")

    def create(self, key, body=None, client=None, resource="supplier-advances"):
        return (client or self.client).post(
            f"/lab/{resource}/", json.dumps(self.body() if body is None else body),
            content_type="application/json", HTTP_X_FINANCE_REQUEST_ID=str(key))

    def status(self, key):
        return self.client.get(f"/lab/requests/{key}/")

    def test_committed_create_lost_acknowledgement_recovers_original_and_current_revision(self):
        key = uuid4()
        committed = []
        def lost_ack():
            with patch("finance_allocation_lab.recovery.create_with_identity", wraps=__import__(
                    "finance_allocation_lab.recovery", fromlist=["create_with_identity"]).create_with_identity):
                # Callback runs only after the real outer create transaction commits.
                original_save = DraftCreateReceipt.save
                def observe(row, *args, **kwargs):
                    original_save(row, *args, **kwargs)
                    if row.status == 201:
                        transaction.on_commit(lambda: committed.append(row.pk))
                with patch.object(DraftCreateReceipt, "save", observe):
                    response = self.create(key)
            self.assertEqual(response.status_code, 201)
            self.assertTrue(committed)
            raise ConnectionError("Synthetic response lost AFTER commit")
        with self.assertRaises(ConnectionError):
            lost_ack()
        status = self.status(key)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(set(status.json()), {
            "request_id", "state", "resource", "editor_path", "draft_id", "original_revision"})
        self.assertNotIn(self.body()["purpose"], status.content.decode())
        row = SupplierAdvanceDraft.objects.get()
        updated = self.put(row.pk, {**self.body(), "amount": "1.01", "expected_revision": 1})
        self.assertEqual(updated.status_code, 200)
        replay = self.create(key)
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay["X-Finance-Replayed"], "true")
        self.assertEqual(replay.json()["amount"], "0.10")
        self.assertEqual(replay.json()["revision"], 1)
        self.assertEqual(self.status(key).json()["original_revision"], 1)
        self.assertEqual(SupplierAdvanceDraft.objects.count(), 1)
        self.assertEqual(SupplierAdvanceRevision.objects.count(), 2)
        self.assertEqual(DraftCreateReceipt.objects.count(), 1)
        self.assertEqual(self.client.get(f"/lab/supplier-advances/{row.pk}/").json()["revision"], 2)
        self.assertEqual(self.business_counts(), self.before)

    def test_same_identity_canonical_payload_replay_and_conflicting_body_or_resource(self):
        key = uuid4()
        first = self.create(key)
        reordered = dict(reversed(list(self.body().items())))
        self.assertEqual(self.create(key, reordered).json(), first.json())
        conflict = self.create(key, {**self.body(), "amount": "0.11"})
        self.assertEqual(conflict.status_code, 409)
        self.assertIn("different input", conflict.json()["detail"])
        self.assertEqual(self.create(key, self.body(), resource="invoices").status_code, 409)
        self.assertEqual(SupplierInvoiceDraft.objects.count(), 0)
        self.assertEqual(SupplierAdvanceDraft.objects.count(), 1)

    def test_validation_and_failed_receipt_write_roll_back_identity_draft_and_history(self):
        key = uuid4()
        self.assertEqual(self.create(key, {**self.body(), "amount": "-1"}).status_code, 400)
        self.assertEqual(DraftCreateReceipt.objects.count(), 0)
        original_save = DraftCreateReceipt.save
        def fail_completed(row, *args, **kwargs):
            if row.status == 201:
                raise RuntimeError("Synthetic receipt storage failure")
            return original_save(row, *args, **kwargs)
        with patch.object(DraftCreateReceipt, "save", fail_completed), self.assertRaises(RuntimeError):
            self.create(key)
        self.assertEqual((DraftCreateReceipt.objects.count(), SupplierAdvanceDraft.objects.count(),
                          SupplierAdvanceRevision.objects.count()), (0, 0, 0))
        self.assertEqual(self.status(key).status_code, 404)
        self.assertEqual(self.create(key).status_code, 201)
        self.assertEqual(self.business_counts(), self.before)

    def test_other_actor_entity_unsigned_inactive_and_staff_cannot_lookup_or_replay(self):
        key = uuid4()
        self.assertEqual(self.create(key).status_code, 201)
        self.client.logout()
        self.assertEqual(self.status(key).status_code, 403)
        other = get_user_model().objects.create_user(username="synthetic_other_finance", password=None)
        self.client.force_login(other)
        with override_settings(FINANCE_LAB_ACTOR=other.username):
            self.assertEqual(self.status(key).status_code, 404)
            # Same UUID in another actor scope is an independent request.
            self.assertEqual(self.create(key).status_code, 201)
        self.client.force_login(self.actor)
        other_entity = LegalEntity.objects.create(code="test-finance-recovery-other", name="Synthetic other")
        with patch("foundation.finance_lab_views._target", return_value=other_entity):
            self.assertEqual(self.status(key).status_code, 404)
        self.actor.is_staff = True
        self.actor.save(update_fields=("is_staff",))
        self.assertEqual(self.status(key).status_code, 403)
        self.assertEqual(self.create(key).status_code, 403)
        self.actor.is_staff = False
        self.actor.is_active = False
        self.actor.save(update_fields=("is_staff", "is_active"))
        self.assertEqual(self.status(key).status_code, 403)
        self.assertEqual(DraftCreateReceipt.objects.count(), 2)

    def test_missing_status_is_not_absence_guarantee_and_malformed_key_cannot_create(self):
        result = self.status(uuid4())
        self.assertEqual(result.status_code, 404)
        self.assertIn("may still be running", result.json()["detail"])
        self.assertEqual(self.create("not-a-uuid").status_code, 400)
        self.assertEqual(SupplierAdvanceDraft.objects.count(), 0)
        self.assertEqual(DraftCreateReceipt.objects.count(), 0)

    def test_every_draft_type_uses_same_guard_and_normal_graph_excludes_lab(self):
        for resource in RESOURCES:
            with self.subTest(resource=resource):
                with patch("finance_allocation_lab.recovery.create_with_identity",
                           return_value=__import__("django.http", fromlist=["JsonResponse"]).JsonResponse({"probe": True}, status=201)) as hook:
                    response = self.create(uuid4(), {}, resource=resource)
                self.assertEqual(response.status_code, 201)
                hook.assert_called_once()
        with override_settings(ROOT_URLCONF="foundation.urls"):
            for path in ("/lab/review/", f"/lab/requests/{uuid4()}/", "/lab-review-ui/"):
                with self.assertRaises(Resolver404):
                    resolve(path)
        with override_settings(INSTALLED_APPS=[name for name in __import__(
                "django.conf", fromlist=["settings"]).settings.INSTALLED_APPS if name != "finance_allocation_lab"]):
            self.assertFalse(any(app == "finance_allocation_lab" for app, _ in MigrationLoader(connection).graph.nodes))

    def test_readonly_review_all_existing_types_deterministic_scoped_and_no_financial_totals(self):
        empty = self.client.get("/lab/review/")
        self.assertEqual(empty.status_code, 200, empty.content)
        self.assertEqual(len(empty.json()["groups"]), len(RESOURCES))
        self.assertTrue(all(group["items"] == [] for group in empty.json()["groups"]))
        advance = self.create(uuid4()).json()
        invoice = self.create(uuid4(), InvoiceFixture.body(self), resource="invoices").json()
        other_entity = LegalEntity.objects.create(code="test-finance-other-review", name="Synthetic other")
        SupplierAdvanceDraft.objects.create(legal_entity=other_entity, payload=self.body())
        before = (DraftCreateReceipt.objects.count(), SupplierAdvanceRevision.objects.count(), self.business_counts())
        review = self.client.get("/lab/review/").json()
        self.assertEqual(review, self.client.get("/lab/review/").json())
        groups = {group["resource"]: group for group in review["groups"]}
        self.assertEqual([r["id"] for r in groups["supplier-advances"]["items"]], [advance["id"]])
        self.assertEqual([r["id"] for r in groups["invoices"]["items"]], [invoice["id"]])
        self.assertEqual(set(groups["invoices"]["items"][0]), {"id", "revision", "label", "kind", "updated_at"})
        self.assertNotIn('"amount"', json.dumps(review))
        self.assertNotIn('"total"', json.dumps(review))
        self.assertEqual(before, (DraftCreateReceipt.objects.count(), SupplierAdvanceRevision.objects.count(), self.business_counts()))

    def concurrent(self, different=False):
        key, barrier = uuid4(), Barrier(2)
        def work(index):
            connections.close_all()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                client = Client()
                client.force_login(actor)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                barrier.wait(timeout=10)
                response = self.create(key, {**self.body(), "amount": f"0.{index+10}" if different else "0.10"}, client)
                return pid, response.status_code, response.json()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(work, range(2)))
        self.assertEqual(len({row[0] for row in values}), 2)
        self.assertEqual(sorted(row[1] for row in values), [201, 409] if different else [201, 201])
        if not different:
            self.assertEqual(values[0][2], values[1][2])
        self.assertEqual((SupplierAdvanceDraft.objects.count(), SupplierAdvanceRevision.objects.count(),
                          DraftCreateReceipt.objects.count()), (1, 1, 1))
        self.assertEqual(self.business_counts(), self.before)

    def test_independent_concurrent_identical_requests_commit_one_draft_history_and_outcome(self):
        self.concurrent()

    def test_independent_concurrent_conflicting_requests_do_not_overwrite_outcome(self):
        self.concurrent(different=True)
