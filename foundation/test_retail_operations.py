"""Synthetic session/API contracts reused by the Retail & Sales workspace."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    ApprovedSellingPrice, IdempotencyRecord, JournalEntry, JournalLine,
    QuotePayment, QuoteReservation, SalesQuote, StockBalance, StockMovement,
    StockReservation, WorkflowEvent,
)
from .purchasing_services import configure_selling_price
from .test_purchasing_workflows import PurchasingFixtureMixin


class RetailOperationsTests(PurchasingFixtureMixin, TestCase):
    """No preview data: the shared existing fixture runs only in Django's test DB."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.sales)
        self.csrf = self.client.get("/api/auth/csrf/").data["csrfToken"]

    def payload(self, **overrides):
        return {
            "business_unit_id": self.unit.pk,
            "customer_id": self.customer.pk,
            "kind": "stock",
            "discount_percent": "0.00",
            "discount_reason": "TEST ONLY existing authorized price",
            "payment_plan": "full",
            "lines": [{
                "variant_id": self.variant.pk, "location_id": self.location.pk,
                "quantity": "2.00",
                "specifications": {"fabric": "TEST ONLY linen", "width_cm": "180"},
            }],
        } | overrides

    def post(self, path, payload, key, *, csrf=True):
        return self.client.post(
            path, payload, format="json", HTTP_IDEMPOTENCY_KEY=key,
            **({"HTTP_X_CSRFTOKEN": self.csrf} if csrf else {}),
        )

    def create(self, key="retail-create", **overrides):
        result = self.post("/api/purchasing/quotes/", self.payload(**overrides), key)
        self.assertEqual(result.status_code, 201, result.data)
        return result

    def snapshot(self):
        models = (SalesQuote, QuotePayment, QuoteReservation, StockBalance,
                  StockMovement, StockReservation, WorkflowEvent,
                  IdempotencyRecord, JournalEntry, JournalLine)
        return {model.__name__: list(model.objects.order_by("pk").values()) for model in models}

    def assert_no_costs(self, value):
        if isinstance(value, dict):
            self.assertFalse(set(value) & {
                "cost", "unit_cost", "landed_cost", "supplier_unit_cost",
                "inventory_value", "on_hand_value", "gross_margin",
            })
            for child in value.values():
                self.assert_no_costs(child)
        elif isinstance(value, list):
            for child in value:
                self.assert_no_costs(child)

    def test_stock_quote_retains_price_specs_and_original_retry_after_price_change(self):
        payload = self.payload(discount_percent="5.00")
        result = self.post("/api/purchasing/quotes/", payload, "retail-snapshot")
        self.assertEqual(result.status_code, 201, result.data)
        row = result.data["lines"][0]
        self.assertEqual(row["selling_price_snapshot"], "2000.00")
        self.assertEqual(row["specifications"], payload["lines"][0]["specifications"])
        self.assertEqual(result.data["total_quote_value"], "3800.00")
        self.assertEqual(ApprovedSellingPrice.objects.get(
            business_unit=self.unit, variant=self.variant, is_current=True,
        ).amount, Decimal("2000.00"))
        configure_selling_price(
            actor=self.admin, business_unit=self.unit, variant=self.variant,
            amount=Decimal("2200.00"),
        )
        before = self.snapshot()
        replay = self.post("/api/purchasing/quotes/", payload, "retail-snapshot")
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay.data, result.data)
        conflict = self.post(
            "/api/purchasing/quotes/", payload | {"discount_percent": "6.00"},
            "retail-snapshot",
        )
        self.assertEqual(conflict.status_code, 409, conflict.data)
        detail = self.client.get(
            f'/api/purchasing/quotes/{result.data["quote_id"]}/',
            {"business_unit_id": self.unit.pk},
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["lines"][0]["selling_price_snapshot"], "2000.00")
        self.assert_no_costs(detail.data)
        self.assertEqual(before, self.snapshot())

    def test_stock_collection_csrf_half_deposit_gate_and_payment_replay(self):
        self.make_receipt()
        quote_id = self.create().data["quote_id"]
        path = f"/api/purchasing/quotes/{quote_id}/payments/"
        first = {"amount": "1960.00", "method": "simulated_card", "purpose": "quote"}
        before = self.snapshot()
        self.assertEqual(self.post(path, first, "retail-no-csrf", csrf=False).status_code, 403)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.post(path, first, "retail-49-percent").status_code, 201)
        balance = StockBalance.objects.get(business_unit=self.unit, variant=self.variant)
        self.assertEqual(balance.reserved_quantity, Decimal("0.00"))
        self.assertEqual(SalesQuote.objects.get(pk=quote_id).status, "approved")
        second = {"amount": "40.00", "method": "simulated_cash", "purpose": "quote"}
        accepted = self.post(path, second, "retail-reach-half")
        self.assertEqual(accepted.status_code, 201, accepted.data)
        balance.refresh_from_db()
        self.assertEqual(balance.reserved_quantity, Decimal("2.00"))
        self.assertEqual(SalesQuote.objects.get(pk=quote_id).status, "confirmed")
        before = self.snapshot()
        replay = self.post(path, second, "retail-reach-half")
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay.data, accepted.data)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(QuotePayment.objects.filter(quote_id=quote_id).count(), 2)

    def test_special_order_existing_reasoned_forty_percent_is_not_stock_reservation(self):
        self.make_receipt()
        result = self.create(
            key="retail-special",
            kind="special_order", payment_plan="downpayment", downpayment_percent="40.00",
            payment_terms_reason="TEST ONLY explicit existing per-order schedule",
            lines=[{
                "variant_id": self.variant.pk, "quantity": "2.00",
                "specifications": {"finish": "TEST ONLY bespoke fabric"},
            }],
        )
        quote_id = result.data["quote_id"]
        self.assertFalse(result.data["special_order_value_bands_configured"])
        paid = self.post(f"/api/purchasing/quotes/{quote_id}/payments/", {
            "amount": "1600.00", "method": "simulated_card", "purpose": "quote",
        }, "retail-special-forty")
        self.assertEqual(paid.status_code, 201, paid.data)
        self.assertFalse(StockReservation.objects.filter(status="active").exists())
        self.assertEqual(StockBalance.objects.get(
            business_unit=self.unit, variant=self.variant,
        ).reserved_quantity, Decimal("0.00"))
        self.assert_no_costs(result.data)

    def test_existing_three_working_day_hold_limit_and_replay(self):
        self.make_receipt()
        quote_id = self.create().data["quote_id"]
        path = f"/api/purchasing/quotes/{quote_id}/holds/"
        before = self.snapshot()
        denied = self.post(path, {"working_days": 4}, "retail-invalid-hold")
        self.assertEqual(denied.status_code, 400, denied.data)
        self.assertEqual(before, self.snapshot())
        held = self.post(path, {"working_days": 3}, "retail-hold")
        self.assertEqual(held.status_code, 201, held.data)
        self.assertEqual(held.data["working_days"], 3)
        self.assertEqual(held.data["calendar_closed_weekdays"], [4])
        before = self.snapshot()
        replay = self.post(path, {"working_days": 3}, "retail-hold")
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay.data, held.data)
        self.assertEqual(before, self.snapshot())

    def test_quote_register_detail_and_actions_recheck_unit_grant_not_staff_status(self):
        quote_id = self.create().data["quote_id"]
        before = self.snapshot()
        path = "/api/purchasing/quotes/"
        listed = self.client.get(path, {"business_unit_id": self.unit.pk})
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual([row["quote_id"] for row in listed.data["rows"]], [quote_id])
        self.assertEqual(set(listed.data["rows"][0]), {
            "quote_id", "kind", "status", "customer_id", "total_quote_value",
            "currency", "created_at",
        })
        self.assert_no_costs(listed.data)
        self.assertEqual(self.client.get(path, {"business_unit_id": self.roche.pk}).status_code, 404)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(path, {"business_unit_id": self.unit.pk}).status_code, 404)
        self.client.force_login(self.roche_user)
        self.assertEqual(self.client.get(
            f"{path}{quote_id}/", {"business_unit_id": self.unit.pk},
        ).status_code, 404)
        self.client.force_login(self.sales)
        self.sales_grant.revoked_at = timezone.now()
        self.sales_grant.save(update_fields=["revoked_at"])
        self.assertEqual(self.client.get(path, {"business_unit_id": self.unit.pk}).status_code, 404)
        self.assertEqual(self.post(
            f"{path}{quote_id}/payments/",
            {"amount": "2000.00", "method": "simulated_cash", "purpose": "quote"},
            "retail-revoked",
        ).status_code, 404)
        self.assertEqual(before, self.snapshot())
