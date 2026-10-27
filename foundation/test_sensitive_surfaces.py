"""Bounded disclosure checks: real Django/PG responses, synthetic fixtures only."""
import json
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

from django.apps import apps
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    IdempotencyRecord, Location, ReportDataCapability, ReportExportAudit,
    QuotePayment, ReportingPanel, StockBalance,
    UserActionGrant,
)
from .purchasing_services import create_purchase_commitment
from .reporting_services import (
    add_reporting_role_member, assign_report_panel, create_reporting_role,
    grant_report_data, revoke_reporting_access,
)
from .test_reporting import ReportingFixtureMixin


COST_FIELDS = {
    "unit_cost", "supplier_unit_cost", "purchase_value", "eligible_landed_charges",
    "landed_cost_allocation", "landed_cost_allocations", "landed_cost_rate_percent",
    "inventory_value", "on_hand_value", "unit_landed_cost", "cost_of_goods_sold",
    "gross_margin", "gross_margin_percent", "value_delta", "transferred_value",
}
SALES_FIELDS = {
    "selling_price", "quantity", "description", "item_id", "warehouse_location",
    "brand_name",
}


class SensitiveSurfaceTests(ReportingFixtureMixin, TestCase):
    def session(self, user=None):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(user or self.sales)
        token = client.get("/api/auth/csrf/").data["csrfToken"]
        return client, token

    def post(self, client, token, path, payload, key=""):
        return client.post(path, payload, format="json", HTTP_X_CSRFTOKEN=token,
                           HTTP_IDEMPOTENCY_KEY=key)

    def facts(self):
        # Excludes auth/session tables and never includes passwords. Captured
        # after fixture authority changes, before the operation being checked.
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in apps.get_app_config("foundation").get_models()
        }

    def assert_no_cost_fields(self, data):
        if isinstance(data, dict):
            self.assertFalse(set(data) & COST_FIELDS, set(data) & COST_FIELDS)
            for value in data.values():
                self.assert_no_cost_fields(value)
        elif isinstance(data, list):
            for value in data:
                self.assert_no_cost_fields(value)

    def committed_payload(self):
        commitment = create_purchase_commitment(
            actor=self.sales, idempotency_key="TEST-private-purchase-price",
            business_unit=self.unit, supplier=self.supplier,
            lines=[{"variant": self.variant, "ordered_quantity": "10.00",
                    "unit_cost": "987.65"}],
        )
        return {
            "business_unit_id": self.unit.pk, "supplier_id": self.supplier.pk,
            "purchase_commitment_id": commitment["purchase_commitment_id"],
            "lines": [{
                "commitment_line_id": commitment["lines"][0]["commitment_line_id"],
                "variant_id": self.variant.pk, "location_id": self.location.pk,
                "quantity": "2.00",
                # Deliberately no unit_cost: this is a stored commitment price,
                # not merely an echo of a cost the requester supplied.
            }],
            "charges": [{"category": "shipping", "amount": "400.00", "eligible": True}],
        }

    def test_receipt_ack_and_retry_do_not_disclose_stored_commitment_cost_without_grants(self):
        payload = self.committed_payload()
        client, token = self.session()
        key = "TEST-private-receipt"
        first = self.post(client, token, "/api/purchasing/receipts/", payload, key)
        self.assertEqual(first.status_code, 201)
        self.assert_no_cost_fields(first.data)
        self.assertEqual(set(first.data), {
            "purchase_receipt_id", "stock_journal_entry_id", "stock_movement_ids",
            "purchase_commitment_id", "currency",
        })
        private = IdempotencyRecord.objects.get(key=key).result
        self.assertEqual(private["purchase_value"], "1975.30")
        self.assertEqual(private["inventory_value"], "2375.30")
        before = self.facts()
        self.sales.is_staff = True
        self.sales.save(update_fields=["is_staff"])
        replay = self.post(client, token, "/api/purchasing/receipts/", payload, key)
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.data, first.data, "Staff alone does not grant costs")
        self.assertEqual(self.facts(), before)
        self.assertEqual(IdempotencyRecord.objects.get(key=key).result, private)
        conflict = self.post(client, token, "/api/purchasing/receipts/",
                             {**payload, "charges": []}, key)
        self.assertEqual(conflict.status_code, 409)
        self.assertNotIn("1975.30", json.dumps(conflict.data))
        self.assertNotIn("987.65", json.dumps(conflict.data))
        self.assertEqual(self.facts(), before)

    def test_receipt_replay_rechecks_independent_cost_scopes_and_role_revocation(self):
        payload = self.committed_payload()
        client, token = self.session()
        key = "TEST-cost-scope-receipt"
        self.assertEqual(self.post(client, token, "/api/purchasing/receipts/", payload, key).status_code, 201)
        private = IdempotencyRecord.objects.get(key=key).result
        supplier_fields = {"purchase_value"}
        landed_fields = {"eligible_landed_charges", "landed_cost_allocations"}
        combined_fields = {"inventory_value", "landed_cost_rate_percent"}
        for caps, unit, expected in (
            ([], self.unit, set()),
            ([ReportDataCapability.SUPPLIER_COST], self.unit, supplier_fields),
            ([ReportDataCapability.LANDED_COST], self.unit, landed_fields),
            ([ReportDataCapability.SUPPLIER_COST, ReportDataCapability.LANDED_COST],
             self.unit, supplier_fields | landed_fields | combined_fields),
            ([ReportDataCapability.SUPPLIER_COST, ReportDataCapability.LANDED_COST],
             self.roche, set()),
        ):
            with self.subTest(capabilities=caps, unit=unit.code):
                grants = [grant_report_data(actor=self.admin, target_user=self.sales,
                          capability=cap, business_unit=unit)[0] for cap in caps]
                try:
                    before = self.facts()
                    response = self.post(client, token, "/api/purchasing/receipts/", payload, key)
                    self.assertEqual(response.status_code, 201)
                    self.assertEqual(set(response.data) & COST_FIELDS, expected)
                    for field in expected:
                        self.assertEqual(response.data[field], private[field])
                    self.assertEqual(self.facts(), before)
                finally:
                    for grant in grants:
                        revoke_reporting_access(actor=self.admin, grant=grant)
        # A panel assignment alone is not cost/data authority.
        assign_report_panel(actor=self.admin, target_user=self.sales, panel=ReportingPanel.PURCHASE_HISTORY)
        self.assert_no_cost_fields(self.post(client, token, "/api/purchasing/receipts/", payload, key).data)
        role = create_reporting_role(actor=self.admin, code="test-cost-scope", name="TEST ONLY cost reviewer")
        member, _ = add_reporting_role_member(actor=self.admin, role=role, target_user=self.sales)
        for cap in (ReportDataCapability.SUPPLIER_COST, ReportDataCapability.LANDED_COST):
            grant_report_data(actor=self.admin, target_role=role, capability=cap, business_unit=self.unit)
        before = self.facts()
        response = self.post(client, token, "/api/purchasing/receipts/", payload, key)
        self.assertEqual(set(response.data) & COST_FIELDS, supplier_fields | landed_fields | combined_fields)
        self.assertEqual(self.facts(), before)
        member.revoked_at = timezone.now()
        member.save(update_fields=["revoked_at"])
        before = self.facts()
        self.assert_no_cost_fields(self.post(client, token, "/api/purchasing/receipts/", payload, key).data)
        self.assertEqual(self.facts(), before)
        self.assertEqual(IdempotencyRecord.objects.get(key=key).result, private)

    def test_quote_action_detail_storage_and_error_responses_have_no_internal_costs(self):
        self.make_receipt(key="TEST-sales-surface-opening")
        client, token = self.session()
        created = self.post(client, token, "/api/purchasing/quotes/", {
            "business_unit_id": self.unit.pk, "customer_id": self.customer.pk,
            "kind": "stock", "discount_reason": "TEST ONLY synthetic price decision",
            "lines": [{"variant_id": self.variant.pk,
            "location_id": self.location.pk, "quantity": "2.00", "specifications": {"fabric": "TEST ONLY"}}],
        }, "TEST-sales-surface-quote")
        self.assertEqual(created.status_code, 201, created.data)
        self.assert_no_cost_fields(created.data)
        prefix = f'/api/purchasing/quotes/{created.data["quote_id"]}'
        hold = self.post(client, token, f"{prefix}/holds/", {"working_days": 3}, "TEST-surface-hold")
        self.assertEqual(hold.status_code, 201)
        self.assert_no_cost_fields(hold.data)
        before = self.facts()
        denied = self.post(client, token, f"{prefix}/deliver/", {}, "TEST-surface-unpaid")
        self.assertEqual(denied.status_code, 400)
        self.assert_no_cost_fields(denied.data)
        self.assertEqual(self.facts(), before)
        payment = self.post(client, token, f"{prefix}/payments/", {
            "amount": "4000.00", "method": QuotePayment.Method.SIMULATED_CASH, "purpose": "quote",
        }, "TEST-surface-payment")
        self.assertEqual(payment.status_code, 201)
        self.assert_no_cost_fields(payment.data)
        ready = self.post(client, token, f"{prefix}/ready/", {}, "TEST-surface-ready")
        self.assertEqual(ready.status_code, 200)
        self.assert_no_cost_fields(ready.data)
        handover = self.post(client, token, f"{prefix}/deliver/", {}, "TEST-surface-delivery")
        self.assertEqual(handover.status_code, 200)
        self.assert_no_cost_fields(handover.data)
        for path, query in (
            (f"{prefix}/", {"business_unit_id": self.unit.pk}),
            ("/api/purchasing/quotes/", {"business_unit_id": self.unit.pk}),
            ("/api/purchasing/storage-charges/", {"business_unit_id": self.unit.pk}),
            (f'/api/purchasing/returns/sales/{created.data["quote_id"]}/', {"business_unit_id": self.unit.pk}),
        ):
            response = client.get(path, query)
            self.assertEqual(response.status_code, 200, path)
            self.assert_no_cost_fields(response.data)
        UserActionGrant.objects.filter(user=self.sales, business_unit=self.unit,
            action=UserActionGrant.Action.CREATE_ORDER).update(revoked_at=timezone.now())
        before = self.facts()
        denied = client.get(f"{prefix}/", {"business_unit_id": self.unit.pk})
        self.assertEqual(denied.status_code, 404)
        self.assert_no_cost_fields(denied.data)
        self.assertEqual(self.facts(), before)

    def test_inventory_fields_and_restricted_registers_remain_scoped(self):
        self.make_receipt(key="TEST-safe-inventory")
        client, token = self.session()
        for path, query in (
            ("/api/stock/", {"unit": self.unit.pk}),
            ("/api/stock-balances/", {"unit": self.unit.pk}),
            ("/api/stock-catalog-matches/", {"unit": self.unit.pk, "code": self.variant.sku}),
            ("/api/stock-receipt-candidates/", {"unit": self.unit.pk, "code": self.variant.sku}),
        ):
            response = client.get(path, query)
            self.assertEqual(response.status_code, 200, path)
            self.assert_no_cost_fields(response.data)
        balance = StockBalance.objects.get(business_unit=self.unit, variant=self.variant, location=self.location)
        detail = client.get(f"/api/stock-balances/{balance.pk}/", {"unit": self.unit.pk})
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn("reserved_quantity", detail.data)
        self.assertNotIn("on_hand_quantity", detail.data)
        for path in (
            "/api/reports/purchase_history/receipts/",
            "/api/reports/inventory/reservations/", "/api/reports/sales/deliveries/",
            "/api/governance/purchasing/counterparties/", f"/api/governance/purchasing/{self.unit.pk}/",
            "/api/governance/warehouse-capacity/audit/",
        ):
            response = client.get(path, {"unit": self.unit.pk})
            self.assertEqual(response.status_code, 403, path)
            self.assert_no_cost_fields(response.data)
        capacity = client.get("/api/warehouse-capacity/")
        self.assertEqual(capacity.status_code, 200)
        for measurement in ("total_floor_area_sqm", "allocated_storage_floor_area_sqm",
                            "received_goods_volume_cbm", "available_floor_area_sqm"):
            self.assertNotIn(measurement, json.dumps(capacity.data))
        roche, _ = self.session(self.roche_user)
        self.assertEqual(roche.get("/api/stock-balances/", {"unit": self.unit.pk}).status_code, 403)
        self.assertEqual(roche.get(f"/api/stock-balances/{balance.pk}/", {"unit": self.roche.pk}).status_code, 404)
        other_location = Location.objects.filter(business_units=self.roche).exclude(
            business_units=self.unit).first()
        self.assertIsNotNone(other_location, "Use an exclusive unit location, not a shared warehouse")
        self.assertEqual(client.get("/api/stock-balances/", {
            "unit": self.unit.pk, "location": other_location.pk,
        }).status_code, 403)

    def test_sales_report_exports_reauthorize_and_include_full_non_cost_results(self):
        self.make_delivered_sale(key="TEST-export-review-one")
        self.make_delivered_sale(key="TEST-export-review-two", quantity="1.00")
        client, token = self.session()
        query = {"unit": self.unit.pk, "period": "monthly", "page_size": 1}
        for fmt in ("xlsx", "docx"):
            denied = self.post(client, token, "/api/reports/sales/export/", {**query, "format": fmt})
            self.assertEqual(denied.status_code, 403)
        self.assertEqual(ReportExportAudit.objects.count(), 0)
        assign_report_panel(actor=self.admin, target_user=self.sales, panel=ReportingPanel.SALES)
        self.assertEqual(client.get("/api/reports/sales/", query).status_code, 403)
        data_grant, _ = grant_report_data(actor=self.admin, target_user=self.sales,
            capability=ReportDataCapability.SALES, business_unit=self.unit)
        report = client.get("/api/reports/sales/", query)
        self.assertEqual(report.status_code, 200)
        self.assertEqual(set(report.data["fields"]), SALES_FIELDS)
        self.assertEqual(len(report.data["rows"]), 1)
        self.assertEqual(report.data["pagination"]["total_count"], 2)
        self.assert_no_cost_fields(report.data)
        for fmt in ("xlsx", "docx"):
            response = self.post(client, token, "/api/reports/sales/export/", {**query, "format": fmt})
            self.assertEqual(response.status_code, 200)
            with ZipFile(BytesIO(response.content)) as office:
                path = "xl/worksheets/sheet1.xml" if fmt == "xlsx" else "word/document.xml"
                root = ElementTree.fromstring(office.read(path))
                ns = "http://schemas.openxmlformats.org/" + (
                    "spreadsheetml/2006/main" if fmt == "xlsx" else "wordprocessingml/2006/main")
                if fmt == "xlsx":
                    rows = root.findall(f".//{{{ns}}}row")
                else:
                    rows = root.findall(f".//{{{ns}}}tr")
                cell_tag = "c" if fmt == "xlsx" else "tc"
                records = [
                    ["".join(cell.itertext()).strip() for cell in row.findall(f"{{{ns}}}{cell_tag}")]
                    for row in rows
                ]
                records = [row for row in records if len(row) == 6 and row[3] == "TVF-002"]
                self.assertEqual(len(records), 2, "Both authorized rows, not the one-row screen page")
                self.assertEqual(sorted(row[1] for row in records), ["1.00", "2.00"])
                text = " ".join(root.itertext())
                self.assertNotIn("2400.00", text, "Private COGS must not enter either Office file")
            audit = ReportExportAudit.objects.order_by("pk").last()
            self.assertFalse({field.split(".")[-1] for field in audit.fields} & COST_FIELDS)
            self.assertEqual(audit.business_unit_ids, [self.unit.pk])
        self.assertEqual(ReportExportAudit.objects.count(), 2)
        revoke_reporting_access(actor=self.admin, grant=data_grant)
        for fmt in ("xlsx", "docx"):
            self.assertEqual(self.post(client, token, "/api/reports/sales/export/",
                {**query, "format": fmt}).status_code, 403)
        self.assertEqual(ReportExportAudit.objects.count(), 2)

    def test_all_unassigned_report_panels_and_exports_deny_without_cost_payloads(self):
        client, token = self.session()
        before = self.facts()
        for panel in ReportingPanel.values:
            for fmt in ("xlsx", "docx"):
                response = self.post(client, token, f"/api/reports/{panel}/export/",
                    {"unit": self.unit.pk, "format": fmt})
                self.assertEqual(response.status_code, 403, (panel, fmt))
                self.assert_no_cost_fields(response.data)
            response = client.get(f"/api/reports/{panel}/", {"unit": self.unit.pk})
            self.assertEqual(response.status_code, 403, panel)
            self.assert_no_cost_fields(response.data)
        self.assertEqual(self.facts(), before)
