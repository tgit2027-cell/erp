"""Fixed assets: registered, approved by a second person, depreciated monthly, sold or written off; synthetic only."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .cash_accounts import cash_overview, create_account
from .master_admin_testing import by_master_admin
from .financial_statements import balance_sheet, income_statement
from .fixed_assets import (
    approve_asset, asset_detail, assets_overview, dispose_asset, reject_asset, run_depreciation, submit_asset,
)
from .models import (
    DepreciationRun, FixedAsset, IdempotencyRecord, JournalEntry, PurchaseReceipt, StockBalance, StockMovement, UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied
from .test_purchasing_workflows import PurchasingFixtureMixin
from .supplier_bills import record_bill
from .supplier_payments import _received
from .transaction_services import InvalidTransaction, _post_journal


def month_start(day, back=0):
    first = day.replace(day=1)
    for _ in range(back):
        first = (first - datetime.timedelta(days=1)).replace(day=1)
    return first


@override_settings(APP_ENV="demo")
class FixedAssetTests(PurchasingFixtureMixin, TestCase):
    """Admin registers and runs depreciation; sales approves and disposes."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        A = UserActionGrant.Action
        for user, action in [(cls.admin, A.MANAGE_ASSETS), (cls.admin, A.MANAGE_CASH), (cls.admin, A.VIEW_LEDGER),
                             (cls.sales, A.APPROVE_ASSET)]:
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()
        self.start = month_start(self.today, 3)
        self.bank = by_master_admin(create_account)(actor=self.admin, business_unit=self.unit, name="TEST ONLY QNB current", kind="bank",
                                   opening_balance="100000.00", opening_date=month_start(self.today, 12).isoformat())

    def register(self, **data):
        payload = {"name": "TEST ONLY showroom sofas", "category": "furniture", "purchase_date": self.start.isoformat(),
                   "cost": "12000.00", "residual_value": "0", "life_months": "", "account_id": self.bank["id"], **data}
        return submit_asset(actor=self.admin, business_unit=self.unit, data=payload)

    def approve(self, asset):
        return approve_asset(actor=self.sales, business_unit=self.unit, asset_id=asset["id"])

    def depreciate(self, month):
        return run_depreciation(actor=self.admin, business_unit=self.unit, month=month.isoformat()[:7])

    def balance(self, code, sheet=None):
        sheet = sheet or balance_sheet(actor=self.admin, business_unit=self.unit)
        rows = [row for section in sheet["sections"] for row in section["rows"]]
        return next((row["balance"] for row in rows if row.get("code") == code), "0.00")

    def test_a_purchase_is_booked_once_someone_else_approves_it(self):
        asset = self.register()
        self.assertRegex(asset["number"], rf"^FA-{self.unit.code}-00001$")
        self.assertEqual((asset["status"], asset["life_months"], asset["monthly"]), ("submitted", 60, "200.00"),
                         "Furniture defaults to a 5-year life")
        self.assertIsNone(assets_overview(actor=self.admin, business_unit=self.unit)["next_run"],
                          "Nothing to depreciate before approval")
        UserActionGrant.objects.create(user=self.admin, business_unit=self.unit, action=UserActionGrant.Action.APPROVE_ASSET)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Someone other than the person who registered"):
            approve_asset(actor=self.admin, business_unit=self.unit, asset_id=asset["id"])
        approved = self.approve(asset)
        entry = JournalEntry.objects.get(pk=approved["journal_entry_id"])
        self.assertEqual(entry.kind, JournalEntry.Kind.FIXED_ASSET)
        self.assertEqual(sorted((line.account.code, line.debit, line.credit) for line in entry.lines.all()), [
            ("TEST_CASH", Decimal("0.00"), Decimal("12000.00")), ("TEST_FIXED_ASSETS", Decimal("12000.00"), Decimal("0.00")),
        ])
        overview = cash_overview(actor=self.admin, business_unit=self.unit, account_id=self.bank["id"])
        self.assertEqual(overview["accounts"][0]["balance"], "88000.00")
        self.assertEqual(self.balance("TEST_FIXED_ASSETS"), "12000.00")
        statement = income_statement(actor=self.admin, business_unit=self.unit, start=self.start.isoformat(), end=self.today.isoformat())
        self.assertEqual(statement["net_profit"]["period"], "0.00", "Buying an asset is not a cost of the month")

        rejected = reject_asset(actor=self.sales, business_unit=self.unit, asset_id=self.register(name="TEST ONLY van")["id"],
                                reason="TEST ONLY get a second quote")
        self.assertEqual((rejected["status"], rejected["journal_entry_id"]), ("rejected", None))

    def test_depreciation_runs_month_by_month_and_catches_up(self):
        sofas = self.approve(self.register())
        overview = assets_overview(actor=self.admin, business_unit=self.unit)
        self.assertEqual(overview["next_run"], {"month": self.start.isoformat()[:7], "total": "200.00", "assets": 1, "unbilled": []})
        with self.assertRaisesMessage(InvalidTransaction, f"the next month to run is {self.start:%B %Y}"):
            self.depreciate(month_start(self.today, 1))
        self.depreciate(self.start)
        # A laptop bought in the first month but approved after its run is caught up next month.
        laptop = self.approve(self.register(name="TEST ONLY laptop", category="computers", cost="3600.00"))
        self.assertEqual(laptop["life_months"], 36)
        after = self.depreciate(month_start(self.today, 2))
        self.assertEqual(after["last_run"]["total"], "400.00", "200 sofas + 2 x 100 laptop")
        self.depreciate(month_start(self.today, 1))
        with self.assertRaisesMessage(InvalidTransaction, "has not ended yet"):
            self.depreciate(month_start(self.today))
        self.assertIsNone(assets_overview(actor=self.admin, business_unit=self.unit)["next_run"],
                          "The current month is not due until it ends")
        rows = {row["number"]: row for row in assets_overview(actor=self.admin, business_unit=self.unit)["rows"]}
        self.assertEqual((rows[sofas["number"]]["depreciated"], rows[sofas["number"]]["book_value"]), ("600.00", "11400.00"))
        self.assertEqual(rows[laptop["number"]]["depreciated"], "300.00")
        self.assertEqual(self.balance("TEST_ACCUM_DEPRECIATION"), "-900.00", "Accumulated depreciation reduces assets")
        self.assertEqual(DepreciationRun.objects.count(), 3)
        schedule = asset_detail(actor=self.admin, business_unit=self.unit, asset_id=laptop["id"])["schedule"]
        self.assertEqual(len(schedule), 35, "The caught-up month carries two months")
        self.assertEqual(schedule[0]["amount"], "200.00")
        self.assertEqual(sum(Decimal(month["amount"]) for month in schedule), Decimal("3600.00"))
        self.assertEqual([month["posted"] for month in schedule[:4]], [True, True, False, False])
        with self.assertRaisesMessage(WorkflowPermissionDenied, "run depreciation"):
            run_depreciation(actor=self.sales, business_unit=self.unit, month=self.today.isoformat()[:7])

    def test_the_last_month_takes_whatever_is_left(self):
        asset = self.approve(self.register(cost="1000.00", residual_value="100.00", life_months="7"))
        schedule = asset_detail(actor=self.admin, business_unit=self.unit, asset_id=asset["id"])["schedule"]
        self.assertEqual([month["amount"] for month in schedule], ["128.57", "128.57", "128.57", "128.58", "128.57", "128.57", "128.57"])
        self.assertEqual(sum(Decimal(month["amount"]) for month in schedule), Decimal("900.00"))

    def test_the_first_month_counts_only_the_days_after_the_purchase(self):
        days = (month_start(self.today, 2) - self.start).days
        asset = self.approve(self.register(purchase_date=self.start.replace(day=21).isoformat()))
        schedule = asset_detail(actor=self.admin, business_unit=self.unit, asset_id=asset["id"])["schedule"]
        first = (Decimal(200) * (days - 20) / days).quantize(Decimal("0.01"))
        self.assertEqual((schedule[0]["amount"], schedule[1]["amount"]), (str(first), "200.00"))
        self.assertEqual(len(schedule), 61, "The days missed in the first month are used up in month 61")
        self.assertEqual(sum(Decimal(month["amount"]) for month in schedule), Decimal("12000.00"))
        self.depreciate(self.start)
        self.assertEqual(assets_overview(actor=self.admin, business_unit=self.unit)["last_run"]["total"], str(first))

    def test_a_month_waits_until_delivered_goods_are_billed(self):
        self.approve(self.register())
        receipt = PurchaseReceipt.objects.get(pk=self.make_receipt(key="fa-unbilled")["purchase_receipt_id"])
        PurchaseReceipt.objects.filter(pk=receipt.pk).update(created_at=timezone.make_aware(
            datetime.datetime.combine(self.start.replace(day=15), datetime.time(10))))
        next_run = assets_overview(actor=self.admin, business_unit=self.unit)["next_run"]
        self.assertEqual([row["receipt_id"] for row in next_run["unbilled"]], [receipt.pk])
        with self.assertRaisesMessage(InvalidTransaction, f"Record the supplier bills for every delivery up to the end of {self.start:%B %Y}"):
            self.depreciate(self.start)
        UserActionGrant.objects.get_or_create(user=self.admin, business_unit=self.unit, action=UserActionGrant.Action.PAY_SUPPLIER)
        record_bill(actor=self.admin, idempotency_key="fa-bill", receipt_id=receipt.pk, bill_number="TEST-FA-1",
                    bill_date=self.start.replace(day=16).isoformat(), amount=_received(receipt).payable)
        self.assertEqual(assets_overview(actor=self.admin, business_unit=self.unit)["next_run"]["unbilled"], [])
        self.assertEqual(self.depreciate(self.start)["last_run"]["total"], "200.00")

    def test_a_finished_construction_moves_from_assets_under_construction(self):
        with transaction.atomic():
            operation = IdempotencyRecord.objects.create(key="fa-auc", operation="test_fixed_assets", payload_hash="0" * 64,
                                                         status=IdempotencyRecord.Status.COMPLETE, completed_at=timezone.now())
            _post_journal(legal_entity=self.unit.legal_entity, business_unit=self.unit, idempotency=operation,
                          kind=JournalEntry.Kind.RECEIPT, description="TEST ONLY showroom fit-out costs",
                          document_date=self.start, lines=[("TEST_ASSETS_UNDER_CONSTRUCTION", Decimal("30000.00"), Decimal("0.00"), "x"),
                                                           ("TEST_CASH", Decimal("0.00"), Decimal("30000.00"), "x")])
        self.assertEqual(assets_overview(actor=self.admin, business_unit=self.unit)["under_construction"], "30000.00")
        with self.assertRaisesMessage(InvalidTransaction, "Only 30,000.00 QAR is waiting on assets under construction"):
            self.register(source="construction", cost="31000.00", account_id=None)
        built = self.register(name="TEST ONLY showroom fit-out", source="construction", cost="24000.00", account_id=None,
                              category="furniture")
        self.assertEqual((built["source"], built["paid_from"]), ("construction", None))
        self.assertEqual(assets_overview(actor=self.admin, business_unit=self.unit)["under_construction"], "6000.00",
                         "The waiting asset already claims its share")
        approved = self.approve(built)
        lines = sorted((line.account.code, line.debit, line.credit)
                       for line in JournalEntry.objects.get(pk=approved["journal_entry_id"]).lines.all())
        self.assertEqual(lines, [("TEST_ASSETS_UNDER_CONSTRUCTION", Decimal("0.00"), Decimal("24000.00")),
                                 ("TEST_FIXED_ASSETS", Decimal("24000.00"), Decimal("0.00"))])
        self.assertEqual(self.balance("TEST_ASSETS_UNDER_CONSTRUCTION"), "6000.00")
        self.assertEqual(self.balance("TEST_FIXED_ASSETS"), "24000.00")

    def test_stock_taken_for_the_company_becomes_an_asset(self):
        receipt = PurchaseReceipt.objects.get(pk=self.make_receipt(key="fa-stock", quantity="4.00", unit_cost="1000.00",
                                                                     charges=[])["purchase_receipt_id"])
        UserActionGrant.objects.get_or_create(user=self.admin, business_unit=self.unit, action=UserActionGrant.Action.PAY_SUPPLIER)
        record_bill(actor=self.admin, idempotency_key="fa-stock-bill", receipt_id=receipt.pk, bill_number="TEST-FA-2",
                    bill_date=self.today.isoformat(), amount=_received(receipt).payable)
        balance = StockBalance.objects.get(business_unit=self.unit, variant=self.variant, location=self.location)
        before_quantity, before_value = balance.on_hand_quantity, balance.on_hand_value
        unit_value = (before_value / before_quantity).quantize(Decimal("0.01"))
        option = next(row for row in assets_overview(actor=self.admin, business_unit=self.unit)["stock_options"]
                      if row["variant_id"] == self.variant.pk and row["location_id"] == self.location.pk)
        self.assertEqual(option["unit_value"], str(unit_value))
        with self.assertRaisesMessage(InvalidTransaction, "is free at"):
            self.register(source="stock", variant_id=self.variant.pk, location_id=self.location.pk, quantity="100000", account_id=None)
        asset = self.register(name="TEST ONLY office sofa", source="stock", variant_id=self.variant.pk, location_id=self.location.pk,
                              quantity="1", cost="", account_id=None, purchase_date=self.today.isoformat())
        self.assertEqual(asset["source"], "stock")
        approved = self.approve(asset)
        balance.refresh_from_db()
        self.assertEqual(balance.on_hand_quantity, before_quantity - 1)
        moved = before_value - balance.on_hand_value
        self.assertEqual(Decimal(approved["cost"]), moved)
        self.assertEqual(StockMovement.objects.filter(balance=balance, kind=StockMovement.Kind.CAPITALIZED).count(), 1)
        lines = sorted((line.account.code, line.debit, line.credit)
                       for line in JournalEntry.objects.get(pk=approved["journal_entry_id"]).lines.all())
        self.assertEqual(lines, [("TEST_FIXED_ASSETS", moved, Decimal("0.00")), ("TEST_INVENTORY", Decimal("0.00"), moved)])

    def test_selling_records_a_gain_or_loss_and_writing_off_needs_depreciation_first(self):
        sofas = self.approve(self.register())
        van = self.approve(self.register(name="TEST ONLY van", category="vehicles", cost="60000.00"))
        sale_day = month_start(self.today, 1).replace(day=5)
        with self.assertRaisesMessage(InvalidTransaction, "Run depreciation up to"):
            dispose_asset(actor=self.sales, business_unit=self.unit, asset_id=sofas["id"], date=sale_day.isoformat(),
                          reason="TEST ONLY damaged")
        self.depreciate(self.start)
        self.depreciate(month_start(self.today, 2))
        with self.assertRaisesMessage(WorkflowPermissionDenied, "'approve fixed assets'"):
            dispose_asset(actor=self.admin, business_unit=self.unit, asset_id=sofas["id"], date=sale_day.isoformat(), reason="x")
        with self.assertRaisesMessage(InvalidTransaction, "reason"):
            dispose_asset(actor=self.sales, business_unit=self.unit, asset_id=sofas["id"], date=sale_day.isoformat(), reason=" ")
        written_off = dispose_asset(actor=self.sales, business_unit=self.unit, asset_id=sofas["id"], date=sale_day.isoformat(),
                                    reason="TEST ONLY water damage")
        self.assertEqual(written_off["status"], "disposed")
        entry = JournalEntry.objects.get(pk=written_off["disposal"]["journal_entry_id"])
        self.assertEqual(sorted((line.account.code, line.debit, line.credit) for line in entry.lines.all()), [
            ("TEST_ACCUM_DEPRECIATION", Decimal("400.00"), Decimal("0.00")),
            ("TEST_ASSET_DISPOSAL_LOSS", Decimal("11600.00"), Decimal("0.00")),
            ("TEST_FIXED_ASSETS", Decimal("0.00"), Decimal("12000.00")),
        ])
        sold = dispose_asset(actor=self.sales, business_unit=self.unit, asset_id=van["id"], date=sale_day.isoformat(),
                             proceeds="62000.00", account_id=self.bank["id"], reason="TEST ONLY sold to TEST ONLY buyer")
        self.assertEqual(sold["disposal"]["proceeds"], "62000.00")
        lines = {line.account.code: (line.debit, line.credit)
                 for line in JournalEntry.objects.get(pk=sold["disposal"]["journal_entry_id"]).lines.all()}
        self.assertEqual(lines["TEST_ASSET_DISPOSAL_GAIN"], (Decimal("0.00"), Decimal("4000.00")), "62,000 - (60,000 - 2,000)")
        self.assertEqual(self.balance("TEST_FIXED_ASSETS"), "0.00")
        self.assertEqual(self.balance("TEST_ACCUM_DEPRECIATION"), "0.00")
        nothing_left = self.depreciate(month_start(self.today, 1))
        self.assertEqual(nothing_left["last_run"]["total"], "0.00", "Disposed assets are not depreciated")
        with self.assertRaisesMessage(InvalidTransaction, "Only an asset in use"):
            dispose_asset(actor=self.sales, business_unit=self.unit, asset_id=van["id"], date=sale_day.isoformat(), reason="again")

    def test_bad_input_is_refused(self):
        with self.assertRaisesMessage(InvalidTransaction, "Choose a category"):
            self.register(category="spaceship")
        with self.assertRaisesMessage(InvalidTransaction, "less than the cost"):
            self.register(residual_value="12000")
        with self.assertRaisesMessage(InvalidTransaction, "between 1 and 600"):
            self.register(life_months="0")
        with self.assertRaises(InvalidTransaction):
            self.register(purchase_date=(self.today + datetime.timedelta(days=3)).isoformat())
        with self.assertRaisesMessage(InvalidTransaction, "after this date"):
            self.register(purchase_date=month_start(self.today, 14).isoformat())
        with self.assertRaisesMessage(WorkflowPermissionDenied, "'register fixed assets'"):
            submit_asset(actor=self.sales, business_unit=self.unit, data={})
        with self.assertRaisesMessage(WorkflowPermissionDenied, "permission"):
            assets_overview(actor=self.roche_user, business_unit=self.unit)
        with self.assertRaises(IntegrityError), transaction.atomic():
            FixedAsset.objects.create(business_unit=self.unit, number="FA-X", name="x", category="other", purchase_date=self.today,
                                      cost=Decimal("10"), residual_value=Decimal("10"), life_months=12,
                                      paid_from_id=self.bank["id"], submitted_by=self.admin)

    def test_api_round_trip(self):
        manager, approver = APIClient(), APIClient()
        manager.force_authenticate(self.admin)
        approver.force_authenticate(self.sales)
        unit = f"?unit={self.unit.pk}"
        created = manager.post(f"/api/finance/fixed-assets/{unit}", {
            "name": "TEST ONLY POS terminals", "category": "computers", "purchase_date": self.start.isoformat(),
            "cost": "4500", "account_id": self.bank["id"]}, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(manager.post(f"/api/finance/fixed-assets/{created.data['id']}/{unit}", {"decision": "approve"},
                                      format="json").status_code, 403)
        self.assertEqual(approver.post(f"/api/finance/fixed-assets/{created.data['id']}/{unit}", {"decision": "approve"},
                                       format="json").status_code, 200)
        ran = manager.post(f"/api/finance/fixed-assets/depreciation/{unit}", {"month": self.start.isoformat()[:7]}, format="json")
        self.assertEqual(ran.status_code, 200, ran.data)
        self.assertEqual(ran.data["last_run"]["total"], "125.00")
        detail = manager.get(f"/api/finance/fixed-assets/{created.data['id']}/{unit}")
        self.assertEqual(detail.data["schedule"][0], {"month": self.start.isoformat()[:7], "amount": "125.00", "posted": True})
        self.assertEqual(approver.post(f"/api/finance/fixed-assets/{created.data['id']}/{unit}", {"decision": "fly"},
                                       format="json").status_code, 400)
        outsider = APIClient()
        outsider.force_authenticate(self.roche_user)
        self.assertEqual(outsider.get(f"/api/finance/fixed-assets/{unit}").status_code, 403)
