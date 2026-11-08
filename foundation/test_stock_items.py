"""Stock item list, details, photos and cost visibility; synthetic only."""

import shutil
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Item, ProductBrand, SyntheticCounterparty, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .quotation_presentation import item_presentation
from .stock_items import item_detail, items_overview, scan_item, update_item, upload_photo
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
MEDIA = tempfile.mkdtemp(prefix="item-photos-test-")


@override_settings(APP_ENV="demo", MEDIA_ROOT=MEDIA)
class StockItemTests(PurchasingFixtureMixin, TestCase):
    """Admin edits items and sees costs; sales only reads stock."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for action in (UserActionGrant.Action.MANAGE_ITEMS, UserActionGrant.Action.VIEW_COSTS):
            UserActionGrant.objects.create(user=cls.admin, business_unit=cls.unit, action=action)
        UserActionGrant.objects.get_or_create(user=cls.admin, business_unit=cls.unit, action=UserActionGrant.Action.READ_STOCK)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.make_receipt(key="in", quantity="4.00", unit_cost="1000.00",
                          charges=[{"category": "shipping", "amount": Decimal("400.00"), "eligible": True}])

    def row(self, actor, sku=None):
        data = items_overview(actor=actor, business_unit=self.unit)
        return data, next(row for row in data["rows"] if row["sku"] == (sku or self.variant.sku))

    def test_everyone_sees_the_visible_fields_and_only_cost_viewers_see_costs(self):
        data, row = self.row(self.sales)
        self.assertEqual((row["item_code"], row["quantity"], row["selling_price"]), (self.variant.item.item_code, "4.00", "2000.00"))
        self.assertEqual(row["locations"][0]["quantity"], "4.00")
        self.assertNotIn("costs", row)
        self.assertEqual((data["can_edit"], data["can_see_costs"]), (False, False))
        _, row = self.row(self.admin)
        self.assertEqual(row["costs"]["unit_cost"], "1000.00")
        self.assertEqual(row["costs"]["landed_unit_cost"], "1100.00", "Shipping spread over the four pieces")
        self.assertEqual(row["costs"]["average_cost"], "1100.00")

    def test_details_company_and_brand(self):
        item = self.variant.item
        supplier = self.supplier
        brand = ProductBrand.objects.create(name="TEST ONLY Second brand", code="TSB")
        updated = update_item(actor=self.admin, business_unit=self.unit, item_id=item.pk, data={
            "supplier_id": supplier.pk, "brand_id": brand.pk, "supplier_item_code": " AB-123 ", "barcode": "6291234567890",
            "unit_of_measure": "set", "width_cm": "210", "depth_cm": "95.5", "height_cm": "80", "material": "Oak and linen",
            "colour": "Sand", "weight_kg": "48.5", "volume_m3": "1.596", "hs_code": "940161", "lead_time_days": "70",
        })
        self.assertEqual((updated["company"], updated["brand"], updated["supplier_item_code"]), (supplier.name, brand.name, "AB-123"))
        self.assertEqual((updated["width_cm"], updated["volume_m3"], updated["lead_time_days"]), ("210.0", "1.596", 70))
        self.assertIn(supplier, brand.suppliers.all(), "Choosing the company and brand links them")
        choices = item_detail(actor=self.sales, business_unit=self.unit, item_id=item.pk)
        self.assertIn(brand.pk, next(each for each in choices["suppliers"] if each["id"] == supplier.pk)["brand_ids"])
        other = Item.objects.exclude(pk=item.pk).first()
        with self.assertRaisesMessage(InvalidTransaction, "already on another item"):
            update_item(actor=self.admin, business_unit=self.unit, item_id=other.pk, data={"barcode": "6291234567890"})
        with self.assertRaisesMessage(InvalidTransaction, "at most 1 decimals"):
            update_item(actor=self.admin, business_unit=self.unit, item_id=item.pk, data={"width_cm": "1.25"})
        customer = SyntheticCounterparty.objects.filter(party_type=SyntheticCounterparty.Type.CUSTOMER).first()
        with self.assertRaisesMessage(InvalidTransaction, "company we buy from"):
            update_item(actor=self.admin, business_unit=self.unit, item_id=item.pk, data={"supplier_id": customer.pk})
        with self.assertRaisesMessage(WorkflowPermissionDenied, "edit item details"):
            update_item(actor=self.sales, business_unit=self.unit, item_id=item.pk, data={"colour": "Red"})
        update_item(actor=self.admin, business_unit=self.unit, item_id=item.pk, data={"is_active": False})
        self.assertFalse(any(row["sku"] == self.variant.sku for row in items_overview(actor=self.sales, business_unit=self.unit)["rows"]))

    def test_photo_upload_is_checked_and_shown_on_quotations_and_through_the_api(self):
        item = self.variant.item
        with self.assertRaisesMessage(InvalidTransaction, "not the photo type"):
            upload_photo(actor=self.admin, business_unit=self.unit, item_id=item.pk,
                         upload=SimpleUploadedFile("chair.png", b"%PDF-1.4 not a photo"))
        with self.assertRaisesMessage(InvalidTransaction, "PNG, JPG or WebP"):
            upload_photo(actor=self.admin, business_unit=self.unit, item_id=item.pk, upload=SimpleUploadedFile("chair.gif", PNG))
        saved = upload_photo(actor=self.admin, business_unit=self.unit, item_id=item.pk, upload=SimpleUploadedFile("chair.png", PNG))
        self.assertRegex(saved["photo_url"], rf"^/api/items/{item.pk}/photo/[0-9a-f]{{16}}\.png$")
        item.refresh_from_db()
        self.assertEqual(item_presentation(item)["image_path"], saved["photo_url"], "Quotations use the uploaded photo")
        client = APIClient()
        client.force_authenticate(self.sales)
        response = client.get(saved["photo_url"])
        self.assertEqual((response.status_code, response["Content-Type"], response["X-Content-Type-Options"]), (200, "image/png", "nosniff"))
        self.assertEqual(b"".join(response.streaming_content), PNG)
        self.assertEqual(client.get(f"/api/items/{item.pk}/photo/0000000000000000.png").status_code, 404)
        self.assertEqual(APIClient().get(saved["photo_url"]).status_code, 403)

    def test_api(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        unit = f"?unit={self.unit.pk}"
        listed = client.get(f"/api/inventory/items/{unit}&search={self.variant.sku}")
        self.assertEqual((listed.status_code, listed.data["shown"], listed.data["can_see_costs"]), (200, 2, True), "Two finishes match")
        item_id = next(row["item_id"] for row in listed.data["rows"] if row["sku"] == self.variant.sku)
        changed = client.patch(f"/api/inventory/items/{item_id}/{unit}", {"colour": "Sand"}, format="json")
        self.assertEqual((changed.status_code, changed.data["colour"]), (200, "Sand"))
        photo = client.post(f"/api/inventory/items/{item_id}/photo/{unit}", {"file": SimpleUploadedFile("c.png", PNG)}, format="multipart")
        self.assertEqual(photo.status_code, 200, photo.data)
        reader = APIClient()
        reader.force_authenticate(self.sales)
        self.assertNotIn("costs", reader.get(f"/api/inventory/items/{unit}").data["rows"][0])
        self.assertEqual(reader.patch(f"/api/inventory/items/{item_id}/{unit}", {"colour": "Red"}, format="json").status_code, 403)
        outsider = APIClient()
        outsider.force_authenticate(self.roche_user)
        self.assertEqual(outsider.get(f"/api/inventory/items/{unit}").status_code, 403)

    def test_scanning_finds_our_label_the_makers_barcode_or_the_item_code(self):
        item = self.variant.item
        Item.objects.filter(pk=item.pk).update(barcode="4006381333931")
        found = scan_item(actor=self.sales, business_unit=self.unit, code=f"  {self.variant.sku.lower()} ")
        self.assertEqual((found["matched_by"], [row["sku"] for row in found["rows"]]), ("sku", [self.variant.sku]))
        self.assertEqual((found["rows"][0]["quantity"], found["rows"][0]["barcode"]), ("4.00", "4006381333931"))
        self.assertNotIn("costs", found["rows"][0])
        by_barcode = scan_item(actor=self.admin, business_unit=self.unit, code="4006381333931")
        self.assertEqual(by_barcode["matched_by"], "barcode")
        self.assertIn(self.variant.sku, [row["sku"] for row in by_barcode["rows"]])
        self.assertIn("costs", by_barcode["rows"][0])
        self.assertEqual(scan_item(actor=self.sales, business_unit=self.unit, code=item.item_code)["matched_by"], "item_code")
        with self.assertRaisesMessage(InvalidTransaction, "Nothing found for NOPE-1"):
            scan_item(actor=self.sales, business_unit=self.unit, code="NOPE-1")
        with self.assertRaisesMessage(InvalidTransaction, "Scan or type"):
            scan_item(actor=self.sales, business_unit=self.unit, code=" ")
        client = APIClient()
        client.force_authenticate(self.sales)
        response = client.get(f"/api/inventory/scan/?unit={self.unit.pk}&code={self.variant.sku}")
        self.assertEqual((response.status_code, response.data["rows"][0]["sku"]), (200, self.variant.sku))
        outsider = APIClient()
        outsider.force_authenticate(self.roche_user)
        self.assertEqual(outsider.get(f"/api/inventory/scan/?unit={self.unit.pk}&code={self.variant.sku}").status_code, 403)
