"""Owner-approved nonstaff quotation choices and public bilingual snapshots."""
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    ApprovedSellingPrice, BusinessUnitRoleAssignment, Item, SalesQuote,
    SyntheticCounterparty, UserActionGrant, Variant,
)
from .purchasing_services import configure_selling_price
from .test_purchasing_workflows import PurchasingFixtureMixin
from .test_sensitive_surfaces import COST_FIELDS


class QuotationChoicesTests(PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for variant, value in ((cls.variant, "19.99"), (cls.alternate_variant, "10.05")):
            configure_selling_price(actor=cls.admin, business_unit=cls.unit, variant=variant, amount=Decimal(value))
        item = cls.variant.item
        item.name_en = "TEST ONLY chair"
        item.name_ar = "كرسي تجريبي"
        item.category_ar = "فئة تجريبية"
        item.description_ar = "وصف تجريبي"
        item.image_path = "/assets/test-only-chair.png"
        item.save()
        cls.customer.name_ar = "عميل تجريبي"
        cls.customer.contact_en = "TEST ONLY contact"
        cls.customer.contact_ar = "جهة اتصال تجريبية"
        cls.customer.save()

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.sales)
        self.csrf = self.client.get("/api/auth/csrf/").data["csrfToken"]
        self.assertFalse(self.sales.is_staff)

    def facts(self):
        return {
            m._meta.label: list(m.objects.order_by("pk").values())
            for m in apps.get_app_config("foundation").get_models()
        }

    def choices(self, unit=None):
        return self.client.get("/api/purchasing/quotation-choices/", {
            "business_unit_id": (unit or self.unit).pk,
        })

    def payload(self, **overrides):
        return {
            "business_unit_id": self.unit.pk, "customer_id": self.customer.pk,
            "kind": "stock", "discount_percent": "5.25", "discount_reason": "TEST ONLY approved grade",
            "payment_plan": "full", "lines": [
                {"variant_id": self.variant.pk, "location_id": self.location.pk, "quantity": "1.25",
                 "specifications": {"en": "TEST ONLY linen", "ar": "نسيج تجريبي"}},
                {"variant_id": self.alternate_variant.pk, "location_id": self.location.pk, "quantity": "2.75",
                 "specifications": {}},
            ],
        } | overrides

    def post(self, payload, key="quotation-choices-create"):
        return self.client.post("/api/purchasing/quotes/", payload, format="json",
                                HTTP_IDEMPOTENCY_KEY=key, HTTP_X_CSRFTOKEN=self.csrf)

    def no_private_fields(self, value):
        if isinstance(value, dict):
            self.assertFalse(set(value) & COST_FIELDS)
            for child in value.values():
                self.no_private_fields(child)
        elif isinstance(value, list):
            for child in value:
                self.no_private_fields(child)

    def test_nonstaff_choices_are_minimal_authorized_existing_customers_prices_and_quantities_without_writes(self):
        self.make_receipt()
        before = self.facts()
        response = self.choices()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(set(response.data), {
            "business_unit", "current_prices", "current_price_count", "customers", "customer_count", "role_assignments",
            "discount_ceiling",
            "work_calendar", "test_only_label",
        })
        self.assertEqual(response.data["discount_ceiling"], "10.00")
        self.assertTrue(all(row["user_id"] == self.sales.pk for row in response.data["role_assignments"]))
        self.assertEqual([row["counterparty_id"] for row in response.data["customers"]], [self.customer.pk])
        row = next(row for row in response.data["current_prices"] if row["variant_id"] == self.variant.pk)
        self.assertEqual(row["stock"], [{"location_id": self.location.pk, "quantity": "10.00"}])
        self.assertEqual(set(row), {"id", "variant_id", "sku", "amount", "currency", "revision", "approved_by", "item", "stock"})
        self.assertIsNone(row["approved_by"])
        self.no_private_fields(response.data)
        self.assertNotIn(self.supplier.code, str(response.data))
        self.assertEqual(self.facts(), before)

    def test_quantity_requires_separate_read_stock_and_explicit_location_membership(self):
        self.make_receipt()
        UserActionGrant.objects.filter(user=self.sales, business_unit=self.unit, action="read_stock").update(
            revoked_at=timezone.now(),
        )
        response = self.choices()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(row["stock"] == [] for row in response.data["current_prices"]))
        UserActionGrant.objects.filter(user=self.sales, business_unit=self.unit, action="read_stock").update(revoked_at=None)
        self.location.business_units.remove(self.unit)
        response = self.choices()
        self.assertTrue(all(row["stock"] == [] for row in response.data["current_prices"]))

    def test_choices_deny_other_bu_revoked_grant_role_and_anonymous(self):
        self.assertEqual(self.choices(self.roche).status_code, 404)
        self.sales_role.revoked_at = timezone.now()
        self.sales_role.save()
        self.assertEqual(self.choices().status_code, 403)
        self.sales_role.revoked_at = None
        self.sales_role.save()
        self.sales_grant.revoked_at = timezone.now()
        self.sales_grant.save()
        self.assertEqual(self.choices().status_code, 404)
        self.client.logout()
        self.assertIn(self.choices().status_code, (401, 403))

    def test_nonstaff_multiline_document_snapshots_bilingual_content_exact_amounts_and_retry(self):
        payload = self.payload(presentation={
            "valid_until": timezone.localdate().isoformat(),
            "payment_terms_en": "TEST ONLY display text; no gate override",
            "payment_terms_ar": "نص تجريبي", "delivery_time_en": "",
            "delivery_time_ar": "", "department_en": "TEST ONLY retail",
            "department_ar": "قسم تجريبي",
        })
        response = self.post(payload)
        self.assertEqual(response.status_code, 201, response.data)
        quote = SalesQuote.objects.get(pk=response.data["quote_id"])
        self.assertEqual(quote.total_quote_value, Decimal("49.86"))
        before = self.facts()
        retry = self.post(payload)
        self.assertEqual(retry.data, response.data)
        self.assertEqual(before, self.facts())
        document = self.client.get(f"/api/purchasing/quotes/{quote.pk}/",
                                   {"business_unit_id": self.unit.pk}).data["presentation_document"]
        self.assertEqual(document["client"]["contact_ar"], "جهة اتصال تجريبية")
        self.assertEqual(document["lines"][0]["name_ar"], "كرسي تجريبي")
        self.assertEqual(document["lines"][0]["quantity"], "1.25")
        self.assertEqual(document["total_discount"], "2.77")
        self.assertEqual(document["total_quote_value"], "49.86")
        self.assertEqual(document["terms_status"], "pending_owner_text")
        self.assertEqual(quote.payment_plan, "full")
        self.no_private_fields(document)
        item = self.variant.item
        item.name_ar = "اسم جديد تجريبي"
        item.save()
        quote.refresh_from_db()
        self.assertEqual(quote.presentation_document["lines"][0]["name_ar"], "كرسي تجريبي")
        quote.presentation_document = {}
        with self.assertRaises(ValidationError):
            quote.save()
        changed = self.post(self.payload(presentation={"payment_terms_en": "TEST ONLY changed"}))
        self.assertEqual(changed.status_code, 409)

    def test_each_nonstaff_grade_can_apply_only_its_existing_discount_authority(self):
        for index, (role, cap) in enumerate([
            ("salesperson", "10.00"), ("manager", "20.00"), ("commercial_gm", "30.00"), ("ceo", "50.00"),
        ]):
            self.sales_role.role = role
            self.sales_role.save()
            result = self.post(self.payload(discount_percent=cap), f"grade-ok-{index}")
            self.assertEqual(result.status_code, 201, result.data)
            before = self.facts()
            denied = self.post(self.payload(discount_percent=str(Decimal(cap) + Decimal("0.01"))), f"grade-denied-{index}")
            self.assertIn(denied.status_code, (400, 403))
            self.assertEqual(before, self.facts())

    def test_price_and_item_overrides_supplier_customer_and_invalid_later_line_fail_atomically(self):
        for index, payload in enumerate([
            self.payload(selling_price="0.01"),
            self.payload(lines=[self.payload()["lines"][0] | {"unit_price": "0.01"}]),
            self.payload(customer_id=self.supplier.pk),
            self.payload(lines=[self.payload()["lines"][0], self.payload()["lines"][1] | {"quantity": "1.001"}]),
            self.payload(lines=[self.payload()["lines"][0], self.payload()["lines"][1] | {"variant_id": 999999}]),
        ]):
            before = self.facts()
            response = self.post(payload, f"atomic-reject-{index}")
            self.assertIn(response.status_code, (400, 404))
            self.assertEqual(self.facts(), before)

    def test_sales_cannot_create_items_change_prices_or_read_staff_configuration_directory_history_or_exports(self):
        before = self.facts()
        for path, payload in [
            ("/api/items/", {"description": "TEST ONLY forbidden item"}),
            ("/api/variants/", {"item_id": self.variant.item_id}),
            ("/api/purchasing/quotation-choices/", {"name": "TEST ONLY forbidden"}),
            (f"/api/governance/purchasing/{self.unit.pk}/selling-prices/", {"variant_id": self.variant.pk, "amount": "0.01"}),
        ]:
            result = self.client.post(path, payload, format="json", HTTP_X_CSRFTOKEN=self.csrf)
            self.assertIn(result.status_code, (403, 404, 405))
        for path in [
            f"/api/governance/purchasing/{self.unit.pk}/",
            "/api/governance/purchasing/counterparties/",
            f"/api/reports/purchase_history/receipts/?unit={self.unit.pk}",
            f"/api/reports/gross_margin/?unit={self.unit.pk}",
        ]:
            self.assertIn(self.client.get(path).status_code, (403, 404))
        for format_ in ("xlsx", "docx"):
            response = self.client.post("/api/reports/sales/export/",
                                        {"unit": self.unit.pk, "format": format_}, format="json",
                                        HTTP_X_CSRFTOKEN=self.csrf)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.facts(), before)

    def test_validity_display_fields_and_image_safety_have_no_invented_defaults(self):
        result = self.post(self.payload())
        quote = SalesQuote.objects.get(pk=result.data["quote_id"])
        document = quote.presentation_document
        for field in ("valid_until", "payment_terms_en", "payment_terms_ar", "delivery_time_en", "delivery_time_ar"):
            self.assertEqual(document[field], "")
        for display in ({"valid_until": "2000-01-01"}, {"valid_until": "tomorrow"},
                        {"terms_and_conditions": "unapproved"}, {"payment_plan": "free"}):
            before = self.facts()
            self.assertEqual(self.post(self.payload(presentation=display), str(display)).status_code, 400)
            self.assertEqual(self.facts(), before)
        item = self.variant.item
        for path in ("https://example.invalid/image.png", "//evil.invalid/a.png", "/assets/../private.png", "data:image/svg+xml,test"):
            item.image_path = path
            item.save()
            self.assertEqual(next(row for row in self.choices().data["current_prices"]
                                  if row["variant_id"] == self.variant.pk)["item"]["image_path"], "")

    def test_complete_public_document_uses_configured_identity_and_explicit_customer_display_fields(self):
        headers = {self.unit.legal_entity.code: {
            "name_ar": "شركة تجريبية", "contact_en": "TEST ONLY company contact",
            "contact_ar": "اتصال تجريبي", "private_setting": "MUST NOT BE COPIED",
        }}
        display = {
            "contact_person_en": "TEST ONLY contact person", "contact_person_ar": "شخص تجريبي",
            "project_en": "TEST ONLY project", "project_ar": "مشروع تجريبي",
            "address_en": "TEST ONLY address", "address_ar": "عنوان تجريبي",
            "phone": "TEST ONLY phone", "mobile": "TEST ONLY mobile", "email": "synthetic@example.invalid",
            "notes_en": "TEST ONLY display notes", "notes_ar": "ملاحظات تجريبية",
            "line_units": [{"en": "TEST ONLY unit", "ar": "وحدة تجريبية"}, {"en": "", "ar": ""}],
        }
        with override_settings(QUOTATION_COMPANY_HEADERS=headers):
            result = self.post(self.payload(presentation=display))
        self.assertEqual(result.status_code, 201, result.data)
        quote = SalesQuote.objects.get(pk=result.data["quote_id"])
        document = quote.presentation_document
        self.assertEqual(document["company"]["name_en"], "Tivoli Group L.L.C")
        self.assertEqual(document["company"]["trademark_en"], "Tivoli")
        self.assertEqual(document["company"]["name_ar"], "")
        self.assertEqual(document["company"]["identity_status"], "owner_confirmed_registered_name")
        self.assertNotIn("private_setting", str(document))
        self.assertNotIn("MUST NOT BE COPIED", str(document))
        self.assertEqual(document["client"]["account"], self.customer.code)
        for field in ("contact_person_en", "contact_person_ar", "project_en", "project_ar",
                      "address_en", "address_ar", "phone", "mobile", "email"):
            self.assertEqual(document["client"][field], display[field])
        self.assertEqual(document["notes_ar"], display["notes_ar"])
        self.assertEqual(document["lines"][0]["unit_en"], "TEST ONLY unit")
        self.assertEqual(document["lines"][1]["unit_ar"], "")
        self.assertEqual(document["lines"][0]["variant_sku"], self.variant.sku)
        self.assertEqual(document["lines"][0]["specifications"], self.payload()["lines"][0]["specifications"])
        self.assertEqual(document["total_quote_value"], "49.86")
        self.assertEqual(document["terms_status"], "pending_owner_text")
        self.no_private_fields(document)
        # Neither company configuration nor customer-master edits rewrite the snapshot.
        self.customer.name = "TEST ONLY later customer"
        self.customer.save()
        quote.refresh_from_db()
        self.assertEqual(quote.presentation_document, document)

    def test_display_units_are_not_conversions_and_exact_replay_preserves_original_document(self):
        payload = self.payload(presentation={
            "line_units": [{"en": "explicit display only", "ar": ""}, {"en": "", "ar": ""}],
            "notes_en": "TEST ONLY not an accounting instruction",
        })
        result = self.post(payload)
        self.assertEqual(result.status_code, 201, result.data)
        quote = SalesQuote.objects.get(pk=result.data["quote_id"])
        self.assertEqual(list(quote.lines.values_list("quantity", flat=True)), [Decimal("1.25"), Decimal("2.75")])
        before = self.facts()
        with override_settings(QUOTATION_COMPANY_HEADERS={self.unit.legal_entity.code: {"contact_en": "changed"}}):
            replay = self.post(payload)
        self.assertEqual(replay.data, result.data)
        self.assertEqual(self.facts(), before)
        changed = self.post(self.payload(presentation={"notes_en": "changed"}))
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(self.facts(), before)

    def test_invalid_metadata_units_email_and_company_or_legal_overrides_roll_back_without_facts(self):
        cases = [
            {"email": "not an email"}, {"line_units": "pcs"},
            {"line_units": [{"en": "one pair only"}]},
            {"line_units": [{"en": "x" * 81}, {}]},
            {"line_units": [{"en": "x", "conversion": "2"}, {}]},
            {"line_units": [{"en": 12}, {}]},
            {"company": {"name_en": "unapproved override"}},
            {"terms_and_conditions": "unapproved legacy text"},
            {"validity_days": 30}, {"notes_en": "x" * 1001},
        ]
        for index, display in enumerate(cases):
            with self.subTest(display=display):
                before = self.facts()
                result = self.post(self.payload(presentation=display), f"invalid-doc-{index}")
                self.assertEqual(result.status_code, 400, result.data)
                self.assertEqual(before, self.facts())

    def test_missing_company_content_and_units_do_not_apply_validity_or_commercial_defaults(self):
        result = self.post(self.payload())
        quote = SalesQuote.objects.get(pk=result.data["quote_id"])
        document = quote.presentation_document
        self.assertEqual(document["company"]["name_en"], "Tivoli Group L.L.C")
        self.assertEqual(document["company"]["contact_en"], "")
        self.assertEqual(document["company"]["name_ar"], "")
        self.assertTrue(all(row["unit_en"] == row["unit_ar"] == "" for row in document["lines"]))
        self.assertEqual(document["valid_until"], "")
        self.assertEqual(document["payment_terms_en"], "")
        self.assertEqual(document["notes_en"], "")
        self.assertEqual(quote.payment_plan, "full")
        self.assertEqual(document["terms_status"], "pending_owner_text")

    def test_long_multiline_document_preserves_line_order_and_exact_totals_without_operational_postings(self):
        lines = self.payload()["lines"] * 10
        units = [{"en": f"TEST ONLY unit {i + 1}", "ar": ""} for i in range(20)]
        before = self.facts()
        result = self.post(self.payload(lines=lines, presentation={"line_units": units}))
        self.assertEqual(result.status_code, 201, result.data)
        quote = SalesQuote.objects.get(pk=result.data["quote_id"])
        self.assertEqual(len(quote.presentation_document["lines"]), 20)
        self.assertEqual(quote.total_quote_value, Decimal("498.60"))
        self.assertEqual(quote.presentation_document["total_quote_value"], "498.60")
        self.assertEqual([row["unit_en"] for row in quote.presentation_document["lines"]],
                         [row["en"] for row in units])
        after = self.facts()
        for model, rows in before.items():
            if any(name in model.lower() for name in ("stockbalance", "stockmovement", "reservation",
                                                      "journal", "payment", "delivery")):
                self.assertEqual(after[model], rows, model)

    def test_complete_document_reads_recheck_unit_and_revoked_access_without_export_entitlement(self):
        result = self.post(self.payload(presentation={"notes_en": "TEST ONLY private quote context"}))
        path = f"/api/purchasing/quotes/{result.data['quote_id']}/"
        before = self.facts()
        self.assertEqual(self.client.get(path, {"business_unit_id": self.unit.pk}).status_code, 200)
        self.assertIn(self.client.get(path, {"business_unit_id": self.roche.pk}).status_code, (403, 404))
        self.assertEqual(self.facts(), before)
        self.sales_grant.revoked_at = timezone.now()
        self.sales_grant.save()
        self.assertIn(self.client.get(path, {"business_unit_id": self.unit.pk}).status_code, (403, 404))
        for format_ in ("xlsx", "docx"):
            response = self.client.post("/api/reports/sales/export/", {"unit": self.unit.pk, "format": format_},
                                        format="json", HTTP_X_CSRFTOKEN=self.csrf)
            self.assertEqual(response.status_code, 403)

    def test_confirmed_identity_only_applies_to_new_snapshots_and_old_replay_is_immutable(self):
        from .quotation_presentation import quotation_document

        def historical_document(**kwargs):
            document = quotation_document(**kwargs)
            document["company"] = {
                "name_en": "TEST ONLY historical identity", "name_ar": "اسم تجريبي سابق",
                "contact_en": "TEST ONLY historical contact",
                "identity_status": "configured_identity_pending_legal_name_confirmation",
            }
            return document

        with patch("foundation.quotation_presentation.quotation_document", side_effect=historical_document):
            original_response = self.post(self.payload(), "historical-identity")
        self.assertEqual(original_response.status_code, 201)
        historical = SalesQuote.objects.get(pk=original_response.data["quote_id"])
        original_document = historical.presentation_document
        entity_name = self.unit.legal_entity.name
        current = self.post(self.payload(), "confirmed-identity")
        self.assertEqual(current.status_code, 201)
        current_document = SalesQuote.objects.get(pk=current.data["quote_id"]).presentation_document
        self.assertEqual(current_document["company"]["name_en"], "Tivoli Group L.L.C")
        self.assertEqual(current_document["company"]["trademark_en"], "Tivoli")
        self.assertEqual(current_document["company"]["name_ar"], "")
        self.unit.legal_entity.refresh_from_db()
        self.assertEqual(self.unit.legal_entity.name, entity_name, "Accounting/master entities are not renamed")
        before = self.facts()
        replay = self.post(self.payload(), "historical-identity")
        self.assertEqual(replay.data, original_response.data)
        response = self.client.get(f"/api/purchasing/quotes/{historical.pk}/",
                                   {"business_unit_id": self.unit.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["presentation_document"], original_document)
        self.assertEqual(self.facts(), before)
        historical.presentation_document = {**original_document, "company": current_document["company"]}
        with self.assertRaises(ValidationError):
            historical.save()
        historical.refresh_from_db()
        self.assertEqual(historical.presentation_document, original_document)


class LargeCatalogQuotationSearchTests(PurchasingFixtureMixin, TestCase):
    """Choices return one page; the search endpoints find the rest without loading the catalog."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        brand = cls.variant.item.brand
        items = Item.objects.bulk_create([
            Item(brand=brand, item_code=f"TEST-BULK-{n:04d}", description=f"TEST ONLY bulk item {n}",
                 name_en=f"TEST ONLY Bulk sofa {n}", name_ar=f"كنبة تجريبية {n}")
            for n in range(120)
        ])
        variants = Variant.objects.bulk_create([
            Variant(item=item, sku=f"TVF-BULK-{n:04d}", description="TEST ONLY bulk variant")
            for n, item in enumerate(items)
        ])
        ApprovedSellingPrice.objects.bulk_create([
            ApprovedSellingPrice(
                business_unit=cls.unit, variant=variant, amount=Decimal("100.00") + n, currency="QAR",
                revision=1, is_current=True, approved_by=cls.admin, approved_at=timezone.now(),
            )
            for n, variant in enumerate(variants)
        ])
        cls.bulk = variants
        SyntheticCounterparty.objects.bulk_create([
            SyntheticCounterparty(party_type="customer", code=f"TEST-CUST-BULK-{n:03d}", name=f"TEST ONLY Family {n}",
                                  contact_en=f"Tel +974 5550 {n:04d}")
            for n in range(70)
        ])

    def setUp(self):
        self.client = APIClient()
        self.client.force_login(self.sales)

    def get(self, path, **params):
        return self.client.get(path, {"business_unit_id": self.unit.pk, **params})

    def test_choices_return_one_page_with_totals(self):
        data = self.get("/api/purchasing/quotation-choices/").data
        self.assertEqual(len(data["current_prices"]), 50)
        self.assertGreaterEqual(data["current_price_count"], 120)
        self.assertEqual(len(data["customers"]), 50)
        self.assertGreaterEqual(data["customer_count"], 70)

    def test_item_search_by_sku_code_or_name_and_lookup_by_id(self):
        rows = self.get("/api/purchasing/quotation-items/", search="bulk-0107").data["rows"]
        self.assertEqual([row["sku"] for row in rows], ["TVF-BULK-0107"])
        self.assertEqual(rows[0]["amount"], "207.00")
        self.assertIn("stock", rows[0])
        self.assertEqual(len(self.get("/api/purchasing/quotation-items/", search="sofa 11").data["rows"]), 11)
        wide = self.get("/api/purchasing/quotation-items/", search="TVF-BULK").data
        self.assertEqual((len(wide["rows"]), wide["more"]), (50, True))
        ids = f"{self.bulk[3].pk},{self.bulk[99].pk}"
        looked_up = self.get("/api/purchasing/quotation-items/", ids=ids).data["rows"]
        self.assertEqual({row["variant_id"] for row in looked_up}, {self.bulk[3].pk, self.bulk[99].pk})
        too_many = ",".join(str(variant.pk) for variant in self.bulk[:41])
        self.assertEqual(self.get("/api/purchasing/quotation-items/", ids=too_many).status_code, 400)
        self.assertEqual(self.get("/api/purchasing/quotation-items/", search="x" * 81).status_code, 400)

    def test_customer_search_and_access(self):
        rows = self.get("/api/purchasing/quotation-customers/", search="family 42").data["rows"]
        self.assertEqual([row["code"] for row in rows], ["TEST-CUST-BULK-042"])
        self.assertNotIn("supplier", {row["party_type"] for row in rows})
        self.assertEqual(self.client.get("/api/purchasing/quotation-items/", {"business_unit_id": self.roche.pk}).status_code, 404)
        self.client.logout()
        self.assertIn(self.get("/api/purchasing/quotation-customers/").status_code, (401, 403))

    def test_review_checklist_item_and_customer_search(self):
        """The five checks asked for in review, through the API a salesperson uses."""
        item = self.bulk[107].item
        for term, field in (("TVF-BULK-0107", "sku"), (item.item_code, "item code"), ("Bulk sofa 107", "English name"),
                            ("كنبة تجريبية 107", "Arabic name")):
            rows = self.get("/api/purchasing/quotation-items/", search=term).data["rows"]
            self.assertEqual([row["sku"] for row in rows], ["TVF-BULK-0107"], field)
        first_page = {row["code"] for row in self.get("/api/purchasing/quotation-choices/").data["customers"]}
        self.assertNotIn("TEST-CUST-BULK-065", first_page, "The customer is beyond the first 50")
        for term, field in (("TEST-CUST-BULK-065", "code"), ("Family 65", "name"), ("5550 0065", "phone")):
            rows = self.get("/api/purchasing/quotation-customers/", search=term).data["rows"]
            self.assertEqual([row["code"] for row in rows], ["TEST-CUST-BULK-065"], field)
        # An unauthorized unit returns nothing at all: no prices, no customers, no lookups by id.
        for path, params in (
            ("/api/purchasing/quotation-items/", {"search": "BULK"}),
            ("/api/purchasing/quotation-items/", {"ids": str(self.bulk[3].pk)}),
            ("/api/purchasing/quotation-customers/", {"search": "Family"}),
            ("/api/purchasing/quotation-choices/", {}),
        ):
            response = self.client.get(path, {"business_unit_id": self.roche.pk, **params})
            self.assertEqual(response.status_code, 404, path)
            self.assertNotIn("rows", response.data)

    def test_item_search_uses_the_trigram_index(self):
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SET enable_seqscan = off")
            cursor.execute(
                "EXPLAIN SELECT id FROM foundation_variant WHERE UPPER(sku::text) LIKE UPPER(%s)", ["%BULK-01%"],
            )
            plan = "\n".join(row[0] for row in cursor.fetchall())
            cursor.execute("SET enable_seqscan = on")
        self.assertIn("variant_sku_trgm", plan)
