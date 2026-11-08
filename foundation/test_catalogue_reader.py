"""Reading supplier catalogues and price lists into draft items; synthetic only, the AI is faked."""

import io
import shutil
import tempfile
from unittest.mock import patch

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from .catalogue_reader import create_items, discard_import, import_detail, read_catalogue
from .document_ai import DocumentReaderUnavailable
from .models import CatalogueImport, Item, ProductBrand, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .stock_items import items_overview
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

MEDIA = tempfile.mkdtemp(prefix="catalogue-test-")


def catalogue_pdf(pages=1):
    """A page with a red 'product photo' in its top-left quarter, as a PDF."""
    images = []
    for _ in range(pages):
        page = Image.new("RGB", (800, 1000), "white")
        page.paste((200, 30, 30), (80, 100, 400, 500))
        images.append(page)
    buffer = io.BytesIO()
    images[0].save(buffer, "PDF", resolution=72, save_all=True, append_images=images[1:])
    return buffer.getvalue()


def ai_item(**overrides):
    item = {
        "page": 1, "brand": "TEST ONLY Lumen", "brand_item_code": "LM-200", "description": "TEST ONLY Lounge chair, ash frame",
        "category": "Lounge chair", "width_cm": "72", "depth_cm": "80.5", "height_cm": "78", "material": "Ash, boucle",
        "colour": "Ivory", "unit_of_measure": "piece", "price": "1,250.00", "currency": "eur", "barcode": "",
        "has_photo": True, "photo_box": {"left": 100, "top": 100, "right": 500, "bottom": 500}, "check": "",
    }
    item.update(overrides)
    return item


