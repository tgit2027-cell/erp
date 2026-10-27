"""Only disposable synthetic fixtures; the upload itself never reaches Django."""

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from .models import BusinessUnit, Location, StockBalance, SyntheticCounterparty, UserActionGrant, Variant
from .test_transactions import TransactionFixtureMixin


class PackingReceiptCandidateTests(TransactionFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_synthetic_catalog()
        cls.reader = get_user_model().objects.get(username="demo_tivoli")
        cls.source = cls.locations["W41-01"]
        cls.destination = cls.locations["W41-02"]

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.reader)
        self.receive_fixture()

    def candidates(self, **params):
        return self.client.get("/api/stock-receipt-candidates/", {
            "unit": self.business_unit.pk, "code": self.variant.item.item_code, **params,
        })

    def balance(self, variant, location=None, unit=None):
        unit = unit or self.business_unit
        return StockBalance.objects.create(
            variant=variant, business_unit=unit, legal_entity=unit.legal_entity,
            location=location or self.source, on_hand_quantity="1.00",
            on_hand_value="9876.54", reserved_quantity="0.00",
        )

    def test_explicit_ambiguous_variants_are_sorted_deduplicated_and_safe_fields_only(self):
        second = Variant.objects.create(
            item=self.variant.item, sku="AAA-TEST-SECOND", description="TEST ONLY second variant",
        )
        self.balance(second)
        self.balance(self.variant, self.destination)
        with CaptureQueriesContext(connection) as queries:
            response = self.candidates(code=self.variant.item.item_code.lower())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(set(response.data), {"candidates"})
        rows = response.data["candidates"]
        self.assertEqual([row["variant_id"] for row in rows], [second.pk, self.variant.pk])
        for row in rows:
            self.assertEqual(set(row), {
                "variant_id", "variant_sku", "variant_description",
                "item_code", "item_description", "brand_name",
            })
        self.assertNotIn('"on_hand_value"', " ".join(query["sql"] for query in queries))
        exact = self.candidates(code=self.variant.sku.lower())
        self.assertEqual([row["variant_id"] for row in exact.data["candidates"]], [self.variant.pk])

    def test_cross_unit_location_removed_membership_and_revocation_fail_closed(self):
        other_unit = BusinessUnit.objects.get(code="NAT")
        other_location = Location.objects.create(
            code="TEST-CANDIDATE-FOREIGN", name="TEST ONLY other unit",
            legal_entity=other_unit.legal_entity,
        )
        other_location.business_units.add(other_unit)
        foreign = Variant.objects.create(item=self.variant.item, sku="TEST-FOREIGN-VARIANT")
        self.balance(foreign, other_location, other_unit)
        self.assertEqual(self.candidates(code=foreign.sku).data["candidates"], [])
        self.assertEqual(self.candidates(unit=other_unit.pk).status_code, 403)
        self.assertEqual(self.candidates(location=other_location.pk).status_code, 403)
        self.source.business_units.remove(self.business_unit)
        self.assertEqual(self.candidates().data["candidates"], [])
        self.source.business_units.add(self.business_unit)
        UserActionGrant.objects.filter(
            user=self.reader, business_unit=self.business_unit,
            action=UserActionGrant.Action.READ_STOCK,
        ).update(revoked_at=timezone.now())
        self.assertEqual(self.candidates().status_code, 403)

    def test_unstocked_and_nonsynthetic_variants_never_expand_catalog_scope(self):
        absent = Variant.objects.create(item=self.variant.item, sku="TEST-NO-LEDGER")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Variant.objects.create(
                item=self.variant.item, sku="TEST-NON-SYNTHETIC", is_synthetic=False,
            )
        response = self.candidates(code=[absent.sku, "TEST-NON-SYNTHETIC"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"candidates": []})

    def test_get_is_nonmutating_and_read_stock_never_grants_receipt_posting(self):
        supplier = SyntheticCounterparty.objects.create(
            party_type="supplier", code="TEST-SUP-PACKING", name="TEST ONLY packing supplier",
        )
        models = list(apps.get_app_config("foundation").get_models())
        snapshot = lambda: {model.__name__: list(model.objects.order_by("pk").values()) for model in models}
        before = snapshot()
        self.assertEqual(self.candidates().status_code, 200)
        self.assertEqual(self.client.post("/api/stock-receipt-candidates/", {}).status_code, 405)
        denied = self.client.post("/api/purchasing/receipts/", {
            "business_unit_id": self.business_unit.pk,
            "supplier_id": supplier.pk,
            "lines": [{
                "variant_id": self.variant.pk, "location_id": self.source.pk,
                "quantity": "1.00", "unit_cost": "1000.00",
            }],
        }, format="json", HTTP_IDEMPOTENCY_KEY="TEST-READ-DOES-NOT-GRANT-WRITE")
        self.assertEqual(denied.status_code, 403, denied.data)
        self.assertEqual(snapshot(), before)
        self.client.force_authenticate(user=None)
        self.assertEqual(self.candidates().status_code, 403)

    def test_invalid_queries_and_overlarge_variant_choices_are_rejected_not_truncated(self):
        for params in (
            {"code": ""}, {"code": "X" * 81},
            {"code": [f"CODE-{index}" for index in range(81)]}, {"location": "invalid"},
        ):
            with self.subTest(params=params):
                self.assertEqual(self.candidates(**params).status_code, 400)
        variants = Variant.objects.bulk_create([
            Variant(item=self.variant.item, sku=f"TEST-MANY-{index:03}")
            for index in range(200)
        ])
        StockBalance.objects.bulk_create([
            StockBalance(
                business_unit=self.business_unit, legal_entity=self.business_unit.legal_entity,
                variant=variant, location=self.source,
            ) for variant in variants
        ])
        self.assertEqual(self.candidates().status_code, 400)
