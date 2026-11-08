"""Approval limits: discount ceilings per role and the most each approver may approve."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditEvent

from .approval_limits import discount_ceilings, overview, set_discount, set_limit, set_person_discount
from .models import BusinessUnitRoleAssignment, DemoUserMarker, SalesQuote, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied, _discount_cap, _price_approver_role
from .supplier_advances import decide_advance, prepare_advance
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action
Role = BusinessUnitRoleAssignment.Role


@override_settings(APP_ENV="demo")
class ApprovalLimitTests(PurchasingFixtureMixin, TestCase):
    """A Master Admin sets the limits; demo_multi (sales) approves supplier payments."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.get_or_create(user=cls.admin, business_unit=cls.unit, action=A.PAY_SUPPLIER)
        UserActionGrant.objects.get_or_create(user=cls.sales, business_unit=cls.unit, action=A.APPROVE_PAYMENT)
        cls.master = get_user_model().objects.create_user(
            username="test_master_admin", password="unused-test-password", is_staff=True, is_superuser=True)
        DemoUserMarker.objects.create(user=cls.master)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)

    def advance(self, amount):
        return prepare_advance(actor=self.admin, business_unit=self.unit, data={
            "supplier_id": self.supplier.pk, "amount": amount, "currency": "QAR", "paid_on": timezone.localdate().isoformat(),
            "purpose": "TEST ONLY deposit on PO-118"})

    def test_discount_ceilings_default_and_per_unit(self):
        self.assertEqual(discount_ceilings(self.unit)[Role.MANAGER], Decimal("20.00"))
        self.assertEqual(_discount_cap([Role.SALESPERSON], self.unit), Decimal("10.00"))
        set_discount(actor=self.master, business_unit=self.unit, role=Role.SALESPERSON, percent="5")
        self.assertEqual(_discount_cap([Role.SALESPERSON], self.unit), Decimal("5.00"))
        with self.assertRaises(WorkflowPermissionDenied):
            _price_approver_role([Role.SALESPERSON], Decimal("8.00"), self.unit)
        self.assertEqual(_price_approver_role([Role.SALESPERSON, Role.MANAGER], Decimal("8.00"), self.unit), Role.MANAGER)
        with self.assertRaises(InvalidTransaction):  # a manager may not have less than a salesperson
            set_discount(actor=self.master, business_unit=self.unit, role=Role.MANAGER, percent="4")
        with self.assertRaises(InvalidTransaction):
            set_discount(actor=self.master, business_unit=self.unit, role=Role.CEO, percent="101")
        result = set_discount(actor=self.master, business_unit=self.unit, role=Role.SALESPERSON, percent="")
        self.assertEqual(next(row for row in result["discounts"] if row["role"] == Role.SALESPERSON)["percent"], "10.00")
        self.assertTrue(AuditEvent.objects.filter(detail__what="discount ceiling").exists())

    def test_an_approver_cannot_go_over_their_limit(self):
        set_limit(actor=self.master, business_unit=self.unit, action=A.APPROVE_PAYMENT, user_id=self.sales.pk, max_amount="50,000")
        big = self.advance("50000.01")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Your approval limit here is QAR 50,000.00 and this advance is QAR 50,000.01."):
            decide_advance(actor=self.sales, business_unit=self.unit, advance_id=big["id"], action="approve")
        small = self.advance("50000.00")
        self.assertEqual(decide_advance(actor=self.sales, business_unit=self.unit, advance_id=small["id"], action="approve")["status"], "approved")
        # Rejecting is never limited.
        self.assertEqual(decide_advance(actor=self.sales, business_unit=self.unit, advance_id=big["id"], action="reject",
                                        reason="Too large")["status"], "rejected")
        set_limit(actor=self.master, business_unit=self.unit, action=A.APPROVE_PAYMENT, user_id=self.sales.pk, max_amount="")
        again = self.advance("90000.00")
        self.assertEqual(decide_advance(actor=self.sales, business_unit=self.unit, advance_id=again["id"], action="approve")["status"], "approved")

    def test_the_screen_lists_approvers_and_only_admins_change_it(self):
        data = overview(actor=self.master, business_unit=self.unit)
        payments = next(row for row in data["approvals"] if row["action"] == A.APPROVE_PAYMENT)
        self.assertIn(self.sales.pk, [person["id"] for person in payments["people"]])
        self.assertEqual([row["role"] for row in data["discounts"]], [Role.SALESPERSON, Role.MANAGER, Role.COMMERCIAL_GM, Role.CEO])
        with self.assertRaises(WorkflowPermissionDenied):
            overview(actor=self.sales, business_unit=self.unit)
        with self.assertRaises(InvalidTransaction):  # the person cannot approve expenses here
            set_limit(actor=self.master, business_unit=self.unit, action=A.APPROVE_EXPENSE, user_id=self.sales.pk, max_amount="10")
        with self.assertRaises(InvalidTransaction):
            set_limit(actor=self.master, business_unit=self.unit, action=A.APPROVE_PAY, user_id=self.sales.pk, max_amount="10")
        client = APIClient()
        client.force_authenticate(self.master)
        response = client.post(f"/api/governance/approval-limits/?unit={self.unit.pk}",
                               {"action": A.APPROVE_PAYMENT, "user_id": self.sales.pk, "max_amount": "25000"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        payments = next(row for row in response.json()["approvals"] if row["action"] == A.APPROVE_PAYMENT)
        self.assertEqual(next(person for person in payments["people"] if person["id"] == self.sales.pk)["limit"], "25000.00")
        client.force_authenticate(self.sales)
        self.assertEqual(client.get(f"/api/governance/approval-limits/?unit={self.unit.pk}").status_code, 403)

    def test_each_person_can_have_their_own_discount_for_stock_sales(self):
        self.make_receipt()
        seller = next(row for row in overview(actor=self.master, business_unit=self.unit)["people_discounts"] if row["id"] == self.sales.pk)
        self.assertEqual((seller["roles"], seller["role_percent"], seller["percent"]), (["Salesperson"], "10.00", None))
        with self.assertRaises(WorkflowPermissionDenied):  # above the 10% a salesperson has
            self.make_quote(key="own-1", discount="15.00")
        set_person_discount(actor=self.master, business_unit=self.unit, user_id=self.sales.pk, percent="15")
        quote = self.make_quote(key="own-2", discount="15.00")
        self.assertEqual(quote["status"], SalesQuote.Status.APPROVED)
        # Special orders keep the role's discount.
        with self.assertRaises(WorkflowPermissionDenied):
            self.make_quote(key="own-3", kind=SalesQuote.Kind.SPECIAL_ORDER, discount="15.00", payment_plan="downpayment",
                            downpayment_percent="50.00", payment_terms_reason="TEST ONLY terms")
        # It can also be lower than the role's.
        set_person_discount(actor=self.master, business_unit=self.unit, user_id=self.sales.pk, percent="2")
        with self.assertRaises(WorkflowPermissionDenied):
            self.make_quote(key="own-4", discount="5.00")
        with self.assertRaises(InvalidTransaction):
            set_person_discount(actor=self.master, business_unit=self.unit, user_id=self.sales.pk, percent="51")
        with self.assertRaises(InvalidTransaction):  # no sales role here
            set_person_discount(actor=self.master, business_unit=self.unit, user_id=self.master.pk, percent="5")
        with self.assertRaises(WorkflowPermissionDenied):
            set_person_discount(actor=self.sales, business_unit=self.unit, user_id=self.sales.pk, percent="5")
        client = APIClient()
        client.force_authenticate(self.master)
        response = client.post(f"/api/governance/approval-limits/?unit={self.unit.pk}",
                               {"kind": "person_discount", "user_id": self.sales.pk, "percent": ""}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(next(row for row in response.json()["people_discounts"] if row["id"] == self.sales.pk)["percent"])
        self.assertEqual(self.make_quote(key="own-5", discount="5.00")["status"], SalesQuote.Status.APPROVED)
        self.assertTrue(AuditEvent.objects.filter(detail__what="personal discount").exists())
