from datetime import date, timedelta
from io import BytesIO, StringIO
import os
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    BusinessUnit,
    DemoReportSalesFact,
    DemoUserMarker,
    ReportBudget,
    ReportDataCapability,
    ReportDataGrant,
    ReportExportAudit,
    ReportPanelGrant,
    ReportingPanel,
    ReportingRole,
    ReportingRoleMembership,
)
from .reporting_exports import report_export_manifest
from .reporting_services import add_reporting_role_member, assign_report_panel


FIXTURE_PREFIX = "report-review-v1:"


@override_settings(APP_ENV="demo")
class ReportingReviewFixtureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            call_command("seed_demo", verbosity=0)
            call_command("seed_demo_reports", verbosity=0)
        User = get_user_model()
        cls.admin = User.objects.get(username="demo_admin")
        cls.tivoli = User.objects.get(username="demo_tivoli")
        cls.roche = User.objects.get(username="demo_roche")
        cls.natuzzi = User.objects.get(username="demo_natuzzi")
        cls.units = {
            unit.code: unit
            for unit in BusinessUnit.objects.filter(code__in=("TVL", "RBB", "NAT"))
        }
        cls.manager_role = ReportingRole.objects.get(code="test-report-managing-partner")

    @staticmethod
    def filters(unit, period, start, end):
        return {
            "unit": unit.pk,
            "period": period,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }

    def test_fixture_load_is_repeatable_and_reset_preserves_manual_records_and_password_hashes(self):
        original_passwords = {
            username: get_user_model().objects.get(username=username).password
            for username in ("demo_admin", "demo_tivoli", "demo_roche", "demo_natuzzi")
        }
        self.assertEqual(
            DemoReportSalesFact.objects.filter(demo_fixture_key__startswith=FIXTURE_PREFIX).count(),
            15,
        )
        self.assertEqual(
            ReportBudget.objects.filter(demo_fixture_key__startswith=FIXTURE_PREFIX).count(),
            9,
        )
        first_keys = set(
            DemoReportSalesFact.objects.filter(demo_fixture_key__startswith=FIXTURE_PREFIX)
            .values_list("demo_fixture_key", flat=True)
        )
        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            call_command("seed_demo_reports", verbosity=0)
        self.assertEqual(
            set(
                DemoReportSalesFact.objects.filter(demo_fixture_key__startswith=FIXTURE_PREFIX)
                .values_list("demo_fixture_key", flat=True)
            ),
            first_keys,
        )
        self.assertEqual(
            DemoReportSalesFact.objects.filter(demo_fixture_key__startswith=FIXTURE_PREFIX).count(),
            15,
        )

        manual_budget = ReportBudget.objects.create(
            business_unit=self.units["TVL"],
            metric=ReportBudget.Metric.SUPPLIER_PURCHASE,
            granularity=ReportBudget.Granularity.DAILY,
            period_start=date(2020, 1, 1),
            period_end=date(2020, 1, 2),
            amount="123.00",
            created_by=self.admin,
            updated_by=self.admin,
        )
        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            call_command("seed_demo_reports", "--reset", verbosity=0)
        self.assertEqual(
            DemoReportSalesFact.objects.filter(demo_fixture_key__startswith=FIXTURE_PREFIX).count(),
            0,
        )
        self.assertEqual(
            ReportBudget.objects.filter(demo_fixture_key__startswith=FIXTURE_PREFIX).count(),
            0,
        )
        self.assertTrue(ReportBudget.objects.filter(pk=manual_budget.pk).exists())
        self.assertEqual(
            {
                username: get_user_model().objects.get(username=username).password
                for username in original_passwords
            },
            original_passwords,
        )
        self.assertTrue(DemoUserMarker.objects.filter(user=self.admin, is_synthetic=True).exists())
        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            call_command("seed_demo_reports", verbosity=0)
        self.assertEqual(
            DemoReportSalesFact.objects.filter(demo_fixture_key__startswith=FIXTURE_PREFIX).count(),
            15,
        )

    def test_loader_refuses_non_demo_environment_without_mutating_facts(self):
        before = DemoReportSalesFact.objects.count()
        with patch.dict(os.environ, {"APP_ENV": "production"}):
            with self.assertRaises(CommandError):
                call_command("seed_demo_reports", verbosity=0, stdout=StringIO())
        self.assertEqual(DemoReportSalesFact.objects.count(), before)

    def test_seeded_roles_are_scoped_and_sensitive_data_is_not_granted(self):
        self.assertTrue(self.manager_role.can_manage_report_panels)
        role_specs = {
            "TVL": ("demo_tivoli", "test-report-sales-tvl"),
            "RBB": ("demo_roche", "test-report-sales-rbb"),
            "NAT": ("demo_natuzzi", "test-report-sales-nat"),
        }
        for unit_code, (username, role_code) in role_specs.items():
            user = get_user_model().objects.get(username=username)
            role = ReportingRole.objects.get(code=role_code)
            membership = ReportingRoleMembership.objects.get(user=user, role=role, revoked_at__isnull=True)
            self.assertTrue(membership.is_synthetic)
            capabilities = set(
                ReportDataGrant.objects.filter(role=role, revoked_at__isnull=True)
                .values_list("capability", flat=True)
            )
            self.assertEqual(capabilities, {ReportDataCapability.SALES})
            self.assertEqual(
                set(
                    ReportDataGrant.objects.filter(role=role, revoked_at__isnull=True)
                    .values_list("business_unit__code", flat=True)
                ),
                {unit_code},
            )
            self.assertFalse(
                ReportPanelGrant.objects.filter(
                    role=role,
                    panel__in=(ReportingPanel.COSTS_EXPENSES, ReportingPanel.GROSS_MARGIN),
                    revoked_at__isnull=True,
                ).exists()
            )

        manager_membership = ReportingRoleMembership.objects.get(
            user=self.admin,
            role=self.manager_role,
            revoked_at__isnull=True,
        )
        self.assertTrue(manager_membership.is_synthetic)
        manager_capabilities = set(
            ReportDataGrant.objects.filter(role=self.manager_role, revoked_at__isnull=True)
            .values_list("capability", flat=True)
        )
        self.assertEqual(manager_capabilities, {ReportDataCapability.SALES, ReportDataCapability.BUDGETS})
        self.assertFalse(
            ReportDataGrant.objects.filter(role=self.manager_role)
            .exclude(capability__in=(ReportDataCapability.SALES, ReportDataCapability.BUDGETS))
            .exists()
        )
        admin_client = APIClient()
        admin_client.force_login(self.admin)
        self.assertTrue(admin_client.get("/api/auth/me/").data["can_manage_report_panels"])
        sales_client = APIClient()
        sales_client.force_login(self.tivoli)
        self.assertFalse(sales_client.get("/api/auth/me/").data["can_manage_report_panels"])

    def test_report_only_sales_examples_do_not_enter_operational_sales_or_budget_actuals(self):
        today = timezone.localdate()
        month_start = today.replace(day=1)
        quarter_start = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
        client = APIClient()
        client.force_authenticate(user=self.admin)

        for unit_code, unit in self.units.items():
            for period, start in (
                ("daily", today),
                ("monthly", month_start),
                ("quarterly", quarter_start),
            ):
                end = today
                sales = client.get(
                    "/api/reports/sales/",
                    self.filters(unit, period, start, end),
                )
                self.assertEqual(sales.status_code, 200, sales.data)
                self.assertEqual(sales.data["rows"], [], (unit_code, period))
                self.assertEqual(sales.data["pagination"]["total_count"], 0)
                self.assertEqual(sales.data["summary"], {"sales_amount": "0.00", "quantity": "0.00"})
                self.assertIn("TEST ONLY", sales.data["policy_label"])
                self.assertIn("SalesReportLine records only", sales.data["note"])
                self.assertIn("excluded", sales.data["note"])
                self.assertGreater(
                    DemoReportSalesFact.objects.filter(business_unit=unit).count(),
                    0,
                )
                budgets = client.get(
                    "/api/reports/budgets_actuals/",
                    self.filters(unit, period, start, end),
                )
                self.assertEqual(budgets.status_code, 200, budgets.data)
                self.assertEqual(len(budgets.data["rows"]), 1, (unit_code, period, budgets.data))
                self.assertEqual(budgets.data["rows"][0]["business_unit"], unit_code)
                self.assertIn("TEST ONLY", budgets.data["policy_label"])
                if budgets.data["rows"][0]["metric"] == ReportBudget.Metric.SALES:
                    self.assertEqual(budgets.data["rows"][0]["actual"], "0.00")

    def test_sales_role_cannot_read_cross_unit_reports_or_sensitive_fields(self):
        client = APIClient()
        client.force_authenticate(user=self.roche)
        today = timezone.localdate()
        start = today - timedelta(days=30)
        end = today
        roche_report = client.get(
            "/api/reports/sales/",
            self.filters(self.units["RBB"], "daily", start, end),
        )
        self.assertEqual(roche_report.status_code, 200, roche_report.data)
        self.assertEqual(roche_report.data["rows"], [])
        self.assertEqual(roche_report.data["pagination"]["total_count"], 0)
        self.assertEqual(
            set(roche_report.data["fields"]),
            {
                "selling_price",
                "quantity",
                "description",
                "item_id",
                "warehouse_location",
                "brand_name",
            },
        )
        self.assertNotIn("supplier_unit_cost", roche_report.data["fields"])
        self.assertNotIn("landed_cost", roche_report.data["fields"])
        self.assertNotIn("gross_margin", roche_report.data["fields"])

        cross_unit = client.get(
            "/api/reports/sales/",
            self.filters(self.units["TVL"], "daily", start, end),
        )
        self.assertEqual(cross_unit.status_code, 403, cross_unit.data)

        assign_report_panel(
            actor=self.admin,
            target_user=self.roche,
            panel=ReportingPanel.GROSS_MARGIN,
        )
        denied_margin = client.get(
            "/api/reports/gross_margin/",
            self.filters(self.units["RBB"], "daily", start, end),
        )
        self.assertEqual(denied_margin.status_code, 403, denied_margin.data)

    def test_nonstaff_panel_manager_can_assign_and_revoke_panels_but_not_grant_data(self):
        manager_role = ReportingRole.objects.create(
            code="test-report-panel-manager-only",
            name="TEST ONLY panel manager without data grants",
            can_manage_report_panels=True,
            created_by=self.admin,
            is_synthetic=True,
        )
        add_reporting_role_member(actor=self.admin, role=manager_role, target_user=self.roche)
        client = APIClient()
        client.force_login(self.roche)
        before_data_grants = set(
            ReportDataGrant.objects.filter(user=self.roche, revoked_at__isnull=True)
            .values_list("capability", "business_unit_id")
        )
        self.assertEqual(client.get("/api/governance/report-panels/").status_code, 200)
        self.assertEqual(client.get("/api/governance/reports/").status_code, 403)

        assigned = client.post(
            "/api/governance/report-panels/",
            {
                "action": "assign_panel",
                "panel": ReportingPanel.BUDGETS_ACTUALS,
                "user_id": self.roche.pk,
            },
            format="json",
        )
        self.assertEqual(assigned.status_code, 201, assigned.data)
        after_data_grants = set(
            ReportDataGrant.objects.filter(user=self.roche, revoked_at__isnull=True)
            .values_list("capability", "business_unit_id")
        )
        self.assertEqual(after_data_grants, before_data_grants)
        today = timezone.localdate()
        denied_budget = client.get(
            "/api/reports/budgets_actuals/",
            self.filters(self.units["RBB"], "daily", today, today),
        )
        self.assertEqual(denied_budget.status_code, 403, denied_budget.data)

        ordinary_client = APIClient()
        ordinary_client.force_login(self.natuzzi)
        self.assertEqual(ordinary_client.get("/api/governance/report-panels/").status_code, 403)

        sales_role = ReportingRole.objects.get(code="test-report-sales-tvl")
        role_data_grants_before = set(
            ReportDataGrant.objects.filter(role=sales_role, revoked_at__isnull=True)
            .values_list("capability", "business_unit_id")
        )
        role_assignment = client.post(
            "/api/governance/report-panels/",
            {
                "action": "assign_panel",
                "panel": ReportingPanel.INVENTORY,
                "role_id": sales_role.pk,
            },
            format="json",
        )
        self.assertEqual(role_assignment.status_code, 201, role_assignment.data)
        role_data_grants_after = set(
            ReportDataGrant.objects.filter(role=sales_role, revoked_at__isnull=True)
            .values_list("capability", "business_unit_id")
        )
        self.assertEqual(role_data_grants_after, role_data_grants_before)
        tivoli_client = APIClient()
        tivoli_client.force_authenticate(user=self.tivoli)
        denied_inventory = tivoli_client.get(
            "/api/reports/inventory/",
            self.filters(self.units["TVL"], "daily", today, today),
        )
        self.assertEqual(denied_inventory.status_code, 403, denied_inventory.data)

        unsupported = client.post(
            "/api/governance/report-panels/",
            {"action": "grant_data", "capability": ReportDataCapability.GROSS_MARGIN},
            format="json",
        )
        self.assertEqual(unsupported.status_code, 400, unsupported.data)
        revoked = client.post(
            "/api/governance/report-panels/",
            {"action": "revoke_panel", "grant_id": assigned.data["grant_id"]},
            format="json",
        )
        self.assertEqual(revoked.status_code, 200, revoked.data)
        self.assertTrue(
            ReportPanelGrant.objects.get(pk=assigned.data["grant_id"]).revoked_at is not None
        )
        role_revoked = client.post(
            "/api/governance/report-panels/",
            {"action": "revoke_panel", "grant_id": role_assignment.data["grant_id"]},
            format="json",
        )
        self.assertEqual(role_revoked.status_code, 200, role_revoked.data)
        self.assertTrue(
            ReportPanelGrant.objects.get(pk=role_assignment.data["grant_id"]).revoked_at is not None
        )

    def test_session_exports_exclude_report_only_examples_and_are_audited(self):
        today = timezone.localdate()
        start = today - timedelta(days=30)
        query = self.filters(self.units["RBB"], "daily", start, today)
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.roche)
        csrf_token = client.get("/api/auth/csrf/").data["csrfToken"]

        report = client.get("/api/reports/sales/", query)
        self.assertEqual(report.status_code, 200, report.data)
        self.assertEqual(report.data["rows"], [])
        self.assertEqual(report.data["pagination"]["total_count"], 0)
        self.assertIn("SalesReportLine records only", report.data["note"])
        expected_fields, expected_rows = report_export_manifest(report.data)

        for file_format, archive_path in (
            ("xlsx", "xl/worksheets/sheet1.xml"),
            ("docx", "word/document.xml"),
        ):
            response = client.post(
                "/api/reports/sales/export/",
                {**query, "format": file_format},
                format="json",
                HTTP_X_CSRFTOKEN=csrf_token,
            )
            self.assertEqual(response.status_code, 200, response.content[:300])
            with ZipFile(BytesIO(response.content)) as archive:
                self.assertIsNone(archive.testzip())
                document = archive.read(archive_path).decode()
                ElementTree.fromstring(document)
            self.assertNotIn("RBB-001", document)
            self.assertNotIn("TVF-002", document)
            self.assertNotIn("NAT-001", document)
            self.assertNotIn("supplier_unit_cost", document)

        self.assertEqual(ReportExportAudit.objects.count(), 2)
        for audit in ReportExportAudit.objects.order_by("file_format"):
            self.assertEqual(audit.user_id, self.roche.pk)
            self.assertEqual(audit.business_unit_ids, [self.units["RBB"].pk])
            self.assertEqual(audit.fields, expected_fields)
            self.assertEqual(audit.row_count, expected_rows)

