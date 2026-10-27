"""Disposable synthetic return intake tests; no refund or stock posting."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps
from django.db import DatabaseError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    BusinessUnitRoleAssignment, IdempotencyRecord, Location, SalesQuote, SalesReturnEvent,
    SalesReturnIntake, SalesReturnPolicy, UserActionGrant,
)
from .test_reporting import ReportingFixtureMixin
from .purchasing_services import create_sales_quote, deliver_sales_quote, notify_quote_ready


class SalesReturnIntakeTests(ReportingFixtureMixin, TestCase):
    def setUp(self):
        self.quote, _ = self.make_delivered_sale()
        self.line = self.quote.lines.get()
        self.client = APIClient()
        self.client.force_authenticate(user=self.sales)
        self.url = "/api/purchasing/returns/"
        self.data = {
            "business_unit_id": self.unit.pk, "quote_id": self.quote.pk,
            "request_date": timezone.localdate(self.quote.delivered_at).isoformat(), "receiving_mode": "showroom",
            "location_id": self.location.pk,
            "receipt_collection_details": "TEST ONLY showroom received, synthetic item reference A",
            "proposed_refund_amount": "2000.00",
            "original_payment_method": "card", "requested_refund_method": "card",
            "refund_preference": "usually_original",
            "lines": [{"quote_line_id": self.line.pk, "quantity": "1.00"}],
        }

    def post(self, data=None, key="test-return", url=None):
        return self.client.post(
            url or self.url, data if data is not None else self.data,
            format="json", HTTP_IDEMPOTENCY_KEY=key,
        )

    def inspect(self, intake_id, condition="good", key="test-inspection"):
        return self.post({
            "business_unit_id": self.unit.pk,
            "lines": [{"quote_line_id": self.line.pk, "condition": condition,
                       "inspection_notes": "TEST ONLY visual condition observation"}],
        }, key=key, url=f"{self.url}{intake_id}/inspection/")

    def operational_snapshot(self):
        excluded = {"IdempotencyRecord", "SalesReturnIntake", "SalesReturnEvent", "SalesReturnPolicy"}
        return {
            model.__name__: list(model.objects.order_by("pk").values())
            for model in apps.get_app_config("foundation").get_models()
            if model.__name__ not in excluded
        }

    def test_intake_and_inspection_append_only_nonposting_facts_with_sale_metadata(self):
        before = self.operational_snapshot()
        response = self.post()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "awaiting_inspection")
        self.assertEqual(response.data["customer"], {"id": self.customer.pk, "name": self.customer.name})
        self.assertEqual(response.data["business_unit"]["id"], self.unit.pk)
        self.assertEqual(response.data["lines"][0]["item_id"], self.variant.item.item_code)
        self.assertEqual(response.data["lines"][0]["brand"], self.variant.item.brand.name)
        self.assertEqual(response.data["lines"][0]["condition"], "uninspected")
        self.assertEqual(response.data["original_method_source"], "user_recorded_not_verified")
        self.assertEqual(response.data["policy"]["date_basis"], "delivery")
        self.assertEqual(response.data["policy"]["status"], "owner_confirmed_pending_legal_validation")
        self.assertEqual(response.data["policy"]["provisional_calendar_days"], 14)
        self.assertIn("not a verified statement of Qatar law", response.data["policy"]["source"])
        inspection = self.inspect(response.data["id"])
        self.assertEqual(inspection.status_code, 200, inspection.data)
        self.assertEqual(inspection.data["status"], "finance_pending")
        self.assertEqual(inspection.data["lines"][0]["condition"], "good")
        self.assertEqual([event["kind"] for event in inspection.data["events"]],
                         ["intake_recorded", "inspection_recorded"])
        self.assertEqual(before, self.operational_snapshot())
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, SalesQuote.Status.DELIVERED)

    def test_not_good_inspection_records_findings_without_disposition_or_refund(self):
        response = self.post({**self.data, "receiving_mode": "warehouse_collection",
                              "requested_refund_method": "cash", "refund_preference": "cash"})
        self.assertEqual(response.status_code, 200)
        before = self.operational_snapshot()
        inspection = self.inspect(response.data["id"], condition="not_good")
        self.assertEqual(inspection.status_code, 200, inspection.data)
        self.assertEqual(inspection.data["status"], "finance_pending")
        self.assertNotIn("disposition", inspection.data)
        self.assertNotIn("refund_executed", inspection.data)
        self.assertEqual(before, self.operational_snapshot())

    def test_intake_and_inspection_replay_deduplicate_and_changed_payload_conflicts(self):
        first = self.post()
        self.assertEqual(first.status_code, 200)
        replay = self.post()
        self.assertEqual(replay.data, first.data)
        self.assertEqual(SalesReturnIntake.objects.count(), 1)
        self.assertEqual(SalesReturnEvent.objects.count(), 1)
        conflict = self.post({**self.data, "proposed_refund_amount": "1999.99"})
        self.assertEqual(conflict.status_code, 409)
        inspection = self.inspect(first.data["id"])
        self.assertEqual(self.inspect(first.data["id"]).data, inspection.data)
        self.assertEqual(self.inspect(first.data["id"], condition="not_good").status_code, 409)
        self.assertEqual(SalesReturnEvent.objects.count(), 2)
        self.assertEqual(self.inspect(first.data["id"], key="new-inspection").status_code, 400)
        # Replaying intake after inspection returns its original intake result.
        self.assertEqual(self.post().data, first.data)

    def test_atomic_rollback_if_either_audit_insert_fails(self):
        before = self.operational_snapshot()
        key_count = IdempotencyRecord.objects.count()
        with patch("foundation.return_services.SalesReturnEvent.objects.create", side_effect=RuntimeError("TEST ONLY")):
            with self.assertRaises(RuntimeError):
                self.post()
        self.assertEqual(SalesReturnIntake.objects.count(), 0)
        self.assertEqual(IdempotencyRecord.objects.count(), key_count)
        created = self.post()
        key_count = IdempotencyRecord.objects.count()
        with patch("foundation.return_services.SalesReturnEvent.objects.create", side_effect=RuntimeError("TEST ONLY")):
            with self.assertRaises(RuntimeError):
                self.inspect(created.data["id"])
        intake = SalesReturnIntake.objects.get()
        self.assertEqual(intake.status, "awaiting_inspection")
        self.assertEqual(intake.lines[0]["condition"], "uninspected")
        self.assertEqual(SalesReturnEvent.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.count(), key_count)
        self.assertEqual(before, self.operational_snapshot())

    def test_invalid_inputs_and_foreign_lines_locations_leave_no_partial_records(self):
        variants = [
            {**self.data, "lines": []},
            {**self.data, "lines": [{"quote_line_id": self.line.pk, "quantity": "0"}]},
            {**self.data, "lines": [{"quote_line_id": self.line.pk, "quantity": "3.00"}]},
            {**self.data, "lines": self.data["lines"] * 2},
            {**self.data, "lines": [{"quote_line_id": 9999999, "quantity": "1"}]},
            {**self.data, "location_id": Location.objects.filter(business_units=self.roche).exclude(business_units=self.unit).first().pk},
            {**self.data, "receiving_mode": "invented"},
            {**self.data, "original_payment_method": "bank"},
            {**self.data, "requested_refund_method": "bank"},
            {**self.data, "proposed_refund_amount": "-0.01"},
            {**self.data, "proposed_refund_amount": "1.001"},
            {**self.data, "request_date": "not a date"},
            {**self.data, "status": "finance_approved"},
        ]
        key_count = IdempotencyRecord.objects.count()
        for index, data in enumerate(variants):
            with self.subTest(index=index):
                self.assertEqual(self.post(data, key=f"invalid-{index}").status_code, 400)
        self.assertEqual(SalesReturnIntake.objects.count(), 0)
        self.assertEqual(SalesReturnEvent.objects.count(), 0)
        self.assertEqual(IdempotencyRecord.objects.count(), key_count)

    def test_unauthorized_cross_unit_and_missing_role_requests_are_denied(self):
        intake = self.post().data["id"]
        self.client.force_authenticate(user=self.roche_user)
        self.assertEqual(self.post().status_code, 404)
        self.assertEqual(self.client.get(self.url, {"business_unit_id": self.unit.pk}).status_code, 404)
        self.assertEqual(self.client.get(f"{self.url}sales/{self.quote.pk}/",
                                        {"business_unit_id": self.roche.pk}).status_code, 404)
        self.assertEqual(self.inspect(intake).status_code, 404)
        self.client.force_authenticate(user=self.admin)  # staff is not an intake grant
        self.assertEqual(self.post().status_code, 404)
        self.client.force_authenticate(user=self.sales)
        BusinessUnitRoleAssignment.objects.filter(user=self.sales).update(revoked_at=timezone.now())
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.client.get(self.url, {"business_unit_id": self.unit.pk}).status_code, 403)

    def test_grant_revocation_blocks_reads_and_even_previously_successful_replay(self):
        intake = self.post().data["id"]
        UserActionGrant.objects.filter(user=self.sales, action="create_order").update(revoked_at=timezone.now())
        self.assertEqual(self.post().status_code, 404)
        self.assertEqual(self.inspect(intake).status_code, 404)
        self.assertEqual(self.client.get(self.url, {"business_unit_id": self.unit.pk}).status_code, 404)

    def test_configurable_days_snapshot_and_confirmed_delivery_date_basis(self):
        policy = SalesReturnPolicy.objects.create(business_unit=self.unit, provisional_calendar_days=21)
        response = self.post({**self.data, "request_date": (
            timezone.localdate(self.quote.delivered_at) + timedelta(days=21)).isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["policy"]["provisional_calendar_days"], 21)
        self.assertEqual(response.data["policy"]["date_basis"], "delivery")
        policy.provisional_calendar_days = 14
        policy.save()
        listed = self.client.get(self.url, {"business_unit_id": self.unit.pk})
        self.assertEqual(listed.data["policy"]["provisional_calendar_days"], 14)
        self.assertEqual(listed.data["rows"][0]["policy"]["provisional_calendar_days"], 21)
        self.assertTrue(response.data["delivery_window"]["eligible"])
        with self.assertRaises(DatabaseError), transaction.atomic():
            policy.date_basis = "unconfigured"
            policy.save()

    def test_session_csrf_required_and_anonymous_access_denied(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.assertIn(self.post().status_code, [403, 401])
        self.client.force_login(self.sales)
        self.assertEqual(self.post().status_code, 403)
        token = self.client.get("/api/auth/csrf/").data["csrfToken"]
        response = self.client.post(self.url, self.data, format="json",
                                   HTTP_IDEMPOTENCY_KEY="session-return", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200, response.data)

    @override_settings(APP_ENV="production")
    def test_non_demo_environment_fails_closed(self):
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.client.get(self.url, {"business_unit_id": self.unit.pk}).status_code, 403)

    def test_get_pagination_order_field_allowlist_and_no_writes(self):
        for index in range(3):
            self.assertEqual(self.post(key=f"page-{index}").status_code, 200)
        before = self.operational_snapshot()
        key_count = IdempotencyRecord.objects.count()
        page1 = self.client.get(self.url, {"business_unit_id": self.unit.pk, "page_size": 2})
        page2 = self.client.get(self.url, {"business_unit_id": self.unit.pk, "page_size": 2, "page": 2})
        self.assertEqual(page1.data["pagination"]["total_count"], 3)
        ids = [r["id"] for r in page1.data["rows"] + page2.data["rows"]]
        self.assertEqual(ids, list(SalesReturnIntake.objects.values_list("pk", flat=True)))
        self.assertEqual(len(set(ids)), 3)
        source = self.client.get(f"{self.url}sales/{self.quote.pk}/", {"business_unit_id": self.unit.pk})
        self.assertEqual(source.status_code, 200)
        self.assertEqual(set(source.data), {"quote_id", "delivered_at", "customer", "business_unit", "lines", "locations", "delivery_window"})
        self.assertEqual(set(source.data["lines"][0]), {
            "quote_line_id", "variant_id", "item_id", "description", "brand", "quantity",
        })
        self.assertEqual(key_count, IdempotencyRecord.objects.count())
        self.assertEqual(SalesReturnEvent.objects.count(), 3)
        self.assertEqual(before, self.operational_snapshot())
        self.assertEqual(SalesReturnPolicy.objects.count(), 0)

    def test_undelivered_sales_and_incomplete_inspection_do_not_advance(self):
        quote_data = self.make_quote(key="undelivered")
        self.assertEqual(self.post({**self.data, "quote_id": quote_data["quote_id"]}).status_code, 400)
        response = self.post()
        inspected = self.post({
            "business_unit_id": self.unit.pk, "lines": [{"quote_line_id": 999999, "condition": "good",
                                                        "inspection_notes": "TEST ONLY"}],
        }, key="wrong-inspection", url=f'{self.url}{response.data["id"]}/inspection/')
        self.assertEqual(inspected.status_code, 400)
        self.assertEqual(SalesReturnIntake.objects.get().status, "awaiting_inspection")
        self.assertEqual(SalesReturnEvent.objects.count(), 1)

    def test_multiple_returned_items_require_independent_complete_inspection(self):
        self.make_receipt(key="multi-return-receipt", variant=self.alternate_variant)
        result = create_sales_quote(
            actor=self.sales, idempotency_key="multi-return-sale", business_unit=self.unit,
            customer=self.customer, kind="stock", discount_percent=Decimal("0.00"),
            discount_reason="TEST ONLY multi-item return fixture",
            lines=[
                {"variant": variant, "location": self.location, "quantity": Decimal("1.00"),
                 "specifications": {"test_only": True}}
                for variant in (self.variant, self.alternate_variant)
            ],
        )
        quote = SalesQuote.objects.get(pk=result["quote_id"])
        self.pay(quote, key="multi-return-payment", amount=str(quote.total_quote_value))
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key="multi-return-ready",
                           message="TEST ONLY multi-item return fixture")
        deliver_sales_quote(actor=self.sales, quote=quote, idempotency_key="multi-return-delivery")
        lines = [{"quote_line_id": line.pk, "quantity": "1.00"} for line in quote.lines.all()]
        response = self.post({**self.data, "quote_id": quote.pk, "lines": lines})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["lines"]), 2)
        inspection_url = f'{self.url}{response.data["id"]}/inspection/'
        inspected_lines = [
            {"quote_line_id": line["quote_line_id"],
             "condition": "good" if index == 0 else "not_good", "inspection_notes": "TEST ONLY observed"}
            for index, line in enumerate(lines)
        ]
        incomplete = self.post({"business_unit_id": self.unit.pk, "lines": inspected_lines[:1]},
                               key="incomplete", url=inspection_url)
        self.assertEqual(incomplete.status_code, 400)
        self.assertEqual(SalesReturnIntake.objects.get().status, "awaiting_inspection")
        before = self.operational_snapshot()
        complete = self.post({"business_unit_id": self.unit.pk, "lines": inspected_lines},
                             key="complete", url=inspection_url)
        self.assertEqual(complete.status_code, 200, complete.data)
        self.assertEqual([line["condition"] for line in complete.data["lines"]], ["good", "not_good"])
        self.assertEqual(complete.data["status"], "finance_pending")
        self.assertEqual(before, self.operational_snapshot())

    def test_audit_is_database_append_only_and_location_membership_is_rechecked(self):
        response = self.post()
        event = SalesReturnEvent.objects.get()
        for operation in (
            lambda: SalesReturnEvent.objects.filter(pk=event.pk).update(kind="inspection_recorded"),
            lambda: SalesReturnEvent.objects.filter(pk=event.pk).delete(),
        ):
            with self.assertRaises(DatabaseError), transaction.atomic():
                operation()
        self.location.business_units.remove(self.unit)
        self.assertEqual(self.client.get(self.url, {"business_unit_id": self.unit.pk}).data["rows"], [])
        self.assertEqual(self.inspect(response.data["id"]).status_code, 404)
        self.assertEqual(SalesReturnEvent.objects.count(), 1)
        self.assertEqual(SalesReturnIntake.objects.get().status, "awaiting_inspection")

    def test_delivery_window_boundary_day_is_stored_and_replay_preserves_original_rule(self):
        delivered = timezone.localdate(self.quote.delivered_at)
        deadline = delivered + timedelta(days=14)
        data = {**self.data, "request_date": deadline.isoformat()}
        response = self.post(data)
        self.assertEqual(response.status_code, 200, response.data)
        window = response.data["delivery_window"]
        self.assertEqual(window["date_basis"], "delivery")
        self.assertEqual(window["delivery_date"], delivered.isoformat())
        self.assertEqual(window["deadline"], deadline.isoformat())
        self.assertEqual(window["request_date"], deadline.isoformat())
        self.assertTrue(window["eligible"])
        self.assertEqual(SalesReturnIntake.objects.get().delivery_window, window)
        SalesReturnPolicy.objects.create(business_unit=self.unit, provisional_calendar_days=7)
        self.assertEqual(self.post(data).data, response.data)
        self.assertEqual(SalesReturnIntake.objects.count(), 1)
        self.assertEqual(SalesReturnEvent.objects.count(), 1)

    def test_outside_delivery_window_and_pre_delivery_requests_are_rejected_without_writes(self):
        delivered = timezone.localdate(self.quote.delivered_at)
        before = self.operational_snapshot()
        key_count = IdempotencyRecord.objects.count()
        for offset in [-1, 15]:
            with self.subTest(offset=offset):
                request_date = delivered + timedelta(days=offset)
                response = self.post({**self.data, "request_date": request_date.isoformat()}, key=f"date-{offset}")
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn("delivery", str(response.data).lower())
                if offset == 15:
                    self.assertIn((delivered + timedelta(days=14)).isoformat(), str(response.data))
        self.assertEqual(SalesReturnIntake.objects.count(), 0)
        self.assertEqual(SalesReturnEvent.objects.count(), 0)
        self.assertEqual(IdempotencyRecord.objects.count(), key_count)
        self.assertEqual(before, self.operational_snapshot())

    def test_source_assessment_uses_actual_delivery_local_date_not_sale_or_payment_dates(self):
        # Synthetic source timestamp deliberately distinguishes UTC from Qatar date.
        actual_delivery = datetime(2027, 12, 31, 22, 30, tzinfo=datetime_timezone.utc)
        SalesQuote.objects.filter(pk=self.quote.pk).update(delivered_at=actual_delivery)
        source = self.client.get(f"{self.url}sales/{self.quote.pk}/", {
            "business_unit_id": self.unit.pk, "request_date": "2028-01-15",
        })
        self.assertEqual(source.status_code, 200, source.data)
        window = source.data["delivery_window"]
        self.assertEqual(window["delivery_date"], "2028-01-01")
        self.assertEqual(window["deadline"], "2028-01-15")
        self.assertTrue(window["eligible"])
        outside = self.client.get(f"{self.url}sales/{self.quote.pk}/", {
            "business_unit_id": self.unit.pk, "request_date": "2028-01-16",
        })
        self.assertFalse(outside.data["delivery_window"]["eligible"])
        self.assertEqual(SalesReturnIntake.objects.count(), 0)
        self.assertNotEqual(timezone.localdate(self.quote.created_at).isoformat(), window["delivery_date"])
        self.assertNotEqual(timezone.localdate(self.quote.payments.first().created_at).isoformat(), window["delivery_date"])

    def test_review_uses_stored_delivery_window_without_recalculating_finance_or_stock(self):
        response = self.post()
        original = response.data["delivery_window"]
        SalesReturnPolicy.objects.create(business_unit=self.unit, provisional_calendar_days=7)
        before = self.operational_snapshot()
        inspection = self.inspect(response.data["id"], condition="not_good")
        self.assertEqual(inspection.status_code, 200, inspection.data)
        self.assertEqual(inspection.data["delivery_window"], original)
        self.assertEqual(inspection.data["status"], "finance_pending")
        self.assertEqual(before, self.operational_snapshot())
