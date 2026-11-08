"""Commission and rewards: the year's records, the four-person run and payment through payroll; synthetic only."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import incentives as inc
from .models import (
    Budget, BudgetAmount, BusinessUnitWorkCalendar, DemoUserMarker, IdempotencyRecord, IncentiveEntitlement, IncentiveInstalment,
    IncentiveRun, JournalEntry, JournalLine, UserActionGrant,
)
from .pay_records import set_pay
from .payroll import approve_payroll, prepare_payroll
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import save_employee
from .test_payroll import AISHA_IBAN, valid
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction, _post_journal

A = UserActionGrant.Action


@override_settings(APP_ENV="demo")
class IncentiveTests(PurchasingFixtureMixin, TestCase):
    """admin prepares (Finance), hr checks, roche_user approves (management), payroll_lead releases and approves payroll.

    Aisha (the demo salesperson) is on sales commission; Omar on a performance reward; Layla is proposed only.
    """

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        cls.year = timezone.localdate().year - 1
        users = get_user_model().objects
        cls.hr = users.create_user(username="test_incentive_hr", password="unused-test-password", first_name="Hana")
        cls.payroll_lead = users.create_user(username="test_incentive_payroll", password="unused-test-password", first_name="Paul")
        for user in (cls.hr, cls.payroll_lead):
            DemoUserMarker.objects.create(user=user)
        for user, action in ((cls.admin, A.PREPARE_INCENTIVES), (cls.admin, A.CHECK_INCENTIVES), (cls.admin, A.MANAGE_PAY), (cls.admin, A.MANAGE_STAFF),
                             (cls.hr, A.CHECK_INCENTIVES), (cls.roche_user, A.APPROVE_INCENTIVES),
                             (cls.payroll_lead, A.APPROVE_PAY)):
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)
        BusinessUnitWorkCalendar.objects.update_or_create(business_unit=cls.unit, defaults={
            "closed_weekdays": [4], "closed_dates": [], "is_configured": True})

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)

    # ------------------------------------------------------------ helpers

    def call(self, service, actor=None, **data):
        return service(actor=actor or self.admin, business_unit=self.unit, data={"year": self.year, **data})

    def staff(self, name, user=None):
        person = save_employee(actor=self.admin, business_unit=self.unit, data={
            "full_name": f"TEST ONLY {name}", "job_title": "Sales" if user else "Hub coordinator", "department": "Showroom",
            "nationality": "Indian", "joined_on": "2020-01-01", **({"user_id": user.pk} if user else {})})
        set_pay(actor=self.admin, business_unit=self.unit, data={
            "employee_id": person["id"], "effective_from": "2020-01", "bank_name": "TEST ONLY bank", "iban": valid(AISHA_IBAN),
            "reason": "TEST ONLY offer letter", "basic": "6000"})
        return person["id"]

    def post(self, key, lines):
        with transaction.atomic():
            operation = IdempotencyRecord.objects.create(key=key, operation="test_incentives", payload_hash="0" * 64,
                                                         status=IdempotencyRecord.Status.COMPLETE, completed_at=timezone.now())
            _post_journal(legal_entity=self.unit.legal_entity, business_unit=self.unit, idempotency=operation,
                          kind=JournalEntry.Kind.RECEIPT, description=f"TEST ONLY {key}", document_date=datetime.date(self.year, 6, 15),
                          lines=lines)

    def set_up_year(self):
        self.call(inc.open_year)
        budget = Budget.objects.create(business_unit=self.unit, year=self.year, status=Budget.Status.APPROVED, prepared_by=self.admin,
                                       approved_by=self.roche_user, approved_at=timezone.now())
        for month in range(1, 13):
            BudgetAmount.objects.create(budget=budget, line="sales", month=month, amount=Decimal("100000"))
            BudgetAmount.objects.create(budget=budget, line="cost_of_sales", month=month, amount=Decimal("60000"))
        self.post("revenue", [("TEST_RECEIVABLE", Decimal("1260000"), Decimal("0"), "TEST ONLY"),
                              ("TEST_REVENUE", Decimal("0"), Decimal("1260000"), "TEST ONLY")])
        self.post("cost", [("TEST_COGS", Decimal("750000"), Decimal("0"), "TEST ONLY"),
                           ("TEST_INVENTORY", Decimal("0"), Decimal("750000"), "TEST ONLY")])
        view = self.call(inc.save_unit, unit_id=self.unit.pk, collections_target="1000000", collections_actual="900000",
                         operational_achievement="100", minimum_margin="35")
        unit_row = view["units"][0]
        self.assertFalse(unit_row["validated"])
        self.assertEqual((unit_row["figures"]["revenue_target"], unit_row["figures"]["gp_actual"]), ("1200000.00", "510000.00"))
        self.call(inc.validate_unit, id=unit_row["id"])
        self.call(inc.save_company, ebitda_target="300000", ebitda_actual="285000",
                  operational={"collections": "100", "gp_margin": "90", "inventory": "80", "strategic": "110"})

    def propose(self, employee, **plan):
        return self.call(inc.save_entitlement, employee=employee, unit_id=self.unit.pk, **plan)

    def plan_id(self, view, employee):
        return next(row["id"] for row in view["entitlements"] if row["employee"] == employee and row["status"] != "withdrawn")

    def line(self, lines, employee):
        return next(line for line in lines if line["employee"] == employee)

    # ------------------------------------------------------------ tests

    def test_central_figures_score_the_company_and_missing_data_stays_pending(self):
        self.call(inc.open_year)
        view = inc.overview(actor=self.admin, business_unit=self.unit, year=self.year)
        self.assertEqual(view["policy_status"], "provisional")
        self.assertEqual(view["policy"]["reward_bands"]["PR3"], "3000")
        self.assertTrue(any("No revenue-generating unit" in reason for reason in view["company"]["pending"]))
        with self.assertRaisesMessage(InvalidTransaction, "already set up"):
            self.call(inc.open_year)
        self.call(inc.save_unit, unit_id=self.unit.pk)
        unit_row = self.call(inc.save_company)["units"][0]
        self.assertIn("has no approved budget", unit_row["pending"][0])
        with self.assertRaisesMessage(InvalidTransaction, "nothing to validate"):
            self.call(inc.validate_unit, id=unit_row["id"])
        Budget.objects.filter(business_unit=self.unit).delete()
        IncentiveEntitlement.objects.all().delete()
        self.set_up_year_after_open()

    def set_up_year_after_open(self):
        budget = Budget.objects.create(business_unit=self.unit, year=self.year, status=Budget.Status.APPROVED, prepared_by=self.admin,
                                       approved_by=self.roche_user, approved_at=timezone.now())
        BudgetAmount.objects.create(budget=budget, line="sales", month=1, amount=Decimal("1000000"))
        view = self.call(inc.save_unit, unit_id=self.unit.pk, collections_target="10", collections_actual="10", operational_achievement="100")
        self.call(inc.validate_unit, id=view["units"][0]["id"])
        self.post("later sale", [("TEST_RECEIVABLE", Decimal("50"), Decimal("0"), "TEST ONLY"),
                                 ("TEST_REVENUE", Decimal("0"), Decimal("50"), "TEST ONLY")])
        unit_row = self.call(inc.save_company)["units"][0]
        self.assertFalse(unit_row["validated"])
        self.assertIn("since they changed", unit_row["pending"][-1])

    def test_plans_are_approved_by_someone_else_and_never_overlap(self):
        self.call(inc.open_year)
        aisha = self.staff("Aisha Rahman", user=self.sales)
        view = self.propose(aisha, plan="sales_commission", channel="retail", revenue_target="1000000", gp_target="300000")
        plan = self.plan_id(view, aisha)
        with self.assertRaisesMessage(InvalidTransaction, "already has a plan covering"):
            self.propose(aisha, plan="performance_reward", reward_band="PR1", category="hub_admin", first_month=6, last_month=12)
        with self.assertRaisesMessage(InvalidTransaction, "support functions are not given one"):
            self.propose(self.staff("Omar Said"), plan="performance_reward", channel="retail")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "someone else must approve"):
            self.call(inc.decide_entitlement, id=plan, decision="approve")
        with self.assertRaises(WorkflowPermissionDenied):  # cannot approve plans at all
            self.call(inc.decide_entitlement, actor=self.payroll_lead, id=plan, decision="approve")
        view = self.call(inc.decide_entitlement, actor=self.hr, id=plan, decision="approve")
        self.assertEqual(next(row for row in view["entitlements"] if row["id"] == plan)["approved_by"], "Hana")
        # Changing a plan's terms sends it back for approval; entering results does not.
        view = self.call(inc.save_entitlement, id=plan, employee=aisha, unit_id=self.unit.pk, plan="sales_commission", channel="retail",
                         revenue_target="1000000", gp_target="300000", revenue_actual="900000", gp_actual="250000",
                         collection_fraction="0.9")
        self.assertEqual(next(row for row in view["entitlements"] if row["id"] == plan)["status"], "approved")
        view = self.call(inc.save_entitlement, id=plan, employee=aisha, unit_id=self.unit.pk, plan="sales_commission", channel="retail",
                         revenue_target="800000", gp_target="300000")
        self.assertEqual(next(row for row in view["entitlements"] if row["id"] == plan)["status"], "proposed")
        # Supervisor shares of one team pool cannot pass 100%.
        team = self.call(inc.save_team, code="T1", name="TEST ONLY showroom team", unit_id=self.unit.pk,
                         revenue_target="2000000", gp_target="600000", collection_fraction="1")["teams"][0]["id"]
        self.propose(self.staff("Sami Lead"), plan="supervisor_hybrid", channel="retail", team=team, team_share="70")
        with self.assertRaisesMessage(InvalidTransaction, "cannot be more than 100%"):
            self.propose(self.staff("Rana Lead"), plan="supervisor_hybrid", channel="retail", team=team, team_share="40")

    def test_the_run_is_prepared_checked_approved_released_and_paid_by_payroll(self):
        self.set_up_year()
        aisha = self.staff("Aisha Rahman", user=self.sales)
        omar = self.staff("Omar Said")
        layla = self.staff("Layla Haddad")
        self.propose(aisha, plan="sales_commission", channel="retail", revenue_target="1000000", gp_target="300000",
                     revenue_actual="1250000", gp_actual="288000", collection_fraction="0.8")
        self.propose(omar, plan="performance_reward", reward_band="PR3", category="hub_admin", rating="meets")
        view = self.propose(layla, plan="performance_reward", reward_band="PR1", category="operations", rating="meets")
        for person in (aisha, omar):
            self.call(inc.decide_entitlement, actor=self.hr, id=self.plan_id(view, person), decision="approve")
        view = inc.overview(actor=self.admin, business_unit=self.unit, year=self.year)
        self.assertEqual((view["company"]["cpi"], view["company"]["company_factor"]), ("97.75", "90"))
        self.assertEqual(view["units"][0]["unit_factor"], "101.94")

        self.call(inc.open_year, year=self.year + 1)
        with self.assertRaisesMessage(InvalidTransaction, "once the year is over"):
            inc.prepare_run(actor=self.admin, business_unit=self.unit, data={"year": self.year + 1})
        run = self.call(inc.prepare_run)["runs"][0]
        self.assertEqual(self.line(run["lines"], aisha)["amount"], "6786.00")
        # 3,000 x (50% x 90 + 25% x 101.9375 + 25% x 100) = 2,864.53
        self.assertEqual(self.line(run["lines"], omar)["amount"], "2864.53")
        self.assertEqual(self.line(run["lines"], layla)["pending"], ["This plan is not approved yet."])
        self.assertEqual((run["commission_total"], run["reward_total"], run["pending_count"]), ("6786.00", "2864.53", 1))

        with self.assertRaisesMessage(WorkflowPermissionDenied, "someone else must check"):
            self.call(inc.check_run, run_id=run["id"])
        run = self.call(inc.check_run, actor=self.hr, run_id=run["id"])["runs"][0]
        UserActionGrant.objects.create(user=self.hr, business_unit=self.unit, action=A.APPROVE_INCENTIVES)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "someone else must approve"):
            self.call(inc.approve_run, actor=self.hr, run_id=run["id"])
        with self.assertRaises(WorkflowPermissionDenied):  # the salesperson it pays has no approval permission, and is paid by it
            self.call(inc.approve_run, actor=self.sales, run_id=run["id"])
        approved = self.call(inc.approve_run, actor=self.roche_user, run_id=run["id"])["runs"][0]
        self.assertEqual(approved["status"], "approved")
        entry = IncentiveRun.objects.get(pk=run["id"]).journal_entries.get()
        lines = JournalLine.objects.filter(entry=entry)
        self.assertEqual(lines.get(account__code="TEST_EXP_COMMISSION").debit, Decimal("6786.00"))
        self.assertEqual(lines.get(account__code="TEST_EXP_REWARDS").debit, Decimal("2864.53"))
        self.assertEqual(lines.get(account__code="TEST_COMMISSION_PAYABLE").credit, Decimal("9650.53"))
        with self.assertRaisesMessage(InvalidTransaction, "frozen"):
            self.call(inc.save_company, ebitda_target="1")
        with self.assertRaisesMessage(InvalidTransaction, "in an approved run"):
            self.call(inc.decide_entitlement, id=self.plan_id(view, aisha), decision="withdraw")

        UserActionGrant.objects.create(user=self.roche_user, business_unit=self.unit, action=A.APPROVE_PAY)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "someone else must release"):
            self.call(inc.release_run, actor=self.roche_user, run_id=run["id"])
        with self.assertRaisesMessage(InvalidTransaction, "Set the month fixed rewards are paid in"):
            self.call(inc.release_run, actor=self.payroll_lead, run_id=run["id"])
        april = f"{self.year + 1}-04"
        self.call(inc.set_reward_month, actor=self.payroll_lead, reward_payment_month=april)
        released = self.call(inc.release_run, actor=self.payroll_lead, run_id=run["id"])["runs"][0]
        self.assertEqual(released["status"], "released")
        self.assertEqual([(row["employee"], row["month"], row["amount"]) for row in released["instalments"]], [
            ("TEST ONLY Aisha Rahman", april, "2714.40"), ("TEST ONLY Omar Said", april, "2864.53"),
            ("TEST ONLY Aisha Rahman", f"{self.year + 1}-08", "2035.80"), ("TEST ONLY Aisha Rahman", f"{self.year + 1}-12", "2035.80")])

        payroll = prepare_payroll(actor=self.admin, business_unit=self.unit, data={"month": april})
        paid = {row["employee"]["id"]: row for row in payroll["lines"]}
        self.assertEqual(paid[aisha]["net"], "8714.40")
        self.assertEqual(paid[omar]["net"], "8864.53")
        self.assertEqual(paid[layla]["net"], "6000.00")
        self.assertEqual(payroll["totals"]["commission"], "5578.93")
        posted = approve_payroll(actor=self.payroll_lead, business_unit=self.unit, run_id=payroll["id"])
        paid_out = JournalLine.objects.filter(entry_id=posted["journal_entry"])
        self.assertEqual(paid_out.get(account__code="TEST_COMMISSION_PAYABLE").debit, Decimal("5578.93"))
        self.assertEqual(IncentiveInstalment.objects.filter(paid_in__isnull=False).count(), 2)
        self.assertEqual(IncentiveRun.objects.get(pk=run["id"]).status, IncentiveRun.Status.RELEASED)  # Aug and Dec to come

        # Layla's plan is approved later: a second run picks up only her.
        self.call(inc.decide_entitlement, actor=self.hr, id=self.plan_id(view, layla), decision="approve")
        second = self.call(inc.prepare_run)["runs"][0]
        self.assertEqual((second["number"], [line["employee"] for line in second["lines"]]), (2, [layla]))

        # Aisha sees only her own statement.
        mine = inc.overview(actor=self.sales, business_unit=self.unit, year=self.year)
        self.assertFalse(mine["full"])
        self.assertNotIn("entitlements", mine)
        self.assertEqual([row["amount"] for row in mine["mine"]["instalments"]], ["2714.40", "2035.80", "2035.80"])
        self.assertEqual(mine["mine"]["lines"][0]["amount"], "6786.00")

    def test_a_changed_input_must_be_prepared_again_and_the_api(self):
        self.set_up_year()
        omar = self.staff("Omar Said")
        view = self.propose(omar, plan="performance_reward", reward_band="PR3", category="hub_admin", rating="meets")
        self.call(inc.decide_entitlement, actor=self.hr, id=self.plan_id(view, omar), decision="approve")
        run = self.call(inc.prepare_run)["runs"][0]
        self.call(inc.save_company, ebitda_target="300000", ebitda_actual="200000",
                  operational={"collections": "100", "gp_margin": "90", "inventory": "80", "strategic": "110"})
        with self.assertRaisesMessage(InvalidTransaction, "Prepare it again"):
            self.call(inc.check_run, actor=self.hr, run_id=run["id"])
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.post(f"/api/hr/incentives/?unit={self.unit.pk}", {"action": "prepare", "year": self.year}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["runs"][0]["lines"][0]["amount"], "1889.53")  # EBITDA 66.7% scores 30, CPI 73.75 gives 25%
        client.force_authenticate(self.hr)
        self.assertEqual(client.post(f"/api/hr/incentives/?unit={self.unit.pk}",
                                     {"action": "check", "year": self.year, "run_id": run["id"]}, format="json").status_code, 200)
        self.assertEqual(client.post(f"/api/hr/incentives/?unit={self.unit.pk}", {"action": "nope"}, format="json").status_code, 400)
        # Policy versions: a change makes a new provisional version, approved by someone else.
        policy = response.json()["policy"]
        policy["reward_bands"]["PR3"] = "3100"
        self.assertEqual(client.post(f"/api/hr/incentives/?unit={self.unit.pk}",
                                     {"action": "policy", "year": self.year, "policy": policy}, format="json").status_code, 403)
        self.admin.is_superuser = True  # Setup lock (Nov 2026): only the Master Admin changes the policy.
        self.admin.save(update_fields=["is_superuser"])
        client.force_authenticate(self.admin)
        policy = response.json()["policy"]
        policy["reward_bands"]["PR3"] = "3100"
        self.assertEqual(client.post(f"/api/hr/incentives/?unit={self.unit.pk}",
                                     {"action": "policy", "year": self.year, "policy": policy}, format="json").status_code, 200)
        with self.assertRaises(WorkflowPermissionDenied):
            self.call(inc.approve_policy)
        view = self.call(inc.approve_policy, actor=self.roche_user)
        self.assertEqual((view["policy_version"], view["policy_status"]), (2, "approved"))

    def test_a_supervisors_team_part_counts_only_the_salespeoples_sales(self):
        self.set_up_year()
        team = self.call(inc.save_team, code="T1", name="TEST ONLY team", unit_id=self.unit.pk, revenue_target="2000000",
                         gp_target="600000", collection_fraction="1")["teams"][0]["id"]
        seller, lead = self.staff("Aisha Rahman", user=self.sales), self.staff("Sami Lead")
        self.propose(seller, plan="sales_commission", channel="retail", revenue_target="1000000", gp_target="300000",
                     revenue_actual="2200000", gp_actual="600000", collection_fraction="1", team=team)
        view = self.propose(lead, plan="supervisor_hybrid", channel="retail", revenue_target="500000", gp_target="150000",
                            revenue_actual="400000", gp_actual="150000", collection_fraction="1", team=team, team_share="100")
        for person in (seller, lead):
            self.call(inc.decide_entitlement, actor=self.hr, id=self.plan_id(view, person), decision="approve")
        lines = inc.overview(actor=self.admin, business_unit=self.unit, year=self.year)["preview"]["lines"]
        # Team pool on 2,200,000 against 2,000,000 (the lead's 400,000 is not added): 14,000 + 200,000 x 0.85% = 15,700; 60% of it.
        self.assertEqual(self.line(lines, lead)["amount"], "9420.00")
