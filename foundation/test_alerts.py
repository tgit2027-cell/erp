"""Email alerts for approvals waiting and documents about to expire."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditEvent

from .alerts import alerts_overview, send_alerts, set_preference
from .approval_limits import set_limit
from .go_live_imports import decide, submit
from .models import AlertEmail, AlertPreference, DemoUserMarker, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .supplier_advances import prepare_advance
from .test_go_live_imports import SUPPLIER_HEAD, xlsx
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action


@override_settings(APP_ENV="demo", ERP_BASE_URL="https://erp.example.invalid")
class AlertTests(PurchasingFixtureMixin, TestCase):
    """demo_admin prepares; demo_multi (sales) and demo_roche approve supplier payments."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for user, action in ((cls.admin, A.PAY_SUPPLIER), (cls.sales, A.APPROVE_PAYMENT), (cls.roche_user, A.APPROVE_PAYMENT),
                             (cls.admin, A.IMPORT_DATA), (cls.roche_user, A.APPROVE_IMPORT), (cls.sales, A.APPROVE_SUPPLIER)):
            UserActionGrant.objects.get_or_create(user=user, business_unit=cls.unit, action=action)
        cls.admin.is_superuser = True  # Setup lock (Nov 2026): only the Master Admin brings go-live files in.
        cls.admin.save(update_fields=["is_superuser"])
        for user, email in ((cls.admin, "admin@example.invalid"), (cls.sales, "sales@example.invalid"), (cls.roche_user, "")):
            user.email = email
            user.save(update_fields=["email"])
        cls.master = get_user_model().objects.create_user(username="test_alerts_master", password="unused-test-password",
                                                          is_staff=True, is_superuser=True)
        DemoUserMarker.objects.create(user=cls.master)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        # Assigned on the Alerts screen: both approvers get payment alerts; demo_roche also imports, demo_multi supplier documents.
        set_preference(actor=self.master, user_id=self.sales.pk, types=["payments", "supplier_documents"])
        set_preference(actor=self.master, user_id=self.roche_user.pk, types=["payments", "imports"])

    def advance(self, amount):
        return prepare_advance(actor=self.admin, business_unit=self.unit, data={
            "supplier_id": self.supplier.pk, "amount": amount, "currency": "QAR", "paid_on": timezone.localdate().isoformat(),
            "purpose": "TEST ONLY deposit on PO-118"})

    def test_approvers_are_emailed_once_and_never_the_preparer(self):
        first = self.advance("12000")
        send_alerts(expiring=False)
        self.assertEqual([message.to for message in mail.outbox], [["sales@example.invalid"]])
        message = mail.outbox[0]
        self.assertEqual(message.subject, "Tivoli ERP: 1 thing is waiting for your approval")
        self.assertIn(f"Advance {first['number']} to synthetic import supplier, QAR 12,000.00", message.body)
        self.assertIn("https://erp.example.invalid/#/finance/advances", message.body)
        # demo_roche has no email address: recorded, not sent. The preparer gets nothing.
        self.assertEqual(AlertEmail.objects.get(user=self.roche_user).status, AlertEmail.Status.NO_EMAIL)
        self.assertFalse(AlertEmail.objects.filter(user=self.admin).exists())
        # Nothing new: nothing sent again.
        send_alerts(expiring=False)
        self.assertEqual(len(mail.outbox), 1)
        # A new one, above demo_multi's limit, goes to nobody with an address.
        set_limit(actor=self.master, business_unit=self.unit, action=A.APPROVE_PAYMENT, user_id=self.sales.pk, max_amount="20000")
        self.advance("25000")
        send_alerts(expiring=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(AlertEmail.objects.filter(user=self.roche_user).count(), 2)

    def test_only_assigned_types_are_sent_to_the_chosen_address(self):
        set_preference(actor=self.master, user_id=self.sales.pk, types=["supplier_documents"])
        self.advance("100")
        call_command("send_alerts", "--only", "approvals", stdout=open("/dev/null", "w"))
        self.assertEqual(mail.outbox, [])
        set_preference(actor=self.master, user_id=self.sales.pk, types=["payments"], email="omar.alerts@example.invalid")
        send_alerts(expiring=False)
        self.assertEqual([message.to for message in mail.outbox], [["omar.alerts@example.invalid"]])
        # Nobody is assigned: nothing is sent.
        AlertPreference.objects.all().delete()
        self.advance("200")
        send_alerts()
        self.assertEqual(len(mail.outbox), 1)

    def test_types_must_be_ones_the_person_can_act_on(self):
        with self.assertRaisesMessage(InvalidTransaction, "cannot act on: Go-live imports"):
            set_preference(actor=self.master, user_id=self.sales.pk, types=["imports"])
        with self.assertRaises(InvalidTransaction):
            set_preference(actor=self.master, user_id=self.sales.pk, types=["boats"])
        with self.assertRaisesMessage(InvalidTransaction, "not valid"):
            set_preference(actor=self.master, user_id=self.sales.pk, email="not-an-address")
        data = alerts_overview(actor=self.master)
        sales = next(person for person in data["people"] if person["id"] == self.sales.pk)
        self.assertEqual((sales["offered"], sales["types"], sales["sends_to"]),
                         (["payments", "suppliers", "supplier_documents"], ["payments", "supplier_documents"], "sales@example.invalid"))
        self.assertNotIn(self.master.pk, [person["id"] for person in data["people"]])  # nothing to alert them about
        self.assertTrue(AuditEvent.objects.filter(detail__what="alerts").exists())

    def test_supplier_cr_about_to_expire(self):
        soon = (timezone.localdate() + datetime.timedelta(days=10)).isoformat()
        batch = submit(actor=self.admin, kind="suppliers", file_name="TEST ONLY suppliers.xlsx", content=xlsx([SUPPLIER_HEAD,
            ["SUP-0077", "Gulf Timber Trading WLL", "45123", soon, "No", "", "", "Rashid Mahmoud", "", "", ""]]))
        send_alerts(expiring=False)  # the import itself is waiting for demo_roche, who has no address
        self.assertEqual(mail.outbox, [])
        decide(actor=self.roche_user, batch_id=batch["id"], action="approve")
        send_alerts(approvals=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Tivoli ERP: 1 document needs attention")
        self.assertIn(f"Gulf Timber Trading WLL: the CR expires on {datetime.date.fromisoformat(soon):%d %b %Y}", mail.outbox[0].body)
        send_alerts(approvals=False)
        self.assertEqual(len(mail.outbox), 1)

    def test_the_screen_is_for_admins(self):
        self.advance("100")
        send_alerts()
        data = alerts_overview(actor=self.master)
        self.assertEqual(data["emails"][0]["kind"], "approvals")
        self.assertIn(self.sales.pk, [person["id"] for person in data["people"]])
        with self.assertRaises(WorkflowPermissionDenied):
            alerts_overview(actor=self.sales)
        client = APIClient()
        client.force_authenticate(self.master)
        response = client.post("/api/governance/alerts/", {"user_id": self.sales.pk, "types": ["payments"]}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(next(person for person in response.json()["people"] if person["id"] == self.sales.pk)["types"], ["payments"])
        self.assertEqual(client.post("/api/governance/alerts/", {"action": "send"}, format="json").json()["just_sent"], 0)
