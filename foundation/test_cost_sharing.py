"""Cost sharing between business units: prepared, approved by a second person, posted in every unit; synthetic only."""

from decimal import Decimal
from unittest.mock import patch

from django.db.models import Sum
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .cost_sharing import decide_share, prepare_share, share_detail, sharing_overview
from .models import BusinessUnit, ClosedMonth, JournalEntry, JournalLine, LegalEntity, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction, _begin_idempotent_operation, _complete, _post_journal

A = UserActionGrant.Action


@override_settings(APP_ENV="demo")
class CostSharingTests(PurchasingFixtureMixin, TestCase):
    """Tivoli booked the rent; admin prepares shares and sales approves them."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        cls.natuzzi = BusinessUnit.objects.get(code="NAT")
        for user, action in [(cls.admin, A.PREPARE_JOURNAL), (cls.sales, A.APPROVE_JOURNAL)]:
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.today = timezone.localdate()
        self.book("TEST_EXP_RENT", "30000.00")

    def book(self, code, amount, unit=None):
        unit = unit or self.unit
        operation, _ = _begin_idempotent_operation(f"test-cost-{code}-{unit.pk}-{amount}", "test_cost", {"code": code})
        entry = _post_journal(legal_entity=unit.legal_entity, business_unit=unit, idempotency=operation,
                              kind=JournalEntry.Kind.EXPENSE, description="TEST ONLY cost",
                              lines=[(code, Decimal(amount), Decimal("0"), "cost"), ("TEST_CASH", Decimal("0"), Decimal(amount), "paid")],
                              document_date=self.today)
        _complete(operation, {"journal_entry_id": entry.pk})

    def balance(self, unit, code):
        sums = JournalLine.objects.filter(entry__business_unit=unit, account__code=code, entry__status=JournalEntry.Status.POSTED
                                          ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
        return (sums["debit"] or Decimal("0")) - (sums["credit"] or Decimal("0"))

    def share(self, actor=None, **data):
        payload = {"account_code": "TEST_EXP_RENT", "date": self.today.isoformat(), "amount": "30000.00",
                   "description": "TEST ONLY rent for the shared building",
                   "units": [{"business_unit_id": self.roche.pk, "percent": "35"},
                             {"business_unit_id": self.natuzzi.pk, "percent": "25"}], **data}
        return prepare_share(actor=actor or self.admin, business_unit=self.unit, data=payload)

    def test_a_share_moves_each_units_part_only_after_someone_else_approves_it(self):
        share = self.share()
        self.assertRegex(share["number"], rf"^CS-{self.unit.code}-{self.today.year}-00001$")
        self.assertEqual((share["status"], share["moved"], share["kept"], share["kept_percent"]),
                         ("submitted", "18000.00", "12000.00", "40.00"))
        self.assertEqual([line["amount"] for line in share["lines"]], ["10500.00", "7500.00"])
        self.assertEqual(self.balance(self.roche, "TEST_EXP_RENT"), 0, "Nothing moves before approval")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "'approve journal entries' permission"):
            decide_share(actor=self.admin, business_unit=self.unit, share_id=share["id"], action="approve")
        UserActionGrant.objects.create(user=self.admin, business_unit=self.unit, action=A.APPROVE_JOURNAL)
        with self.assertRaisesMessage(WorkflowPermissionDenied, "someone else must approve"):
            decide_share(actor=self.admin, business_unit=self.unit, share_id=share["id"], action="approve")
        share = decide_share(actor=self.sales, business_unit=self.unit, share_id=share["id"], action="approve")
        self.assertEqual(share["status"], "approved")
        self.assertEqual(self.balance(self.unit, "TEST_EXP_RENT"), Decimal("12000.00"))
        self.assertEqual(self.balance(self.roche, "TEST_EXP_RENT"), Decimal("10500.00"))
        self.assertEqual(self.balance(self.natuzzi, "TEST_EXP_RENT"), Decimal("7500.00"))
        company = JournalLine.objects.filter(account__code="TEST_UNIT_SHARING").aggregate(debit=Sum("debit"), credit=Sum("credit"))
        self.assertEqual(company["debit"], company["credit"], "The sharing account nets to zero across the company")
        self.assertEqual(JournalEntry.objects.filter(kind=JournalEntry.Kind.COST_SHARE).count(), 3)
        with self.assertRaises(InvalidTransaction):
            decide_share(actor=self.sales, business_unit=self.unit, share_id=share["id"], action="approve")
        # The receiving unit sees what it received.
        UserActionGrant.objects.create(user=self.sales, business_unit=self.roche, action=A.APPROVE_JOURNAL)
        received = sharing_overview(actor=self.sales, business_unit=self.roche)
        self.assertEqual((received["totals"]["received"], received["shares"][0]["direction"], received["shares"][0]["received"]),
                         ("10500.00", "received", "10500.00"))
        self.assertFalse(received["shares"][0]["can_decide"])
        sent = sharing_overview(actor=self.admin, business_unit=self.unit)
        self.assertEqual(sent["totals"]["sent"], "18000.00")
        rent = next(row for row in sent["accounts"] if row["code"] == "TEST_EXP_RENT")
        self.assertEqual(rent["available"], "12000.00")

    def test_a_share_cannot_move_more_than_the_unshared_cost(self):
        self.share()  # 18,000 waiting
        with self.assertRaisesMessage(InvalidTransaction, "12,000.00 QAR of rent"):
            self.share(units=[{"business_unit_id": self.roche.pk, "percent": "50"}])
        with self.assertRaisesMessage(InvalidTransaction, "0.00 QAR of electricity and water"):
            self.share(account_code="TEST_EXP_UTILITIES", amount="100.00")
        with self.assertRaisesMessage(InvalidTransaction, "more than 100%"):
            self.share(units=[{"business_unit_id": self.roche.pk, "percent": "60"}, {"business_unit_id": self.natuzzi.pk, "percent": "50"}])
        with self.assertRaisesMessage(InvalidTransaction, "listed twice"):
            self.share(units=[{"business_unit_id": self.roche.pk, "percent": "1"}, {"business_unit_id": self.roche.pk, "percent": "1"}])
        with self.assertRaisesMessage(InvalidTransaction, "choose another active unit"):
            self.share(units=[{"business_unit_id": self.unit.pk, "percent": "10"}])
        with self.assertRaisesMessage(InvalidTransaction, "Choose the kind of cost"):
            self.share(account_code="TEST_COGS")
        other = BusinessUnit.objects.create(legal_entity=LegalEntity.objects.create(name="TEST ONLY Other Co", code="test-other"),
                                            name="TEST ONLY Other", code="OTH")
        with self.assertRaisesMessage(InvalidTransaction, "choose another active unit"):
            self.share(units=[{"business_unit_id": other.pk, "percent": "10"}])
        with self.assertRaisesMessage(InvalidTransaction, "at most two decimals"):
            self.share(units=[{"business_unit_id": self.roche.pk, "percent": "10.123"}])
        ClosedMonth.objects.create(legal_entity=self.natuzzi.legal_entity, business_unit=self.natuzzi, month=self.today.replace(day=1),
                                   closed_by=self.admin, entry_count=0, total_debit=0)
        with self.assertRaisesMessage(InvalidTransaction, f"is closed for {self.natuzzi.name}"):
            self.share(amount="100.00", units=[{"business_unit_id": self.natuzzi.pk, "percent": "10"}])

    def test_rounding_stays_with_the_booking_unit_and_a_full_split_moves_everything(self):
        self.book("TEST_EXP_UTILITIES", "100.00")
        share = self.share(account_code="TEST_EXP_UTILITIES", amount="100.00",
                           units=[{"business_unit_id": self.roche.pk, "percent": "33.33"},
                                  {"business_unit_id": self.natuzzi.pk, "percent": "33.33"}])
        self.assertEqual((share["moved"], share["kept"]), ("66.66", "33.34"))
        with self.assertRaisesMessage(InvalidTransaction, "Give the reason"):
            decide_share(actor=self.sales, business_unit=self.unit, share_id=share["id"], action="reject")
        rejected = decide_share(actor=self.sales, business_unit=self.unit, share_id=share["id"], action="reject",
                                reason="TEST ONLY use thirds")
        self.assertEqual((rejected["status"], rejected["decision_note"]), ("rejected", "TEST ONLY use thirds"))
        share = self.share(account_code="TEST_EXP_UTILITIES", amount="100.00",
                           units=[{"business_unit_id": self.roche.pk, "percent": "66.67"},
                                  {"business_unit_id": self.natuzzi.pk, "percent": "33.33"}])
        self.assertEqual([line["amount"] for line in share["lines"]], ["66.67", "33.33"])
        self.assertEqual(share["kept"], "0.00")
        share = decide_share(actor=self.sales, business_unit=self.unit, share_id=share["id"], action="approve")
        self.assertEqual(self.balance(self.unit, "TEST_EXP_UTILITIES"), 0)
        detail = share_detail(actor=self.admin, business_unit=self.unit, share_id=share["id"])
        self.assertTrue(all(line["journal_entry_id"] for line in detail["lines"]))

    def test_the_api_prepares_and_approves_a_share(self):
        client = APIClient()
        client.force_login(self.admin)
        response = client.post(f"/api/finance/cost-sharing/?unit={self.unit.pk}", {
            "account_code": "TEST_EXP_RENT", "date": self.today.isoformat(), "amount": "1000.00",
            "description": "TEST ONLY rent", "units": [{"business_unit_id": self.roche.pk, "percent": "20"}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        share_id = response.json()["id"]
        client.force_login(self.sales)
        response = client.post(f"/api/finance/cost-sharing/{share_id}/?unit={self.unit.pk}", {"action": "approve"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        listed = client.get(f"/api/finance/cost-sharing/?unit={self.unit.pk}&status=approved").json()
        self.assertEqual([row["moved"] for row in listed["shares"]], ["200.00"])
        response = client.get(f"/api/finance/cost-sharing/?unit={self.roche.pk}")
        self.assertEqual(response.status_code, 403, "Sales has no journal or ledger permission in Roche Bobois")
