"""Go-live imports of customers and suppliers, approved by a second person; synthetic only."""

import csv
from decimal import Decimal
import datetime
import io
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from openpyxl import Workbook, load_workbook
from rest_framework.test import APIClient

from .go_live_imports import decide, imports_overview, preview, submit, template
from .models import ImportBatch, SupplierKyc, SyntheticCounterparty, UserActionGrant, next_customer_number
from .purchasing_services import WorkflowPermissionDenied
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action
CUSTOMER_HEAD = ["Name*", "Name in Arabic", "Title", "Customer type", "QID", "Phone or other contact", "Contact in Arabic",
                 "Registered for", "Client number", "Project number"]
SUPPLIER_HEAD = ["Supplier code", "Legal name*", "CR number*", "CR expiry", "VAT registered", "VAT number", "Address",
                 "Signatory name*", "Signatory title", "Email", "Phone"]


def xlsx(rows, sheet="Data"):
    book = Workbook()
    book.active.title = sheet
    for row in rows:
        book.active.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def text_csv(rows):
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue().encode("utf-8-sig")


@override_settings(APP_ENV="demo")
class GoLiveImportTests(PurchasingFixtureMixin, TestCase):
    """Admin prepares; demo_roche approves; demo_sales has neither."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.get_or_create(user=cls.admin, business_unit=cls.unit, action=A.IMPORT_DATA)
        cls.admin.is_superuser = True  # Setup lock (Nov 2026): only the Master Admin brings files in.
        cls.admin.save(update_fields=["is_superuser"])
        UserActionGrant.objects.get_or_create(user=cls.roche_user, business_unit=cls.unit, action=A.APPROVE_IMPORT)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)

    def customers(self, *rows):
        return xlsx([CUSTOMER_HEAD, *rows])

    def send(self, kind, content, name=None):
        return submit(actor=self.admin, kind=kind, file_name=name or f"TEST ONLY {kind}.xlsx", content=content)

    def test_template_has_the_headings_an_example_and_the_guide(self):
        name, content = template(actor=self.admin, kind="customers")
        self.assertEqual(name, "tivoli-customers-import-template.xlsx")
        book = load_workbook(io.BytesIO(content))
        self.assertEqual(book.sheetnames, ["Data", "How to fill it"])
        self.assertEqual([cell.value for cell in book["Data"][1]], CUSTOMER_HEAD)
        self.assertEqual(book["How to fill it"]["A2"].value, "Name")
        # The template's own example row passes the checks.
        self.assertEqual(preview(actor=self.admin, kind="customers", file_name=name, content=content)["bad"], 0)
        name, content = template(actor=self.admin, kind="suppliers")
        self.assertEqual(preview(actor=self.admin, kind="suppliers", file_name=name, content=content)["bad"], 0)

    def test_preview_lists_each_rows_problems_and_saves_nothing(self):
        SyntheticCounterparty.objects.create(party_type="customer", code="TEST-CUST-C-000777", name="TEST ONLY Existing",
                                             qid="28763400999", client_number="C-000777")
        content = self.customers(
            ["Ahmed Al-Kuwari", "", "Mr", "", "28763400123", "", "", "Retail", "C-4512", ""],
            ["", "", "Captain", "", "123", "", "", "", "", ""],
            ["Mariam Saeed", "", "", "", "", "", "", "Projects", "", ""],
            ["Noor Hamad", "", "", "", "28763400123", "", "", "both", "C-000777", "P-12"],
            ["Existing Again", "", "", "", "28763400999", "", "", "Retail", "X12", ""],
        )
        result = preview(actor=self.admin, kind="customers", file_name="TEST ONLY old.xlsx", content=content)
        self.assertEqual((result["rows"], result["good"], result["bad"]), (5, 1, 4))
        problems = {row["row"]: row["problems"] for row in result["problems"]}
        self.assertEqual(problems[3], ["Name is missing.", "Title “Captain” is not on the list.",
                                       "QID must be 11 digits starting with 2 or 3."])
        self.assertEqual(problems[4], ["Give a QID, or a phone number or other contact when the customer declined the QID."])
        self.assertEqual(problems[5], ["QID 28763400123 is also on row 2.", "Client number C-000777 is already in use."])
        self.assertEqual(problems[6], ["QID 28763400999 is already registered.", "Client number must look like C-000123."])
        self.assertEqual(result["sample"][0]["client_number"], "C-004512")
        self.assertEqual(ImportBatch.objects.count(), 0)
        with self.assertRaisesMessage(InvalidTransaction, "4 of 5 rows have problems"):
            self.send("customers", content)

    def test_file_shape_problems_are_explained(self):
        cases = [
            (b"", "x.xlsx", "could not be read as an Excel workbook"),
            (b"a", "x.pdf", "as an .xlsx or .csv file"),
            (xlsx([["Customer", "Phone"], ["A", "1"]]), "x.xlsx", "Missing: Name"),
            (xlsx([CUSTOMER_HEAD]), "x.xlsx", "headings but no rows"),
            ("Name\nأحمد\n".encode("utf-16"), "x.csv", "Save the CSV file as UTF-8"),
        ]
        for content, name, message in cases:
            with self.subTest(message), self.assertRaisesMessage(InvalidTransaction, message):
                preview(actor=self.admin, kind="customers", file_name=name, content=content)
        with self.assertRaisesMessage(InvalidTransaction, "at most 5000"):
            preview(actor=self.admin, kind="customers", file_name="x.csv",
                    content=text_csv([["Name"], *[[f"P{index}"] for index in range(5001)]]))

    def test_customers_keep_old_numbers_and_new_numbers_carry_on_after_them(self):
        content = self.customers(
            ["Ahmed Al-Kuwari", "أحمد", "Mr", "", "28763400123", "", "", "Retail", "C-004512", ""],
            ["Mariam Saeed", "", "Ms", "company", "", "+974 5555 0101", "", "both", "", "P-000045"],
            ["Noor Hamad", "", "", "", "", "TEST ONLY phone", "", "Retail", "", ""],
        )
        batch = self.send("customers", content)
        self.assertEqual((batch["status"], batch["row_count"], batch["can_withdraw"], batch["can_approve"]),
                         ("waiting", 3, True, False))
        self.assertFalse(SyntheticCounterparty.objects.filter(name="TEST ONLY Ahmed Al-Kuwari").exists())
        done = decide(actor=self.roche_user, batch_id=batch["id"], action="approve")
        self.assertEqual((done["status"], done["created"], done["decided_by"]), ("imported", 3, "Roche Demo"))
        ahmed, mariam, noor = SyntheticCounterparty.objects.filter(pk__in=ImportBatch.objects.get().created_ids).order_by("pk")
        self.assertEqual((ahmed.client_number, ahmed.qid, ahmed.title, ahmed.name_ar, ahmed.code),
                         ("C-004512", "28763400123", "mr", "أحمد", "TEST-CUST-C-004512"))
        self.assertEqual((mariam.client_number, mariam.project_number, mariam.qid_declined, mariam.customer_type),
                         ("C-004513", "P-000045", True, "company"))
        self.assertEqual((noor.client_number, noor.project_number), ("C-004514", None))
        self.assertEqual(next_customer_number("client"), "C-004515")
        self.assertEqual(next_customer_number("project"), "P-000046")

    def test_suppliers_come_with_an_approved_kyc_file(self):
        content = xlsx([SUPPLIER_HEAD,
                        ["SUP-0042", "Gulf Timber Trading WLL", "45123", "30/06/2027", "Yes", "300012345600003", "TEST ONLY street",
                         "Rashid Mahmoud", "GM", "orders@example.com", "+974 4444 0202"],
                        ["", "Doha Fabrics", "cr-88/1", "", "", "", "", "Salma Ali", "", "", ""]])
        batch = self.send("suppliers", content)
        decide(actor=self.roche_user, batch_id=batch["id"], action="approve")
        first, second = SupplierKyc.objects.select_related("supplier").order_by("pk")
        self.assertEqual((first.supplier.code, first.supplier.name, first.status, first.cr_expiry.isoformat(), first.vat_number),
                         ("TEST-SUP-SUP-0042", "TEST ONLY Gulf Timber Trading WLL", "approved", "2027-06-30", "300012345600003"))
        self.assertEqual((first.opened_by, first.decided_by), (self.admin, self.roche_user))
        self.assertEqual(first.documents_due, timezone.localdate() + datetime.timedelta(days=90))
        self.assertEqual(list(first.events.values_list("event", flat=True)), ["imported", "approved"])
        self.assertEqual((second.supplier.code, second.cr_number, second.vat_registered), ("TEST-SUP-CR-88-1", "CR-88/1", False))
        # The same file again is refused, because those suppliers are now on file.
        result = preview(actor=self.admin, kind="suppliers", file_name="again.xlsx", content=content)
        self.assertEqual(result["bad"], 2)
        self.assertIn("A supplier with CR number 45123 is already on file.", result["problems"][0]["problems"])

    def test_csv_works_too(self):
        content = text_csv([CUSTOMER_HEAD, ["أحمد الكواري", "", "", "", "28763400123", "", "", "", "", ""]])
        result = preview(actor=self.admin, kind="customers", file_name="TEST ONLY old.csv", content=content)
        self.assertEqual((result["good"], result["sample"][0]["name"]), (1, "TEST ONLY أحمد الكواري"))

    def test_second_person_rules_rejection_and_withdrawal(self):
        content = self.customers(["Ahmed", "", "", "", "28763400123", "", "", "", "", ""])
        UserActionGrant.objects.create(user=self.admin, business_unit=self.unit, action=A.APPROVE_IMPORT)
        batch = self.send("customers", content)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "someone else must approve it"):
            decide(actor=self.admin, batch_id=batch["id"], action="approve")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only the person who sent the file"):
            decide(actor=self.roche_user, batch_id=batch["id"], action="withdraw")
        with self.assertRaisesMessage(InvalidTransaction, "Give a reason"):
            decide(actor=self.roche_user, batch_id=batch["id"], action="reject")
        rejected = decide(actor=self.roche_user, batch_id=batch["id"], action="reject", note="TEST ONLY wrong unit")
        self.assertEqual((rejected["status"], rejected["decision_note"]), ("rejected", "TEST ONLY wrong unit"))
        with self.assertRaisesMessage(InvalidTransaction, "already decided"):
            decide(actor=self.roche_user, batch_id=batch["id"], action="approve")
        again = self.send("customers", content)
        self.assertEqual(decide(actor=self.admin, batch_id=again["id"], action="withdraw")["status"], "withdrawn")
        self.assertEqual(SyntheticCounterparty.objects.filter(qid="28763400123").count(), 0)

    def test_rows_are_checked_again_at_approval(self):
        batch = self.send("customers", self.customers(["Ahmed", "", "", "", "28763400123", "", "", "", "", ""]))
        SyntheticCounterparty.objects.create(party_type="customer", code="TEST-CUST-C-000900", name="TEST ONLY Meanwhile",
                                             qid="28763400123", client_number="C-000900")
        with self.assertRaisesMessage(InvalidTransaction, "1 rows no longer pass the checks, for example row 1: QID 28763400123 is already registered."):
            decide(actor=self.roche_user, batch_id=batch["id"], action="approve")
        self.assertEqual(ImportBatch.objects.get().status, "waiting")

    def test_access(self):
        for call in (lambda: imports_overview(actor=self.sales), lambda: template(actor=self.sales, kind="customers")):
            with self.assertRaises(WorkflowPermissionDenied):
                call()
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only the Master Admin can send a go-live file"):
            submit(actor=self.roche_user, kind="customers", file_name="x.xlsx", content=b"")
        overview = imports_overview(actor=self.roche_user)
        self.assertEqual((overview["can_prepare"], overview["can_approve"]), (False, True))
        self.assertEqual([kind["kind"] for kind in overview["kinds"]], ["customers", "suppliers", "items", "opening_stock", "trial_balance",
                                                                          "customer_balances", "supplier_balances", "fixed_assets", "staff_loans"])

    def test_endpoints(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.get("/api/governance/imports/template/suppliers/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('filename="tivoli-suppliers-import-template.xlsx"', response["Content-Disposition"])
        content = self.customers(["Ahmed", "", "", "", "28763400123", "", "", "", "", ""])
        upload = lambda: io.BytesIO(content)  # noqa: E731
        file = upload()
        file.name = "TEST ONLY old.xlsx"
        response = client.post("/api/governance/imports/preview/", {"kind": "customers", "file": file}, format="multipart")
        self.assertEqual((response.status_code, response.json()["good"]), (200, 1))
        file = upload()
        file.name = "TEST ONLY old.xlsx"
        response = client.post("/api/governance/imports/", {"kind": "customers", "file": file}, format="multipart")
        self.assertEqual(response.status_code, 201)
        batch_id = response.json()["id"]
        client.force_authenticate(self.roche_user)
        response = client.post(f"/api/governance/imports/{batch_id}/", {"action": "approve"}, format="json")
        self.assertEqual((response.status_code, response.json()["status"]), (200, "imported"))
        self.assertEqual(client.get("/api/governance/imports/").json()["recent"][0]["id"], batch_id)
        client.force_authenticate(self.sales)
        self.assertEqual(client.get("/api/governance/imports/").status_code, 403)
        self.assertFalse(client.get("/api/auth/me/").json()["can_import"])


ITEM_HEAD = ["Item code*", "Description*", "Description in Arabic", "Category", "Brand*", "Supplier code", "Brand item code",
             "Barcode", "Unit", "Finish", "Finish code (SKU)", "Material", "Width cm", "Depth cm", "Height cm",
             "Selling price QAR", "Business unit", "Supplier price", "Supplier price currency"]


def item(code, description="TEST ONLY sofa", brand="TEST ONLY Tivoli Home", **values):
    row = dict.fromkeys(ITEM_HEAD, "")
    row.update({"Item code*": code, "Description*": description, "Brand*": brand})
    names = {"supplier": "Supplier code", "barcode": "Barcode", "unit": "Unit", "finish": "Finish", "sku": "Finish code (SKU)",
             "width": "Width cm", "price": "Selling price QAR", "unit_code": "Business unit", "cost": "Supplier price",
             "currency": "Supplier price currency", "category": "Category"}
    for key, value in values.items():
        row[names[key]] = value
    return [row[heading] for heading in ITEM_HEAD]


@override_settings(APP_ENV="demo")
class GoLiveItemImportTests(PurchasingFixtureMixin, TestCase):
    """Items, with their finishes and selling prices."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.get_or_create(user=cls.admin, business_unit=cls.unit, action=A.IMPORT_DATA)
        cls.admin.is_superuser = True  # Setup lock (Nov 2026): only the Master Admin brings files in.
        cls.admin.save(update_fields=["is_superuser"])
        UserActionGrant.objects.get_or_create(user=cls.roche_user, business_unit=cls.unit, action=A.APPROVE_IMPORT)

    setUp = GoLiveImportTests.setUp
    send = GoLiveImportTests.send

    def items(self, *rows):
        return xlsx([ITEM_HEAD, *rows])

    def test_items_keep_their_codes_and_repeated_codes_add_finishes(self):
        from .models import ApprovedSellingPrice, Item, ProductBrand

        supplier = SyntheticCounterparty.objects.create(party_type="supplier", code="TEST-SUP-SUP-0042", name="TEST ONLY Gulf Timber")
        content = self.items(
            item("TVF-0142", supplier="SUP-0042", finish="Sand linen", width="220", price="8500", cost="3100", currency="eur",
                 category="Sofas"),
            item("tvf-0142", description="", brand="", finish="Grey velvet", sku="TVF-0142-GRV", price="8900", unit_code="tvl"),
            item("TVF-0200", description="TEST ONLY floor lamp", brand="TEST ONLY Lumen", unit="pcs"),
        )
        result = preview(actor=self.admin, kind="items", file_name="TEST ONLY items.xlsx", content=content)
        self.assertEqual((result["rows"], result["bad"]), (3, 0), result["problems"])
        batch = self.send("items", content)
        done = decide(actor=self.roche_user, batch_id=batch["id"], action="approve")
        self.assertEqual(done["created"], 2)
        sofa = Item.objects.get(item_code="TVF-0142")
        self.assertEqual((sofa.supplier, sofa.brand.name, sofa.width_cm, sofa.supplier_price, sofa.supplier_price_currency,
                          sofa.category_label), (supplier, "TEST ONLY Tivoli Home", Decimal("220.0"), Decimal("3100.00"), "EUR", "Sofas"))
        self.assertEqual(list(sofa.variants.order_by("pk").values_list("sku", "description")),
                         [("TVF-0142-SAN", "Sand linen"), ("TVF-0142-GRV", "Grey velvet")])
        self.assertIn(supplier, sofa.brand.suppliers.all())
        sand, grey = sofa.variants.order_by("pk")
        # Each brand has its own prices: a TVF- item is priced for Tivoli only.
        self.assertEqual(list(ApprovedSellingPrice.objects.filter(variant=sand).values_list("business_unit__code", "amount")),
                         [("TVL", Decimal("8500.00"))])
        self.assertEqual(list(ApprovedSellingPrice.objects.filter(variant=grey).values_list("business_unit__code", "amount", "approved_by")),
                         [("TVL", Decimal("8900.00"), self.roche_user.pk)])
        lamp = Item.objects.get(item_code="TVF-0200")
        self.assertEqual((lamp.unit_of_measure, lamp.variants.get().sku, lamp.variants.get().description), ("piece", "TVF-0200-STD", "Standard"))
        self.assertEqual(ProductBrand.objects.filter(name="TEST ONLY Lumen").count(), 1)

    def test_item_rows_are_checked(self):
        from .models import Item

        Item.objects.create(brand=self.variant.item.brand, item_code="TVF-OLD", description="TEST ONLY old")
        content = self.items(
            item("TVF-OLD"),
            item("TVF-1", supplier="NOPE", unit="box", price="-1", cost="10"),
            item("TVF-1", finish="Standard"),
            item("bad code!", description="", brand=""),
            item("TVF-2", unit_code="XYZ", width="wide"),
            item("TVF-3", price="100", unit_code="RBB"),
            item("OLD-3", price="100"),
        )
        result = preview(actor=self.admin, kind="items", file_name="x.xlsx", content=content)
        problems = {row["row"]: row["problems"] for row in result["problems"]}
        self.assertEqual(problems[2], ["Item code TVF-OLD is already in the ERP."])
        self.assertEqual(problems[3], ["Unit must be piece, set, pair, metre or square metre.",
                                       "No supplier has the code NOPE. Import the suppliers first or leave it blank.",
                                       "Give the supplier price's currency as three letters, for example EUR or QAR.",
                                       "Selling price must be more than zero."])
        self.assertEqual(problems[4], ["Item TVF-1 already has the finish Standard on row 3."])
        self.assertEqual(problems[5], ["Item code may use capital letters, digits and dashes only, 2 to 40 characters.",
                                       "Description is missing.", "Brand is missing."])
        self.assertEqual(problems[6], ["Width must be a number.", "There is no active business unit with the code XYZ.",
                                       "A business unit is given without a selling price."])
        self.assertEqual(problems[7], ["Item TVF-3 belongs to TVL by its code, but the business unit given is RBB."])
        self.assertEqual(problems[8], ["Give the business unit for the selling price, because the item code does not start with "
                                       "TVF-, RBB- or NAT-."])

    def test_item_template_example_passes(self):
        name, content = template(actor=self.admin, kind="items")
        result = preview(actor=self.admin, kind="items", file_name=name, content=content)
        self.assertEqual(result["bad"], 0, result["problems"])


