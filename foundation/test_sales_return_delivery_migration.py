"""Real schema upgrade proof on disposable synthetic PostgreSQL data."""

from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from .models import IdempotencyRecord, SalesReturnEvent, SalesReturnIntake, SalesReturnPolicy
from .test_reporting import ReportingFixtureMixin
from . import test_sales_returns


class SalesReturnDeliveryMigrationTests(ReportingFixtureMixin, TransactionTestCase):
    post = test_sales_returns.SalesReturnIntakeTests.post
    inspect = test_sales_returns.SalesReturnIntakeTests.inspect

    def test_existing_intakes_are_assessed_without_rewriting_audit_outcomes_or_business_state(self):
        self.create_fixture()
        test_sales_returns.SalesReturnIntakeTests.setUp(self)
        SalesReturnPolicy.objects.create(business_unit=self.unit, provisional_calendar_days=21)
        delivery_date = timezone.localdate(self.quote.delivered_at)
        first = self.post({**self.data, "request_date": (
            delivery_date + timedelta(days=21)).isoformat()}, key="migration-in-window")
        second = self.post(key="migration-outside-window")
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        SalesReturnIntake.objects.filter(pk=second.data["id"]).update(
            request_date=delivery_date + timedelta(days=22),
        )
        events_before = list(SalesReturnEvent.objects.order_by("pk").values())
        replay_before = list(IdempotencyRecord.objects.order_by("pk").values())
        old = ("foundation", "0017_synthetic_sales_return_intake")
        new = ("foundation", "0018_sales_return_delivery_window")
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        historical_models = executor.loader.project_state([old]).apps
        def business_facts():
            excluded = {"IdempotencyRecord", "SalesReturnIntake", "SalesReturnEvent", "SalesReturnPolicy"}
            return {
                model.__name__: list(model.objects.order_by("pk").values())
                for model in historical_models.get_app_config("foundation").get_models()
                if model.__name__ not in excluded
            }
        business_before = business_facts()
        try:
            executor = MigrationExecutor(connection)
            executor.migrate([old])
            historical = executor.loader.project_state([old]).apps
            Policy = historical.get_model("foundation", "SalesReturnPolicy")
            Intake = historical.get_model("foundation", "SalesReturnIntake")
            self.assertEqual(Policy.objects.get().date_basis, "unconfigured")
            for intake in Intake.objects.all():
                intake.policy_snapshot["date_basis"] = "unconfigured"
                intake.save(update_fields=["policy_snapshot"])
            MigrationExecutor(connection).migrate([new])
            self.assertEqual(SalesReturnPolicy.objects.get().date_basis, "delivery")
            within = SalesReturnIntake.objects.get(pk=first.data["id"])
            outside = SalesReturnIntake.objects.get(pk=second.data["id"])
            self.assertTrue(within.delivery_window["eligible"])
            self.assertEqual(within.delivery_window["deadline"], (delivery_date + timedelta(days=21)).isoformat())
            self.assertFalse(outside.delivery_window["eligible"])
            self.assertEqual(within.policy_snapshot["date_basis"], "unconfigured", "Historical snapshot must remain historical")
            self.assertEqual(outside.status, "awaiting_inspection")
            self.assertEqual(events_before, list(SalesReturnEvent.objects.order_by("pk").values()))
            self.assertEqual(replay_before, list(IdempotencyRecord.objects.order_by("pk").values()))
            self.assertEqual(business_before, business_facts())
            # Current request code must run against the current schema, not
            # assume later additive quotation columns existed in migration 18.
            MigrationExecutor(connection).migrate(latest)
            self.assertEqual(self.inspect(outside.pk).status_code, 400)
            self.assertEqual(business_before, business_facts())
        finally:
            MigrationExecutor(connection).migrate(latest)
