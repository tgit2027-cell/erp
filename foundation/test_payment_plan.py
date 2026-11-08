"""Payment plan by week and month, by bank and currency, within each bank's daily foreign-currency limit; synthetic only."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .cash_accounts import create_account
from .master_admin_testing import by_master_admin
from .cash_forecast import add_planned_item, cash_forecast
from .models import PurchaseReceipt, UserActionGrant
from .payment_plan import _working, payment_plan, set_bank_limit
from .purchasing_services import WorkflowPermissionDenied, receive_imported_purchase
from .supplier_bills import record_bill
from .supplier_payments import _received, request_payment
from . import test_purchasing_workflows as purchasing_tests
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action
D = Decimal


@override_settings(APP_ENV="demo")
class PaymentPlanTests(PurchasingFixtureMixin, TestCase):
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

    def bank(self, name, bank_name, default=False):
        return by_master_admin(create_account)(actor=self.admin, business_unit=self.unit, name=name, kind="bank", bank_name=bank_name,
                              opening_date=self.today.isoformat(), is_default=default)

    def plan(self, name, amount, currency="QAR", days=7, repeat="once", account=None):
        return add_planned_item(actor=self.admin, business_unit=self.unit, data={
            "name": name, "direction": "out", "amount": amount, "currency": currency, "repeat": repeat, "account": account,
            "first_date": (self.today + datetime.timedelta(days=days)).isoformat()})

    def lines(self, plan):
        return [line for period in plan["periods"] for group in period["groups"] for line in group["lines"]]

    def test_foreign_transfers_stay_within_the_bank_daily_limit(self):
        qnb = self.bank("TEST ONLY QNB current", "QNB", default=True)
        by_master_admin(set_bank_limit)(actor=self.admin, business_unit=self.unit, data={"bank_name": "qnb", "daily_limit": "100000"})
        # No dollar rate in the books yet: the indicative 3.64 is used.
        self.plan("TEST ONLY Italian factory", "20000", "USD")   # 72,800 QAR
        self.plan("TEST ONLY Shipping line", "10000", "USD")     # 36,400 QAR: no room left that day
        self.plan("TEST ONLY Showroom rent", "50000", "QAR")     # QAR is not limited
        self.plan("TEST ONLY Big order", "50000", "USD", days=28)  # 182,000 QAR: more than a day, so split
        plan = payment_plan(actor=self.sales, business_unit=self.unit, view="week", periods=8)
        due = _working(self.today + datetime.timedelta(days=7))
        after = _working(due + datetime.timedelta(days=1))
        sent = {line["payee"]: line for line in self.lines(plan)}
        self.assertEqual(sent["TEST ONLY Italian factory"]["send_on"][0]["date"], due.isoformat())
        self.assertEqual(sent["TEST ONLY Showroom rent"]["send_on"][0]["date"], due.isoformat())
        moved = sent["TEST ONLY Shipping line"]
        self.assertEqual((moved["first_day"], moved["after_due"], moved["split"]), (after.isoformat(), True, False))
        self.assertEqual(plan["busy_days"][0], {"bank_name": "QNB", "date": due.isoformat(), "due": "109200.00",
                                                "limit": "100000.00", "over_by": "9200.00"})
        self.assertEqual((len(plan["busy_days"]), plan["busy_days"][1]["over_by"]), (2, "82000.00"))
        big = [line for line in self.lines(plan) if line["payee"] == "TEST ONLY Big order"]
        parts = [part for line in big for part in line["send_on"]]
        self.assertTrue(big[0]["split"])
        self.assertEqual([part["value"] for part in parts], ["100000.00", "82000.00"])
        self.assertEqual(sum(D(part["amount"]) for part in parts), D("50000.00"))
        # Every working day stays within the limit.
        for period in plan["periods"]:
            for day in period["days"]:
                self.assertLessEqual(D(day["banks"][0]["used"]), D("100000"))
        self.assertEqual({row["payee"] for row in plan["moved"]}, {"TEST ONLY Shipping line", "TEST ONLY Big order"})
        # All of it goes from the default QNB account, dollars and riyals shown apart.
        group = next(period for period in plan["periods"] if period["start"] <= due.isoformat() <= period["end"])["groups"][0]
        self.assertEqual(group["account"]["id"], qnb["id"])
        self.assertEqual([money["currency"] for money in group["currencies"]], ["QAR", "USD"])
        # Without the limit everything goes on its due day.
        by_master_admin(set_bank_limit)(actor=self.admin, business_unit=self.unit, data={"bank_name": "QNB", "daily_limit": ""})
        free = payment_plan(actor=self.sales, business_unit=self.unit, view="week", periods=8)
        self.assertEqual((free["busy_days"], free["moved"]), ([], []))
        self.assertEqual(free["limits"], [{"bank_name": "QNB", "daily_limit": None, "set_by": None}])

    def test_supplier_bills_in_their_currency_by_month_and_account(self):
        rate = "4.00000000"
        receipt_id = receive_imported_purchase(
            actor=self.sales, idempotency_key="eur", business_unit=self.unit, supplier=self.supplier,
            lines=[{"variant": self.variant, "location": self.location, "quantity": D("2.00"), "unit_cost": D("1000.00")}],
            charges=[], currency="EUR", exchange_rate=rate, rate_source="TEST ONLY bank rate", rate_date=self.today,
        )["purchase_receipt_id"]
        receipt = PurchaseReceipt.objects.get(pk=receipt_id)
        record_bill(actor=self.admin, idempotency_key="bill", receipt_id=receipt_id, bill_number="INV-1",
                    bill_date=self.today, amount=_received(receipt).payable)
        # No bank account yet: the payment shows under "no bank account chosen".
        plan = payment_plan(actor=self.sales, business_unit=self.unit, view="month", periods=3, supplier_days=0)
        line = self.lines(plan)[0]
        self.assertEqual((line["currency"], line["amount"], line["value"], line["source"]), ("EUR", "2000.00", "8000.00", "suppliers"))
        self.assertEqual(plan["periods"][0]["groups"][0]["account"]["id"], None)
        self.assertEqual(plan["periods"][0]["currencies"], [{"currency": "EUR", "amount": "2000.00", "value": "8000.00"}])
        # The euro rate now comes from the books, and a planned euro payment counts at it in the forecast too.
        self.assertIn({"currency": "EUR", "rate": "4", "source": "books"}, plan["rates"])
        self.bank("TEST ONLY CBQ", "CBQ", default=True)
        hsbc = self.bank("TEST ONLY HSBC", "HSBC")
        self.plan("TEST ONLY Paris office rent", "1000", "EUR", days=0, account=hsbc["id"])
        forecast = cash_forecast(actor=self.sales, business_unit=self.unit, weeks=4, supplier_days=60)
        self.assertEqual(forecast["weeks"][0]["out"], "4000.00")
        # Part of the bill is prepared: the prepared part and the rest show separately.
        request_payment(actor=self.admin, idempotency_key="pay", receipt_id=receipt_id, amount="500.00",
                        paid_on=self.today.isoformat(), bank_reference="TT-1", exchange_rate="4.00000000",
                        rate_source="TEST ONLY bank rate", rate_date=self.today.isoformat())
        plan = payment_plan(actor=self.sales, business_unit=self.unit, view="month", periods=1, supplier_days=0)
        groups = plan["periods"][0]["groups"]
        self.assertEqual([group["account"]["bank_name"] for group in groups], ["CBQ", "HSBC"])
        amounts = sorted((line["source"], line["amount"]) for line in groups[0]["lines"])
        self.assertEqual(amounts, [("prepared", "500.00"), ("suppliers", "1500.00")])
        self.assertEqual(groups[1]["lines"][0]["payee"], "TEST ONLY Paris office rent")

    def test_rules_permissions_and_api(self):
        with self.assertRaises(WorkflowPermissionDenied):
            set_bank_limit(actor=self.sales, business_unit=self.unit, data={"bank_name": "QNB", "daily_limit": "1"})
        with self.assertRaisesMessage(InvalidTransaction, "above zero"):
            by_master_admin(set_bank_limit)(actor=self.admin, business_unit=self.unit, data={"bank_name": "QNB", "daily_limit": "0"})
        with self.assertRaisesMessage(InvalidTransaction, "by week or by month"):
            payment_plan(actor=self.sales, business_unit=self.unit, view="year")
        with self.assertRaisesMessage(InvalidTransaction, "Show from 1 to 12 months"):
            payment_plan(actor=self.sales, business_unit=self.unit, view="month", periods=13)
        with self.assertRaises(WorkflowPermissionDenied):
            payment_plan(actor=self.roche_user, business_unit=self.unit)
        with self.assertRaisesMessage(InvalidTransaction, "QAR, USD, EUR or GBP"):
            self.plan("x", "1", "JPY")
        # Weekly payments repeat, and weeks run Sunday to Saturday.
        self.plan("TEST ONLY Cleaning", "500", repeat="weekly", days=0)
        client = APIClient()
        client.force_login(self.admin)
        response = client.post(f"/api/finance/payment-plan/limits/?unit={self.unit.pk}", {"bank_name": "Doha Bank", "daily_limit": "250000"}, format="json")
        self.assertEqual(response.status_code, 403, response.content)  # Setup lock (Nov 2026): Master Admin only.
        self.admin.is_superuser = True
        self.admin.save(update_fields=["is_superuser"])
        response = client.post(f"/api/finance/payment-plan/limits/?unit={self.unit.pk}", {"bank_name": "Doha Bank", "daily_limit": "250000"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        data = client.get(f"/api/finance/payment-plan/?unit={self.unit.pk}&view=week&periods=4").json()
        self.assertEqual(len(data["periods"]), 4)
        self.assertEqual(datetime.date.fromisoformat(data["periods"][0]["start"]).weekday(), 6)
        # Four cleanings fall due in four weeks; one due on a Friday or Saturday is sent the next Sunday.
        values = [period["value"] for period in data["periods"]]
        self.assertTrue(set(values) <= {"0.00", "500.00", "1000.00"} and values.count("500.00") >= 2, values)
        self.assertEqual(data["limits"][0]["daily_limit"], "250000.00")
        self.assertEqual(len(data["periods"][0]["days"]), 5)