STOCK_HEAD = ["Business unit*", "Location*", "Finish code (SKU)*", "Quantity*", "Unit cost QAR*"]
TB_HEAD = ["Business unit*", "Account*", "Debit", "Credit", "Bank or cash account", "Note"]


@override_settings(APP_ENV="demo")
class GoLiveOpeningBalanceTests(PurchasingFixtureMixin, TestCase):
    """Opening stock and the opening trial balance."""

    setUpTestData = classmethod(GoLiveItemImportTests.setUpTestData.__func__)
    setUp = GoLiveImportTests.setUp

    def day(self):
        return (timezone.localdate() - datetime.timedelta(days=1)).isoformat()

    def send(self, kind, rows, head):
        return submit(actor=self.admin, kind=kind, file_name=f"TEST ONLY {kind}.xlsx", content=xlsx([head, *rows]),
                      balances_on=self.day())

    def ledger(self, code):
        from django.db.models import Sum

        from .models import JournalLine
        totals = JournalLine.objects.filter(account__code=code, entry__business_unit=self.unit).aggregate(d=Sum("debit"), c=Sum("credit"))
        return (totals["d"] or 0) - (totals["c"] or 0)

    def test_opening_stock_becomes_stock_and_is_booked_against_opening_balances(self):
        from .models import JournalEntry, StockBalance

        StockBalance.objects.filter(variant=self.variant, location=self.location).update(on_hand_quantity=0, on_hand_value=0, reserved_quantity=0)
        before = self.ledger("TEST_INVENTORY")
        batch = self.send("opening_stock", [["tvl", self.location.code.lower(), self.variant.sku, "4", "3650.50"]], STOCK_HEAD)
        self.assertEqual(batch["balances_on"], self.day())
        decide(actor=self.roche_user, batch_id=batch["id"], action="approve")
        balance = StockBalance.objects.get(variant=self.variant, location=self.location, business_unit=self.unit)
        self.assertEqual((balance.on_hand_quantity, balance.on_hand_value), (Decimal("4.00"), Decimal("14602.00")))
        self.assertEqual(self.ledger("TEST_INVENTORY") - before, Decimal("14602.00"))
        self.assertEqual(self.ledger("TEST_OPENING_BALANCES"), Decimal("-14602.00"))
        entry = JournalEntry.objects.get(kind="opening_balance")
        self.assertEqual(entry.document_date.isoformat(), self.day())
        # The same place again is refused: it already has stock.
        result = preview(actor=self.admin, kind="opening_stock", file_name="x.xlsx", balances_on=self.day(),
                         content=xlsx([STOCK_HEAD, ["TVL", self.location.code, self.variant.sku, "1", "10"]]))
        self.assertIn(f"{self.variant.sku} at {self.location.code} already has stock in the ERP.", result["problems"][0]["problems"])

    def test_opening_stock_rows_are_checked(self):
        from .models import Location

        virtual = Location.objects.filter(kind="virtual").first()
        content = xlsx([STOCK_HEAD,
                        ["NOPE", "W41-01", "NOPE-SKU", "0", ""],
                        ["TVL", virtual.code, self.variant.sku, "1", "10"],
                        ["TVL", "NOWHERE", self.variant.sku, "x", "-1"]])
        result = preview(actor=self.admin, kind="opening_stock", file_name="x.xlsx", content=content, balances_on=self.day())
        problems = {row["row"]: row["problems"] for row in result["problems"]}
        self.assertEqual(problems[2], ["There is no active business unit with the code NOPE.", "No item has the finish code NOPE-SKU. Import the items first.",
                                       "Quantity must be more than zero.", "Unit cost is missing."])
        self.assertEqual(problems[3], [f"{virtual.code} is a virtual location and cannot take opening stock."])
        self.assertEqual(problems[4], ["There is no location with the code NOWHERE.", "Quantity must be a number.", "Unit cost cannot be negative."])
        for bad in ("", "tomorrow", (timezone.localdate() + datetime.timedelta(days=1)).isoformat()):
            with self.subTest(bad), self.assertRaises(InvalidTransaction):
                preview(actor=self.admin, kind="opening_stock", file_name="x.xlsx", content=content, balances_on=bad)

    def bank(self, name="TEST ONLY QNB current", opening_balance="0.00"):
        from .models import CashAccount

        return CashAccount.objects.create(business_unit=self.unit, name=name, kind="bank", account_type="current", created_by=self.admin,
                                          opening_balance=opening_balance, opening_date=timezone.localdate() - datetime.timedelta(days=30))

    def test_trial_balance_must_balance_and_control_lines_wait_on_opening_balances(self):
        self.bank()
        self.bank("TEST ONLY CBQ old", opening_balance="500.00")
        unbalanced = xlsx([TB_HEAD, ["TVL", "TEST_CASH", "1000", "", "test only qnb  current", ""], ["TVL", "Rent", "", "900", "", ""],
                           ["TVL", "made up", "1", "", "", ""], ["TVL", "TEST_CASH", "5", "5", "TEST ONLY QNB current", ""],
                           ["TVL", "TEST_CASH", "1", "", "", ""], ["TVL", "TEST_CASH", "1", "", "Nowhere bank", ""],
                           ["TVL", "TEST_CASH", "1", "", "TEST ONLY CBQ old", ""], ["TVL", "Rent", "", "4", "TEST ONLY QNB current", ""]])
        result = preview(actor=self.admin, kind="trial_balance", file_name="x.xlsx", content=unbalanced, balances_on=self.day())
        problems = {row["row"]: row["problems"] for row in result["problems"]}
        self.assertEqual(problems[2], ["TVL does not balance: debits 1,009.00, credits 909.00, a difference of 100.00."])
        self.assertEqual(problems[4], ["“made up” is not an ERP account. Use a code from the 'ERP accounts' sheet."])
        self.assertEqual(problems[5], ["Give either a debit or a credit, not both."])
        self.assertEqual(problems[6], ["Say which bank account or cash box this cash line is, as named under Bank and cash."])
        self.assertEqual(problems[7], ["TVL has no bank account or cash box called “Nowhere bank”. Add it under Bank and cash first."])
        self.assertEqual(problems[8], ["TEST ONLY CBQ old already has an opening balance of 500.00. "
                                       "Set it to zero, or leave this line out, so the money is not counted twice."])
        self.assertEqual(problems[9], ["A bank or cash account is only needed on TEST_CASH lines."])
        cash, inventory, grni = self.ledger("TEST_CASH"), self.ledger("TEST_INVENTORY"), self.ledger("TEST_GRNI")
        batch = self.send("trial_balance", [["TVL", "TEST_CASH", "125000", "", "TEST ONLY QNB current", "QNB 1001"], ["TVL", "inventory", "40000", "", "", ""],
                                            ["TVL", "TEST_GRNI", "", "65000", "", ""], ["TVL", "Salaries to pay through WPS", "", "100000", "", ""]],
                          TB_HEAD)
        decide(actor=self.roche_user, batch_id=batch["id"], action="approve")
        self.assertEqual(self.ledger("TEST_CASH") - cash, Decimal("125000.00"))
        self.assertEqual((self.ledger("TEST_INVENTORY") - inventory, self.ledger("TEST_GRNI") - grni), (0, 0))
        self.assertEqual(self.ledger("TEST_OPENING_BALANCES"), Decimal("-25000.00"))
        self.assertEqual(self.ledger("TEST_SALARIES_PAYABLE"), Decimal("-100000.00"))
        self.assertEqual(imports_overview(actor=self.admin)["opening_left"], [{"business_unit": "TVL", "left": "25000.00"}])
        from .models import CashLineAssignment
        assigned = CashLineAssignment.objects.get(account__name="TEST ONLY QNB current")
        self.assertEqual((assigned.journal_line.debit, assigned.journal_line.memo), (Decimal("125000.00"), "TEST ONLY — not Tivoli-approved policy: QNB 1001"))

    def test_template_lists_the_erp_accounts(self):
        self.bank("QNB current")
        name, content = template(actor=self.admin, kind="trial_balance")
        book = load_workbook(io.BytesIO(content))
        self.assertEqual(book.sheetnames, ["Data", "How to fill it", "ERP accounts"])
        codes = [row[0].value for row in book["ERP accounts"].iter_rows(min_row=2)]
        self.assertIn("TEST_CASH", codes)
        self.assertNotIn("TEST_OPENING_BALANCES", codes)
        self.assertEqual(preview(actor=self.admin, kind="trial_balance", file_name=name, content=content, balances_on=self.day())["bad"], 0)
