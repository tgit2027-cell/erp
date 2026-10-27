"""Inventory journeys use rolled-back synthetic PostgreSQL fixtures only."""

from decimal import Decimal

from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    Location, ReportDataCapability, ReportDataGrant, ReportPanelGrant,
    ReportingPanel, StockBalance, StockMovement, UserActionGrant,
)
from .test_reporting import ReportingFixtureMixin
from .transaction_services import reserve_stock, transfer_stock


class InventoryWorkspaceAPITests(ReportingFixtureMixin, TestCase):
    fields = {
        "id", "item_code", "item_description", "brand_name", "brand_code",
        "variant_sku", "variant_description", "location_code", "location_name",
        "unit_code", "unit_name", "quantity", "held_quantity", "sellable_quantity", "is_synthetic",
    }

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.sales)
        self.csrf = self.client.get("/api/auth/csrf/").data["csrfToken"]

    def receipt_payload(self):
        return {
            "business_unit_id": self.unit.pk,
            "supplier_id": self.supplier.pk,
            "idempotency_key": "TEST-warehouse-receipt",
            "lines": [{
                "variant_id": self.variant.pk, "location_id": self.location.pk,
                "quantity": "10.00", "unit_cost": "1000.00",
            }],
            "charges": [],
        }

    def receive(self):
        response = self.client.post(
            "/api/purchasing/receipts/", self.receipt_payload(), format="json",
            HTTP_X_CSRFTOKEN=self.csrf,
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def detail(self, balance, **params):
        return self.client.get(
            f"/api/stock-balances/{balance.pk}/", {"unit": self.unit.pk, **params},
        )

    def business_state(self):
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in apps.get_app_config("foundation").get_models()
        }

    def test_detail_honors_explicit_location_and_rejects_other_unit_or_invalid_location(self):
        self.receive()
        balance = StockBalance.objects.get(
            business_unit=self.unit, variant=self.variant, location=self.location,
        )
        self.assertEqual(self.detail(balance, location=self.location.pk).status_code, 200)
        # A different authorized location is a valid filter, not this bucket.
        self.assertEqual(self.detail(balance, location=self.other_location.pk).status_code, 404)
        foreign = Location.objects.get(code="SNAT")
        self.assertEqual(self.detail(balance, location=foreign.pk).status_code, 403)
        self.assertEqual(self.detail(balance, location="not-an-id").status_code, 400)
        self.assertEqual(
            self.client.get(f"/api/stock-balances/{balance.pk}/", {"unit": self.roche.pk}).status_code,
            404,
        )

    def test_receipt_replay_reservation_and_transfer_project_once_without_get_writes(self):
        first = self.receive()
        self.assertEqual(self.receive(), first)
        reserve_stock(
            idempotency_key="TEST-warehouse-reserve", business_unit=self.unit,
            variant=self.variant, location=self.location, quantity="2.00",
        )
        transfer_args = dict(
            idempotency_key="TEST-warehouse-transfer", business_unit=self.unit,
            variant=self.variant, source_location=self.location,
            destination_location=self.other_location, quantity="3.00",
        )
        first_transfer = transfer_stock(**transfer_args)
        self.assertEqual(transfer_stock(**transfer_args), first_transfer)
        before = self.business_state()
        with CaptureQueriesContext(connection) as queries:
            for location, expected in ((self.location, "5.00"), (self.other_location, "3.00")):
                page = self.client.get("/api/stock-balances/", {
                    "unit": self.unit.pk, "location": location.pk,
                    "search": location.code, "page": 1, "page_size": 1,
                })
                self.assertEqual(page.status_code, 200, page.data)
                self.assertEqual(page.data["pagination"]["total_count"], 1)
                row = page.data["results"][0]
                self.assertEqual(set(row), self.fields)
                self.assertEqual(row["quantity"], expected)
                detail = self.detail(StockBalance.objects.get(pk=row["id"]), location=location.pk)
                self.assertEqual(detail.status_code, 200, detail.data)
                self.assertEqual(detail.data, row)
        self.assertEqual(self.business_state(), before)
        self.assertFalse([
            query["sql"] for query in queries
            if query["sql"].lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}
        ])
        self.assertEqual(StockMovement.objects.filter(kind="transfer_out").count(), 1)
        self.assertEqual(StockMovement.objects.filter(kind="transfer_in").count(), 1)

    def test_receipt_csrf_conflicting_replay_and_revocation_preserve_business_state(self):
        before = self.business_state()
        missing_token = self.client.post(
            "/api/purchasing/receipts/", self.receipt_payload(), format="json",
        )
        self.assertEqual(missing_token.status_code, 403)
        self.assertEqual(self.business_state(), before)
        self.receive()
        changed = self.receipt_payload()
        changed["lines"][0]["quantity"] = "11.00"
        before = self.business_state()
        conflict = self.client.post(
            "/api/purchasing/receipts/", changed, format="json", HTTP_X_CSRFTOKEN=self.csrf,
        )
        self.assertEqual(conflict.status_code, 409, conflict.data)
        self.assertEqual(self.business_state(), before)
        UserActionGrant.objects.filter(
            user=self.sales, business_unit=self.unit, action=UserActionGrant.Action.READ_STOCK,
        ).update(revoked_at=timezone.now())
        balance = StockBalance.objects.get(variant=self.variant, location=self.location)
        before = self.business_state()
        self.assertEqual(self.detail(balance).status_code, 403)
        self.assertEqual(self.client.get("/api/stock-balances/", {"unit": self.unit.pk}).status_code, 403)
        self.assertEqual(self.business_state(), before)

    def test_staff_and_stock_grants_do_not_assign_capacity_or_report_or_receipt_authority(self):
        # Existing fictional staff demo_admin has read_stock but no create_order.
        self.client.force_login(self.admin)
        self.csrf = self.client.get("/api/auth/csrf/").data["csrfToken"]
        self.assertEqual(self.client.get("/api/stock-balances/", {"unit": self.unit.pk}).status_code, 200)
        capacity = self.client.get("/api/warehouse-capacity/")
        self.assertEqual(capacity.status_code, 200, capacity.data)
        self.assertEqual(capacity.data, {"results": []})
        for panel in ("inventory", "transfer_history"):
            denied = self.client.get(f"/api/reports/{panel}/", {"unit": self.unit.pk})
            self.assertEqual(denied.status_code, 403, denied.data)
        before = self.business_state()
        denied = self.client.post(
            "/api/purchasing/receipts/", self.receipt_payload(), format="json",
            HTTP_X_CSRFTOKEN=self.csrf,
        )
        self.assertEqual(denied.status_code, 403, denied.data)
        self.assertEqual(self.business_state(), before)

    def test_inventory_register_requires_assignment_and_data_scope_and_masks_values(self):
        self.receive()
        self.grant_report_access(
            self.sales, ReportingPanel.INVENTORY, [ReportDataCapability.INVENTORY], unit=self.unit,
        )
        report = self.client.get("/api/reports/inventory/", {"unit": self.unit.pk})
        self.assertEqual(report.status_code, 200, report.data)
        self.assertEqual(report.data["rows"][0]["on_hand_quantity"], "10.00")
        self.assertNotIn("inventory_value", report.data["fields"])
        self.assertNotIn("inventory_value", report.data["rows"][0])
        self.assertEqual(
            self.client.get("/api/reports/inventory/", {"unit": self.roche.pk}).status_code, 403,
        )
        foreign = Location.objects.get(code="SNAT")
        self.assertEqual(self.client.get(
            "/api/reports/inventory/", {"unit": self.unit.pk, "location": foreign.pk},
        ).status_code, 403)
        ReportDataGrant.objects.filter(
            user=self.sales, capability=ReportDataCapability.INVENTORY,
        ).update(revoked_at=timezone.now())
        self.assertEqual(
            self.client.get("/api/reports/inventory/", {"unit": self.unit.pk}).status_code, 403,
        )
        self.assertEqual(self.client.get("/api/stock-balances/", {"unit": self.unit.pk}).status_code, 200)

    def test_removed_location_filter_is_denied_and_explicit_reselect_rechecks_current_scope_without_writes(self):
        self.receive()
        transfer_stock(
            idempotency_key="TEST-filter-recovery-transfer", business_unit=self.unit,
            variant=self.variant, source_location=self.location,
            destination_location=self.other_location, quantity="2.25",
        )
        for panel, capability in (
            (ReportingPanel.INVENTORY, ReportDataCapability.INVENTORY),
            (ReportingPanel.TRANSFER_HISTORY, ReportDataCapability.TRANSFER_HISTORY),
        ):
            self.grant_report_access(self.sales, panel, [capability], unit=self.unit)
        self.location.business_units.remove(self.unit)
        before = self.business_state()
        members = list(self.location.business_units.order_by("pk").values_list("pk", flat=True))
        catalog = self.client.get("/api/reports/")
        self.assertEqual(catalog.status_code, 200)
        for panel in (ReportingPanel.INVENTORY, ReportingPanel.TRANSFER_HISTORY):
            with self.subTest(panel=panel):
                locations = catalog.data["scopes"][panel]["locations"]
                self.assertNotIn(self.location.pk, [row["id"] for row in locations])
                self.assertIn(self.other_location.pk, [row["id"] for row in locations])
                route = f"/api/reports/{panel}/"
                denied = self.client.get(route, {"unit": self.unit.pk, "location": self.location.pk})
                self.assertEqual(denied.status_code, 403, denied.data)
                self.assertNotIn("rows", denied.data)
                allowed = self.client.get(route, {"unit": self.unit.pk, "location": self.other_location.pk})
                self.assertEqual(allowed.status_code, 200, allowed.data)
                self.assertEqual(allowed.data["filters"]["location_id"], self.other_location.pk)
                self.assertEqual(allowed.data["scope"]["business_unit"]["id"], self.unit.pk)
                self.assertTrue(allowed.data["rows"])
                self.assertNotIn("inventory_value", allowed.data["fields"])
                self.assertNotIn("transferred_value", allowed.data["fields"])
                self.assertEqual(self.client.get(route, {"unit": self.roche.pk}).status_code, 403)
        self.assertEqual(self.business_state(), before)
        self.assertEqual(list(self.location.business_units.order_by("pk").values_list("pk", flat=True)), members)

    def test_explicit_filter_clear_does_not_restore_revoked_report_data_or_panel_authority(self):
        self.receive()
        self.grant_report_access(
            self.sales, ReportingPanel.INVENTORY, [ReportDataCapability.INVENTORY], unit=self.unit,
        )
        ReportDataGrant.objects.filter(
            user=self.sales, capability=ReportDataCapability.INVENTORY,
        ).update(revoked_at=timezone.now())
        before = self.business_state()
        self.assertEqual(self.client.get(
            "/api/reports/inventory/", {"unit": self.unit.pk},
        ).status_code, 403)
        self.assertEqual(self.business_state(), before)
        # The pre-existing permission is not recreated by clearing/retrying.
        self.assertFalse(ReportDataGrant.objects.filter(
            user=self.sales, capability=ReportDataCapability.INVENTORY,
            revoked_at__isnull=True,
        ).exists())

    def test_transfer_register_page_scope_masking_and_panel_revocation(self):
        self.receive()
        for index in (1, 2, 3):
            transfer_stock(
                idempotency_key=f"TEST-register-transfer-{index}", business_unit=self.unit,
                variant=self.variant, source_location=self.location,
                destination_location=self.other_location, quantity=str(index),
            )
        self.grant_report_access(
            self.sales, ReportingPanel.TRANSFER_HISTORY,
            [ReportDataCapability.TRANSFER_HISTORY], unit=self.unit,
        )
        before = self.business_state()
        quantities = []
        for page_number in (1, 2, 3):
            response = self.client.get("/api/reports/transfer_history/", {
                "unit": self.unit.pk, "location": self.other_location.pk,
                "page": page_number, "page_size": 1,
            })
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["pagination"]["total_count"], 3)
            self.assertEqual(response.data["pagination"]["page"], page_number)
            self.assertNotIn("transferred_value", response.data["fields"])
            self.assertNotIn("transferred_value", response.data["rows"][0])
            quantities.append(Decimal(response.data["rows"][0]["quantity"]))
        self.assertEqual(quantities, [Decimal(1), Decimal(2), Decimal(3)])
        self.assertEqual(self.business_state(), before)
        ReportPanelGrant.objects.filter(
            user=self.sales, panel=ReportingPanel.TRANSFER_HISTORY,
        ).update(revoked_at=timezone.now())
        self.assertEqual(
            self.client.get("/api/reports/transfer_history/", {"unit": self.unit.pk}).status_code, 403,
        )
