"""Procurement API journey; all data belongs to Django's disposable test DB."""

from copy import deepcopy

from django.apps import apps
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    IdempotencyRecord, Location, ReportDataCapability, ReportDataGrant, ReportPanelGrant, ReportingPanel,
    StockBalance, UserActionGrant,
)
from .test_reporting import ReportingFixtureMixin


class ProcurementWorkspaceAPITests(ReportingFixtureMixin, TestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.sales)
        self.csrf = self.client.get("/api/auth/csrf/").data["csrfToken"]

    def post(self, path, payload):
        return self.client.post(
            path, payload, format="json", HTTP_X_CSRFTOKEN=self.csrf,
        )

    def snapshot(self):
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in apps.get_app_config("foundation").get_models()
        }

    def commitment(self):
        payload = {
            "business_unit_id": self.unit.pk, "supplier_id": self.supplier.pk,
            "idempotency_key": "TEST-procurement-commitment",
            "lines": [
                {"variant_id": self.variant.pk, "ordered_quantity": "10.00", "unit_cost": "1000.00"},
                {"variant_id": self.alternate_variant.pk, "ordered_quantity": "5.00", "unit_cost": "200.00"},
            ],
        }
        response = self.post("/api/purchasing/commitments/", payload)
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def receipt(self, commitment, quantities, *, key, charge="0.00"):
        line_ids = {line["variant_id"]: line["commitment_line_id"] for line in commitment["lines"]}
        return {
            "business_unit_id": self.unit.pk, "supplier_id": self.supplier.pk,
            "idempotency_key": key,
            "purchase_commitment_id": commitment["purchase_commitment_id"],
            "lines": [
                {"commitment_line_id": line_ids[variant.pk], "variant_id": variant.pk,
                 "location_id": self.location.pk, "quantity": quantity}
                for variant, quantity in zip((self.variant, self.alternate_variant), quantities)
            ],
            "charges": [{"category": "shipping", "amount": charge, "eligible": True}],
        }

    def progress(self):
        response = self.client.get(
            "/api/purchasing/commitments/",
            {"business_unit_id": self.unit.pk, "page_size": 100},
        )
        self.assertEqual(response.status_code, 200, response.data)
        return {
            row["variant_id"]: (row["ordered_quantity"], row["received_to_date"], row["remaining_quantity"])
            for row in response.data["rows"]
        }

    def test_two_partial_receipts_progress_replay_landed_reconciliation_and_ledger(self):
        commitment = self.commitment()
        self.assertEqual(self.progress(), {
            self.variant.pk: ("10.00", "0.00", "10.00"),
            self.alternate_variant.pk: ("5.00", "0.00", "5.00"),
        })
        for quantities, charge, allocations, expected_progress, key in [
            (("4.00", "2.00"), "880.00", ["800.00", "80.00"],
             {self.variant.pk: ("10.00", "4.00", "6.00"),
              self.alternate_variant.pk: ("5.00", "2.00", "3.00")}, "TEST-procurement-first"),
            (("6.00", "3.00"), "1320.00", ["1200.00", "120.00"],
             {self.variant.pk: ("10.00", "10.00", "0.00"),
              self.alternate_variant.pk: ("5.00", "5.00", "0.00")}, "TEST-procurement-second"),
        ]:
            payload = self.receipt(commitment, quantities, key=key, charge=charge)
            response = self.post("/api/purchasing/receipts/", payload)
            self.assertEqual(response.status_code, 201, response.data)
            self.assertNotIn("landed_cost_rate_percent", response.data)
            self.assertNotIn("landed_cost_allocations", response.data)
            private = IdempotencyRecord.objects.get(key=key).result
            self.assertEqual(private["landed_cost_rate_percent"], "20.0000")
            self.assertEqual(private["landed_cost_allocations"], allocations)
            self.assertEqual(self.progress(), expected_progress)
            after = self.snapshot()
            replay = self.post("/api/purchasing/receipts/", payload)
            self.assertEqual(replay.status_code, 201, replay.data)
            self.assertEqual(replay.data, response.data)
            self.assertEqual(self.snapshot(), after, "Replay must not duplicate any business/audit facts")
        balances = StockBalance.objects.filter(business_unit=self.unit, location=self.location)
        self.assertEqual(
            {row.variant_id: str(row.on_hand_quantity) for row in balances},
            {self.variant.pk: "10.00", self.alternate_variant.pk: "5.00"},
        )

    def test_any_line_overreceipt_conflicting_retry_and_revocation_do_not_partially_write(self):
        commitment = self.commitment()
        payload = self.receipt(commitment, ("4.00", "2.00"), key="TEST-procurement-accepted")
        self.assertEqual(self.post("/api/purchasing/receipts/", payload).status_code, 201)
        after = self.snapshot()
        invalid = self.receipt(commitment, ("1.00", "4.00"), key="TEST-procurement-over")
        self.assertEqual(self.post("/api/purchasing/receipts/", invalid).status_code, 400)
        self.assertEqual(self.snapshot(), after, "A valid first line must roll back with the invalid second line")
        conflict = deepcopy(payload)
        conflict["lines"][0]["quantity"] = "3.00"
        self.assertEqual(self.post("/api/purchasing/receipts/", conflict).status_code, 409)
        self.assertEqual(self.snapshot(), after)
        missing_csrf = self.client.post("/api/purchasing/receipts/", invalid, format="json")
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(self.snapshot(), after)
        UserActionGrant.objects.filter(
            user=self.sales, business_unit=self.unit, action=UserActionGrant.Action.CREATE_ORDER,
        ).update(revoked_at=timezone.now())
        revoked = self.snapshot()
        self.assertEqual(self.post("/api/purchasing/receipts/", payload).status_code, 403)
        self.assertEqual(self.client.get(
            "/api/purchasing/commitments/", {"business_unit_id": self.unit.pk},
        ).status_code, 404)
        self.assertEqual(self.snapshot(), revoked)

    def test_receipt_register_requires_separate_scope_masks_cost_and_rechecks_revocation(self):
        commitment = self.commitment()
        payload = self.receipt(commitment, ("4.00", "2.00"), key="TEST-procurement-read", charge="880.00")
        created = self.post("/api/purchasing/receipts/", payload)
        self.assertEqual(created.status_code, 201, created.data)
        query = {"unit": self.unit.pk, "page_size": 20}
        url = "/api/reports/purchase_history/receipts/"
        self.assertEqual(self.client.get(url, query).status_code, 403)
        self.grant_report_access(
            self.sales, ReportingPanel.PURCHASE_HISTORY, [ReportDataCapability.PURCHASE_HISTORY],
            unit=self.unit,
        )
        before = self.snapshot()
        response = self.client.get(url, query)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["pagination"]["total_count"], 1)
        row = response.data["rows"][0]
        for field in ("purchase_value", "eligible_landed_charges", "charges", "landed_cost_rate_percent"):
            self.assertNotIn(field, row)
        for line in row["lines"]:
            for field in ("unit_cost", "purchase_value", "landed_cost_allocation", "unit_landed_cost"):
                self.assertNotIn(field, line)
        detail = self.client.get(f"{url}{row['receipt_id']}/", query)
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertNotIn("eligible_landed_charges", detail.data["receipt"])
        self.assertEqual(self.client.get(url, {"unit": self.roche.pk}).status_code, 403)
        self.assertEqual(self.client.get(
            url, {**query, "location": Location.objects.get(code="SNAT").pk},
        ).status_code, 403)
        self.assertEqual(self.client.get(
            url, {**query, "location": "SNAT"},
        ).status_code, 400)
        self.assertEqual(self.snapshot(), before, "Authorized and denied GETs must not write")
        ReportDataGrant.objects.filter(
            user=self.sales, capability=ReportDataCapability.PURCHASE_HISTORY,
        ).update(revoked_at=timezone.now())
        self.assertEqual(self.client.get(url, query).status_code, 403)
        ReportPanelGrant.objects.filter(
            user=self.sales, panel=ReportingPanel.PURCHASE_HISTORY,
        ).update(revoked_at=timezone.now())
        self.assertEqual(self.client.get(f"{url}{row['receipt_id']}/", query).status_code, 403)

    def test_foreign_receipt_register_shows_rate_supplier_total_and_charge_currencies(self):
        today = timezone.localdate().isoformat()
        created = self.post("/api/purchasing/receipts/", {
            "business_unit_id": self.unit.pk, "supplier_id": self.supplier.pk, "idempotency_key": "TEST-fx-register",
            "lines": [{"variant_id": self.variant.pk, "location_id": self.location.pk, "quantity": "2.00",
                       "unit_cost": "1000.00"}],
            "charges": [
                {"category": "shipping", "amount": "100.00", "eligible": True, "currency": "USD",
                 "exchange_rate": "3.64", "rate_source": "TEST ONLY freight rate", "rate_date": today},
                {"category": "clearance", "amount": "50.00", "eligible": True},
            ],
            "currency": "EUR", "exchange_rate": "4.00", "rate_source": "TEST ONLY bank rate", "rate_date": today,
        })
        self.assertEqual(created.status_code, 201, created.data)
        url = "/api/reports/purchase_history/receipts/"
        query = {"unit": self.unit.pk}
        self.grant_report_access(
            self.sales, ReportingPanel.PURCHASE_HISTORY, [ReportDataCapability.PURCHASE_HISTORY], unit=self.unit,
        )
        masked = self.client.get(f"{url}{created.data['purchase_receipt_id']}/", query).data["receipt"]
        self.assertEqual((masked["currency"], masked["exchange_rate"]), ("EUR", "4.00000000"))
        self.assertNotIn("foreign_purchase_value", masked)
        self.assertNotIn("charges", masked)
        self.grant_report_access(
            self.sales, ReportingPanel.PURCHASE_HISTORY,
            [ReportDataCapability.SUPPLIER_COST, ReportDataCapability.LANDED_COST], unit=self.unit,
        )
        receipt = self.client.get(f"{url}{created.data['purchase_receipt_id']}/", query).data["receipt"]
        self.assertEqual((receipt["foreign_purchase_value"], receipt["purchase_value"]), ("2000.00", "8000.00"))
        self.assertIn("foreign_purchase_value", receipt["fields"])
        shipping, clearance = receipt["charges"]
        self.assertEqual(
            (shipping["currency"], shipping["foreign_amount"], shipping["amount"], shipping["exchange_rate"]),
            ("USD", "100.00", "364.00", "3.64000000"),
        )
        self.assertEqual(clearance, {"category": "clearance", "amount": "50.00", "eligible": True})
