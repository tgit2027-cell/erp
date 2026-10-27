"""The first full proof runs end to end on the seeded demo data and reconciles."""

from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from .first_proof import run_first_proof
from .models import CustomerInvoice, JournalEntry, PurchaseReceipt, PurchaseReceiptCharge, SalesQuote


@override_settings(APP_ENV="demo")
class FirstProofTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch.dict("os.environ", {"APP_ENV": "demo"}):
            call_command("seed_demo", stdout=StringIO())
        cls.admin = get_user_model().objects.get(username="demo_admin")

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)

    def test_every_step_runs_and_the_unit_reconciles(self):
        result = run_first_proof(admin=self.admin)
        report = result["reconciliation"]
        self.assertTrue(report["all_match"], [c["label"] for c in report["checks"] if not c["matches"]])
        # 6 × EUR 1,200 at 4.215 = QAR 30,348.00; USD 850 at 3.64 = QAR 3,094.00; customs QAR 1,800.00.
        receipt = result["receipt"]
        self.assertEqual(receipt["purchase_value"], "30348.00")
        self.assertEqual(receipt["foreign_purchase_value"], "7200.00")
        self.assertEqual(receipt["eligible_landed_charges"], "4894.00")
        self.assertEqual(receipt["inventory_value"], "35242.00")
        charges = PurchaseReceiptCharge.objects.filter(receipt_id=receipt["purchase_receipt_id"]).order_by("pk")
        self.assertEqual([(c.category, c.currency) for c in charges], [("shipping", "USD"), ("clearance", "QAR")])
        invoice = CustomerInvoice.objects.get(number=result["invoice_number"])
        self.assertEqual(invoice.balance_due, Decimal("0.00"))
        held = SalesQuote.objects.exclude(status=SalesQuote.Status.DELIVERED).get(customer__code="TEST-CUST-PROOF-2")
        self.assertEqual(Decimal(report["summary"]["deposits_held"]), (held.total_quote_value * Decimal("0.30")).quantize(Decimal("0.01")))
        self.assertEqual([step["step"] for step in result["steps"]], [
            "Selling price", "Purchase order abroad", "Receipt with landed cost", "Sale paid before delivery",
            "Delivery and invoice", "Deposit held on an open order", "Reconciliation",
        ])

    def test_running_it_again_adds_nothing(self):
        first = run_first_proof(admin=self.admin)
        counts = (PurchaseReceipt.objects.count(), CustomerInvoice.objects.count(), JournalEntry.objects.count())
        second = run_first_proof(admin=self.admin)
        self.assertEqual(
            (PurchaseReceipt.objects.count(), CustomerInvoice.objects.count(), JournalEntry.objects.count()), counts,
        )
        self.assertEqual(second["invoice_number"], first["invoice_number"])
        self.assertTrue(second["reconciliation"]["all_match"])

    def test_command_prints_each_step(self):
        out = StringIO()
        call_command("seed_first_proof", stdout=out)
        text = out.getvalue()
        self.assertIn("Receipt with landed cost", text)
        self.assertIn("22 of 22 checks match", text)
