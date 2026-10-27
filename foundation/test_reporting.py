from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
import subprocess
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree

from django.test import TestCase
from django.utils.functional import SimpleLazyObject
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    Item,
    Location,
    DemoReportSalesFact,
    PurchaseReceipt,
    QuoteReservation,
    ReportBudget,
    ReportDataCapability,
    ReportExportAudit,
    ReportPanelGrant,
    ReportingPanel,
    ReportingRoleMembership,
    SalesQuote,
    SalesReportLine,
    StockBalance,
    StorageCharge,
    UserActionGrant,
)
from .purchasing_services import accrue_storage_charges, deliver_sales_quote, notify_quote_ready
from .reporting_exports import report_export_tables
from .reporting_services import (
    ReportingPermissionDenied,
    add_reporting_role_member,
    assign_report_panel,
    build_report,
    configure_report_budget,
    create_reporting_role,
    grant_report_data,
    list_reporting_configuration,
    revoke_reporting_access,
    set_item_category,
)
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import transfer_stock


class ReportingFixtureMixin(PurchasingFixtureMixin):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()

    def grant_report_access(self, user, panel, capabilities, *, unit=None, company=None):
        assign_report_panel(
            actor=self.admin,
            target_user=user,
            panel=panel,
        )
        for capability in capabilities:
            grant_report_data(
                actor=self.admin,
                target_user=user,
                capability=capability,
                business_unit=unit,
                legal_entity=company,
            )

    def make_delivered_sale(self, *, key="report-sale", quantity="2.00", unit=None, user=None):
        unit = unit or self.unit
        user = user or self.sales
        self.make_receipt(key=f"{key}-receipt", quantity="10.00")
        quote_data = self.make_quote(key=f"{key}-quote", quantity=quantity)
        quote = SalesQuote.objects.get(pk=quote_data["quote_id"])
        self.pay(quote, key=f"{key}-payment", amount=str(quote.total_quote_value))
        notify_quote_ready(
            actor=user,
            quote=quote,
            idempotency_key=f"{key}-ready",
            message="TEST ONLY: synthetic report fixture ready",
        )
        delivered = deliver_sales_quote(
            actor=user,
            quote=quote,
            idempotency_key=f"{key}-delivery",
        )
        return quote, delivered


