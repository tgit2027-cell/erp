"""Disposable PostgreSQL fixtures only; no demo/preview data is modified."""

from decimal import Decimal

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    BusinessUnit, Item, Location, StockBalance, StockExample, StockMovement,
    StockReservation, UserActionGrant, Variant,
)
from .stock_ledger_views import StockQuantitySerializer, scoped_ledger_quantities
from .test_transactions import TransactionFixtureMixin
from .transaction_services import (
    ReceiptLine, deliver_reserved_stock, receive_stock, reserve_stock, transfer_stock,
)


class StockLedgerQuantityTests(TransactionFixtureMixin, TestCase):
    fields = {
        "id", "item_code", "item_description", "brand_name", "brand_code",
        "variant_sku", "variant_description", "location_code", "location_name", "unit_code",
        "unit_name", "quantity", "held_quantity", "sellable_quantity", "is_synthetic",
    }

    @classmethod
    def setUpTestData(cls):
        cls.create_synthetic_catalog()
        cls.reader = get_user_model().objects.get(username="demo_tivoli")
        cls.other_unit = BusinessUnit.objects.get(code="NAT")
        cls.source = cls.locations["W41-01"]
        cls.destination = cls.locations["W41-02"]

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.reader)

    def project(self, **params):
        return self.client.get("/api/stock-balances/", {"unit": self.business_unit.pk, **params})

    def quantities(self):
        response = self.project()
        self.assertEqual(response.status_code, 200, response.data)
        return {row["location_name"]: row["quantity"] for row in response.data["results"]}

    def reserve(self, quantity="2.00", key="projection-reservation"):
        return reserve_stock(
            idempotency_key=key, business_unit=self.business_unit,
            variant=self.variant, location=self.source, quantity=quantity,
        )

    def test_receipt_reservation_transfer_delivery_appear_once_and_never_add_examples(self):
        example = StockExample.objects.create(
            item=self.variant.item, variant=self.variant, location=self.source,
            responsible_business_unit=self.business_unit, quantity="99.00",
        )
        receipt = self.receive_fixture()
        self.assertEqual(self.receive_fixture(), receipt)
        self.assertEqual(self.quantities(), {self.source.name: "10.00"})
        reservation = self.reserve()
        self.assertEqual(self.reserve(), reservation)
        self.assertEqual(self.quantities(), {self.source.name: "8.00"})
        args = dict(
            idempotency_key="projection-transfer", business_unit=self.business_unit,
            variant=self.variant, source_location=self.source,
            destination_location=self.destination, quantity="3.00",
        )
        transfer = transfer_stock(**args)
        self.assertEqual(transfer_stock(**args), transfer)
        self.assertEqual(self.quantities(), {self.source.name: "5.00", self.destination.name: "3.00"})
        delivery_args = dict(
            idempotency_key="projection-delivery", reservation_id=reservation["reservation_id"],
            unit_selling_price="2000.00",
        )
        delivery = deliver_reserved_stock(**delivery_args)
        self.assertEqual(deliver_reserved_stock(**delivery_args), delivery)
        self.assertEqual(self.quantities(), {self.source.name: "5.00", self.destination.name: "3.00"})
        self.assertEqual(self.project().data["pagination"]["total_count"], 2)
        self.assertEqual(StockMovement.objects.count(), 5)  # receipt, landed, transfer pair, delivery
        example.refresh_from_db()
        self.assertEqual(example.quantity, Decimal("99.00"))
        self.assertFalse(StockExample.objects.filter(variant=self.variant, location=self.destination).exists())
        legacy = self.client.get(f"/api/stock/{example.pk}/", {"unit": self.business_unit.pk})
        self.assertEqual(legacy.data["quantity"], "99.00")

    def test_release_state_fixture_uses_updated_balance_not_reservation_or_movement_history(self):
        self.receive_fixture()
        reservation = self.reserve()
        self.assertEqual(self.quantities()[self.source.name], "8.00")
        movement_count = StockMovement.objects.count()
        # No release service exists. Set a coherent released state ONLY in this
        # rolled-back fixture; this test adds no release behavior to the app.
        with transaction.atomic():
            StockReservation.objects.filter(pk=reservation["reservation_id"]).update(
                status=StockReservation.Status.RELEASED,
            )
            StockBalance.objects.filter(pk=self.balance_at().pk).update(reserved_quantity=Decimal("0.00"))
        self.assertEqual(self.quantities()[self.source.name], "10.00")
        self.assertEqual(StockMovement.objects.count(), movement_count)

    def test_empty_and_fully_reserved_balances_never_fall_back_to_static_examples(self):
        self.assertGreater(StockExample.objects.count(), 0)
        self.assertEqual(self.project().data["results"], [])
        self.receive_fixture()
        self.reserve("10.00")
        self.assertEqual(self.quantities(), {self.source.name: "0.00"})
        self.assertEqual(self.project().data["pagination"]["total_count"], 1)

    def other_balance(self, *, shared=False):
        location = Location.objects.get(code="SNAT")
        if shared:
            self.source.business_units.add(self.other_unit)
            location = self.source
        receive_stock(
            idempotency_key="other-unit-receipt", business_unit=self.other_unit,
            lines=[ReceiptLine(variant=self.variant, location=location, quantity=Decimal("7.00"),
                               unit_cost=Decimal("1.00"))],
            eligible_landed_charges=Decimal("0.00"),
        )
        return StockBalance.objects.get(business_unit=self.other_unit, variant=self.variant)

    def test_unit_location_header_direct_id_and_revoked_grant_denials(self):
        self.receive_fixture()
        other = self.other_balance()
        self.assertEqual(self.project(unit=self.other_unit.pk).status_code, 403)
        self.assertEqual(self.client.get("/api/stock-balances/", HTTP_X_BUSINESS_UNIT=str(self.other_unit.pk)).status_code, 403)
        self.assertEqual(self.project(location=other.location_id).status_code, 403)
        self.assertEqual(self.client.get(f"/api/stock-balances/{other.pk}/", {"unit": self.business_unit.pk}).status_code, 404)
        own = self.balance_at()
        UserActionGrant.objects.filter(user=self.reader, business_unit=self.business_unit).update(revoked_at=timezone.now())
        self.assertEqual(self.project().status_code, 403)
        self.assertEqual(self.client.get(f"/api/stock-balances/{own.pk}/", {"unit": self.business_unit.pk}).status_code, 403)

    def test_shared_location_does_not_merge_units_and_removed_membership_hides_balance(self):
        self.receive_fixture()
        self.other_balance(shared=True)
        self.assertEqual(self.quantities(), {self.source.name: "10.00"})
        own = self.balance_at()
        self.source.business_units.remove(self.business_unit)
        self.assertEqual(self.project().data["results"], [])
        self.assertEqual(self.project(location=self.source.pk).status_code, 403)
        self.assertEqual(self.client.get(f"/api/stock-balances/{own.pk}/", {"unit": self.business_unit.pk}).status_code, 404)

    def test_create_order_or_staff_does_not_replace_read_stock_and_expired_access_is_denied(self):
        UserActionGrant.objects.filter(user=self.reader, business_unit=self.business_unit).update(revoked_at=timezone.now())
        UserActionGrant.objects.create(user=self.reader, business_unit=self.business_unit, action="create_order")
        self.reader.is_staff = True
        self.reader.save(update_fields=["is_staff"])
        self.assertEqual(self.project().status_code, 403)
        UserActionGrant.objects.filter(user=self.reader, action="read_stock").update(
            revoked_at=None, starts_at=timezone.now() - timezone.timedelta(days=2),
            ends_at=timezone.now() - timezone.timedelta(days=1),
        )
        self.assertEqual(self.project().status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.project().status_code, 403)

    def test_exact_allowlist_decimal_strings_and_read_only_http_methods(self):
        self.receive_fixture(quantity="10.37")
        self.reserve("0.12")
        row = self.project().data["results"][0]
        self.assertEqual(set(row), self.fields)
        self.assertEqual(row["quantity"], "10.25")
        self.assertEqual(row["location_code"], self.source.code)
        detail = self.client.get(f"/api/stock-balances/{row['id']}/", {"unit": self.business_unit.pk})
        self.assertEqual(detail.data, row)
        self.reader.is_staff = True
        self.reader.save(update_fields=["is_staff"])
        self.assertEqual(set(self.project().data["results"][0]), self.fields)
        for path in ("/api/stock-balances/", f"/api/stock-balances/{row['id']}/"):
            for method in ("post", "put", "patch", "delete"):
                self.assertEqual(getattr(self.client, method)(path, {}, format="json").status_code, 405)

    def test_search_pagination_scope_and_ledger_only_buckets(self):
        self.receive_fixture()
        for index in range(4):
            variant = Variant.objects.create(
                item=self.variant.item, sku=f"PROJECTION-{index}", description="TEST ONLY",
            )
            receive_stock(
                idempotency_key=f"page-receipt-{index}", business_unit=self.business_unit,
                lines=[ReceiptLine(variant=variant, location=self.source,
                                   quantity=Decimal("1.00"), unit_cost=Decimal("1.00"))],
                eligible_landed_charges=Decimal("0.00"),
            )
        other = self.other_balance()
        expected = list(StockBalance.objects.filter(business_unit=self.business_unit).order_by(
            "variant__item__item_code", "variant__sku", "pk",
        ).values_list("pk", flat=True))
        actual = []
        for page, size in ((1, 2), (2, 2), (3, 1)):
            response = self.project(page=page, page_size=2)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["pagination"]["total_count"], 5)
            self.assertEqual(len(response.data["results"]), size)
            actual.extend(row["id"] for row in response.data["results"])
        self.assertEqual(actual, expected)
        for term in (self.variant.item.item_code, self.variant.item.description,
                     self.variant.item.brand.name, self.variant.sku, self.source.name,
                     self.source.code):
            with self.subTest(term=term):
                result = self.project(search=term, location=self.source.pk)
                self.assertEqual(result.status_code, 200)
                self.assertIn(self.balance_at().pk, [row["id"] for row in result.data["results"]])
                self.assertTrue(all(row["unit_code"] == "TVL" for row in result.data["results"]))
        self.assertEqual(self.project(search="Natuzzi").data["results"], [])
        foreign_code_search = self.project(search=other.location.code)
        self.assertEqual(foreign_code_search.status_code, 200)
        self.assertEqual(foreign_code_search.data["results"], [])
        self.assertEqual(foreign_code_search.data["pagination"]["total_count"], 0)
        for query in ({"page": 0}, {"page_size": 101}, {"page": 4, "page_size": 2}, {"location": "bad"}):
            self.assertEqual(self.project(**query).status_code, 400)

    def test_gets_create_or_update_no_records_and_page_metadata_has_no_cost_fields(self):
        self.receive_fixture()
        models = list(apps.get_app_config("foundation").get_models())
        before = {model: list(model.objects.order_by("pk").values()) for model in models}
        self.project()
        self.client.get(f"/api/stock-balances/{self.balance_at().pk}/", {"unit": self.business_unit.pk})
        self.client.get("/api/stock/", {"unit": self.business_unit.pk})
        self.assertEqual(
            {model: list(model.objects.order_by("pk").values()) for model in models}, before,
        )
        self.assertEqual(set(self.project().data), {"results", "pagination"})

    def test_quantity_serialization_does_not_fetch_value_columns_or_related_rows_per_row(self):
        self.receive_fixture()
        request = type("Request", (), {
            "user": self.reader, "query_params": {"unit": str(self.business_unit.pk)},
            "headers": {}, "session": {},
        })()
        _unit, records = scoped_ledger_quantities(request)
        with CaptureQueriesContext(connection) as queries:
            data = StockQuantitySerializer(list(records), many=True).data
        self.assertEqual(len(queries), 1)
        self.assertEqual(data[0]["quantity"], "10.00")
        self.assertNotIn('"on_hand_value"', queries[0]["sql"])

    def test_catalog_match_is_read_only_and_only_returns_exact_authorized_ledger_codes(self):
        self.receive_fixture()
        models = list(apps.get_app_config("foundation").get_models())
        before = {model: list(model.objects.order_by("pk").values()) for model in models}
        response = self.client.get("/api/stock-catalog-matches/", {
            "unit": self.business_unit.pk,
            "code": [
                self.variant.item.item_code.lower(),
                self.variant.sku.lower(),
                "NO-SYNTHETIC-MATCH",
            ],
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(set(response.data), {"matched_codes"})
        self.assertEqual(
            response.data["matched_codes"],
            [self.variant.item.item_code.lower(), self.variant.sku.lower()],
        )
        self.assertEqual(
            {model: list(model.objects.order_by("pk").values()) for model in models},
            before,
        )
        for query in (
            {"unit": self.business_unit.pk},
            {"unit": self.business_unit.pk, "code": " "},
            {"unit": self.business_unit.pk, "code": "X" * 81},
            {"unit": self.business_unit.pk, "code": [f"ITEM-{index}" for index in range(81)]},
        ):
            self.assertEqual(self.client.get("/api/stock-catalog-matches/", query).status_code, 400)
        self.assertEqual(
            self.client.post(
                "/api/stock-catalog-matches/",
                {"unit": self.business_unit.pk, "code": [self.variant.sku]},
                format="json",
            ).status_code,
            405,
        )

    def test_catalog_match_denies_cross_unit_and_user_without_existing_read_stock_grant(self):
        self.receive_fixture()
        response = self.client.get("/api/stock-catalog-matches/", {
            "unit": self.other_unit.pk,
            "code": self.variant.item.item_code,
        })
        self.assertEqual(response.status_code, 403)

    def test_catalog_match_excludes_nonmember_locations_and_honors_revoked_grant(self):
        unlinked = Location.objects.create(
            legal_entity=self.business_unit.legal_entity,
            code="test-unlinked-preview-location",
            name="TEST ONLY unlinked location",
        )
        item = Item.objects.create(
            brand=self.variant.item.brand,
            item_code="TEST-UNLINKED-CATALOG",
            description="TEST ONLY unlinked item",
        )
        variant = Variant.objects.create(
            item=item, sku="TEST-UNLINKED-SKU", description="TEST ONLY unlinked variant",
        )
        StockBalance.objects.create(
            legal_entity=self.business_unit.legal_entity,
            business_unit=self.business_unit,
            variant=variant,
            location=unlinked,
            on_hand_quantity="1.00",
        )
        response = self.client.get("/api/stock-catalog-matches/", {
            "unit": self.business_unit.pk,
            "code": variant.sku,
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["matched_codes"], [])

        grant = UserActionGrant.objects.get(
            user=self.reader,
            business_unit=self.business_unit,
            action=UserActionGrant.Action.READ_STOCK,
        )
        grant.revoked_at = timezone.now()
        grant.save(update_fields=["revoked_at"])
        response = self.client.get("/api/stock-catalog-matches/", {
            "unit": self.business_unit.pk,
            "code": variant.sku,
        })
        self.assertEqual(response.status_code, 403)

        no_grant = get_user_model().objects.create_user(username="packing_preview_no_grant")
        self.client.force_authenticate(no_grant)
        response = self.client.get("/api/stock-catalog-matches/", {
            "unit": self.business_unit.pk,
            "code": self.variant.item.item_code,
        })
        self.assertEqual(response.status_code, 403)
