"""Synthetic calendar grouping regressions, not production fiscal policy."""

from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import ReportDataCapability, ReportingPanel, SalesReportLine
from .test_reporting import ReportingFixtureMixin


class ReportingCalendarBoundaryTests(ReportingFixtureMixin, TestCase):
    def check_boundary(self, boundary, period, before_label, after_label):
        # Use the configured timezone, never replace it with a test calendar.
        midnight = timezone.make_aware(
            datetime.fromisoformat(boundary), timezone.get_current_timezone()
        )
        start = midnight.date() - timedelta(days=1)
        end = midnight.date()
        start_at = midnight - timedelta(days=1)
        end_at = midnight + timedelta(days=1)
        instants = [
            start_at - timedelta(microseconds=1),  # excluded
            start_at,                            # included start
            midnight - timedelta(microseconds=1),
            midnight,                            # next calendar bucket
            end_at - timedelta(microseconds=1),   # included end day
            end_at,                              # excluded
        ]
        for index, instant in enumerate(instants, start=1):
            quote, _ = self.make_delivered_sale(
                key=f"calendar-{period}-{index}", quantity=str(index)
            )
            SalesReportLine.objects.filter(quote_line__quote=quote).update(created_at=instant)
        self.grant_report_access(
            self.sales, ReportingPanel.SALES, [ReportDataCapability.SALES], unit=self.unit
        )
        client = APIClient()
        client.force_authenticate(user=self.sales)
        response = client.get("/api/reports/sales/", {
            "unit": self.unit.pk,
            "location": self.location.pk,
            "period": period,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "page_size": 2,
        })
        self.assertEqual(response.status_code, 200, response.data)
        report = response.data
        self.assertEqual(report["pagination"]["total_count"], 4)
        self.assertEqual(len(report["rows"]), 2)
        self.assertEqual(report["summary"], {"sales_amount": "28000.00", "quantity": "14.00"})
        self.assertEqual(report["trend"], [
            {"period": before_label, "sales_amount": "10000.00", "quantity": "5.00"},
            {"period": after_label, "sales_amount": "18000.00", "quantity": "9.00"},
        ])
        self.assertEqual(
            sum(Decimal(row["quantity"]) for row in report["trend"]),
            Decimal("14.00"),
        )
        self.assertEqual(set(report["fields"]), {
            "selling_price", "quantity", "description", "item_id",
            "warehouse_location", "brand_name",
        })

    def test_daily_midnight_bounds_and_aggregation(self):
        self.check_boundary("2024-02-29", "daily", "2024-02-28", "2024-02-29")

    def test_monthly_leap_day_to_march_bounds_and_aggregation(self):
        self.check_boundary("2024-03-01", "monthly", "2024-02", "2024-03")

    def test_quarterly_march_to_april_bounds_and_aggregation(self):
        self.check_boundary("2024-04-01", "quarterly", "2024-Q1", "2024-Q2")

    def test_year_boundary_daily_monthly_and_quarterly_aggregation(self):
        # Each subcase has independent transactional fixtures via a savepoint.
        from django.db import transaction

        for period, before, after in [
            ("daily", "2024-12-31", "2025-01-01"),
            ("monthly", "2024-12", "2025-01"),
            ("quarterly", "2024-Q4", "2025-Q1"),
        ]:
            with self.subTest(period=period), transaction.atomic():
                self.check_boundary("2025-01-01", period, before, after)
                transaction.set_rollback(True)