class ReportingAuthorizationTests(ReportingFixtureMixin, TestCase):
    def test_sales_report_has_only_the_allowlisted_fields_and_does_not_expand_operational_grants(self):
        item = Item.objects.get(pk=self.variant.item_id)
        set_item_category(actor=self.admin, item=item, category_label="TEST ONLY Seating")
        self.make_delivered_sale()
        self.grant_report_access(
            self.sales,
            ReportingPanel.SALES,
            [ReportDataCapability.SALES],
            unit=self.unit,
        )
        original_grants = list(
            UserActionGrant.objects.filter(user=self.sales).values_list(
                "business_unit_id", "action", "revoked_at"
            )
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        response = client.get(
            "/api/reports/sales/",
            {
                "unit": self.unit.pk,
                "period": "daily",
                "start": timezone.localdate().isoformat(),
                "end": timezone.localdate().isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["fields"],
            [
                "selling_price",
                "quantity",
                "description",
                "item_id",
                "warehouse_location",
                "brand_name",
            ],
        )
        self.assertEqual(len(response.data["rows"]), 1)
        row = response.data["rows"][0]
        self.assertEqual(row["item_id"], "TVF-002")
        self.assertEqual(row["selling_price"], "2000.00")
        self.assertEqual(row["quantity"], "2.00")
        self.assertEqual(
            set(row),
            {
                "selling_price",
                "quantity",
                "description",
                "item_id",
                "warehouse_location",
                "brand_name",
            },
        )
        self.assertEqual(response.data["summary"]["sales_amount"], "4000.00")
        self.assertEqual(response.data["summary"]["quantity"], "2.00")
        self.assertNotIn("customer", response.data)
        self.assertNotIn("cost", row)
        self.assertNotIn("supplier", row)
        self.assertEqual(
            list(
                UserActionGrant.objects.filter(user=self.sales).values_list(
                    "business_unit_id", "action", "revoked_at"
                )
            ),
            original_grants,
        )
        self.assertEqual(SalesReportLine.objects.count(), 1)

    def test_ordinary_sales_uses_only_transactional_lines_for_pagination_totals_and_trends(self):
        self.make_delivered_sale(key="operational-pagination-base")
        for index in range(5):
            self.make_delivered_sale(
                key=f"operational-pagination-{index}",
                quantity="1.00",
            )
        today = timezone.localdate()
        local_noon = timezone.make_aware(
            datetime.combine(today, time(hour=12)),
            timezone.get_current_timezone(),
        )
        for index in range(5):
            item = Item.objects.create(
                brand=self.variant.item.brand,
                item_code=f"DEMO-PAGE-{index}",
                description=f"TEST ONLY report-only page {index}",
                is_synthetic=True,
            )
            DemoReportSalesFact.objects.create(
                demo_fixture_key=f"pagination-fact-{index}",
                business_unit=self.unit,
                location=self.location,
                item=item,
                category_label="TEST ONLY Seating",
                quantity=Decimal("1.25"),
                selling_price=Decimal("100.00"),
                revenue=Decimal("9000.00"),
                created_at=local_noon + timedelta(seconds=index),
            )
        self.grant_report_access(
            self.sales,
            ReportingPanel.SALES,
            [ReportDataCapability.SALES],
            unit=self.unit,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        query = {
            "unit": self.unit.pk,
            "period": "daily",
            "start": today.isoformat(),
            "end": today.isoformat(),
            "page_size": 2,
        }
        with (
            patch("foundation.reporting_services._item_aggregation") as item_aggregation,
            patch("foundation.reporting_services._category_aggregation") as category_aggregation,
        ):
            first = client.get("/api/reports/sales/", {**query, "page": 1})
            second = client.get("/api/reports/sales/", {**query, "page": 2})
            third = client.get("/api/reports/sales/", {**query, "page": 3})
            full = build_report(
                actor=self.sales,
                panel=ReportingPanel.SALES,
                query_params=query,
                paginate=False,
            )
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(third.status_code, 200, third.data)
        self.assertEqual(first.data["pagination"]["total_count"], 6)
        self.assertEqual(first.data["pagination"]["total_pages"], 3)
        self.assertEqual(first.data["pagination"]["next_page"], 2)
        self.assertEqual(second.data["pagination"]["previous_page"], 1)
        self.assertEqual(third.data["pagination"]["next_page"], None)
        paged_rows = first.data["rows"] + second.data["rows"] + third.data["rows"]
        self.assertEqual(paged_rows, full["rows"])
        self.assertEqual(len(paged_rows), SalesReportLine.objects.count())
        self.assertTrue(all(not row["item_id"].startswith("DEMO-PAGE-") for row in paged_rows))
        self.assertEqual(len(first.data["rows"]), 2)
        self.assertEqual(set(paged_rows[0]), {
            "selling_price",
            "quantity",
            "description",
            "item_id",
            "warehouse_location",
            "brand_name",
        })
        self.assertEqual(first.data["summary"], {"sales_amount": "14000.00", "quantity": "7.00"})
        self.assertEqual(first.data["trend"], [{
            "period": today.isoformat(),
            "sales_amount": "14000.00",
            "quantity": "7.00",
        }])
        self.assertIn("SalesReportLine records only", first.data["note"])
        self.assertNotIn("DEMO-PAGE", str(first.data))
        item_aggregation.assert_not_called()
        category_aggregation.assert_not_called()

    def test_sales_date_filters_include_local_endpoints_and_exclude_adjacent_days(self):
        report_day = timezone.localdate() - timedelta(days=1)
        start_at = timezone.make_aware(
            datetime.combine(report_day, time.min),
            timezone.get_current_timezone(),
        )
        end_at = timezone.make_aware(
            datetime.combine(report_day + timedelta(days=1), time.min),
            timezone.get_current_timezone(),
        )
        timestamps = [
            start_at - timedelta(microseconds=1),
            start_at,
            end_at - timedelta(microseconds=1),
            end_at,
        ]
        for index, created_at in enumerate(timestamps):
            quote, _ = self.make_delivered_sale(
                key=f"date-boundary-sale-{index}",
                quantity="1.00",
            )
            SalesReportLine.objects.filter(quote_line__quote=quote).update(created_at=created_at)
            item = Item.objects.create(
                brand=self.variant.item.brand,
                item_code=f"DEMO-DATE-{index}",
                description=f"TEST ONLY report-only date {index}",
                is_synthetic=True,
            )
            DemoReportSalesFact.objects.create(
                demo_fixture_key=f"date-boundary-fact-{index}",
                business_unit=self.unit,
                location=self.location,
                item=item,
                quantity=Decimal("1.00"),
                selling_price=Decimal("10.00"),
                revenue=Decimal("10000.00"),
                created_at=created_at,
            )
        self.grant_report_access(
            self.sales,
            ReportingPanel.SALES,
            [ReportDataCapability.SALES],
            unit=self.unit,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        response = client.get(
            "/api/reports/sales/",
            {
                "unit": self.unit.pk,
                "period": "daily",
                "start": report_day.isoformat(),
                "end": report_day.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["pagination"]["total_count"], 2)
        self.assertEqual(response.data["summary"]["sales_amount"], "4000.00")
        self.assertEqual(response.data["summary"]["quantity"], "2.00")
        self.assertTrue(all(not row["item_id"].startswith("DEMO-DATE-") for row in response.data["rows"]))

    def test_generic_report_panels_paginate_after_authorized_unit_filtering(self):
        for variant in (self.variant, self.alternate_variant):
            StockBalance.objects.create(
                legal_entity=self.unit.legal_entity,
                business_unit=self.unit,
                variant=variant,
                location=self.location,
                on_hand_quantity=Decimal("1.00"),
                reserved_quantity=Decimal("0.00"),
                on_hand_value=Decimal("0.00"),
                is_synthetic=True,
            )
        self.grant_report_access(
            self.sales,
            ReportingPanel.INVENTORY,
            [ReportDataCapability.INVENTORY],
            unit=self.unit,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        query = {"unit": self.unit.pk, "page_size": 1}
        first = client.get("/api/reports/inventory/", {**query, "page": 1})
        second = client.get("/api/reports/inventory/", {**query, "page": 2})
        full = build_report(
            actor=self.sales,
            panel=ReportingPanel.INVENTORY,
            query_params=query,
            paginate=False,
        )
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data["pagination"]["total_count"], len(full["rows"]))
        self.assertEqual(
            first.data["rows"] + second.data["rows"],
            full["rows"],
        )
        self.assertEqual(
            client.get("/api/reports/inventory/", {"unit": self.roche.pk, "page": 20}).status_code,
            403,
        )

    def test_panel_assignment_alone_is_not_data_access_and_business_unit_scope_is_enforced(self):
        self.make_delivered_sale()
        panel_grant, _ = assign_report_panel(
            actor=self.admin,
            target_user=self.sales,
            panel=ReportingPanel.SALES,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        favorites_before_data = client.get("/api/favorites/")
        self.assertEqual(favorites_before_data.status_code, 200, favorites_before_data.data)
        self.assertNotIn(
            "reports",
            {item["destination"] for item in favorites_before_data.data["available_destinations"]},
        )
        denied_favorite = client.post("/api/favorites/", {"destination": "reports"}, format="json")
        self.assertEqual(denied_favorite.status_code, 403)
        missing_data = client.get("/api/reports/sales/", {"unit": self.unit.pk})
        self.assertEqual(missing_data.status_code, 403)

        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.SALES,
            business_unit=self.unit,
        )
        favorite_access = client.get("/api/favorites/")
        self.assertIn(
            "reports",
            {item["destination"] for item in favorite_access.data["available_destinations"]},
        )
        favorite = client.post("/api/favorites/", {"destination": "reports"}, format="json")
        self.assertEqual(favorite.status_code, 201, favorite.data)
        revoke_reporting_access(actor=self.admin, grant=panel_grant)
        after_revocation = client.get("/api/favorites/")
        self.assertNotIn(
            "reports",
            {item["destination"] for item in after_revocation.data["available_destinations"]},
        )
        other_unit = client.get("/api/reports/sales/", {"unit": self.roche.pk})
        self.assertEqual(other_unit.status_code, 403)
        other_unit_location = Location.objects.get(code="SRBB")
        wrong_location = client.get(
            "/api/reports/sales/",
            {"unit": self.unit.pk, "location": other_unit_location.pk},
        )
        self.assertEqual(wrong_location.status_code, 403)
        company_view_without_company_grant = client.get(
            "/api/reports/sales/",
            {"company": self.unit.legal_entity_id},
        )
        self.assertEqual(company_view_without_company_grant.status_code, 403)
        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.SALES,
            business_unit=self.roche,
        )
        implicit_cross_unit = client.get("/api/reports/sales/")
        self.assertEqual(implicit_cross_unit.status_code, 403)

    def test_role_assignment_separates_panel_data_scope_and_revocation(self):
        self.make_delivered_sale()
        role = create_reporting_role(
            actor=self.admin,
            code="test-sales-viewer",
            name="TEST ONLY Sales Viewer",
        )
        membership, created = add_reporting_role_member(actor=self.admin, role=role, target_user=self.sales)
        self.assertTrue(created)
        assign_report_panel(
            actor=self.admin,
            target_role=role,
            panel=ReportingPanel.SALES,
        )
        data_grant, _ = grant_report_data(
            actor=self.admin,
            target_role=role,
            capability=ReportDataCapability.SALES,
            business_unit=self.unit,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        response = client.get("/api/reports/sales/", {"unit": self.unit.pk})
        self.assertEqual(response.status_code, 200, response.data)

        revoke_reporting_access(actor=self.admin, grant=data_grant)
        revoked = client.get("/api/reports/sales/", {"unit": self.unit.pk})
        self.assertEqual(revoked.status_code, 403)
        self.assertIsNone(ReportingRoleMembership.objects.get(pk=membership.pk).revoked_at)

    def test_company_consolidation_requires_explicit_company_wide_grant(self):
        self.make_delivered_sale()
        self.grant_report_access(
            self.sales,
            ReportingPanel.SALES,
            [ReportDataCapability.SALES],
            company=self.unit.legal_entity,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        response = client.get(
            "/api/reports/sales/",
            {"company": self.unit.legal_entity_id, "period": "quarterly"},
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["scope"]["scope_type"], "company")
        self.assertEqual(response.data["summary"]["sales_amount"], "4000.00")

    def test_admin_configuration_is_denied_to_ordinary_users(self):
        client = APIClient()
        client.force_authenticate(user=self.sales)
        response = client.get("/api/governance/reports/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            ReportPanelGrant.objects.filter(user=self.sales, panel=ReportingPanel.SALES).count(),
            0,
        )

    def test_session_request_user_configuration_and_report_scope(self):
        staff_client = APIClient()
        staff_client.force_login(self.admin)
        with patch(
            "foundation.reporting_views.list_reporting_configuration",
            wraps=list_reporting_configuration,
        ) as configuration_service:
            configuration = staff_client.get("/api/governance/reports/")
        self.assertEqual(configuration.status_code, 200, configuration.data)
        self.assertIsInstance(
            configuration_service.call_args.kwargs["actor"],
            SimpleLazyObject,
        )

        ordinary_client = APIClient()
        ordinary_client.force_login(self.sales)
        self.assertEqual(ordinary_client.get("/api/governance/reports/").status_code, 403)

        self.make_delivered_sale(key="session-report-scope")
        self.grant_report_access(
            self.sales,
            ReportingPanel.SALES,
            [ReportDataCapability.SALES],
            unit=self.unit,
        )
        authorized = ordinary_client.get(
            "/api/reports/sales/",
            {"unit": self.unit.pk},
        )
        self.assertEqual(authorized.status_code, 200, authorized.data)
        cross_scope = ordinary_client.get(
            "/api/reports/sales/",
            {"unit": self.roche.pk},
        )
        self.assertEqual(cross_scope.status_code, 403)

    def test_anonymous_report_queries_exports_and_configuration_are_denied(self):
        client = APIClient()
        self.assertIn(client.get("/api/reports/").status_code, (401, 403))
        self.assertIn(client.get("/api/reports/sales/").status_code, (401, 403))
        self.assertIn(
            client.post("/api/reports/sales/export/", {"format": "xlsx"}, format="json").status_code,
            (401, 403),
        )
        self.assertIn(client.get("/api/governance/reports/").status_code, (401, 403))
        self.assertEqual(ReportExportAudit.objects.count(), 0)

    def test_staff_can_assign_role_panel_and_scoped_data_via_api(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        role_response = client.post(
            "/api/governance/reports/",
            {"action": "create_role", "code": "test-monthly-sales", "name": "TEST ONLY Monthly Sales"},
            format="json",
        )
        self.assertEqual(role_response.status_code, 201, role_response.data)
        role_id = role_response.data["role_id"]
        member_response = client.post(
            "/api/governance/reports/",
            {"action": "add_member", "role_id": role_id, "user_id": self.sales.pk},
            format="json",
        )
        self.assertEqual(member_response.status_code, 201, member_response.data)
        panel_response = client.post(
            "/api/governance/reports/",
            {"action": "assign_panel", "role_id": role_id, "panel": ReportingPanel.SALES},
            format="json",
        )
        self.assertEqual(panel_response.status_code, 201, panel_response.data)
        data_response = client.post(
            "/api/governance/reports/",
            {
                "action": "grant_data",
                "role_id": role_id,
                "capability": ReportDataCapability.SALES,
                "business_unit_id": self.unit.pk,
            },
            format="json",
        )
        self.assertEqual(data_response.status_code, 201, data_response.data)

        sales_client = APIClient()
        sales_client.force_authenticate(user=self.sales)
        panels = sales_client.get("/api/reports/")
        self.assertEqual(panels.status_code, 200, panels.data)
        self.assertIn(ReportingPanel.SALES, {panel["code"] for panel in panels.data["panels"]})
        report = sales_client.get("/api/reports/sales/", {"unit": self.unit.pk})
        self.assertEqual(report.status_code, 200, report.data)
        revoke_member_response = client.post(
            "/api/governance/reports/",
            {"action": "revoke_member", "membership_id": member_response.data["membership_id"]},
            format="json",
        )
        self.assertEqual(revoke_member_response.status_code, 200, revoke_member_response.data)
        self.assertEqual(sales_client.get("/api/reports/sales/", {"unit": self.unit.pk}).status_code, 403)

    def test_permission_revocation_removes_report_access_immediately(self):
        self.make_delivered_sale()
        panel_grant, _ = assign_report_panel(
            actor=self.admin,
            target_user=self.sales,
            panel=ReportingPanel.SALES,
        )
        data_grant, _ = grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.SALES,
            business_unit=self.unit,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        self.assertEqual(client.get("/api/reports/sales/", {"unit": self.unit.pk}).status_code, 200)
        revoke_reporting_access(actor=self.admin, grant=panel_grant)
        self.assertEqual(client.get("/api/reports/sales/", {"unit": self.unit.pk}).status_code, 403)
        revoke_reporting_access(actor=self.admin, grant=data_grant)


class ReportingSensitiveFieldsTests(ReportingFixtureMixin, TestCase):
    def test_purchase_history_masks_supplier_and_landed_cost_until_each_capability_is_granted(self):
        self.make_receipt(key="purchase-report-receipt", quantity="4.00", unit_cost="1000.00")
        self.grant_report_access(
            self.sales,
            ReportingPanel.PURCHASE_HISTORY,
            [ReportDataCapability.PURCHASE_HISTORY],
            unit=self.unit,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        query = {"unit": self.unit.pk, "start": timezone.localdate().isoformat(), "end": timezone.localdate().isoformat()}
        response = client.get("/api/reports/purchase_history/", query)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("supplier_unit_cost", response.data["fields"])
        self.assertNotIn("purchase_value", response.data["fields"])
        self.assertNotIn("landed_cost_allocation", response.data["fields"])
        self.assertNotIn("purchase_value", response.data["rows"][0])
        masked_export = client.post(
            "/api/reports/purchase_history/export/",
            {**query, "format": "xlsx"},
            format="json",
        )
        self.assertEqual(masked_export.status_code, 200, masked_export.content[:300])
        with ZipFile(BytesIO(masked_export.content)) as archive:
            purchase_sheet = archive.read("xl/worksheets/sheet1.xml").decode()
        self.assertNotIn("supplier_unit_cost", purchase_sheet)
        self.assertNotIn("purchase_value", purchase_sheet)

        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.SUPPLIER_COST,
            business_unit=self.unit,
        )
        supplier_costs = client.get("/api/reports/purchase_history/", query)
        self.assertEqual(supplier_costs.status_code, 200, supplier_costs.data)
        self.assertIn("purchase_value", supplier_costs.data["fields"])
        self.assertNotIn("landed_cost_allocation", supplier_costs.data["fields"])

        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.LANDED_COST,
            business_unit=self.unit,
        )
        landed_costs = client.get("/api/reports/purchase_history/", query)
        self.assertEqual(landed_costs.status_code, 200, landed_costs.data)
        self.assertIn("landed_cost_allocation", landed_costs.data["fields"])


class PurchasingReadSideReportAPITests(ReportingFixtureMixin, TestCase):
    def test_receipt_register_scopes_rows_and_masks_each_cost_capability(self):
        receipt_result = self.make_receipt(
            key="readside-receipt",
            quantity="4.00",
            unit_cost="1000.00",
            charges=[
                {"category": "shipping", "amount": Decimal("200.00"), "eligible": True},
                {"category": "clearance", "amount": Decimal("50.00"), "eligible": True},
            ],
        )
        receipt = PurchaseReceipt.objects.get(pk=receipt_result["purchase_receipt_id"])
        self.grant_report_access(
            self.sales,
            ReportingPanel.PURCHASE_HISTORY,
            [ReportDataCapability.PURCHASE_HISTORY],
            unit=self.unit,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        query = {
            "unit": self.unit.pk,
            "start": timezone.localdate().isoformat(),
            "end": timezone.localdate().isoformat(),
            "page": 1,
            "page_size": 1,
        }
        register = client.get("/api/reports/purchase_history/receipts/", query)
        self.assertEqual(register.status_code, 200, register.data)
        self.assertEqual(register.data["pagination"]["total_count"], 1)
        row = register.data["rows"][0]
        self.assertEqual(row["receipt_id"], receipt.pk)
        self.assertNotIn("purchase_value", row)
        self.assertNotIn("charges", row)
        self.assertNotIn("purchase_value", row["lines"][0])
        self.assertNotIn("landed_cost_allocation", row["lines"][0])

        detail = client.get(
            f"/api/reports/purchase_history/receipts/{receipt.pk}/",
            query,
        )
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["receipt"]["receipt_id"], receipt.pk)
        self.assertNotIn("eligible_landed_charges", detail.data["receipt"])

        supplier_grant, _ = grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.SUPPLIER_COST,
            business_unit=self.unit,
        )
        supplier_visible = client.get("/api/reports/purchase_history/receipts/", query)
        self.assertEqual(supplier_visible.status_code, 200, supplier_visible.data)
        supplier_row = supplier_visible.data["rows"][0]
        self.assertEqual(supplier_row["purchase_value"], "4000.00")
        self.assertEqual(supplier_row["lines"][0]["supplier_unit_cost"], "1000.00")
        self.assertNotIn("landed_cost_allocation", supplier_row["lines"][0])
        self.assertNotIn("eligible_landed_charges", supplier_row)

        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.LANDED_COST,
            business_unit=self.unit,
        )
        both_visible = client.get(
            f"/api/reports/purchase_history/receipts/{receipt.pk}/",
            query,
        )
        self.assertEqual(both_visible.status_code, 200, both_visible.data)
        both_row = both_visible.data["receipt"]
        self.assertEqual(both_row["eligible_landed_charges"], "250.00")
        self.assertEqual(len(both_row["charges"]), 2)
        self.assertEqual(both_row["landed_cost_rate_percent"], "6.2500")
        self.assertEqual(both_row["inventory_value"], "4250.00")
        self.assertEqual(both_row["lines"][0]["landed_cost_allocation"], "250.00")

        revoke_reporting_access(actor=self.admin, grant=supplier_grant)
        landed_only = client.get(
            f"/api/reports/purchase_history/receipts/{receipt.pk}/",
            query,
        )
        self.assertEqual(landed_only.status_code, 200, landed_only.data)
        self.assertNotIn("purchase_value", landed_only.data["receipt"])
        self.assertIn("eligible_landed_charges", landed_only.data["receipt"])
        self.assertNotIn("landed_cost_rate_percent", landed_only.data["receipt"])

        foreign_unit = client.get(
            "/api/reports/purchase_history/receipts/",
            {**query, "unit": self.roche.pk},
        )
        self.assertEqual(foreign_unit.status_code, 403)
        foreign_detail = client.get(
            f"/api/reports/purchase_history/receipts/{receipt.pk}/",
            {**query, "unit": self.roche.pk},
        )
        self.assertEqual(foreign_detail.status_code, 403)
        roche_location = Location.objects.get(code="SRBB")
        wrong_location = client.get(
            "/api/reports/purchase_history/receipts/",
            {**query, "location": roche_location.pk},
        )
        self.assertEqual(wrong_location.status_code, 403)
        client.force_authenticate(user=self.roche_user)
        self.assertEqual(
            client.get("/api/reports/purchase_history/receipts/", query).status_code,
            403,
        )

    def test_reservations_and_deliveries_require_their_existing_report_scopes(self):
        self.make_receipt(key="readside-reservation-stock")
        quote_data = self.make_quote(key="readside-reservation-quote", quantity="2.00")
        quote = SalesQuote.objects.get(pk=quote_data["quote_id"])
        self.pay(quote, key="readside-reservation-deposit", amount="2000.00")
        reservation = QuoteReservation.objects.get(quote_line__quote=quote)
        self.pay(quote, key="readside-reservation-balance", amount="2000.00")
        notify_quote_ready(
            actor=self.sales,
            quote=quote,
            idempotency_key="readside-reservation-ready",
            message="TEST ONLY synthetic ready notification",
        )
        deliver_sales_quote(
            actor=self.sales,
            quote=quote,
            idempotency_key="readside-reservation-delivery",
        )

        self.grant_report_access(
            self.sales,
            ReportingPanel.INVENTORY,
            [ReportDataCapability.INVENTORY],
            unit=self.unit,
        )
        self.grant_report_access(
            self.sales,
            ReportingPanel.SALES,
            [ReportDataCapability.SALES],
            unit=self.unit,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        query = {
            "unit": self.unit.pk,
            "start": timezone.localdate().isoformat(),
            "end": timezone.localdate().isoformat(),
        }

        reservation_list = client.get("/api/reports/inventory/reservations/", query)
        self.assertEqual(reservation_list.status_code, 200, reservation_list.data)
        self.assertEqual(reservation_list.data["pagination"]["total_count"], 1)
        reservation_row = reservation_list.data["rows"][0]
        self.assertEqual(reservation_row["reservation_id"], reservation.pk)
        self.assertEqual(
            set(reservation_row),
            {
                "reservation_id",
                "status",
                "created_at",
                "item_id",
                "description",
                "brand_name",
                "warehouse_location",
                "quantity",
            },
        )
        self.assertNotIn("on_hand_value", reservation_row)
        reservation_detail = client.get(
            f"/api/reports/inventory/reservations/{reservation.pk}/",
            query,
        )
        self.assertEqual(reservation_detail.status_code, 200, reservation_detail.data)
        self.assertEqual(reservation_detail.data["reservation"]["reservation_id"], reservation.pk)
        self.assertEqual(
            client.get(
                "/api/reports/inventory/reservations/",
                {**query, "unit": self.roche.pk},
            ).status_code,
            403,
        )
        reservation_cross_detail = client.get(
            f"/api/reports/inventory/reservations/{reservation.pk}/",
            {**query, "unit": self.roche.pk},
        )
        self.assertEqual(reservation_cross_detail.status_code, 403)
        roche_location = Location.objects.get(code="SRBB")
        reservation_wrong_location = client.get(
            "/api/reports/inventory/reservations/",
            {**query, "location": roche_location.pk},
        )
        self.assertEqual(reservation_wrong_location.status_code, 403)

        delivery_id = SalesReportLine.objects.filter(
            business_unit=self.unit,
            is_synthetic=True,
        ).latest("pk").pk
        deliveries = client.get("/api/reports/sales/deliveries/", query)
        self.assertEqual(deliveries.status_code, 200, deliveries.data)
        self.assertEqual(deliveries.data["pagination"]["total_count"], 1)
        delivery_row = deliveries.data["rows"][0]
        self.assertEqual(delivery_row["delivery_line_id"], delivery_id)
        self.assertNotIn("cost_of_goods_sold", delivery_row)
        self.assertNotIn("revenue", delivery_row)
        self.assertNotIn("customer_id", delivery_row)
        delivery_detail = client.get(
            f"/api/reports/sales/deliveries/{delivery_id}/",
            query,
        )
        self.assertEqual(delivery_detail.status_code, 200, delivery_detail.data)
        self.assertEqual(delivery_detail.data["delivery"]["delivery_line_id"], delivery_id)
        self.assertEqual(
            client.get(
                "/api/reports/sales/deliveries/",
                {**query, "unit": self.roche.pk},
            ).status_code,
            403,
        )
        delivery_cross_detail = client.get(
            f"/api/reports/sales/deliveries/{delivery_id}/",
            {**query, "unit": self.roche.pk},
        )
        self.assertEqual(delivery_cross_detail.status_code, 403)
        delivery_wrong_location = client.get(
            "/api/reports/sales/deliveries/",
            {**query, "location": roche_location.pk},
        )
        self.assertEqual(delivery_wrong_location.status_code, 403)

        client.force_authenticate(user=self.roche_user)
        self.assertEqual(client.get("/api/reports/inventory/reservations/", query).status_code, 403)
        self.assertEqual(client.get("/api/reports/sales/deliveries/", query).status_code, 403)


class PurchasingReadSidePaginationAPITests(ReportingFixtureMixin, TestCase):
    def assert_two_pages(self, client, path, query, expected_ids, id_key):
        self.assertEqual(len(expected_ids), 3, "Fixture should fill two pages of two and one rows.")
        page_rows = []
        for page in (1, 2):
            response = client.get(
                path,
                {**query, "page": page, "page_size": 2},
            )
            self.assertEqual(response.status_code, 200, response.data)
            metadata = response.data["pagination"]
            self.assertEqual(metadata["page"], page)
            self.assertEqual(metadata["page_size"], 2)
            self.assertEqual(metadata["total_count"], 3)
            self.assertEqual(metadata["total_pages"], 2)
            self.assertEqual(metadata["previous_page"], page - 1 if page == 2 else None)
            self.assertEqual(metadata["next_page"], page + 1 if page == 1 else None)
            rows = response.data["rows"]
            self.assertEqual(len(rows), 2 if page == 1 else 1)
            page_rows.extend(rows)
        observed_ids = [row[id_key] for row in page_rows]
        self.assertEqual(observed_ids, expected_ids)
        self.assertEqual(len(set(observed_ids)), 3)
        return page_rows

    def make_delivered_read_fixture(self, key, location):
        self.make_receipt(
            key=f"{key}-receipt",
            location=location,
            quantity="10.00",
            charges=[
                {"category": "shipping", "amount": Decimal("100.00"), "eligible": True},
            ],
        )
        quote_data = self.make_quote(
            key=f"{key}-quote",
            quantity="1.00",
            location=location,
        )
        quote = SalesQuote.objects.get(pk=quote_data["quote_id"])
        self.pay(
            quote,
            key=f"{key}-payment",
            amount=str(quote.total_quote_value),
        )
        notify_quote_ready(
            actor=self.sales,
            quote=quote,
            idempotency_key=f"{key}-ready",
            message="TEST ONLY synthetic pagination fixture ready",
        )
        deliver_sales_quote(
            actor=self.sales,
            quote=quote,
            idempotency_key=f"{key}-delivery",
        )
        return quote

    def test_receipts_and_landed_charges_page_without_gaps_and_keep_cost_masks(self):
        for index, location in enumerate(
            (self.location, self.location, self.other_location),
            start=1,
        ):
            self.make_delivered_read_fixture(f"receipt-pages-{index}", location)

        self.grant_report_access(
            self.sales,
            ReportingPanel.PURCHASE_HISTORY,
            [ReportDataCapability.PURCHASE_HISTORY],
            unit=self.unit,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        query = {
            "unit": self.unit.pk,
            "start": timezone.localdate().isoformat(),
            "end": timezone.localdate().isoformat(),
        }
        expected_receipts = list(
            PurchaseReceipt.objects.filter(
                business_unit=self.unit,
                is_synthetic=True,
            ).order_by("-created_at", "-pk").values_list("pk", flat=True)
        )
        masked_rows = self.assert_two_pages(
            client,
            "/api/reports/purchase_history/receipts/",
            query,
            expected_receipts,
            "receipt_id",
        )
        for row in masked_rows:
            self.assertNotIn("purchase_value", row)
            self.assertNotIn("eligible_landed_charges", row)
            self.assertNotIn("charges", row)
            self.assertNotIn("supplier_unit_cost", row["lines"][0])
            self.assertNotIn("landed_cost_allocation", row["lines"][0])

        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.SUPPLIER_COST,
            business_unit=self.unit,
        )
        supplier_rows = self.assert_two_pages(
            client,
            "/api/reports/purchase_history/receipts/",
            query,
            expected_receipts,
            "receipt_id",
        )
        for row in supplier_rows:
            self.assertIn("purchase_value", row)
            self.assertIn("supplier_unit_cost", row["lines"][0])
            self.assertNotIn("charges", row)
            self.assertNotIn("landed_cost_allocation", row["lines"][0])
            self.assertNotIn("landed_cost_rate_percent", row)

        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.LANDED_COST,
            business_unit=self.unit,
        )
        authorized_rows = self.assert_two_pages(
            client,
            "/api/reports/purchase_history/receipts/",
            query,
            expected_receipts,
            "receipt_id",
        )
        for row in authorized_rows:
            self.assertEqual(len(row["charges"]), 1)
            self.assertIn("landed_cost_allocation", row["lines"][0])
            self.assertIn("landed_cost_rate_percent", row)

        location_rows = client.get(
            "/api/reports/purchase_history/receipts/",
            {**query, "location": self.location.pk, "page_size": 2},
        )
        self.assertEqual(location_rows.status_code, 200, location_rows.data)
        expected_location_ids = list(
            PurchaseReceipt.objects.filter(
                business_unit=self.unit,
                lines__location=self.location,
                is_synthetic=True,
            ).order_by("-created_at", "-pk").distinct().values_list("pk", flat=True)
        )
        self.assertEqual(location_rows.data["pagination"]["total_count"], 2)
        self.assertEqual(
            [row["receipt_id"] for row in location_rows.data["rows"]],
            expected_location_ids,
        )
        self.assertEqual(
            client.get(
                "/api/reports/purchase_history/receipts/",
                {**query, "unit": self.roche.pk},
            ).status_code,
            403,
        )
        roche_location = Location.objects.get(code="SRBB")
        self.assertEqual(
            client.get(
                "/api/reports/purchase_history/receipts/",
                {**query, "location": roche_location.pk},
            ).status_code,
            403,
        )
        client.force_authenticate(user=self.roche_user)
        self.assertEqual(
            client.get("/api/reports/purchase_history/receipts/", query).status_code,
            403,
        )

    def test_reservation_and_delivery_registers_page_scope_and_mask_fields(self):
        for index, location in enumerate(
            (self.location, self.location, self.other_location),
            start=1,
        ):
            self.make_delivered_read_fixture(f"movement-pages-{index}", location)

        self.grant_report_access(
            self.sales,
            ReportingPanel.INVENTORY,
            [ReportDataCapability.INVENTORY],
            unit=self.unit,
        )
        self.grant_report_access(
            self.sales,
            ReportingPanel.SALES,
            [ReportDataCapability.SALES],
            unit=self.unit,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        query = {
            "unit": self.unit.pk,
            "start": timezone.localdate().isoformat(),
            "end": timezone.localdate().isoformat(),
        }

        expected_reservations = list(
            QuoteReservation.objects.filter(
                quote_line__quote__business_unit=self.unit,
                is_synthetic=True,
            ).order_by("-stock_reservation__created_at", "-pk").values_list("pk", flat=True)
        )
        reservations = self.assert_two_pages(
            client,
            "/api/reports/inventory/reservations/",
            query,
            expected_reservations,
            "reservation_id",
        )
        for row in reservations:
            self.assertEqual(
                set(row),
                {
                    "reservation_id",
                    "status",
                    "created_at",
                    "item_id",
                    "description",
                    "brand_name",
                    "warehouse_location",
                    "quantity",
                },
            )
            self.assertNotIn("on_hand_value", row)
            self.assertNotIn("supplier_unit_cost", row)

        expected_deliveries = list(
            SalesReportLine.objects.filter(
                business_unit=self.unit,
                is_synthetic=True,
            ).order_by("-created_at", "-pk").values_list("pk", flat=True)
        )
        deliveries = self.assert_two_pages(
            client,
            "/api/reports/sales/deliveries/",
            query,
            expected_deliveries,
            "delivery_line_id",
        )
        for row in deliveries:
            self.assertEqual(
                set(row),
                {
                    "delivery_line_id",
                    "delivered_at",
                    "selling_price",
                    "quantity",
                    "description",
                    "item_id",
                    "warehouse_location",
                    "brand_name",
                },
            )
            self.assertNotIn("revenue", row)
            self.assertNotIn("cost_of_goods_sold", row)

        expected_location_reservations = list(
            QuoteReservation.objects.filter(
                quote_line__quote__business_unit=self.unit,
                stock_reservation__balance__location=self.location,
                is_synthetic=True,
            ).order_by("-stock_reservation__created_at", "-pk").values_list("pk", flat=True)
        )
        location_reservations = client.get(
            "/api/reports/inventory/reservations/",
            {**query, "location": self.location.pk, "page_size": 2},
        )
        self.assertEqual(location_reservations.status_code, 200, location_reservations.data)
        self.assertEqual(location_reservations.data["pagination"]["total_count"], 2)
        self.assertEqual(
            [row["reservation_id"] for row in location_reservations.data["rows"]],
            expected_location_reservations,
        )
        self.assertEqual(len(expected_location_reservations), 2)

        expected_location_deliveries = list(
            SalesReportLine.objects.filter(
                business_unit=self.unit,
                location=self.location,
                is_synthetic=True,
            ).order_by("-created_at", "-pk").values_list("pk", flat=True)
        )
        location_deliveries = client.get(
            "/api/reports/sales/deliveries/",
            {**query, "location": self.location.pk, "page_size": 2},
        )
        self.assertEqual(location_deliveries.status_code, 200, location_deliveries.data)
        self.assertEqual(location_deliveries.data["pagination"]["total_count"], 2)
        self.assertEqual(
            [row["delivery_line_id"] for row in location_deliveries.data["rows"]],
            expected_location_deliveries,
        )
        self.assertEqual(len(expected_location_deliveries), 2)

        for path in (
            "/api/reports/inventory/reservations/",
            "/api/reports/sales/deliveries/",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    client.get(path, {**query, "unit": self.roche.pk}).status_code,
                    403,
                )
                self.assertEqual(
                    client.get(
                        path,
                        {
                            **query,
                            "location": Location.objects.get(
                                code="SRBB"
                            ).pk,
                        },
                    ).status_code,
                    403,
                )
        client.force_authenticate(user=self.roche_user)
        self.assertEqual(
            client.get("/api/reports/inventory/reservations/", query).status_code,
            403,
        )
        self.assertEqual(
            client.get("/api/reports/sales/deliveries/", query).status_code,
            403,
        )


class ReportingSensitiveReportAPITests(ReportingFixtureMixin, TestCase):
    def test_best_seller_categories_require_separate_category_capability(self):
        item = Item.objects.get(pk=self.variant.item_id)
        set_item_category(actor=self.admin, item=item, category_label="TEST ONLY Seating")
        self.make_delivered_sale()
        demo_item = Item.objects.create(
            brand=self.variant.item.brand,
            item_code="DEMO-BEST-SELLER",
            description="TEST ONLY report-only best seller",
            is_synthetic=True,
        )
        DemoReportSalesFact.objects.create(
            demo_fixture_key="report-only-best-seller-decoy",
            business_unit=self.unit,
            location=self.location,
            item=demo_item,
            category_label="TEST ONLY report-only category",
            quantity=Decimal("90.00"),
            selling_price=Decimal("9999.00"),
            revenue=Decimal("899910.00"),
            created_at=timezone.now(),
        )
        self.grant_report_access(
            self.sales,
            ReportingPanel.BEST_SELLERS,
            [ReportDataCapability.SALES],
            unit=self.unit,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        query = {"unit": self.unit.pk}
        without_category = client.get("/api/reports/best_sellers/", query)
        self.assertEqual(without_category.status_code, 200, without_category.data)
        self.assertTrue(without_category.data["categories_permission_required"])
        self.assertIsNone(without_category.data["best_selling_categories"])
        self.assertEqual(without_category.data["summary"], {"sales_amount": "4000.00", "quantity": "2.00"})
        self.assertEqual(len(without_category.data["best_selling_items"]), 1)
        self.assertEqual(without_category.data["best_selling_items"][0]["item_id"], "TVF-002")
        self.assertNotIn("DEMO-BEST-SELLER", str(without_category.data))

        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.CATEGORY_SALES,
            business_unit=self.unit,
        )
        with_category = client.get("/api/reports/best_sellers/", query)
        self.assertEqual(with_category.status_code, 200, with_category.data)
        self.assertEqual(with_category.data["best_selling_categories"][0]["category"], "TEST ONLY Seating")
        self.assertEqual(len(with_category.data["best_selling_categories"]), 1)
        self.assertNotIn("report-only category", str(with_category.data))

    def test_gross_margin_and_inventory_values_require_explicit_cost_permissions(self):
        self.make_delivered_sale(key="margin-sale")
        assign_report_panel(actor=self.admin, target_user=self.sales, panel=ReportingPanel.GROSS_MARGIN)
        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.SALES,
            business_unit=self.unit,
        )
        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.GROSS_MARGIN,
            business_unit=self.unit,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        denied = client.get("/api/reports/gross_margin/", {"unit": self.unit.pk})
        self.assertEqual(denied.status_code, 403)

        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.SUPPLIER_COST,
            business_unit=self.unit,
        )
        margin = client.get("/api/reports/gross_margin/", {"unit": self.unit.pk})
        self.assertEqual(margin.status_code, 200, margin.data)
        self.assertIn("cost_of_goods_sold", margin.data["fields"])

        assign_report_panel(actor=self.admin, target_user=self.sales, panel=ReportingPanel.INVENTORY)
        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.INVENTORY,
            business_unit=self.unit,
        )
        inventory = client.get("/api/reports/inventory/", {"unit": self.unit.pk})
        self.assertEqual(inventory.status_code, 200, inventory.data)
        self.assertNotIn("inventory_value", inventory.data["fields"])
        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.LANDED_COST,
            business_unit=self.unit,
        )
        valued_inventory = client.get("/api/reports/inventory/", {"unit": self.unit.pk})
        self.assertEqual(valued_inventory.status_code, 200, valued_inventory.data)
        self.assertIn("inventory_value", valued_inventory.data["fields"])

    def test_transfer_value_is_masked_without_both_cost_capabilities(self):
        self.make_receipt(key="transfer-report-receipt", quantity="5.00")
        transfer_stock(
            idempotency_key="report-transfer",
            business_unit=self.unit,
            variant=self.variant,
            source_location=self.location,
            destination_location=self.other_location,
            quantity=Decimal("1.00"),
        )
        self.grant_report_access(
            self.sales,
            ReportingPanel.TRANSFER_HISTORY,
            [ReportDataCapability.TRANSFER_HISTORY],
            unit=self.unit,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        query = {"unit": self.unit.pk}
        masked = client.get("/api/reports/transfer_history/", query)
        self.assertEqual(masked.status_code, 200, masked.data)
        self.assertNotIn("transferred_value", masked.data["fields"])
        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.SUPPLIER_COST,
            business_unit=self.unit,
        )
        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.LANDED_COST,
            business_unit=self.unit,
        )
        full = client.get("/api/reports/transfer_history/", query)
        self.assertEqual(full.status_code, 200, full.data)
        self.assertIn("transferred_value", full.data["fields"])


