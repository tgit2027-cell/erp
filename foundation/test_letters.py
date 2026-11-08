"""Staff letters: asking, issuing, the no-liability wording, the PDF and who can see what; synthetic only."""

import base64
from io import BytesIO
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .letters import decide_letter, letter_pdf, letters_overview, render_letter, request_letter, set_letter_settings
from .models import StaffLetter, UserActionGrant
from .pay_records import set_pay
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import add_document, save_employee
from .test_payroll import AISHA_IBAN, valid
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction
from .master_admin_testing import by_master_admin

A = UserActionGrant.Action


def signature_png():
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (300, 100), (255, 255, 255, 0))
    ImageDraw.Draw(image).line([(10, 80), (90, 20), (170, 70), (290, 30)], fill=(20, 40, 120, 255), width=5)
    out = BytesIO()
    image.save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def pdf_text(pdf):
    import pypdfium2

    document = pypdfium2.PdfDocument(pdf)
    try:
        return "\n".join(page.get_textpage().get_text_range() for page in document)
    finally:
        document.close()


@override_settings(APP_ENV="demo")
class StaffLetterTests(PurchasingFixtureMixin, TestCase):
    """Admin is HR with salary access; demo_multi (sales) is Aisha, who asks for her own letters."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for action in (A.MANAGE_PAY, A.MANAGE_STAFF, A.VIEW_PAY):
            UserActionGrant.objects.get_or_create(user=cls.admin, business_unit=cls.unit, action=action)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.aisha = save_employee(actor=self.admin, business_unit=self.unit, data={
            "full_name": "TEST ONLY Aisha Rahman", "job_title": "Senior Sales Consultant", "department": "Sales",
            "nationality": "Filipino", "joined_on": "2021-03-14", "user_id": self.sales.pk})
        add = lambda kind, number: add_document(actor=self.admin, business_unit=self.unit, employee_id=self.aisha["id"],
                                                data={"kind": kind, "number": number, "expires_on": "2030-01-01"})
        add("qid", "28860812345")
        add("passport", "P1234567A")
        set_pay(actor=self.admin, business_unit=self.unit, data={
            "employee_id": self.aisha["id"], "effective_from": "2021-03", "basic": "6000", "housing": "2500", "transport": "500",
            "mobile": "150", "bank_name": "TEST ONLY Doha Bank", "iban": valid(AISHA_IBAN), "reason": "TEST ONLY offer letter"})

    def set_up_letters(self, **extra):
        return by_master_admin(set_letter_settings)(actor=self.admin, business_unit=self.unit, data={
            "company_name": "TEST ONLY Tivoli Group", "address": "TEST ONLY Salwa Road, Doha, Qatar",
            "signatory_name": "TEST ONLY Hana Saleh", "signatory_title": "HR Manager", "signature": signature_png(), **extra})

    def test_a_letter_is_ready_at_once_filled_in_and_signed(self):
        self.set_up_letters()
        letter = request_letter(actor=self.sales, data={"kind": "loan", "addressee": "TEST ONLY QNB, Doha", "include_iban": True})
        self.assertEqual(letter["status"], "issued")
        self.assertEqual(letter["decided_by"], "Issued automatically")
        self.assertRegex(letter["number"], rf"^HRL-{self.unit.code}-{timezone.localdate().year}-0001$")
        self.assertTrue(letter["can_download"])

        name, pdf = letter_pdf(actor=self.sales, letter_id=letter["id"])
        self.assertEqual(name, f"{letter['number']}.pdf")
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertIn(b"/Subtype /Image", pdf, "The preset signature is drawn on the letter")
        text = " ".join(pdf_text(pdf).split())
        for expected in ("TEST ONLY Tivoli Group", "TEST ONLY QNB, Doha", "TEST ONLY Aisha Rahman", "28860812345", "P1234567A",
                         "14 March 2021", "Senior Sales Consultant", "QAR 9,150.00", "Housing allowance", valid(AISHA_IBAN),
                         "TEST ONLY Hana Saleh", "HR Manager"):
            self.assertIn(expected, text)
        self.assertIn("This certificate is not a guarantee", text)
        self.assertIn("accepts no liability or obligation for any loan", text)
        self.assertEqual(StaffLetter.objects.get(pk=letter["id"]).downloads, 1)

    def test_only_credit_letters_carry_the_loan_wording_and_the_iban(self):
        self.set_up_letters()
        letter = request_letter(actor=self.sales, data={"kind": "to_whom", "include_salary": False})
        text = " ".join(pdf_text(letter_pdf(actor=self.sales, letter_id=letter["id"])[1]).split())
        self.assertIn("To whom it may concern", text)
        self.assertIn("without any financial or other responsibility", text)
        self.assertNotIn("not a guarantee", text)
        self.assertNotIn("9,150.00", text, "Salary left out when the employee asks")
        with self.assertRaisesMessage(InvalidTransaction, "only on bank letters"):
            request_letter(actor=self.sales, data={"kind": "embassy", "addressee": "TEST ONLY Embassy", "include_iban": True})
        with self.assertRaisesMessage(InvalidTransaction, "Give the embassy"):
            request_letter(actor=self.sales, data={"kind": "embassy"})

    def test_details_are_fixed_when_the_letter_is_issued(self):
        self.set_up_letters()
        letter = request_letter(actor=self.sales, data={"kind": "family"})
        set_pay(actor=self.admin, business_unit=self.unit, data={
            "employee_id": self.aisha["id"], "effective_from": timezone.localdate().replace(day=1).isoformat()[:7], "basic": "9000",
            "reason": "TEST ONLY raise"})
        text = " ".join(pdf_text(letter_pdf(actor=self.sales, letter_id=letter["id"])[1]).split())
        self.assertIn("9,150.00", text)
        self.assertIn("Ministry of Interior", text)

    def test_a_letter_waits_for_hr_when_something_is_missing(self):
        letter = request_letter(actor=self.sales, data={"kind": "polo"})
        self.assertEqual(letter["status"], "requested")
        self.assertIn("not set up the letters", letter["note"])
        self.set_up_letters()
        save_employee(actor=self.admin, business_unit=self.unit, data={
            "full_name": "TEST ONLY Ravi Kumar", "job_title": "Driver", "department": "Delivery", "nationality": "Indian",
            "joined_on": "2022-01-01", "user_id": self.roche_user.pk})
        waiting = request_letter(actor=self.roche_user, data={"kind": "to_whom"})
        self.assertEqual(waiting["status"], "requested")
        self.assertIn("no QID or passport or current pay", waiting["note"])
        overview = letters_overview(actor=self.admin, business_unit=self.unit)
        self.assertEqual({row["id"] for row in overview["to_issue"]}, {letter["id"], waiting["id"]})
        with self.assertRaisesMessage(InvalidTransaction, "Add it first"):
            decide_letter(actor=self.admin, letter_id=waiting["id"], action="issue")
        with self.assertRaisesMessage(InvalidTransaction, "Say why"):
            decide_letter(actor=self.admin, letter_id=waiting["id"], action="reject")
        rejected = decide_letter(actor=self.admin, letter_id=waiting["id"], action="reject", reason="TEST ONLY add your QID first")
        self.assertEqual(rejected["status_label"], "Not issued")
        issued = decide_letter(actor=self.admin, letter_id=letter["id"], action="issue")
        self.assertEqual(issued["status"], "issued")
        self.assertNotEqual(issued["decided_by"], "Issued automatically")

    def test_hr_can_ask_to_check_each_letter_first(self):
        self.set_up_letters(hr_checks_first=True)
        letter = request_letter(actor=self.sales, data={"kind": "bank", "addressee": "TEST ONLY Dukhan Bank"})
        self.assertEqual(letter["status"], "requested")
        self.assertEqual(letter["note"], "")
        with self.assertRaisesMessage(InvalidTransaction, "not ready to download"):
            letter_pdf(actor=self.sales, letter_id=letter["id"])
        with self.assertRaises(WorkflowPermissionDenied):
            decide_letter(actor=self.sales, letter_id=letter["id"], action="issue")
        self.assertEqual(decide_letter(actor=self.admin, letter_id=letter["id"], action="issue")["status"], "issued")

    def test_privacy_and_signature_checks(self):
        self.set_up_letters()
        letter = request_letter(actor=self.sales, data={"kind": "to_whom"})
        save_employee(actor=self.admin, business_unit=self.unit, data={
            "full_name": "TEST ONLY Ravi Kumar", "job_title": "Driver", "department": "Delivery", "nationality": "Indian",
            "joined_on": "2022-01-01", "user_id": self.roche_user.pk})
        with self.assertRaises(WorkflowPermissionDenied):
            letter_pdf(actor=self.roche_user, letter_id=letter["id"])
        with self.assertRaises(WorkflowPermissionDenied):
            request_letter(actor=self.roche_user, data={"kind": "to_whom", "employee_id": self.aisha["id"]})
        mine = letters_overview(actor=self.sales)
        self.assertEqual([row["id"] for row in mine["mine"]], [letter["id"]])
        self.assertFalse(mine["can_issue"])
        self.assertNotIn("signature", mine["settings"], "Staff never receive the signature picture itself")
        with self.assertRaises(WorkflowPermissionDenied):
            set_letter_settings(actor=self.sales, business_unit=self.unit, data={})
        with self.assertRaisesMessage(InvalidTransaction, "must be a PNG"):
            self.set_up_letters(signature="data:image/jpeg;base64,AAAA")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only the Master Admin can change the letter templates"):
            set_letter_settings(actor=self.admin, business_unit=self.unit, data={"company_name": "TEST ONLY Tivoli Group"})
        kept = by_master_admin(set_letter_settings)(actor=self.admin, business_unit=self.unit, data={
            "company_name": "TEST ONLY Tivoli Group", "signatory_name": "TEST ONLY Hana Saleh", "signatory_title": "Head of HR"})
        self.assertTrue(kept["has_signature"])

    def test_through_the_api(self):
        self.set_up_letters()
        client = APIClient()
        client.force_authenticate(self.sales)
        response = client.post("/api/hr/letters/", {"kind": "embassy", "addressee": "TEST ONLY Embassy of the Philippines, Doha",
                                                    "purpose": "a visit visa for my mother"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        response = client.get(f"/api/hr/letters/{response.json()['id']}/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("for a visit visa for my mother", " ".join(pdf_text(response.content).split()))
        self.assertEqual(client.get("/api/hr/letters/").json()["mine"][0]["downloads"], 1)

    def test_a_long_letter_still_fits_one_page(self):
        content = {"company_name": "TEST ONLY Tivoli", "address": "", "date": "02 November 2026", "addressee": "TEST ONLY Bank",
                   "subject": "Salary certificate", "paragraphs": ["word " * 120, "second " * 40, "third " * 40],
                   "details": [["Name", "TEST ONLY"]], "salary": [["Basic salary", "1.00"], ["Total monthly salary", "1.00"]],
                   "signatory_name": "TEST ONLY", "signatory_title": "HR", "signature": ""}
        import pypdfium2

        self.assertEqual(len(pypdfium2.PdfDocument(render_letter("HRL-TEST", content))), 1)
