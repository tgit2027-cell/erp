"""Bank accounts and cash boxes: money in and out per account; synthetic only."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .cash_accounts import cash_overview, create_account, move_line, record_entry
from .master_admin_testing import by_master_admin
from .models import CashAccount, CashEntry, JournalEntry, QuotePayment, SalesQuote, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .scenario_reconciliation import build_scenario_reconciliation
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

MANAGE = UserActionGrant.Action.MANAGE_CASH


@override_settings(APP_ENV="demo")
class CashAccountTests(PurchasingFixtureMixin, TestCase):
    """Admin records bank and cash; sales takes customer payments."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.create(user=cls.admin, business_unit=cls.unit, action=MANAGE)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()
        self.opened = (self.today - datetime.timedelta(days=60)).isoformat()

    def account(self, name, kind="bank", **extra):
        if kind == "bank":
            extra.setdefault("bank_name", "TEST ONLY QNB")
        return by_master_admin(create_account)(actor=self.admin, business_unit=self.unit, name=name, kind=kind,
                              opening_date=extra.pop("opening_date", self.opened), **extra)

    def entry(self, kind, account, amount, key, **extra):
        return record_entry(actor=self.admin, idempotency_key=key, business_unit=self.unit, kind=kind,
                            account_id=account["id"], amount=amount, entry_date=extra.pop("day", self.today.isoformat()),
                            **extra)

    def customer_payment(self, key, amount, method):
        self.make_receipt(key=f"stock-{key}")
        quote = SalesQuote.objects.get(pk=self.make_quote(key=f"quote-{key}", quantity="1.00")["quote_id"])
        self.pay(quote, key=key, amount=amount, method=method)

    def overview(self, account=None, **extra):
        return cash_overview(actor=self.admin, business_unit=self.unit, account_id=account and account["id"], **extra)

    def test_customer_money_goes_to_the_default_cash_box_or_bank_until_moved(self):
        bank = self.account("TEST ONLY QNB current", bank_name="TEST ONLY QNB", account_last4="4417", opening_balance="50000.00")
        second = self.account("TEST ONLY CBQ collections", bank_name="TEST ONLY CBQ")
        till = self.account("TEST ONLY Showroom till", kind="cash", opening_balance="500.00")
        self.assertEqual((bank["is_default"], second["is_default"], till["is_default"]), (True, False, True))
        self.customer_payment("cash", "1000.00", QuotePayment.Method.SIMULATED_CASH)
        self.customer_payment("card", "2000.00", QuotePayment.Method.SIMULATED_CARD)
        overview = self.overview()
        balances = {row["name"]: row["balance"] for row in overview["accounts"]}
        self.assertEqual(balances, {"TEST ONLY QNB current": "52000.00", "TEST ONLY CBQ collections": "0.00",
                                    "TEST ONLY Showroom till": "1500.00"})
        self.assertEqual(overview["total"], "53500.00")
        self.assertEqual(overview["selected_id"], bank["id"], "The default bank account opens first")
        card = next(row for row in self.overview(bank)["movements"] if row["money_in"] == "2000.00")
        self.assertEqual((card["source"], card["balance"], card["fixed"]), ("Customer payment", "52000.00", False))
        self.assertTrue(card["description"].startswith("customer payment on quote"), card["description"])

        moved = move_line(actor=self.admin, business_unit=self.unit, line_id=card["line_id"], account_id=second["id"])
        self.assertEqual(moved["account_id"], second["id"])
        balances = {row["name"]: row["balance"] for row in self.overview()["accounts"]}
        self.assertEqual((balances["TEST ONLY QNB current"], balances["TEST ONLY CBQ collections"]), ("50000.00", "2000.00"))
        with self.assertRaisesMessage(WorkflowPermissionDenied, "record bank and cash"):
            move_line(actor=self.sales, business_unit=self.unit, line_id=card["line_id"], account_id=bank["id"])

    def test_money_with_no_account_yet_is_shown_as_not_placed(self):
        self.customer_payment("early", "750.00", QuotePayment.Method.SIMULATED_CASH)
        overview = self.overview()
        self.assertEqual(overview["accounts"], [])
        self.assertEqual([(row["money_in"], row["account_id"]) for row in overview["unplaced"]], [("750.00", None)])
        self.account("TEST ONLY Till", kind="cash")
        self.assertEqual(self.overview()["unplaced"], [])

    def test_transfers_charges_and_interest_post_and_tie_to_the_reconciliation(self):
        bank = self.account("TEST ONLY QNB current", opening_balance="10000.00")
        till = self.account("TEST ONLY Till", kind="cash", opening_balance="3000.00")
        self.customer_payment("cash-in", "1000.00", QuotePayment.Method.SIMULATED_CASH)
        transfer = self.entry("transfer", till, "2500.00", "deposit", to_account_id=bank["id"], reference="DEP-1")
        self.assertEqual(transfer["description"], "Transfer from TEST ONLY Till to TEST ONLY QNB current")
        self.entry("bank_charge", bank, "35.00", "fee")
        self.entry("interest", bank, "12.50", "interest")
        balances = {row["name"]: (row["balance"], row["money_in"], row["money_out"]) for row in self.overview()["accounts"]}
        self.assertEqual(balances, {"TEST ONLY QNB current": ("12477.50", "2512.50", "35.00"),
                                    "TEST ONLY Till": ("1500.00", "1000.00", "2500.00")})
        entry = JournalEntry.objects.get(pk=transfer["journal_entry_id"])
        self.assertEqual(entry.kind, JournalEntry.Kind.CASH_ENTRY)
        fee = CashEntry.objects.get(kind="bank_charge").journal_entry
        self.assertEqual({line.account.code: (line.debit, line.credit) for line in fee.lines.select_related("account")}, {
            "TEST_BANK_CHARGES": (Decimal("35.00"), Decimal("0.00")), "TEST_CASH": (Decimal("0.00"), Decimal("35.00")),
        })
        moves = self.overview(bank)["movements"]
        self.assertTrue(all(row["fixed"] for row in moves))
        with self.assertRaisesMessage(InvalidTransaction, "stay on the account"):
            move_line(actor=self.admin, business_unit=self.unit, line_id=moves[0]["line_id"], account_id=till["id"])
        self.assertEqual(self.entry("bank_charge", bank, "35.00", "fee"), self.entry("bank_charge", bank, "35.00", "fee"))
        self.assertEqual(CashEntry.objects.count(), 3)
        report = build_scenario_reconciliation(self.unit)
        cash = next(row for row in report["checks"] if row["label"].startswith("Cash in the journal"))
        self.assertTrue(cash["matches"], cash)
        self.assertTrue(report["all_match"], [row["label"] for row in report["checks"] if not row["matches"]])

    def test_period_opening_balance_and_running_balance(self):
        bank = self.account("TEST ONLY QNB current", opening_balance="1000.00")
        last_month = (self.today.replace(day=1) - datetime.timedelta(days=1)).isoformat()
        self.entry("interest", bank, "100.00", "old", day=last_month)
        self.entry("bank_charge", bank, "40.00", "new")
        row = self.overview(bank)["accounts"][0]
        self.assertEqual((row["opening"], row["money_in"], row["money_out"], row["balance"]),
                         ("1100.00", "0.00", "40.00", "1060.00"))
        self.assertEqual([m["balance"] for m in self.overview(bank)["movements"]], ["1060.00"])
        whole = self.overview(bank, start=self.opened)
        self.assertEqual([m["balance"] for m in whole["movements"]], ["1100.00", "1060.00"])
        with self.assertRaisesMessage(InvalidTransaction, "start date must be on or before"):
            self.overview(bank, start=self.today.isoformat(), end=last_month)

    def test_one_cash_account_and_several_accounts_under_one_bank(self):
        self.account("TEST ONLY Cash", kind="cash", opening_balance="800.00")
        with self.assertRaisesMessage(InvalidTransaction, "already has its cash account"):
            self.account("TEST ONLY Second till", kind="cash")
        with self.assertRaisesMessage(InvalidTransaction, "Choose the type of bank account"):
            self.account("TEST ONLY CBQ deposit", bank_name="TEST ONLY CBQ", account_type="deposit")
        for name, kind_of, opening in (("current", "current", "40000.00"), ("savings", "savings", "15000.00"),
                                       ("LTR", "ltr", "25000.00"), ("LG margin", "lg", "5000.00")):
            self.account(f"TEST ONLY CBQ {name}", bank_name="TEST ONLY CBQ", account_type=kind_of, opening_balance=opening)
        self.account("TEST ONLY QNB current", account_type="current", opening_balance="1000.00")
        overview = self.overview()
        self.assertEqual([(row["name"], row["account_type"]) for row in overview["accounts"]], [
            ("TEST ONLY Cash", ""), ("TEST ONLY CBQ current", "current"), ("TEST ONLY CBQ savings", "savings"),
            ("TEST ONLY CBQ LTR", "ltr"), ("TEST ONLY CBQ LG margin", "lg"), ("TEST ONLY QNB current", "current")])
        self.assertEqual((overview["total"], overview["facilities_total"]), ("56800.00", "-20000.00"),
                         "LTR and LG are facilities, not money available")

    def test_account_and_entry_rules(self):
        bank = self.account("TEST ONLY QNB current")
        with self.assertRaisesMessage(InvalidTransaction, "already has an account called"):
            self.account("test only qnb CURRENT")
        with self.assertRaisesMessage(InvalidTransaction, "last four digits"):
            self.account("TEST ONLY Other", account_last4="12a4")
        with self.assertRaisesMessage(InvalidTransaction, "bank account or a cash box"):
            self.account("TEST ONLY Safe", kind="vault")
        tomorrow = (self.today + datetime.timedelta(days=1)).isoformat()
        with self.assertRaisesMessage(InvalidTransaction, "cannot be in the future"):
            self.entry("interest", bank, "1.00", "future", day=tomorrow)
        with self.assertRaisesMessage(InvalidTransaction, "two different accounts"):
            self.entry("transfer", bank, "1.00", "self", to_account_id=bank["id"])
        before = (datetime.date.fromisoformat(self.opened) - datetime.timedelta(days=1)).isoformat()
        with self.assertRaisesMessage(InvalidTransaction, "opened on"):
            self.entry("interest", bank, "1.00", "before", day=before)
        roche_account = CashAccount.objects.create(business_unit=self.roche, name="TEST ONLY Roche bank", kind="bank", account_type="current",
                                                   opening_date=self.today, created_by=self.admin)
        with self.assertRaisesMessage(InvalidTransaction, "Choose a bank account or cash box of"):
            self.entry("interest", {"id": roche_account.pk}, "1.00", "other-unit")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "record bank and cash"):
            record_entry(actor=self.sales, idempotency_key="no", business_unit=self.unit, kind="interest",
                         account_id=bank["id"], amount="1.00")
        new_default = self.account("TEST ONLY CBQ", is_default=True)
        self.assertEqual(list(CashAccount.objects.filter(business_unit=self.unit, is_default=True).values_list("pk", flat=True)),
                         [new_default["id"]])
        fee = CashEntry.objects.get(pk=self.entry("bank_charge", bank, "5.00", "rules-fee")["id"])
        with self.assertRaises(ValidationError):
            fee.save()
        with self.assertRaises(ValidationError):
            fee.delete()
        with transaction.atomic(), self.assertRaises(IntegrityError):
            CashAccount.objects.filter(pk=bank["id"]).update(currency="USD")

    def test_viewing_and_api(self):
        bank = self.account("TEST ONLY QNB current", opening_balance="100.00")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "view the general ledger, record bank and cash or approve"):
            cash_overview(actor=self.sales, business_unit=self.unit)
        UserActionGrant.objects.create(user=self.sales, business_unit=self.unit, action=UserActionGrant.Action.VIEW_LEDGER)
        self.assertFalse(cash_overview(actor=self.sales, business_unit=self.unit)["can_manage"])
        client = APIClient()
        client.force_authenticate(self.admin)
        unit = f"?unit={self.unit.pk}"
        body = {"kind": "bank_charge", "account_id": bank["id"], "amount": "7.00", "entry_date": self.today.isoformat()}
        self.assertEqual(client.post(f"/api/finance/cash/entries/{unit}", body, format="json").status_code, 400)
        posted = client.post(f"/api/finance/cash/entries/{unit}", body, format="json", HTTP_IDEMPOTENCY_KEY="api-fee")
        self.assertEqual(posted.status_code, 201, posted.data)
        refused = client.post(f"/api/finance/cash/accounts/{unit}", {"name": "TEST ONLY Till", "kind": "cash",
                                                                      "opening_date": self.opened}, format="json")
        self.assertEqual(refused.status_code, 403)  # Setup lock (Nov 2026): only the Master Admin opens accounts.
        self.admin.is_superuser = True
        self.admin.save(update_fields=["is_superuser"])
        created = client.post(f"/api/finance/cash/accounts/{unit}", {"name": "TEST ONLY Till", "kind": "cash",
                                                                      "opening_date": self.opened}, format="json")
        self.assertEqual((created.status_code, created.data["is_default"]), (201, True))
        listing = client.get(f"/api/finance/cash/{unit}&account={bank['id']}")
        self.assertEqual((listing.status_code, next(row for row in listing.data["accounts"] if row["id"] == bank["id"])["balance"]),
                         (200, "93.00"))
        viewer = APIClient()
        viewer.force_authenticate(self.roche_user)
        self.assertEqual(viewer.get(f"/api/finance/cash/{unit}").status_code, 403)
