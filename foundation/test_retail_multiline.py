"""Existing quote-line contract; synthetic disposable PostgreSQL only."""
from copy import deepcopy
from decimal import Decimal

from django.apps import apps
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import ApprovedSellingPrice, Location, UserActionGrant
from .purchasing_services import configure_selling_price
from .test_purchasing_workflows import PurchasingFixtureMixin


class RetailMultilineTests(PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for variant, amount in ((cls.variant, "19.99"), (cls.alternate_variant, "10.05")):
            configure_selling_price(
                actor=cls.admin, business_unit=cls.unit, variant=variant, amount=Decimal(amount),
            )
        cls.unassigned = Location.objects.create(
            legal_entity=cls.unit.legal_entity, code="test-only-unassigned-multiline",
            name="TEST ONLY explicitly unassigned location",
        )

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.sales)
        self.csrf = self.client.get("/api/auth/csrf/").data["csrfToken"]

    def payload(self):
        return {
            "business_unit_id": self.unit.pk, "customer_id": self.customer.pk,
            "kind": "stock", "discount_percent": "5.25",
            "discount_reason": "TEST ONLY existing salesperson authority", "payment_plan": "full",
            "lines": [
                {"variant_id": variant.pk, "location_id": self.location.pk,
                 "quantity": quantity, "specifications": {"finish": finish}}
                for variant, quantity, finish in (
                    (self.variant, "1.25", "TEST ONLY linen"),
                    (self.alternate_variant, "2.75", "TEST ONLY oak"),
                )
            ],
        }

    def post(self, payload, key="test-only-multiline"):
        return self.client.post(
            "/api/purchasing/quotes/", payload, format="json",
            HTTP_X_CSRFTOKEN=self.csrf, HTTP_IDEMPOTENCY_KEY=key,
        )

    def snapshot(self):
        return {model._meta.label: list(model.objects.order_by("pk").values())
                for model in apps.get_app_config("foundation").get_models()}

    def assert_no_costs(self, value):
        if isinstance(value, dict):
            self.assertFalse(set(value) & {
                "cost", "unit_cost", "landed_cost", "supplier_unit_cost", "cost_of_goods_sold",
                "inventory_value", "on_hand_value", "gross_margin", "gross_margin_percent",
                "purchase_history", "inventory_history", "transfer_history",
            })
            for child in value.values():
                self.assert_no_costs(child)
        elif isinstance(value, list):
            for child in value:
                self.assert_no_costs(child)

    def test_two_items_keep_exact_quantity_spec_price_and_per_line_rounding(self):
        payload = self.payload()
        response = self.post(payload)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["total_list_value"], "52.63")
        self.assertEqual(response.data["total_discount"], "2.77")
        self.assertEqual(response.data["total_quote_value"], "49.86")
        self.assertEqual([line["net_total"] for line in response.data["lines"]], ["23.68", "26.18"])
        self.assertEqual([line["net_unit_price"] for line in response.data["lines"]], ["18.94", "9.52"])
        self.assertEqual([line["quantity"] for line in response.data["lines"]], ["1.25", "2.75"])
        self.assertEqual([line["specifications"] for line in response.data["lines"]],
                         [line["specifications"] for line in payload["lines"]])
        self.assertEqual([line["selling_price_snapshot"] for line in response.data["lines"]], ["19.99", "10.05"])
        self.assert_no_costs(response.data)
        detail = self.client.get(
            f'/api/purchasing/quotes/{response.data["quote_id"]}/',
            {"business_unit_id": self.unit.pk},
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["total_quote_value"], "49.86")
        self.assert_no_costs(detail.data)

    def test_exact_retry_after_price_change_preserves_both_snapshots_and_changed_payload_conflicts(self):
        payload = self.payload()
        first = self.post(payload)
        self.assertEqual(first.status_code, 201)
        configure_selling_price(
            actor=self.admin, business_unit=self.unit, variant=self.alternate_variant,
            amount=Decimal("99.99"),
        )
        before = self.snapshot()
        retry = self.post(payload)
        self.assertEqual(retry.data, first.data)
        self.assertEqual(retry.status_code, 201)
        changed = deepcopy(payload)
        changed["lines"][1]["quantity"] = "2.76"
        conflict = self.post(changed)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(before, self.snapshot())

    def test_invalid_or_unauthorized_later_line_writes_nothing(self):
        for changed, expected in (
            ({"quantity": "1.001"}, 400), ({"variant_id": 999999999}, 404),
            ({"location_id": self.unassigned.pk}, 400), ({"specifications": []}, 400),
        ):
            with self.subTest(invalid_fields=list(changed)):
                payload = self.payload()
                payload["lines"][1].update(changed)
                before = self.snapshot()
                response = self.post(payload)
                self.assertEqual(response.status_code, expected, response.data)
                self.assertEqual(before, self.snapshot())

    def test_missing_second_price_rolls_back_operation_and_entire_quote(self):
        ApprovedSellingPrice.objects.filter(
            business_unit=self.unit, variant=self.alternate_variant, is_current=True,
        ).update(is_current=False)
        before = self.snapshot()
        response = self.post(self.payload())
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(before, self.snapshot())

    def test_homogeneous_special_order_supported_but_mixed_stock_location_is_rejected(self):
        payload = self.payload() | {
            "kind": "special_order", "payment_terms_reason": "TEST ONLY existing full payment choice",
        }
        for line in payload["lines"]:
            del line["location_id"]
        response = self.post(payload)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["kind"], "special_order")
        mixed = deepcopy(payload)
        mixed["lines"][1]["location_id"] = self.location.pk
        before = self.snapshot()
        rejected = self.post(mixed, "test-only-mixed-rejected")
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(before, self.snapshot())

    def test_revocation_cross_bu_and_csrf_still_block_entire_multiline_submission(self):
        payload = self.payload() | {"business_unit_id": self.roche.pk}
        before = self.snapshot()
        self.assertEqual(self.post(payload).status_code, 403)
        self.assertEqual(before, self.snapshot())
        UserActionGrant.objects.filter(
            user=self.sales, business_unit=self.unit, action="create_order",
        ).update(revoked_at=timezone.now())
        before = self.snapshot()
        self.assertEqual(self.post(self.payload()).status_code, 403)
        self.assertEqual(before, self.snapshot())
        no_token = self.client.post(
            "/api/purchasing/quotes/", self.payload(), format="json",
            HTTP_IDEMPOTENCY_KEY="test-only-no-token",
        )
        self.assertEqual(no_token.status_code, 403)
        self.assertEqual(before, self.snapshot())