@override_settings(APP_ENV="demo", MEDIA_ROOT=MEDIA)
class CatalogueReaderTests(PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for action in (UserActionGrant.Action.MANAGE_ITEMS, UserActionGrant.Action.VIEW_COSTS, UserActionGrant.Action.READ_STOCK):
            UserActionGrant.objects.get_or_create(user=cls.admin, business_unit=cls.unit, action=action)
        # Sales may add items but not see costs.
        UserActionGrant.objects.create(user=cls.sales, business_unit=cls.unit, action=UserActionGrant.Action.MANAGE_ITEMS)
        Item.objects.filter(pk=cls.variant.item_id).update(item_code="TVF-007", supplier_item_code="lm-200")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        for target, value in (("foundation.catalogue_reader.is_available", True),):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def read(self, items, *, actor=None, name="lumen-2026.pdf", data=None, **kwargs):
        upload = SimpleUploadedFile(name, data if data is not None else catalogue_pdf())
        with patch("foundation.catalogue_reader.ask", return_value={"summary": "TEST ONLY 2026 price list, EUR ex-works", "items": items}) as ask:
            result = read_catalogue(actor=actor or self.admin, business_unit=self.unit, supplier_id=self.supplier.pk,
                                    upload=upload, **kwargs)
        return result, ask

    def test_reading_a_pdf_cuts_photos_suggests_codes_and_flags_duplicates(self):
        result, ask = self.read([ai_item(), ai_item(brand_item_code="LM-300", has_photo=False, width_cm="about 70",
                                                    price="", currency="")])
        block = ask.call_args.args[0][0]
        self.assertEqual((block["type"], block["source"]["media_type"]), ("document", "application/pdf"))
        self.assertIn(self.supplier.name, ask.call_args.kwargs["instructions"])
        first, second = result["drafts"]
        self.assertEqual((first["item_code"], second["item_code"]), ("TVF-008", "TVF-009"), "After the highest TVF code")
        self.assertEqual((first["width_cm"], first["depth_cm"], first["cost"], first["currency"]), ("72.0", "80.5", "1250.00", "EUR"))
        self.assertEqual(first["duplicates"], [f"Brand code LM-200 is already item TVF-007 ({self.variant.item.brand.name})."])
        self.assertEqual(second["check"], ["The width could not be read."])
        self.assertEqual(second["photo_url"], "")
        stored = CatalogueImport.objects.get().drafts[0]["photo"]["name"]
        with default_storage.open(stored, "rb") as handle:
            photo = Image.open(handle)
            photo.load()
        # The red block fills the middle of the cut-out (page drawn at 2x).
        red, green, _ = photo.getpixel((photo.width // 2, photo.height // 2))
        self.assertTrue(red > 180 and green < 80, (red, green))
        self.assertGreater(photo.width, 300)
        self.assertEqual(result["import"]["pages_read"], "1-1")

        hidden = import_detail(actor=self.sales, business_unit=self.unit, import_id=result["import"]["id"])
        self.assertFalse(hidden["can_see_costs"])
        self.assertNotIn("cost", hidden["drafts"][0])

    def test_creating_ticked_drafts_makes_items_with_photo_brand_and_supplier_price(self):
        result, _ = self.read([ai_item(), ai_item(brand_item_code="LM-300")])
        record = CatalogueImport.objects.get()
        with self.captureOnCommitCallbacks(execute=True):
            created = create_items(actor=self.admin, business_unit=self.unit, import_id=record.pk, choices=[
                {"index": 0, "brand_name": "TEST ONLY Lumen", "description": "TEST ONLY Lumen lounge chair", "colour": "Ivory"},
            ])
        self.assertEqual([row["item_code"] for row in created["created"]], ["TVF-008"])
        item = Item.objects.get(item_code="TVF-008")
        self.assertEqual((item.supplier, item.brand.name, item.supplier_item_code), (self.supplier, "TEST ONLY Lumen", "LM-200"))
        self.assertEqual((str(item.supplier_price), item.supplier_price_currency), ("1250.00", "EUR"))
        self.assertIn(self.supplier, item.brand.suppliers.all())
        self.assertEqual(item.variants.get().sku, "TVF-008-IVO")
        self.assertTrue(item.photo and item.image_path.startswith(f"/api/items/{item.pk}/photo/"))
        self.assertFalse(default_storage.exists(record.drafts[0]["photo"]["name"]), "Draft photos are removed once finished")
        listed = items_overview(actor=self.admin, business_unit=self.unit, search="TVF-008")["rows"]
        self.assertEqual(listed[0]["costs"]["supplier_price"], "1250.00", "New items show in the unit's list before any stock")
        self.assertEqual(CatalogueImport.objects.get().status, CatalogueImport.Status.DONE)
        with self.assertRaisesMessage(InvalidTransaction, "already been finished"):
            create_items(actor=self.admin, business_unit=self.unit, import_id=record.pk, choices=[{"index": 1, "brand_name": "X"}])

    def test_rules_permissions_and_costs_for_people_who_cannot_see_them(self):
        result, _ = self.read([ai_item(barcode="6291000000001")], actor=self.sales)
        record = CatalogueImport.objects.get()
        self.assertNotIn("cost", result["drafts"][0])
        brand = ProductBrand.objects.first()
        with self.assertRaisesMessage(InvalidTransaction, "choose a brand"):
            create_items(actor=self.sales, business_unit=self.unit, import_id=record.pk, choices=[{"index": 0}])
        with self.assertRaisesMessage(InvalidTransaction, "already used"):
            create_items(actor=self.sales, business_unit=self.unit, import_id=record.pk,
                         choices=[{"index": 0, "brand_id": brand.pk, "item_code": "TVF-007"}])
        created = create_items(actor=self.sales, business_unit=self.unit, import_id=record.pk, choices=[
            {"index": 0, "brand_id": brand.pk, "cost": "1.00", "currency": "EUR", "use_photo": False}])
        item = Item.objects.get(pk=created["created"][0]["item_id"])
        self.assertIsNone(item.supplier_price, "Someone who cannot see costs does not save one")
        self.assertFalse(item.photo)
        self.assertEqual(item.barcode, "6291000000001")

        with self.assertRaises(WorkflowPermissionDenied):
            self.read([ai_item()], actor=self.roche_user)
        with self.assertRaisesMessage(InvalidTransaction, "Choose up to 40 pages"):
            self.read([ai_item()], data=catalogue_pdf(41))
        with self.assertRaisesMessage(InvalidTransaction, "between 1 and 2"):
            self.read([ai_item()], data=catalogue_pdf(2), pages="2-3")
        with self.assertRaisesMessage(InvalidTransaction, "Upload a PDF catalogue"):
            self.read([ai_item()], name="list.docx", data=b"x")
        with self.assertRaisesMessage(InvalidTransaction, "did not find any products"):
            self.read([])
        with patch("foundation.catalogue_reader.is_available", return_value=False), \
                self.assertRaises(DocumentReaderUnavailable):
            self.read([ai_item()])

    def test_excel_price_list_is_sent_as_text_and_pages_can_be_chosen(self):
        from openpyxl import Workbook

        book = Workbook()
        book.active.append(["Code", "Description", "Price EUR"])
        book.active.append(["LM-400", "TEST ONLY Side table", 320])
        buffer = io.BytesIO()
        book.save(buffer)
        result, ask = self.read([ai_item(page=0, has_photo=True)], name="prices.xlsx", data=buffer.getvalue())
        block = ask.call_args.args[0][0]
        self.assertEqual(block["type"], "text")
        self.assertIn("LM-400 | TEST ONLY Side table | 320", block["text"])
        self.assertEqual(result["drafts"][0]["photo_url"], "", "No page to cut a photo from")
        result, ask = self.read([ai_item(page=1)], data=catalogue_pdf(3), pages="2-3")
        self.assertEqual(result["import"]["pages_read"], "2-3")

    def test_api_upload_photo_create_and_discard(self):
        client = APIClient()
        client.force_login(self.admin)
        with patch("foundation.catalogue_reader.ask", return_value={"summary": "", "items": [ai_item(), ai_item(brand_item_code="X")]}):
            response = client.post(f"/api/inventory/catalogue-imports/?unit={self.unit.pk}", {
                "file": SimpleUploadedFile("c.pdf", catalogue_pdf()), "supplier_id": self.supplier.pk, "prefix": "tvf",
            }, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        import_id, photo = response.data["import"]["id"], response.data["drafts"][0]["photo_url"]
        image = client.get(f"{photo}?unit={self.unit.pk}")
        self.assertEqual((image.status_code, image["Content-Type"]), (200, "image/jpeg"))
        self.assertEqual(client.get(f"{photo[:-8]}00000000.jpg?unit={self.unit.pk}").status_code, 404)
        listing = client.get(f"/api/inventory/catalogue-imports/?unit={self.unit.pk}")
        self.assertEqual([row["id"] for row in listing.data["imports"]], [import_id])
        created = client.post(f"/api/inventory/catalogue-imports/{import_id}/create/?unit={self.unit.pk}",
                              {"items": [{"index": 1, "brand_name": "TEST ONLY Lumen"}]}, format="json")
        self.assertEqual(created.status_code, 200, created.data)
        self.assertEqual(client.get(f"{photo}?unit={self.unit.pk}").status_code, 404, "Finished uploads no longer serve drafts")
        detail = client.get(f"/api/inventory/catalogue-imports/{import_id}/?unit={self.unit.pk}")
        self.assertEqual([row["item_code"] for row in detail.data["created"]], ["TVF-009"])

        self.read([ai_item()])
        other = CatalogueImport.objects.filter(status="review").get()
        discard_import(actor=self.admin, business_unit=self.unit, import_id=other.pk)
        self.assertEqual(CatalogueImport.objects.get(pk=other.pk).status, CatalogueImport.Status.DISCARDED)
        outsider = APIClient()
        outsider.force_login(self.roche_user)
        self.assertIn(outsider.get(f"/api/inventory/catalogue-imports/{import_id}/?unit={self.unit.pk}").status_code, (403, 404))