class ReportingExportAndBudgetTests(ReportingFixtureMixin, TestCase):
    def test_bounded_operational_sales_page_matches_unlimited_order_across_pages(self):
        from .reporting_services import _sales_page_queryset

        self.make_delivered_sale(key="bounded-operational-base")
        for index in range(5):
            self.make_delivered_sale(
                key=f"bounded-operational-{index}",
                quantity="1.00",
            )
        lines = SalesReportLine.objects.filter(business_unit=self.unit)
        for index in range(5):
            item = Item.objects.create(
                brand=self.variant.item.brand,
                item_code=f"BOUNDED-DEMO-{index}",
                description=f"TEST ONLY bounded demo {index}",
                is_synthetic=True,
            )
            DemoReportSalesFact.objects.create(
                demo_fixture_key=f"bounded-demo-{index}",
                business_unit=self.unit, location=self.location, item=item,
                quantity=Decimal("1.00"), selling_price=Decimal("999.99"),
                revenue=Decimal("999.99"), created_at=timezone.now(),
            )
        expected = list(_sales_page_queryset(lines))
        self.assertEqual(len(expected), 6)
        for page_size in (1, 2, 3, 4):
            actual = []
            for offset in range(0, len(expected), page_size):
                stop = offset + page_size
                rows = list(_sales_page_queryset(
                    lines, row_limit=stop,
                )[offset:stop])
                self.assertEqual(rows, expected[offset:stop])
                actual.extend(rows)
            self.assertEqual(actual, expected)

    def test_xlsx_and_docx_exports_match_sales_field_allowlist_and_are_audited_with_csrf(self):
        self.make_delivered_sale()
        example_item = Item.objects.create(
            brand=self.variant.item.brand,
            item_code="DEMO-EXPORT-ONLY",
            description="TEST ONLY report-only export decoy",
            is_synthetic=True,
        )
        DemoReportSalesFact.objects.create(
            demo_fixture_key="report-only-export-decoy",
            business_unit=self.unit,
            location=self.location,
            item=example_item,
            quantity=Decimal("9.00"),
            selling_price=Decimal("9999.00"),
            revenue=Decimal("89991.00"),
            created_at=timezone.now(),
        )
        self.grant_report_access(
            self.sales,
            ReportingPanel.SALES,
            [ReportDataCapability.SALES],
            unit=self.unit,
        )
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.sales)
        query = {
            "unit": self.unit.pk,
            "start": timezone.localdate().isoformat(),
            "end": timezone.localdate().isoformat(),
        }
        no_csrf = client.post(
            "/api/reports/sales/export/",
            {**query, "format": "xlsx"},
            format="json",
        )
        self.assertEqual(no_csrf.status_code, 403)
        self.assertEqual(ReportExportAudit.objects.count(), 0)
        csrf_token = client.get("/api/auth/csrf/").data["csrfToken"]

        xlsx = client.post(
            "/api/reports/sales/export/",
            {**query, "format": "xlsx"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(xlsx.status_code, 200, xlsx.content[:300])
        with ZipFile(BytesIO(xlsx.content)) as archive:
            self.assertIsNone(archive.testzip())
            sheet = archive.read("xl/worksheets/sheet1.xml").decode()
            ElementTree.fromstring(sheet)
        self.assertIn("selling_price", sheet)
        self.assertIn("TVF-002", sheet)
        self.assertNotIn("DEMO-EXPORT-ONLY", sheet)
        self.assertNotIn("report-only export decoy", sheet)
        self.assertNotIn("supplier_unit_cost", sheet)
        self.assertNotIn("TEST-CUST", sheet)
        # Exercise the existing application XLSX reader, not just ZIP/XML shape.
        parsed = subprocess.run(
            ["node", "--import", "tsx", "scripts/read-report-xlsx.mjs"],
            input=xlsx.content,
            capture_output=True,
            cwd=Path(__file__).resolve().parent.parent,
            timeout=30,
            check=True,
        )
        workbook = json.loads(parsed.stdout)
        self.assertEqual(len(workbook["sheets"]), 1)
        reader_text = json.dumps(workbook, ensure_ascii=False)
        self.assertIn("selling_price", reader_text)
        self.assertIn("TVF-002", reader_text)
        self.assertIn("4000.00", reader_text)
        self.assertNotIn("DEMO-EXPORT-ONLY", reader_text)
        self.assertNotIn("supplier_unit_cost", reader_text)
        authorized_report = build_report(
            actor=self.sales, panel=ReportingPanel.SALES, query_params=query, paginate=False
        )
        expected_tables = report_export_tables(authorized_report)
        # The reader preserves text and numeric cells as strings.
        read_rows = workbook["sheets"][0]["rows"]
        for title, _key, fields, rows in expected_tables:
            title_index = next(index for index, row in enumerate(read_rows) if row[0] == title)
            self.assertEqual(read_rows[title_index + 1][:len(fields)], fields)
            self.assertEqual(
                [row[:len(fields)] for row in read_rows[title_index + 2:title_index + 2 + len(rows)]],
                [[str(row.get(field, "")) for field in fields] for row in rows],
            )

        docx = client.post(
            "/api/reports/sales/export/",
            {**query, "format": "docx"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(docx.status_code, 200, docx.content[:300])
        with ZipFile(BytesIO(docx.content)) as archive:
            self.assertIsNone(archive.testzip())
            document = archive.read("word/document.xml").decode()
            ElementTree.fromstring(document)
        self.assertIn("selling_price", document)
        self.assertIn("TVF-002", document)
        self.assertNotIn("DEMO-EXPORT-ONLY", document)
        self.assertNotIn("report-only export decoy", document)
        self.assertNotIn("supplier_unit_cost", document)
        # Structural/content proof only: no Office-compatible DOCX reader is
        # installed. This must not be described as Word interoperability.
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        word_tables = ElementTree.fromstring(document).findall(".//w:tbl", ns)
        self.assertEqual(len(word_tables), len(expected_tables))
        for word_table, (_title, _key, fields, rows) in zip(word_tables, expected_tables):
            actual = [
                [
                    "".join(cell.itertext())  # compact writer has only text nodes
                    for cell in table_row.findall("w:tc", ns)
                ]
                for table_row in word_table.findall("w:tr", ns)
            ]
            self.assertEqual(actual, [fields] + [
                [str(row.get(field, "")) for field in fields] for row in rows
            ])
        self.assertEqual(ReportExportAudit.objects.count(), 2)
        self.assertEqual(
            set(ReportExportAudit.objects.order_by("file_format").values_list("file_format", flat=True)),
            {"xlsx", "docx"},
        )
        for audit in ReportExportAudit.objects.all():
            self.assertEqual(
                audit.fields,
                [
                    "selling_price",
                    "quantity",
                    "description",
                    "item_id",
                    "warehouse_location",
                    "brand_name",
                    "summary.sales_amount",
                    "summary.quantity",
                    "trend.period",
                    "trend.sales_amount",
                    "trend.quantity",
                ],
            )
            self.assertEqual(audit.row_count, 3)
            self.assertEqual(audit.business_unit_ids, [self.unit.pk])

    def test_exports_include_the_full_authorized_set_even_when_a_screen_page_is_selected(self):
        self.make_delivered_sale()
        for index in range(5):
            self.make_delivered_sale(
                key=f"paged-export-operational-{index}",
                quantity="1.00",
            )
        today = timezone.localdate()
        local_noon = timezone.make_aware(
            datetime.combine(today, time(hour=12)),
            timezone.get_current_timezone(),
        )
        for index in range(5):
            item = Item.objects.create(
                brand=self.variant.item.brand,
                item_code=f"TEST-EXPORT-{index}",
                description=f"TEST ONLY paged export row {index}",
                category_label="TEST ONLY export",
                is_synthetic=True,
            )
            DemoReportSalesFact.objects.create(
                demo_fixture_key=f"paged-export-fact-{index}",
                business_unit=self.unit,
                location=self.location,
                item=item,
                category_label="TEST ONLY export",
                quantity=Decimal("1.00"),
                selling_price=Decimal("99.00"),
                revenue=Decimal("99.00"),
                created_at=local_noon + timedelta(seconds=index),
            )
        self.grant_report_access(
            self.sales,
            ReportingPanel.SALES,
            [ReportDataCapability.SALES],
            unit=self.unit,
        )
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.sales)
        query = {
            "unit": self.unit.pk,
            "period": "daily",
            "start": today.isoformat(),
            "end": today.isoformat(),
            "page_size": 2,
        }
        page_rows = []
        for page in (1, 2, 3):
            response = client.get("/api/reports/sales/", {**query, "page": page})
            self.assertEqual(response.status_code, 200, response.data)
            page_rows.extend(response.data["rows"])
        self.assertEqual(len(page_rows), 6)
        self.assertEqual(len(client.get("/api/reports/sales/", {**query, "page": 1}).data["rows"]), 2)

        csrf_token = client.get("/api/auth/csrf/").data["csrfToken"]
        xlsx = client.post(
            "/api/reports/sales/export/",
            {**query, "page": 2, "format": "xlsx"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        docx = client.post(
            "/api/reports/sales/export/",
            {**query, "page": 2, "format": "docx"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(xlsx.status_code, 200, xlsx.content[:300])
        self.assertEqual(docx.status_code, 200, docx.content[:300])
        with ZipFile(BytesIO(xlsx.content)) as archive:
            sheet_root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            sheet_text = "".join(sheet_root.itertext())
            spreadsheet_rows = sheet_root.findall(
                ".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"
            )
        with ZipFile(BytesIO(docx.content)) as archive:
            document = ElementTree.fromstring(archive.read("word/document.xml"))
        document_text = "".join(document.itertext())
        self.assertGreaterEqual(len(spreadsheet_rows), 7)
        self.assertEqual(sheet_text.count("TVF-002"), 6)
        self.assertEqual(document_text.count("TVF-002"), 6)
        self.assertNotIn("TEST ONLY paged export row 4", sheet_text)
        self.assertNotIn("TEST ONLY paged export row 4", document_text)
        self.assertNotIn("TEST-EXPORT-4", sheet_text + document_text)
        self.assertNotIn("cost_of_goods_sold", sheet_text + document_text)
        audits = list(ReportExportAudit.objects.order_by("file_format"))
        self.assertEqual([audit.row_count for audit in audits], [8, 8])
        expected_fields = [
            "selling_price",
            "quantity",
            "description",
            "item_id",
            "warehouse_location",
            "brand_name",
            "summary.sales_amount",
            "summary.quantity",
            "trend.period",
            "trend.sales_amount",
            "trend.quantity",
        ]
        self.assertTrue(all(audit.fields == expected_fields for audit in audits))

    def test_export_requires_both_panel_and_data_grants_and_does_not_audit_denial(self):
        self.make_delivered_sale()
        client = APIClient()
        client.force_authenticate(user=self.sales)
        denied = client.post(
            "/api/reports/sales/export/",
            {"format": "xlsx", "unit": self.unit.pk},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(ReportExportAudit.objects.count(), 0)

    def test_budget_actuals_use_explicit_budget_and_actual_permissions(self):
        quote, _ = self.make_delivered_sale(key="budget-sale")
        DemoReportSalesFact.objects.create(
            demo_fixture_key="budget-report-only-sales-decoy",
            business_unit=self.unit,
            location=self.location,
            item=self.variant.item,
            quantity=Decimal("9.00"),
            selling_price=Decimal("9999.00"),
            revenue=Decimal("89991.00"),
            created_at=timezone.now(),
        )
        today = timezone.localdate()
        start = today.replace(day=1)
        if today.month == 12:
            end = date(today.year + 1, 1, 1)
        else:
            end = date(today.year, today.month + 1, 1)
        budget = configure_report_budget(
            actor=self.admin,
            business_unit=self.unit,
            metric=ReportBudget.Metric.SALES,
            granularity=ReportBudget.Granularity.MONTHLY,
            period_start=start.isoformat(),
            period_end=end.isoformat(),
            amount="5000.00",
        )
        self.assertEqual(budget.amount, Decimal("5000.00"))
        assign_report_panel(
            actor=self.admin,
            target_user=self.sales,
            panel=ReportingPanel.BUDGETS_ACTUALS,
        )
        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.BUDGETS,
            business_unit=self.unit,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        query = {
            "unit": self.unit.pk,
            "period": "monthly",
            "start": start.isoformat(),
            "end": today.isoformat(),
        }
        hidden_actual = client.get("/api/reports/budgets_actuals/", query)
        self.assertEqual(hidden_actual.status_code, 200, hidden_actual.data)
        self.assertEqual(hidden_actual.data["rows"], [])

        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.SALES,
            business_unit=self.unit,
        )
        visible = client.get("/api/reports/budgets_actuals/", query)
        self.assertEqual(visible.status_code, 200, visible.data)
        self.assertEqual(len(visible.data["rows"]), 1)
        row = visible.data["rows"][0]
        self.assertEqual(row["budget"], "5000.00")
        self.assertEqual(row["actual"], format(quote.total_quote_value, ".2f"))
        self.assertEqual(row["variance_actual_minus_budget"], format(quote.total_quote_value - Decimal("5000"), ".2f"))

    def test_staff_may_configure_only_test_budget_and_category_values(self):
        item = Item.objects.get(pk=self.variant.item_id)
        response = set_item_category(actor=self.admin, item=item, category_label="TEST ONLY Seating")
        self.assertEqual(response.category_label, "TEST ONLY Seating")
        with self.assertRaises(ReportingPermissionDenied):
            set_item_category(actor=self.sales, item=item, category_label="Unauthorized")
        with self.assertRaises(ReportingPermissionDenied):
            configure_report_budget(
                actor=self.sales,
                business_unit=self.unit,
                metric=ReportBudget.Metric.SALES,
                granularity=ReportBudget.Granularity.MONTHLY,
                period_start=timezone.localdate().replace(day=1).isoformat(),
                period_end=date(timezone.localdate().year, timezone.localdate().month, 28).isoformat(),
                amount="1.00",
            )

    def test_cost_and_expense_panel_denies_missing_capabilities_and_scopes_each_field(self):
        self.make_delivered_sale(key="cost-expense-sale")
        assign_report_panel(
            actor=self.admin,
            target_user=self.sales,
            panel=ReportingPanel.COSTS_EXPENSES,
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        query = {"unit": self.unit.pk}
        denied = client.get("/api/reports/costs_expenses/", query)
        self.assertEqual(denied.status_code, 403)

        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.SUPPLIER_COST,
            business_unit=self.unit,
        )
        costs_only = client.get("/api/reports/costs_expenses/", query)
        self.assertEqual(costs_only.status_code, 200, costs_only.data)
        self.assertIn("supplier_purchase_value", costs_only.data["fields"])
        self.assertNotIn("posted_expense_accounts", costs_only.data["fields"])

        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.EXPENSES,
            business_unit=self.unit,
        )
        costs_and_expenses = client.get("/api/reports/costs_expenses/", query)
        self.assertEqual(costs_and_expenses.status_code, 200, costs_and_expenses.data)
        self.assertIn("posted_expense_accounts", costs_and_expenses.data["fields"])
        showroom_expenses = client.get(
            "/api/reports/costs_expenses/",
            {**query, "location": self.location.pk},
        )
        self.assertEqual(showroom_expenses.status_code, 409)
