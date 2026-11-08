"""Cash-flow forecast from receivables, payables, prepared payments and planned items; synthetic only."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .cash_forecast import add_planned_item, cash_forecast, remove_planned_item
from .models import BusinessUnitRoleAssignment, PurchaseReceipt, SalesQuote, UserActionGrant
from .purchasing_services import (
    WorkflowPermissionDenied, approve_post_handover_balance, deliver_sales_quote, notify_quote_ready, receive_imported_purchase,
)
from .supplier_bills import record_bill
from .supplier_payments import _received, request_payment
from . import test_purchasing_workflows as purchasing_tests
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action


@override_settings(APP_ENV="demo")
class CashForecastTests(PurchasingFixtureMixin, TestCase):
    make_role_user = purchasing_tests.PurchasingWorkflowTests.make_role_user

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for user, action in [(cls.admin, A.MANAGE_CASH), (cls.admin, A.PAY_SUPPLIER), (cls.sales, A.VIEW_LEDGER)]:
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()

    def week(self, forecast, number):
        return forecast["weeks"][number - 1]

    def test_customers_and_suppliers_are_expected_after_the_set_days(self):
        # Sale: 4,000 paid 3,600 up front, 400 owed after delivery (today).
        self.make_receipt(key="stock", charges=[])
        stock = _received(PurchaseReceipt.objects.get()).carried  # no bill: expected 30 days after the receipt
        quote = SalesQuote.objects.get(pk=self.make_quote(key="sale")["quote_id"])
        self.pay(quote, key="deposit", amount="3600.00")
        approve_post_handover_balance(actor=self.make_role_user("synthetic_forecast_gm", BusinessUnitRoleAssignment.Role.BU_GM),
                                      quote=quote, idempotency_key="defer", deferred_balance_percent=Decimal("10.00"),
                                      reason="TEST ONLY balance after handover")
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key="ready")
        deliver_sales_quote(actor=self.sales, quote=quote, idempotency_key="deliver")
        # Purchase: QAR 1,000 billed today.
        receipt_id = receive_imported_purchase(
            actor=self.sales, idempotency_key="buy", business_unit=self.unit, supplier=self.supplier,
            lines=[{"variant": self.variant, "location": self.location, "quantity": Decimal("2.00"), "unit_cost": Decimal("500.00")}],
            charges=[], currency="QAR")["purchase_receipt_id"]
        receipt = PurchaseReceipt.objects.get(pk=receipt_id)
        record_bill(actor=self.admin, idempotency_key="bill", receipt_id=receipt_id, bill_number="INV-1",
                    bill_date=self.today.isoformat(), amount=_received(receipt).payable)
        forecast = cash_forecast(actor=self.sales, business_unit=self.unit, weeks=8)
        opening = Decimal(forecast["opening"])
        self.assertEqual(opening, Decimal("3600.00"), "The deposit is in the bank")
        # 30 days on: the fifth week (days 28 to 34).
        fifth = self.week(forecast, 5)
        self.assertEqual((Decimal(fifth["in"]), Decimal(fifth["out"])), (Decimal("400.00"), 1000 + stock))
        self.assertEqual(Decimal(forecast["closing"]), opening + 400 - 1000 - stock)
        self.assertEqual(forecast["lowest"]["week"], 5)
        self.assertEqual([line["source"] for line in fifth["lines"]], ["customers", "suppliers", "suppliers"])
        # Faster customers, slower payment: 0 days for customers puts the 400 this week, 60 for suppliers pushes it past 8 weeks.
        quick = cash_forecast(actor=self.sales, business_unit=self.unit, weeks=8, customer_days={"individual": 0}, supplier_days=60)
        self.assertEqual(self.week(quick, 1)["in"], "400.00")
        self.assertEqual(Decimal(quick["later"]["out"]), 1000 + stock)
        self.assertEqual(quick["assumptions"]["customer_days"], {"individual": 0, "company": 60, "government": 90})
        # A government customer is expected after 90 days by default, beyond the 8 weeks shown.
        quote.customer.customer_type = "government"
        quote.customer.save(update_fields=["customer_type"])
        government = cash_forecast(actor=self.sales, business_unit=self.unit, weeks=8)
        self.assertEqual((self.week(government, 5)["in"], government["later"]["in"]), ("0.00", "400.00"))
        sooner = cash_forecast(actor=self.sales, business_unit=self.unit, weeks=8, customer_days={"government": "14"})
        self.assertEqual(self.week(sooner, 3)["in"], "400.00")
        with self.assertRaisesMessage(InvalidTransaction, "government customers"):
            cash_forecast(actor=self.sales, business_unit=self.unit, customer_days={"government": "400"})
        quote.customer.customer_type = "individual"
        quote.customer.save(update_fields=["customer_type"])
        # A prepared payment moves to its day and the rest stays on the bill's schedule.
        request_payment(actor=self.admin, idempotency_key="pay", receipt_id=receipt_id, amount="300.00",
                        paid_on=self.today.isoformat(), bank_reference="TT-1")
        prepared = cash_forecast(actor=self.sales, business_unit=self.unit, weeks=8)
        self.assertEqual((Decimal(self.week(prepared, 1)["out"]), Decimal(self.week(prepared, 5)["out"])), (Decimal("300.00"), 700 + stock))

    def test_late_invoices_are_expected_this_week_and_planned_items_repeat(self):
        add_planned_item(actor=self.admin, business_unit=self.unit, data={
            "name": "TEST ONLY Showroom rent", "direction": "out", "amount": "25000", "repeat": "monthly",
            "first_date": (self.today + datetime.timedelta(days=3)).isoformat()})
        add_planned_item(actor=self.admin, business_unit=self.unit, data={
            "name": "TEST ONLY Weekly cleaning", "direction": "out", "amount": "500", "repeat": "weekly",
            "first_date": self.today.isoformat(), "until": (self.today + datetime.timedelta(days=20)).isoformat()})
        promised = add_planned_item(actor=self.admin, business_unit=self.unit, data={
            "name": "TEST ONLY Hotel project milestone", "direction": "in", "amount": "60000",
            "first_date": (self.today + datetime.timedelta(days=10)).isoformat()})
        forecast = cash_forecast(actor=self.sales, business_unit=self.unit, weeks=13)
        planned = next(row for row in forecast["by_source"] if row["key"] == "planned")
        self.assertEqual((planned["in"], planned["out"]), ("60000.00", "76500.00"), "3 rents in 13 weeks and 3 cleanings")
        self.assertTrue(forecast["goes_negative"])
        # Week 1: cleaning and the first rent; week 2 brings the milestone in.
        self.assertEqual((forecast["lowest"]["week"], forecast["lowest"]["closing"]), (1, "-25500.00"))
        with self.assertRaises(WorkflowPermissionDenied):
            add_planned_item(actor=self.sales, business_unit=self.unit, data={"name": "x", "direction": "in", "amount": "1"})
        with self.assertRaisesMessage(InvalidTransaction, "cannot be before the first date"):
            add_planned_item(actor=self.admin, business_unit=self.unit, data={
                "name": "x", "direction": "in", "amount": "1", "first_date": "2026-05-02", "until": "2026-05-01"})
        remove_planned_item(actor=self.admin, business_unit=self.unit, item_id=promised["id"])
        after = cash_forecast(actor=self.sales, business_unit=self.unit, weeks=13)
        self.assertEqual(len(after["planned"]), 2)
        with self.assertRaisesMessage(InvalidTransaction, "Show from 4 to 26 weeks"):
            cash_forecast(actor=self.sales, business_unit=self.unit, weeks=40)
        with self.assertRaises(WorkflowPermissionDenied):
            cash_forecast(actor=self.roche_user, business_unit=self.unit)

    def test_the_api_returns_the_forecast_and_adds_items(self):
        client = APIClient()
        client.force_login(self.admin)
        response = client.post(f"/api/finance/cash-forecast/?unit={self.unit.pk}", {
            "name": "TEST ONLY Salaries", "direction": "out", "amount": "80000", "repeat": "monthly",
            "first_date": self.today.isoformat()}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        data = client.get(f"/api/finance/cash-forecast/?unit={self.unit.pk}&weeks=4&company_days=45").json()
        self.assertEqual(data["assumptions"]["customer_days"]["company"], 45)
        self.assertEqual((len(data["weeks"]), data["weeks"][0]["out"]), (4, "80000.00"))
        self.assertEqual(client.delete(f"/api/finance/cash-forecast/items/{response.json()['id']}/?unit={self.unit.pk}").status_code, 200)
