"""Read-only delivery review, with independent stock identity authorization."""
from decimal import Decimal

from django.apps import apps
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import UserActionGrant
from .purchasing_services import create_sales_quote
from .test_purchasing_workflows import PurchasingFixtureMixin


class DeliveryPreparationTests(PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()

    def setUp(self):
        self.make_receipt(key="preparation-opening")
        self.quote_id = create_sales_quote(
            actor=self.sales, idempotency_key="preparation-multiline",
            business_unit=self.unit, customer=self.customer, kind="stock",
            discount_percent=Decimal("0"), discount_reason="TEST ONLY",
            lines=[{
                "variant": self.variant, "location": self.location,
                "quantity": Decimal(quantity), "specifications": {"finish": label},
            } for quantity, label in (("1.25", "TEST ONLY linen"), ("2.75", "TEST ONLY oak"))],
        )["quote_id"]
        self.client = APIClient()
        self.client.force_login(self.sales)

    def read(self, **query):
        return self.client.get(
            f"/api/purchasing/quotes/{self.quote_id}/preparation/",
            {"business_unit_id": self.unit.pk, "unit": self.unit.pk, **query},
        )

    def snapshot(self):
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in apps.get_app_config("foundation").get_models()
        }

    def test_multiline_exact_quantities_identity_allowlist_and_get_no_writes(self):
        before = self.snapshot()
        response = self.read()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(before, self.snapshot())
        self.assertEqual([line["quantity"] for line in response.data["lines"]], ["1.25", "2.75"])
        progress = response.data["delivery_progress"]
        self.assertEqual([line["remaining_quantity"] for line in progress["lines"]], ["1.25", "2.75"])
        self.assertIsNone(progress["ready_at"])
        for line in response.data["lines"]:
            self.assertEqual(set(line["preparation_identity"]), {
                "item_code", "description", "sku", "brand", "location_code", "location_name",
            })
            self.assertEqual(line["preparation_identity"]["location_code"], self.location.code)
        text = str(response.data)
        for field in ("unit_cost", "landed_cost", "gross_margin", "on_hand_quantity"):
            self.assertNotIn(field, text)

    def test_stock_grant_revocation_masks_identity_without_granting_or_removing_quote_rights(self):
        self.assertIsNotNone(self.read().data["lines"][0]["preparation_identity"])
        UserActionGrant.objects.filter(
            user=self.sales, business_unit=self.unit, action="read_stock",
        ).update(revoked_at=timezone.now())
        response = self.read()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(line["preparation_identity"] is None for line in response.data["lines"]))

    def test_explicit_location_membership_removed_masks_identity(self):
        self.location.business_units.remove(self.unit)
        response = self.read()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(line["preparation_identity"] is None for line in response.data["lines"]))

    def test_quote_revocation_cross_bu_and_unaligned_stock_context_denied(self):
        self.assertEqual(self.read(business_unit_id=self.roche.pk, unit=self.roche.pk).status_code, 404)
        self.assertEqual(self.read(unit=self.roche.pk).status_code, 403)
        UserActionGrant.objects.filter(
            user=self.sales, business_unit=self.unit, action="create_order",
        ).update(revoked_at=timezone.now())
        self.assertEqual(self.read().status_code, 404)

    def test_unauthenticated_wrong_persona_missing_context_and_post_denied(self):
        self.client.logout()
        self.assertEqual(self.read().status_code, 403)
        self.client.force_login(self.roche_user)
        self.assertEqual(self.read().status_code, 404)
        self.client.force_login(self.sales)
        path = f"/api/purchasing/quotes/{self.quote_id}/preparation/"
        self.assertEqual(self.client.get(path, {"business_unit_id": self.unit.pk}).status_code, 400)
        self.assertEqual(self.client.post(path, {}, format="json").status_code, 405)

    def test_preparation_favorite_is_personal_navigation_not_quote_authority(self):
        self.client.force_login(self.roche_user)
        for destination in ("delivery", "delivery.preparation"):
            self.assertEqual(self.client.post("/api/favorites/", {"destination": destination}, format="json").status_code, 201)
        self.assertEqual(len(self.client.get("/api/favorites/").data["favorites"]), 2)
        self.assertEqual(self.read().status_code, 404)
        self.client.force_login(self.sales)
        self.assertEqual(self.client.get("/api/favorites/").data["favorites"], [])
