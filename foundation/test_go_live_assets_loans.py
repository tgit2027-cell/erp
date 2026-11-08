"""Go-live import of fixed assets (asset by asset) and staff loans (loan by employee)."""

import datetime
from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase, override_settings
from django.utils import timezone

from .fixed_assets import _charge, _months_between, asset_detail, submit_asset
from .go_live_imports import decide, preview, submit
from .models import BusinessUnitWorkCalendar, FixedAsset, JournalLine, StaffLoan, UserActionGrant
from .staff_loans import balance, due
from .staff_records import save_employee
from . import test_go_live_imports as go_live
from .test_go_live_imports import TB_HEAD, xlsx
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action
ASSET_HEAD = ["Business unit*", "Asset number", "Name*", "Category*", "Purchase date*", "Cost QAR*", "Depreciation to date QAR",
              "Useful life months", "Value at the end QAR", "Supplier", "Monthly depreciation QAR"]
LOAN_HEAD = ["Business unit*", "Staff number*", "Kind", "Still owed QAR*", "Monthly instalment QAR*", "Next instalment month", "Reason"]


@override_settings(APP_ENV="demo")
class AssetAndLoanImportTests(PurchasingFixtureMixin, TestCase):
    setUp = go_live.GoLiveImportTests.setUp

    @classmethod
    def setUpTestData(cls):
        go_live.GoLiveItemImportTests.setUpTestData.__func__(cls)
        for action in (A.MANAGE_STAFF, A.MANAGE_ASSETS):
            UserActionGrant.objects.get_or_create(user=cls.admin, business_unit=cls.unit, action=action)
        BusinessUnitWorkCalendar.objects.update_or_create(business_unit=cls.unit, defaults={
            "closed_weekdays": [4], "closed_dates": [], "is_configured": True})

    def balances_day(self):
        """The last day of the month before last, so the month after it is already over."""
        first = timezone.localdate().replace(day=1)
        return (first - datetime.timedelta(days=1)).replace(day=1) - datetime.timedelta(days=1)

    def ledger(self, code):
        totals = JournalLine.objects.filter(account__code=code, entry__business_unit=self.unit).aggregate(d=Sum("debit"), c=Sum("credit"))
        return (totals["d"] or 0) - (totals["c"] or 0)

    def bring_in(self, kind, head, rows):
        batch = submit(actor=self.admin, kind=kind, file_name=f"TEST ONLY {kind}.xlsx", content=xlsx([head, *rows]),
                       balances_on=self.balances_day().isoformat())
        return decide(actor=self.roche_user, batch_id=batch["id"], action="approve")

    def problems(self, kind, head, rows):
        result = preview(actor=self.admin, kind=kind, file_name="x.xlsx", content=xlsx([head, *rows]),
                         balances_on=self.balances_day().isoformat())
        return {row["row"]: row["problems"] for row in result["problems"]}

    def test_fixed_assets_come_in_one_by_one_and_keep_depreciating(self):
        day = self.balances_day()
        bought = (day.replace(day=1) - datetime.timedelta(days=900)).replace(day=1)
        self.bring_in("fixed_assets", ASSET_HEAD, [
            ["TVL", "tvl-fa-0042", "TEST ONLY Showroom lighting", "Fit-out", bought.isoformat(), "180,000", "57000", "60", "", "Doha Lighting", "2,900"],
            ["TVL", "", "TEST ONLY Delivery van", "vehicle", bought.isoformat(), "90000", "90000", "24", "", ""],
        ])
        lighting = FixedAsset.objects.get(number="TVL-FA-0042")
        van = FixedAsset.objects.get(name="TEST ONLY Delivery van")
        self.assertTrue(van.number.startswith("FA-TVL-"))
        self.assertEqual((lighting.status, lighting.source, lighting.category, lighting.opening_depreciation),
                         (FixedAsset.Status.IN_USE, FixedAsset.Source.OPENING, "fitout", Decimal("57000.00")))
        start = (day + datetime.timedelta(days=1)).replace(day=1)
        self.assertEqual(lighting.depreciation_from, start)
        self.assertEqual(self.ledger("TEST_FIXED_ASSETS"), Decimal("270000.00"))
        self.assertEqual(self.ledger("TEST_ACCUM_DEPRECIATION"), Decimal("-147000.00"))
        self.assertEqual(self.ledger("TEST_OPENING_BALANCES"), Decimal("-123000.00"))
        # The old system's monthly amount carries on until nothing is left; the van is already fully depreciated.
        self.assertEqual(lighting.opening_months_left, 60 - _months_between(bought, day))
        self.assertEqual((lighting.opening_monthly, van.opening_monthly), (Decimal("2900.00"), Decimal("3750.00")))
        self.assertEqual(_charge(lighting, start, Decimal("57000.00")), Decimal("2900.00"))
        self.assertEqual(_charge(van, start, Decimal("90000.00")), Decimal("0.00"))
        schedule = asset_detail(actor=self.admin, business_unit=self.unit, asset_id=lighting.pk)["schedule"]
        self.assertEqual((len(schedule), schedule[0]["month"], schedule[0]["amount"], schedule[-1]["amount"]),
                         (43, start.isoformat()[:7], "2900.00", "1200.00"))  # 42 x 2,900, then the last 1,200
        self.assertEqual(sum(Decimal(row["amount"]) for row in schedule), Decimal("123000.00"))
        with self.assertRaises(InvalidTransaction):  # nobody registers a go-live asset by hand
            submit_asset(actor=self.admin, business_unit=self.unit, data={
                "name": "x", "category": "furniture", "purchase_date": day.isoformat(), "cost": "10", "source": "opening"})

    def test_fixed_asset_rows_are_checked(self):
        day = self.balances_day()
        self.bring_in("fixed_assets", ASSET_HEAD, [["TVL", "OLD-1", "TEST ONLY Desk", "Furniture", "2025-01-01", "1000", "", "", "", ""]])
        problems = self.problems("fixed_assets", ASSET_HEAD, [
            ["TVL", "FA-TVL-00009", "TEST ONLY a", "Furniture", "2025-01-01", "100", "", "", "", ""],
            ["TVL", "old-1", "TEST ONLY b", "Boats", (day + datetime.timedelta(days=1)).isoformat(), "100", "", "", "", ""],
            ["TVL", "X-2", "TEST ONLY c", "Other", "2025-01-01", "100", "101", "", "", ""],
            ["TVL", "X-3", "TEST ONLY d", "Other", "2015-01-01", "100", "10", "12", "", ""],
            ["TVL", "X-3", "", "Other", "2025-01-01", "", "", "0", "", ""],
            ["TVL", "X-4", "TEST ONLY e", "Other", "2015-01-01", "100", "10", "12", "", "", "200"],
        ])
        self.assertEqual(problems[2], ["Asset numbers starting with FA- are the ERP's own. Leave it blank to get one, or use the old number."])
        self.assertEqual(problems[3], ["Asset number OLD-1 is already in the ERP.",
                                       "Category must be Furniture, Fit-out, Vehicles, Computers, Machinery or Other.",
                                       f"Purchase date is after the balances day ({day:%d %b %Y})."])
        self.assertEqual(problems[4], ["Depreciation to date cannot be more than the cost less the value at the end."])
        self.assertEqual(problems[5], ["Its useful life is over by the balances day, so the depreciation to date should be the "
                                       "cost less the value at the end."])
        self.assertEqual(problems[6], ["Asset number X-3 is also on row 5.", "Name is missing.", "Cost QAR is missing.",
                                       "Useful life months must be a whole number from 1 to 600."])
        # With the old monthly amount given, a life that is over is fine: the ERP carries on until nothing is left.
        self.assertEqual(problems[7], ["Monthly depreciation cannot be more than the cost less the value at the end."])

    def test_staff_loans_come_in_by_employee_and_payroll_takes_them(self):
        person = save_employee(actor=self.admin, business_unit=self.unit, data={
            "full_name": "TEST ONLY Ravi Kumar", "job_title": "Driver", "department": "Delivery", "nationality": "Indian",
            "joined_on": "2020-01-01"})
        day = self.balances_day()
        start = (day + datetime.timedelta(days=1)).replace(day=1)
        later = (start + datetime.timedelta(days=40)).replace(day=1)
        problems = self.problems("staff_loans", LOAN_HEAD, [
            ["TVL", "EMP-99999", "Loan", "100", "10", "", ""],
            ["TVL", person["staff_number"], "Gift", "100", "200", day.strftime("%Y-%m"), ""],
        ])
        self.assertEqual(problems[2], ["Nobody in TVL has the staff number EMP-99999. Add them on the Staff page first."])
        self.assertEqual(problems[3], ["Kind must be Loan or Advance.", "The monthly instalment cannot be more than what is still owed.",
                                       "Next instalment month must be after the balances day; the old system took the earlier ones."])
        self.bring_in("staff_loans", LOAN_HEAD, [
            ["TVL", person["staff_number"].lower(), "", "6,000", "1000", "", "TEST ONLY housing deposit"],
            ["TVL", person["staff_number"], "Advance", "1500", "1500", later.strftime("%Y-%m"), ""],
        ])
        loan, advance = StaffLoan.objects.filter(employee_id=person["id"]).order_by("pk")
        self.assertEqual((loan.kind, loan.status, loan.first_month, balance(loan)), ("loan", "repaying", start, Decimal("6000.00")))
        self.assertEqual((advance.kind, advance.first_month, advance.reason), ("advance", later, "Brought over from the old system at go-live"))
        self.assertEqual(self.ledger("TEST_STAFF_LOANS"), Decimal("7500.00"))
        self.assertEqual(self.ledger("TEST_OPENING_BALANCES"), Decimal("-7500.00"))
        self.assertEqual([row.pk for row, _ in due(loan.employee, start)], [loan.pk])
        self.assertEqual([row.pk for row, _ in due(loan.employee, later)], [loan.pk, advance.pk])

    def test_trial_balance_leaves_asset_and_loan_lines_to_their_imports(self):
        before = self.ledger("TEST_FIXED_ASSETS")
        batch = submit(actor=self.admin, kind="trial_balance", file_name="TEST ONLY tb.xlsx", content=xlsx([TB_HEAD,
            ["TVL", "TEST_FIXED_ASSETS", "180000", "", "", ""], ["TVL", "TEST_ACCUM_DEPRECIATION", "", "57000", "", ""],
            ["TVL", "TEST_STAFF_LOANS", "7500", "", "", ""], ["TVL", "TEST_SALARIES_PAYABLE", "", "130500", "", ""]]),
            balances_on=self.balances_day().isoformat())
        decide(actor=self.roche_user, batch_id=batch["id"], action="approve")
        self.assertEqual(self.ledger("TEST_FIXED_ASSETS") - before, 0)
        self.assertEqual(self.ledger("TEST_STAFF_LOANS"), 0)
        self.assertEqual(self.ledger("TEST_OPENING_BALANCES"), Decimal("130500.00"))
