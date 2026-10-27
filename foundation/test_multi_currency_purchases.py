from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    JournalEntry,
    PurchaseCommitment,
    PurchaseReceipt,
    StockBalance,
)
from .purchasing_services import create_purchase_commitment, receive_imported_purchase
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import IdempotencyConflict, InvalidTransaction


@override_settings(APP_ENV="demo")
class MultiCurrencyPurchaseTests(PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()

    def commit(self, key="eur-commitment", currency="EUR", rate="4.00000000", source="TEST ONLY bank rate", **extra):
        return create_purchase_commitment(
            actor=self.sales, idempotency_key=key, business_unit=self.unit, supplier=self.supplier,
            lines=[{"variant": self.variant, "ordered_quantity": Decimal("5.00"), "unit_cost": Decimal("1000.00")}],
            currency=currency, exchange_rate=rate, rate_source=source,
            rate_date=extra.pop("rate_date", self.today.isoformat()), **extra,
        )

    def receive(self, key="usd-receipt", unit_cost="333.33", rate="3.64000000", currency="USD", **extra):
        return receive_imported_purchase(
            actor=self.sales, idempotency_key=key, business_unit=self.unit, supplier=self.supplier,
            lines=[{"variant": self.variant, "location": self.location, "quantity": Decimal("3.00"),
                    "unit_cost": Decimal(unit_cost)}],
            charges=[{"category": "clearance", "amount": Decimal("100.00"), "eligible": True}],
            currency=currency, exchange_rate=rate, rate_source="TEST ONLY QCB reference rate",
            rate_date=self.today.isoformat(), **extra,
        )

    def test_foreign_receipt_values_stock_and_journal_in_qar_and_keeps_supplier_price(self):
        result = self.receive()
        # 333.33 USD × 3.64 = 1,213.3212 → 1,213.32 QAR per unit; three units.
        self.assertEqual(result["purchase_value"], "3639.96")
        self.assertEqual(result["foreign_purchase_value"], "999.99")
        self.assertEqual(result["transaction_currency"], "USD")
        self.assertEqual(result["exchange_rate"], "3.64000000")
        self.assertEqual(result["inventory_value"], "3739.96")
        receipt = PurchaseReceipt.objects.get(pk=result["purchase_receipt_id"])
        self.assertEqual((receipt.currency, receipt.foreign_purchase_value), ("USD", Decimal("999.99")))
        self.assertEqual(receipt.rate_source, "TEST ONLY QCB reference rate")
        line = receipt.lines.get()
        self.assertEqual((line.foreign_unit_cost, line.unit_cost), (Decimal("333.33"), Decimal("1213.32")))
        balance = StockBalance.objects.get(business_unit=self.unit, variant=self.variant, location=self.location)
        self.assertEqual(balance.on_hand_value, Decimal("3739.96"))
        entry = JournalEntry.objects.get(pk=result["stock_journal_entry_id"])
        debit = sum(entry.lines.values_list("debit", flat=True), Decimal("0"))
        credit = sum(entry.lines.values_list("credit", flat=True), Decimal("0"))
        self.assertEqual((debit, credit), (Decimal("3739.96"), Decimal("3739.96")))
        self.assertEqual(entry.currency, "QAR")

    def test_committed_receipt_uses_commitment_currency_with_the_rate_on_receipt_day(self):
        commitment = PurchaseCommitment.objects.get(pk=self.commit()["purchase_commitment_id"])
        self.assertEqual((commitment.currency, commitment.exchange_rate), ("EUR", Decimal("4.00000000")))
        line = commitment.lines.get()
        result = receive_imported_purchase(
            actor=self.sales, idempotency_key="eur-receipt", business_unit=self.unit, supplier=self.supplier,
            purchase_commitment=commitment,
            lines=[{"variant": self.variant, "location": self.location, "quantity": Decimal("2.00"),
                    "commitment_line": line}],
            charges=[], exchange_rate="4.10000000", rate_source="TEST ONLY bank rate on arrival",
            rate_date=self.today.isoformat(),
        )
        self.assertEqual(result["transaction_currency"], "EUR")
        self.assertEqual(result["purchase_value"], "8200.00")
        self.assertEqual(result["foreign_purchase_value"], "2000.00")
        with self.assertRaises(InvalidTransaction):
            receive_imported_purchase(
                actor=self.sales, idempotency_key="eur-as-usd", business_unit=self.unit, supplier=self.supplier,
                purchase_commitment=commitment,
                lines=[{"variant": self.variant, "location": self.location, "quantity": Decimal("1.00"),
                        "commitment_line": line}],
                charges=[], currency="USD", exchange_rate="3.64", rate_source="x", rate_date=self.today.isoformat(),
            )

    def test_rate_rules_are_enforced(self):
        for kwargs in (
            {"source": ""},
            {"rate": "0"},
            {"rate": "-1"},
            {"rate": 4.0},
            {"rate": "4.123456789"},
            {"currency": "JPY"},
            {"rate_date": (self.today + timedelta(days=1)).isoformat()},
            {"rate_date": "21/10/2026"},
            {"currency": "QAR", "rate": "2"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(InvalidTransaction):
                self.commit(key=f"bad-{sorted(kwargs.items())}", **kwargs)
        self.assertFalse(PurchaseCommitment.objects.exists())
        qar = self.commit(key="plain-qar", currency="QAR", rate=None, source="")
        self.assertEqual((qar["currency"], qar["exchange_rate"]), ("QAR", "1"))

    def test_replay_and_changed_rate_with_the_same_key(self):
        first = self.receive(key="replay-usd")
        self.assertEqual(self.receive(key="replay-usd"), first)
        self.assertEqual(PurchaseReceipt.objects.count(), 1)
        with self.assertRaises(IdempotencyConflict):
            self.receive(key="replay-usd", rate="3.65000000")

    def test_database_requires_a_recorded_rate_for_foreign_documents(self):
        self.receive()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PurchaseReceipt.objects.update(rate_source="")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PurchaseReceipt.objects.update(currency="QAR")

    def test_api_lists_currencies_and_accepts_a_foreign_receipt(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.sales)
        csrf = client.get("/api/auth/csrf/").data["csrfToken"]
        codes = [row["code"] for row in client.get("/api/purchasing/currencies/").data["currencies"]]
        self.assertEqual(codes, ["QAR", "EUR", "GBP", "USD"])
        response = client.post("/api/purchasing/receipts/", {
            "business_unit_id": self.unit.pk, "supplier_id": self.supplier.pk,
            "lines": [{"variant_id": self.variant.pk, "location_id": self.location.pk,
                       "quantity": "1.00", "unit_cost": "500.00"}],
            "charges": [], "currency": "GBP", "exchange_rate": "4.60000000",
            "rate_source": "TEST ONLY bank rate", "rate_date": self.today.isoformat(),
        }, format="json", HTTP_X_CSRFTOKEN=csrf, HTTP_IDEMPOTENCY_KEY="api-gbp")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["transaction_currency"], "GBP")
        self.assertEqual(response.data["exchange_rate"], "4.60000000")
        self.assertEqual(PurchaseReceipt.objects.get().purchase_value, Decimal("2300.00"))


@override_settings(APP_ENV="demo")
class ForeignLandedChargeTests(PurchasingFixtureMixin, TestCase):
    """Owner decision P1: shipping may be billed in EUR or USD; customs clearance is QAR."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()

    def receive(self, charges, key="charges", currency="QAR", **extra):
        terms = {} if currency == "QAR" else {
            "exchange_rate": "4.00000000", "rate_source": "TEST ONLY bank rate", "rate_date": self.today.isoformat(),
        }
        return receive_imported_purchase(
            actor=self.sales, idempotency_key=key, business_unit=self.unit, supplier=self.supplier,
            lines=[{"variant": self.variant, "location": self.location, "quantity": Decimal("2.00"),
                    "unit_cost": Decimal("1000.00")}],
            charges=charges, currency=currency, **terms, **extra,
        )

    def usd_shipping(self, amount="250.00", **extra):
        return {"category": "shipping", "amount": amount, "eligible": True, "currency": "USD",
                "exchange_rate": "3.64000000", "rate_source": "TEST ONLY freight invoice rate",
                "rate_date": self.today.isoformat(), **extra}

    def test_usd_shipping_and_qar_clearance_are_capitalized_in_qar(self):
        result = self.receive([
            self.usd_shipping(),
            {"category": "clearance", "amount": "300.00", "eligible": True},
        ])
        # 250 USD × 3.64 = 910.00 QAR shipping, plus 300.00 QAR clearance.
        self.assertEqual(result["eligible_landed_charges"], "1210.00")
        self.assertEqual(result["inventory_value"], "3210.00")
        self.assertEqual(result["foreign_charges"], [{
            "category": "shipping", "foreign_amount": "250.00", "amount": "910.00", "currency": "USD",
            "exchange_rate": "3.64000000", "rate_source": "TEST ONLY freight invoice rate",
            "rate_date": self.today.isoformat(),
        }])
        shipping, clearance = PurchaseReceipt.objects.get().charges.order_by("pk")
        self.assertEqual((shipping.currency, shipping.foreign_amount, shipping.amount),
                         ("USD", Decimal("250.00"), Decimal("910.00")))
        self.assertEqual((clearance.currency, clearance.foreign_amount, clearance.amount),
                         ("QAR", None, Decimal("300.00")))
        balance = StockBalance.objects.get(business_unit=self.unit, variant=self.variant, location=self.location)
        self.assertEqual(balance.on_hand_value, Decimal("3210.00"))

    def test_eur_shipping_reuses_the_eur_document_rate_when_none_is_given(self):
        result = self.receive([{"category": "shipping", "amount": "100.00", "eligible": True, "currency": "EUR"}],
                              currency="EUR")
        self.assertEqual(result["foreign_charges"][0]["amount"], "400.00")
        self.assertEqual(result["foreign_charges"][0]["exchange_rate"], "4.00000000")

    def test_clearance_must_be_qar_and_foreign_charges_need_a_rate(self):
        for charge in (
            {"category": "clearance", "amount": "50.00", "eligible": True, "currency": "USD",
             "exchange_rate": "3.64", "rate_source": "x", "rate_date": self.today.isoformat()},
            self.usd_shipping(rate_source=""),
            self.usd_shipping(exchange_rate="0"),
            self.usd_shipping(rate_date=None),
            self.usd_shipping(currency="JPY"),
        ):
            with self.subTest(charge=charge), self.assertRaises(InvalidTransaction):
                self.receive([charge], key=f"bad-{sorted(charge.items())}")
        self.assertFalse(PurchaseReceipt.objects.exists())

    def test_replay_and_changed_charge_rate(self):
        first = self.receive([self.usd_shipping()], key="replay")
        self.assertEqual(self.receive([self.usd_shipping()], key="replay"), first)
        with self.assertRaises(IdempotencyConflict):
            self.receive([self.usd_shipping(exchange_rate="3.65000000")], key="replay")

    def test_database_keeps_clearance_in_qar(self):
        self.receive([self.usd_shipping(), {"category": "clearance", "amount": "10.00", "eligible": True}])
        from .models import PurchaseReceiptCharge
        with self.assertRaises(IntegrityError), transaction.atomic():
            PurchaseReceiptCharge.objects.filter(category="shipping").update(category="clearance")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PurchaseReceiptCharge.objects.filter(category="shipping").update(foreign_amount=None)
