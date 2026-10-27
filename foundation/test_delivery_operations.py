"""Delivery read progress and existing whole-quote operations; synthetic only."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    IdempotencyRecord, JournalEntry, JournalLine, QuotePayment, QuoteReservation,
    SalesQuote, StockBalance, StockMovement, StockReservation, WorkflowEvent,
)
from .purchasing_services import create_sales_quote, notify_quote_ready
from .test_purchasing_workflows import PurchasingFixtureMixin


class DeliveryOperationsTests(PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.sales)
        self.csrf = self.client.get("/api/auth/csrf/").data["csrfToken"]

    def detail(self, quote):
        return self.client.get(
            f"/api/purchasing/quotes/{quote.pk}/",
            {"business_unit_id": self.unit.pk},
        )

    def post(self, quote, action, payload, key, *, csrf=True):
        return self.client.post(
            f"/api/purchasing/quotes/{quote.pk}/{action}/", payload, format="json",
            HTTP_IDEMPOTENCY_KEY=key,
            **({"HTTP_X_CSRFTOKEN": self.csrf} if csrf else {}),
        )

    def business_snapshot(self):
        models = (IdempotencyRecord, JournalEntry, JournalLine, QuotePayment,
                  QuoteReservation, StockBalance, StockMovement, StockReservation,
                  WorkflowEvent, SalesQuote)
        return {model.__name__: list(model.objects.order_by("pk").values()) for model in models}

    def test_session_delivery_progress_full_payment_gate_handover_and_replay(self):
        self.make_receipt()
        quote = SalesQuote.objects.get(pk=self.make_quote()["quote_id"])
        response = self.detail(quote)
        self.assertEqual(response.status_code, 200)
        progress = response.data["delivery_progress"]
        self.assertEqual(progress["ready_at"], None)
        self.assertEqual(progress["delivered_at"], None)
        self.assertEqual(progress["lines"][0], {
            "quote_line_id": quote.lines.get().pk, "variant_id": self.variant.pk,
            "ordered_quantity": "2.00", "reserved_to_date_quantity": "0.00",
            "delivered_quantity": "0.00", "remaining_quantity": "2.00",
        })
        payment = self.post(quote, "payments", {
            "amount": "2000.00", "method": "simulated_card", "purpose": "quote",
        }, "delivery-ops-deposit")
        self.assertEqual(payment.status_code, 201, payment.data)
        ready = self.post(quote, "ready", {}, "delivery-ops-ready")
        self.assertEqual(ready.status_code, 200, ready.data)
        progress = self.detail(quote).data["delivery_progress"]
        self.assertIsNotNone(progress["ready_at"])
        self.assertIsNone(progress["delivered_at"])
        self.assertEqual(progress["lines"][0]["reserved_to_date_quantity"], "2.00")
        self.assertEqual(progress["lines"][0]["remaining_quantity"], "2.00")
        before = self.business_snapshot()
        denied = self.post(quote, "deliver", {}, "delivery-ops-underpaid")
        self.assertEqual(denied.status_code, 400, denied.data)
        self.assertEqual(before, self.business_snapshot())
        payment = self.post(quote, "payments", {
            "amount": "2000.00", "method": "simulated_card", "purpose": "quote",
        }, "delivery-ops-balance")
        self.assertEqual(payment.status_code, 201, payment.data)
        delivered = self.post(quote, "deliver", {}, "delivery-ops-handover")
        self.assertEqual(delivered.status_code, 200, delivered.data)
        self.assertEqual(delivered.data["status"], "delivered")
        before = self.business_snapshot()
        detail = self.detail(quote)
        self.assertEqual(before, self.business_snapshot(), "GET must not create/change business facts.")
        self.assertEqual(detail.data["status"], "delivered")
        progress = detail.data["delivery_progress"]
        self.assertEqual(progress["delivered_at"], delivered.data["delivered_at"])
        self.assertEqual(progress["lines"][0]["delivered_quantity"], "2.00")
        self.assertEqual(progress["lines"][0]["remaining_quantity"], "0.00")
        # This is explicitly historical coverage, not live reserved stock.
        self.assertEqual(progress["lines"][0]["reserved_to_date_quantity"], "2.00")
        self.assertFalse(StockReservation.objects.filter(status="active").exists())
        replay = self.post(quote, "deliver", {}, "delivery-ops-handover")
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data, delivered.data)
        self.assertEqual(before, self.business_snapshot())

    def test_progress_decimals_and_independent_multiline_results_have_no_cost_fields(self):
        self.make_receipt(key="decimal-primary")
        self.make_receipt(key="decimal-secondary", variant=self.alternate_variant)
        result = create_sales_quote(
            actor=self.sales, idempotency_key="delivery-ops-multiline",
            business_unit=self.unit, customer=self.customer, kind="stock",
            discount_percent=Decimal("0.00"), discount_reason="TEST ONLY no discount",
            lines=[
                {"variant": variant, "location": self.location, "quantity": Decimal(quantity),
                 "specifications": {"label": "TEST ONLY delivery specification"}}
                for variant, quantity in ((self.variant, "1.25"), (self.alternate_variant, "2.75"))
            ],
        )
        quote = SalesQuote.objects.get(pk=result["quote_id"])
        self.pay(quote, key="decimal-payment", amount=str(quote.total_quote_value))
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key="decimal-ready")
        response = self.post(quote, "deliver", {}, "decimal-delivery")
        self.assertEqual(response.status_code, 200, response.data)
        before = self.business_snapshot()
        progress = self.detail(quote).data["delivery_progress"]
        self.assertEqual([row["ordered_quantity"] for row in progress["lines"]], ["1.25", "2.75"])
        self.assertEqual([row["delivered_quantity"] for row in progress["lines"]], ["1.25", "2.75"])
        self.assertEqual([row["remaining_quantity"] for row in progress["lines"]], ["0.00", "0.00"])
        self.assertEqual([row["quote_line_id"] for row in progress["lines"]],
                         list(quote.lines.order_by("pk").values_list("pk", flat=True)))
        self.assertEqual(set(progress), {"ready_at", "delivered_at", "lines"})
        for row in progress["lines"]:
            self.assertEqual(set(row), {
                "quote_line_id", "variant_id", "ordered_quantity", "reserved_to_date_quantity",
                "delivered_quantity", "remaining_quantity",
            })
        self.assertEqual(before, self.business_snapshot())

    def test_multiline_deposit_failure_rolls_back_then_retry_reserves_and_handover_consumes_each_line_once(self):
        self.make_receipt(key="journey-first-stock")
        result = create_sales_quote(
            actor=self.sales, idempotency_key="journey-quote",
            business_unit=self.unit, customer=self.customer, kind="stock",
            discount_percent=Decimal("0.00"), discount_reason="TEST ONLY existing price",
            lines=[
                {"variant": variant, "location": self.location,
                 "quantity": Decimal(quantity), "specifications": {"finish": "TEST ONLY linen"}}
                for variant, quantity in ((self.variant, "1.25"), (self.alternate_variant, "2.75"))
            ],
        )
        quote = SalesQuote.objects.get(pk=result["quote_id"])
        self.assertEqual(quote.total_quote_value, Decimal("2775.00"))
        payload = {"amount": "1387.50", "method": "simulated_card", "purpose": "quote"}
        before = self.business_snapshot()
        # First line could reserve; second has no ledger stock. All changes,
        # including the payment, operation, event and first reservation roll back.
        denied = self.post(quote, "payments", payload, "journey-deposit")
        self.assertEqual(denied.status_code, 400, denied.data)
        self.assertEqual(before, self.business_snapshot())
        self.assertEqual(list(quote.lines.order_by("pk").values_list("reserved_quantity", flat=True)),
                         [Decimal("0.00"), Decimal("0.00")])

        self.make_receipt(key="journey-second-stock", variant=self.alternate_variant)
        baseline = list(StockBalance.objects.order_by("variant_id").values_list(
            "variant_id", "on_hand_quantity", "reserved_quantity"))
        deposit = self.post(quote, "payments", payload, "journey-deposit")
        self.assertEqual(deposit.status_code, 201, deposit.data)
        reserved = self.business_snapshot()
        replay = self.post(quote, "payments", payload, "journey-deposit")
        self.assertEqual(replay.data, deposit.data)
        self.assertEqual(reserved, self.business_snapshot())
        self.assertEqual(list(quote.lines.order_by("pk").values_list("reserved_quantity", flat=True)),
                         [Decimal("1.25"), Decimal("2.75")])
        self.assertEqual(QuoteReservation.objects.filter(quote_line__quote=quote).count(), 2)
        ready = self.post(quote, "ready", {}, "journey-ready")
        self.assertEqual(ready.status_code, 200, ready.data)
        before = self.business_snapshot()
        self.assertEqual(self.post(quote, "deliver", {}, "journey-unpaid").status_code, 400)
        self.assertEqual(before, self.business_snapshot())
        balance = self.post(quote, "payments", payload, "journey-balance")
        self.assertEqual(balance.status_code, 201, balance.data)
        self.assertEqual(QuoteReservation.objects.filter(quote_line__quote=quote).count(), 2)
        delivered = self.post(quote, "deliver", {}, "journey-handover")
        self.assertEqual(delivered.status_code, 200, delivered.data)
        committed = self.business_snapshot()
        self.assertEqual(self.post(quote, "deliver", {}, "journey-handover").data, delivered.data)
        self.assertEqual(committed, self.business_snapshot())
        quantities = {self.variant.pk: Decimal("1.25"), self.alternate_variant.pk: Decimal("2.75")}
        for variant_id, on_hand, reserved_quantity in baseline:
            stock = StockBalance.objects.get(variant_id=variant_id, location=self.location)
            self.assertEqual(stock.on_hand_quantity, on_hand - quantities[variant_id])
            self.assertEqual(stock.reserved_quantity, reserved_quantity)
        self.assertEqual(StockReservation.objects.filter(
            quote_reservation__quote_line__quote=quote, status="consumed").count(), 2)
        progress = self.detail(quote).data["delivery_progress"]
        self.assertEqual([row["delivered_quantity"] for row in progress["lines"]], ["1.25", "2.75"])
        self.assertEqual([row["remaining_quantity"] for row in progress["lines"]], ["0.00", "0.00"])
        self.assertEqual(committed, self.business_snapshot())

    def test_progress_requires_existing_quote_scope_and_rechecks_revoked_grant(self):
        quote = SalesQuote.objects.get(pk=self.make_quote()["quote_id"])
        before = self.business_snapshot()
        wrong_scope = self.client.get(
            f"/api/purchasing/quotes/{quote.pk}/", {"business_unit_id": self.roche.pk},
        )
        self.assertEqual(wrong_scope.status_code, 404)
        self.client.force_login(self.roche_user)
        self.assertEqual(self.detail(quote).status_code, 404)
        self.client.logout()
        self.assertEqual(self.detail(quote).status_code, 403)
        self.client.force_login(self.sales)
        self.assertEqual(self.detail(quote).status_code, 200)
        self.sales_grant.revoked_at = timezone.now()
        self.sales_grant.save(update_fields=["revoked_at"])
        self.assertEqual(self.detail(quote).status_code, 404)
        self.assertEqual(before, self.business_snapshot())

    def test_delivery_without_csrf_or_with_revoked_grant_does_not_post(self):
        self.make_receipt()
        quote = SalesQuote.objects.get(pk=self.make_quote()["quote_id"])
        self.pay(quote, key="delivery-security-payment", amount=str(quote.total_quote_value))
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key="delivery-security-ready")
        before = self.business_snapshot()
        self.assertEqual(self.post(quote, "deliver", {}, "no-csrf", csrf=False).status_code, 403)
        self.sales_grant.revoked_at = timezone.now()
        self.sales_grant.save(update_fields=["revoked_at"])
        denied = self.post(quote, "deliver", {}, "revoked-delivery")
        self.assertEqual(denied.status_code, 404, denied.data)
        self.assertEqual(before, self.business_snapshot())
