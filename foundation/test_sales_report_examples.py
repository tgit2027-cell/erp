from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    DemoReportSalesFact,
    Location,
    ReportDataCapability,
    ReportExportAudit,
    ReportingPanel,
)
from .reporting_services import assign_report_panel, grant_report_data
from .test_reporting import ReportingFixtureMixin


class SalesExamplesAPITests(ReportingFixtureMixin, TestCase):
    def _sample(self, key, *, location, quantity, revenue, created_at=None):
        return DemoReportSalesFact.objects.create(
            demo_fixture_key=key,
            business_unit=self.unit,
            location=location,
            item=self.variant.item,
            category_label="TEST ONLY synthetic example",
            quantity=Decimal(quantity),
            selling_price=Decimal(revenue),
            revenue=Decimal(revenue),
            created_at=created_at or timezone.now(),
        )

    def _query(self, *, unit=None, location=None):
        today = timezone.localdate()
        return {
            "unit": (unit or self.unit).pk,
            "location": location.pk if location else "",
            "period": "daily",
            "start": today.isoformat(),
            "end": today.isoformat(),
        }

    def _sales_access(self, user, unit):
        assign_report_panel(actor=self.admin, target_user=user, panel=ReportingPanel.SALES)
        grant_report_data(
            actor=self.admin,
            target_user=user,
            capability=ReportDataCapability.SALES,
            business_unit=unit,
        )

    def test_separate_examples_are_scoped_and_do_not_enter_operational_sales_or_exports(self):
        self.make_delivered_sale(key="sales-example-operational-base", quantity="2.00")
        extra_location = Location.objects.create(
            legal_entity=self.unit.legal_entity,
            code="test-sales-example-second-location",
            name="TEST ONLY second Tivoli location",
        )
        extra_location.business_units.add(self.unit)
        self._sample(
            "sales-example-in-primary-location-1",
            location=self.location,
            quantity="1.25",
            revenue="12345.67",
        )
        self._sample(
            "sales-example-in-primary-location-2",
            location=self.location,
            quantity="2.75",
            revenue="99001.23",
        )
        self._sample(
            "sales-example-in-other-location",
            location=extra_location,
            quantity="8.00",
            revenue="888888.88",
        )
        self._sales_access(self.sales, self.unit)
        client = APIClient()
        client.force_authenticate(user=self.sales)
        query = self._query(location=self.location)

        examples = client.get("/api/reports/sales-examples/", query)
        self.assertEqual(examples.status_code, 200, examples.data)
        self.assertEqual(
            set(examples.data),
            {
                "panel",
                "title",
                "source",
                "example_count",
                "summary",
                "currency",
                "filters",
                "scope",
                "policy_label",
                "note",
            },
        )
        self.assertEqual(examples.data["panel"], "sales_examples")
        self.assertEqual(examples.data["title"], "Synthetic examples — not operational sales")
        self.assertEqual(examples.data["source"], "DemoReportSalesFact")
        self.assertEqual(examples.data["example_count"], 2)
        self.assertEqual(
            examples.data["summary"],
            {"illustrative_revenue": "111346.90", "illustrative_quantity": "4.00"},
        )
        self.assertEqual(examples.data["filters"]["location_id"], self.location.pk)
        self.assertEqual(examples.data["scope"]["business_unit_ids"], [self.unit.pk])
        self.assertIn("TEST ONLY", examples.data["policy_label"])
        self.assertIn("operational SalesReportLine", examples.data["note"])
        for forbidden in ("supplier_cost", "landed_cost", "gross_margin", "purchase_history"):
            self.assertNotIn(forbidden, str(examples.data))

        other_location_examples = client.get(
            "/api/reports/sales-examples/",
            self._query(location=extra_location),
        )
        self.assertEqual(other_location_examples.status_code, 200, other_location_examples.data)
        self.assertEqual(other_location_examples.data["example_count"], 1)
        self.assertEqual(
            other_location_examples.data["summary"]["illustrative_revenue"],
            "888888.88",
        )

        operational = client.get("/api/reports/sales/", query)
        self.assertEqual(operational.status_code, 200, operational.data)
        self.assertEqual(operational.data["pagination"]["total_count"], 1)
        self.assertEqual(operational.data["summary"], {"sales_amount": "4000.00", "quantity": "2.00"})
        self.assertEqual(
            operational.data["trend"],
            [{
                "period": timezone.localdate().isoformat(),
                "sales_amount": "4000.00",
                "quantity": "2.00",
            }],
        )
        self.assertEqual(len(operational.data["rows"]), 1)
        self.assertNotIn("sales-example-", str(operational.data))
        self.assertNotIn("111346.90", str(operational.data))
        self.assertIn("SalesReportLine records only", operational.data["note"])

        for file_format in ("xlsx", "docx"):
            exported = client.post(
                "/api/reports/sales/export/",
                {**query, "format": file_format},
                format="json",
            )
            self.assertEqual(exported.status_code, 200, exported.content[:200])
            with ZipFile(BytesIO(exported.content)) as archive:
                contents = "\n".join(
                    archive.read(name).decode("utf-8", errors="replace")
                    for name in archive.namelist()
                    if name.endswith(".xml")
                )
            self.assertNotIn("sales-example-", contents)
            self.assertNotIn("111346.90", contents)
            self.assertNotIn("888888.88", contents)
        self.assertEqual(ReportExportAudit.objects.filter(user=self.sales).count(), 2)

    def test_examples_require_existing_sales_panel_data_grant_and_matching_scope(self):
        client = APIClient()
        client.force_authenticate(user=self.sales)
        query = self._query()

        grant_report_data(
            actor=self.admin,
            target_user=self.sales,
            capability=ReportDataCapability.SALES,
            business_unit=self.unit,
        )
        no_panel = client.get("/api/reports/sales-examples/", query)
        self.assertEqual(no_panel.status_code, 403, no_panel.data)

        assign_report_panel(actor=self.admin, target_user=self.sales, panel=ReportingPanel.SALES)
        allowed = client.get("/api/reports/sales-examples/", query)
        self.assertEqual(allowed.status_code, 200, allowed.data)

        cross_unit = client.get(
            "/api/reports/sales-examples/",
            self._query(unit=self.roche),
        )
        self.assertEqual(cross_unit.status_code, 403, cross_unit.data)

        roche_location = Location.objects.create(
            legal_entity=self.roche.legal_entity,
            code="test-sales-example-roche-location",
            name="TEST ONLY Roche location",
        )
        roche_location.business_units.add(self.roche)
        cross_location = client.get(
            "/api/reports/sales-examples/",
            self._query(location=roche_location),
        )
        self.assertEqual(cross_location.status_code, 403, cross_location.data)

        other_client = APIClient()
        other_client.force_authenticate(user=self.roche_user)
        assign_report_panel(actor=self.admin, target_user=self.roche_user, panel=ReportingPanel.SALES)
        missing_data = other_client.get(
            "/api/reports/sales-examples/",
            self._query(unit=self.roche),
        )
        self.assertEqual(missing_data.status_code, 403, missing_data.data)
