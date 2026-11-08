"""The setup lock (Sam, 5 Nov 2026): only the Master Admin changes setup, rules and permissions.

Day-to-day work stays with the teams. These tests cover the setup pieces that are not already
covered next to their own feature.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .cash_accounts import create_account
from .letters import set_letter_settings
from .models import UserActionGrant
from .pay_records import set_working_hours
from .payment_plan import set_bank_limit
from .payroll import set_wps_settings
from .purchasing_services import WorkflowPermissionDenied, configure_work_calendar
from .test_purchasing_workflows import PurchasingFixtureMixin

A = UserActionGrant.Action


@override_settings(APP_ENV="demo")
class SetupLockTests(PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        # demo_admin holds the finance, payroll and HR permissions, but is not the Master Admin.
        for action in (A.MANAGE_CASH, A.MANAGE_PAY, A.VIEW_PAY, A.MANAGE_STAFF, A.VIEW_STAFF, A.PAY_SUPPLIER):
            UserActionGrant.objects.get_or_create(user=cls.admin, business_unit=cls.unit, action=action)
        UserActionGrant.objects.get_or_create(user=cls.sales, business_unit=cls.unit, action=A.MANAGE_STAFF)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)

    def test_finance_and_hr_settings_are_for_the_master_admin_only(self):
        changes = (
            (create_account, {"name": "TEST ONLY QNB", "kind": "bank", "bank_name": "QNB"}),
            (set_bank_limit, {"data": {"bank_name": "QNB", "daily_limit": "1000"}}),
            (set_wps_settings, {"data": {"employer_eid": "1234567", "payer_bank": "QNB", "payer_iban": ""}}),
            (set_letter_settings, {"data": {"company_name": "TEST ONLY Tivoli Group"}}),
            (set_working_hours, {"data": {"action": "standard"}}),
            (set_working_hours, {"data": {"name": "TEST ONLY night", "starts_at": "08:00", "ends_at": "17:00"}}),
        )
        for service, extra in changes:
            with self.subTest(service=service.__name__), \
                    self.assertRaisesMessage(WorkflowPermissionDenied, "Only the Master Admin"):
                service(actor=self.admin, business_unit=self.unit, **extra)

    def test_hr_sets_the_work_calendar(self):
        calendar = configure_work_calendar(actor=self.sales, business_unit=self.unit, closed_weekdays=[4],
                                           closed_dates=["2026-12-18"])
        self.assertEqual(calendar["closed_dates"], ["2026-12-18"])
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Only HR or the Master Admin"):
            configure_work_calendar(actor=self.roche_user, business_unit=self.unit, closed_weekdays=[4], closed_dates=[])
        client = APIClient()
        client.force_login(self.sales)
        self.assertFalse(self.sales.is_staff)
        response = client.put(f"/api/governance/purchasing/{self.unit.pk}/work-calendar/",
                              {"closed_weekdays": [4, 5], "closed_dates": []}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        client.force_login(self.roche_user)
        response = client.put(f"/api/governance/purchasing/{self.unit.pk}/work-calendar/",
                              {"closed_weekdays": [4], "closed_dates": []}, format="json")
        self.assertEqual(response.status_code, 403)
