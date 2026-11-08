"""What we owe suppliers and other parties, by age; synthetic only."""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import DemoUserMarker, PurchaseReceipt, UserActionGrant
from .payables import payables_aging
from .purchasing_services import WorkflowPermissionDenied
from .test_purchasing_workflows import PurchasingFixtureMixin
from . import test_supplier_payments as payment_tests


@override_settings(APP_ENV="demo")
class PayablesTests(PurchasingFixtureMixin, TestCase):
    """Same fixture as the supplier payment tests: admin prepares payments and sales approves them."""

    setUp = payment_tests.SupplierPaymentTests.setUp
    receive = payment_tests.SupplierPaymentTests.receive
    prepare = payment_tests.SupplierPaymentTests.prepare
    approve = payment_tests.SupplierPaymentTests.approve
    pay = payment_tests.SupplierPaymentTests.pay

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.create(user=cls.admin, business_unit=cls.unit, action=payment_tests.PAY)
        UserActionGrant.objects.create(user=cls.sales, business_unit=cls.unit, action=payment_tests.APPROVE)

    def day(self, receipt_id, days):
        received = PurchaseReceipt.objects.get(pk=receipt_id).created_at
        return (timezone.localtime(received).date() + datetime.timedelta(days=days)).isoformat()

    def charged_receipt(self):
        return self.receive("owed", currency="EUR", unit_cost="1000.00", rate="4.00000000", charges=[
            {"category": "shipping", "amount": Decimal("850.00"), "eligible": True, "currency": "USD",
             "exchange_rate": "3.64000000", "rate_source": "TEST ONLY freight invoice", "rate_date": self.today},
            {"category": "clearance", "amount": Decimal("1800.00"), "eligible": True},
        ])

    def test_goods_and_charges_are_owed_by_age_until_approved_payments_clear_them(self):
        receipt = self.charged_receipt()
        aging = payables_aging(actor=self.admin, business_unit=self.unit, as_of=self.day(receipt, 0))
        [supplier] = aging["suppliers"]
        self.assertEqual(supplier["id"], self.supplier.pk)
        owed = {item["paying_for"]: (item["currency"], item["owed"], item["owed_in_currency"]) for item in supplier["items"]}
        self.assertEqual(owed, {
            "goods": ("EUR", "8000.00", "2000.00"), "shipping": ("USD", "3094.00", "850.00"), "clearance": ("QAR", "1800.00", None),
        })
        self.assertEqual((aging["total"], aging["totals"]["current"]), ("12894.00", "12894.00"))
        for days, bucket in ((31, "days_31_60"), (61, "days_61_90"), (120, "days_91_120"), (121, "over_120")):
            later = payables_aging(actor=self.admin, business_unit=self.unit, as_of=self.day(receipt, days))
            self.assertEqual(later["totals"][bucket], "12894.00", days)
        self.assertEqual(payables_aging(actor=self.admin, business_unit=self.unit, as_of=self.day(receipt, -1))["suppliers"], [])
        by_years = payables_aging(actor=self.admin, business_unit=self.unit, as_of=self.day(receipt, 400), by="years")
        self.assertEqual((by_years["by"], by_years["totals"]["years_1_2"]), ("years", "12894.00"))

        # Half the goods paid at a better rate clears half of what the receipt booked, not what left the bank.
        self.pay(receipt, "1000.00", "3.90000000", key="half-goods")
        aging = payables_aging(actor=self.admin, business_unit=self.unit, as_of=self.day(receipt, 0))
        goods = next(item for item in aging["suppliers"][0]["items"] if item["paying_for"] == "goods")
        self.assertEqual((goods["cleared"], goods["owed"], goods["owed_in_currency"]), ("4000.00", "4000.00", "1000.00"))
        self.assertEqual(aging["total"], "8894.00")

    def test_a_payment_waiting_for_approval_is_shown_but_not_deducted(self):
        receipt = self.receive("waiting", currency="QAR", unit_cost="1500.00")
        self.prepare(receipt, "1000.00", key="waiting-1")
        aging = payables_aging(actor=self.admin, business_unit=self.unit)
        self.assertEqual((aging["total"], aging["waiting_approval"]), ("3000.00", "1000.00"))
        self.assertEqual(aging["suppliers"][0]["items"][0]["waiting_approval"], "1000.00")

    def test_a_payment_dated_after_the_chosen_day_does_not_count_yet(self):
        receipt = self.receive("dated", currency="QAR", unit_cost="1500.00")
        today = datetime.date.fromisoformat(self.today)
        PurchaseReceipt.objects.filter(pk=receipt).update(created_at=timezone.now() - datetime.timedelta(days=10))
        paid_on = (today - datetime.timedelta(days=3)).isoformat()
        self.pay(receipt, "3000.00", key="dated-pay", paid_on=paid_on)
        before = payables_aging(actor=self.admin, business_unit=self.unit, as_of=(today - datetime.timedelta(days=4)).isoformat())
        self.assertEqual((before["total"], before["suppliers"][0]["oldest_days"]), ("3000.00", 6))
        self.assertEqual(payables_aging(actor=self.admin, business_unit=self.unit, as_of=paid_on)["suppliers"], [])

    def test_ledger_viewers_and_payment_staff_can_see_it_but_no_one_else(self):
        self.receive("access", currency="QAR", unit_cost="1500.00")
        viewer = get_user_model().objects.create_user(username="test_payables_viewer", password="unused-test-password")
        DemoUserMarker.objects.create(user=viewer)
        outsider = get_user_model().objects.create_user(username="test_payables_outsider", password="unused-test-password")
        DemoUserMarker.objects.create(user=outsider)
        UserActionGrant.objects.create(user=viewer, business_unit=self.unit, action=UserActionGrant.Action.VIEW_LEDGER)
        for user in (viewer, self.admin, self.sales):
            self.assertEqual(payables_aging(actor=user, business_unit=self.unit)["total"], "3000.00", user.username)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "general ledger or to prepare or approve"):
            payables_aging(actor=outsider, business_unit=self.unit)
        client = APIClient()
        client.force_login(viewer)
        response = client.get("/api/finance/payables/", {"unit": self.unit.pk})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], "3000.00")
        client.force_login(outsider)
        self.assertEqual(client.get("/api/finance/payables/", {"unit": self.unit.pk}).status_code, 403)
