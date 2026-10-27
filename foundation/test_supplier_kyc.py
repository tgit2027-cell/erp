import shutil
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import (
    DemoUserMarker,
    SupplierKyc,
    SupplierKycDocument,
    SupplierKycEvent,
    SyntheticCounterparty,
    TransferPermissionAudit,
    UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied, create_purchase_commitment
from .supplier_kyc import add_document, decide_supplier, open_supplier, submit_supplier, update_supplier
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction
from .transfer_permissions import set_transfer_permission, transfer_permission_overview

Action = UserActionGrant.Action
PDF = b"%PDF-1.4\n% TEST ONLY synthetic document\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

MEDIA = tempfile.mkdtemp(prefix="tivoli-kyc-test-")


def details(**overrides):
    data = {
        "code": "TEST-SUP-LOCAL-1",
        "legal_name": "Doha Timber Trading WLL",
        "address": "Street 12, Industrial Area, Doha",
        "cr_number": "CR-123456",
        "cr_expiry": "2027-12-31",
        "vat_registered": False,
        "vat_number": "",
        "signatory_name": "Test Signatory",
        "signatory_title": "Managing partner",
        "contact_email": "accounts@example.test",
        "contact_phone": "+974 4000 0000",
    }
    data.update(overrides)
    return data


@override_settings(APP_ENV="demo", MEDIA_ROOT=MEDIA)
class SupplierKycTests(PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        User = get_user_model()

        def person(username, *actions):
            user = User.objects.create_user(username=username, password="unused-test-password")
            DemoUserMarker.objects.create(user=user)
            for action in actions:
                UserActionGrant.objects.create(user=user, business_unit=cls.unit, action=action)
            return user

        cls.it = person("test_it_officer", Action.OPEN_SUPPLIER)
        cls.manager = person("test_finance_manager", Action.APPROVE_SUPPLIER)
        cls.both = person("test_it_manager", Action.OPEN_SUPPLIER, Action.APPROVE_SUPPLIER)
        cls.outsider = person("test_outsider")
        cls.sysadmin = User.objects.create_user(
            username="test_kyc_sysadmin", password="unused-test-password", is_staff=True, is_superuser=True,
        )

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)

    def attach(self, kyc_id, kind, content=PDF, name="document.pdf", actor=None):
        return add_document(
            actor=actor or self.it, kyc_id=kyc_id, kind=kind,
            upload=SimpleUploadedFile(name, content, content_type="application/pdf"),
        )

    def ready_file(self, actor=None, **overrides):
        actor = actor or self.it
        kyc = open_supplier(actor=actor, data=details(**overrides))
        self.attach(kyc["id"], "cr_certificate", actor=actor)
        self.attach(kyc["id"], "signatory_id", PNG, "id.png", actor=actor)
        if overrides.get("vat_registered"):
            self.attach(kyc["id"], "vat_certificate", actor=actor)
        return submit_supplier(actor=actor, kyc_id=kyc["id"])

    def commit_to(self, supplier):
        return create_purchase_commitment(
            actor=self.sales, idempotency_key=f"kyc-{supplier.code}", business_unit=self.unit, supplier=supplier,
            lines=[{"variant": self.variant, "ordered_quantity": Decimal("1.00"), "unit_cost": Decimal("100.00")}],
        )

    def test_it_opens_manager_approves_and_only_then_purchasing_accepts_the_supplier(self):
        kyc = open_supplier(actor=self.it, data=details())
        self.assertEqual(kyc["status"], "draft")
        supplier = SyntheticCounterparty.objects.get(pk=kyc["supplier"]["id"])
        self.assertEqual(supplier.name, "TEST ONLY Doha Timber Trading WLL")
        with self.assertRaisesRegex(InvalidTransaction, "not approved yet"):
            self.commit_to(supplier)
        with self.assertRaisesRegex(InvalidTransaction, "Commercial registration.*Signatory ID"):
            submit_supplier(actor=self.it, kyc_id=kyc["id"])
        self.attach(kyc["id"], "cr_certificate")
        self.attach(kyc["id"], "signatory_id", PNG, "passport.png")
        submitted = submit_supplier(actor=self.it, kyc_id=kyc["id"])
        self.assertEqual(submitted["status"], "submitted")
        with self.assertRaisesRegex(InvalidTransaction, "a draft or was rejected"):
            self.attach(kyc["id"], "other")
        approved = decide_supplier(actor=self.manager, kyc_id=kyc["id"], approve=True, note="Documents checked")
        self.assertEqual((approved["status"], approved["decided_by"]["username"]), ("approved", "test_finance_manager"))
        self.commit_to(supplier)
        self.assertEqual(
            list(SupplierKycEvent.objects.filter(kyc_id=kyc["id"]).values_list("event", flat=True)),
            ["opened", "document_added", "document_added", "submitted", "approved"],
        )

    def test_rejection_needs_a_reason_and_a_corrected_file_can_be_resubmitted(self):
        kyc = self.ready_file()
        with self.assertRaisesRegex(InvalidTransaction, "reason"):
            decide_supplier(actor=self.manager, kyc_id=kyc["id"], approve=False, note=" ")
        rejected = decide_supplier(actor=self.manager, kyc_id=kyc["id"], approve=False, note="CR copy is expired")
        self.assertEqual((rejected["status"], rejected["decision_note"]), ("rejected", "CR copy is expired"))
        corrected = update_supplier(actor=self.it, kyc_id=kyc["id"], data=details(cr_expiry="2028-06-30"))
        self.assertEqual((corrected["status"], corrected["decided_by"]), ("draft", None))
        self.attach(kyc["id"], "cr_certificate", name="cr-renewed.pdf")
        submit_supplier(actor=self.it, kyc_id=kyc["id"])
        self.assertEqual(decide_supplier(actor=self.manager, kyc_id=kyc["id"], approve=True, note="")["status"], "approved")
        self.assertIn("rejected", [event["event"] for event in corrected["history"]])

    def test_permissions_and_no_self_approval(self):
        for actor in (self.manager, self.outsider, self.sysadmin):
            with self.assertRaises(WorkflowPermissionDenied):
                open_supplier(actor=actor, data=details())
        kyc = self.ready_file(actor=self.both)
        for actor in (self.it, self.outsider, self.both):
            with self.assertRaises(WorkflowPermissionDenied):
                decide_supplier(actor=actor, kyc_id=kyc["id"], approve=True, note="")
        self.assertEqual(SupplierKyc.objects.get(pk=kyc["id"]).status, "submitted")
        approved = SupplierKyc.objects.get(pk=kyc["id"])
        approved.status, approved.decided_by, approved.decided_at = "approved", self.both, approved.submitted_at
        with self.assertRaises(IntegrityError), transaction.atomic():
            approved.save()

    def test_vat_number_cr_uniqueness_and_document_checks(self):
        with self.assertRaisesRegex(InvalidTransaction, "Vat number is required"):
            open_supplier(actor=self.it, data=details(vat_registered=True))
        kyc = self.ready_file(vat_registered=True, vat_number="300123456700003")
        self.assertEqual(kyc["vat_number"], "300123456700003")
        with self.assertRaisesRegex(InvalidTransaction, "CR number is already on file"):
            open_supplier(actor=self.it, data=details(code="TEST-SUP-LOCAL-2"))
        other = open_supplier(actor=self.it, data=details(code="TEST-SUP-LOCAL-2", cr_number="CR-999"))
        with self.assertRaisesRegex(InvalidTransaction, "PDF, PNG or JPG"):
            self.attach(other["id"], "other", b"MZ\x90\x00", "tool.exe")
        with self.assertRaisesRegex(InvalidTransaction, "does not match"):
            self.attach(other["id"], "other", b"<html>not a pdf</html>", "fake.pdf")
        with patch("foundation.supplier_kyc.MAX_DOCUMENT_BYTES", 10):
            with self.assertRaisesRegex(InvalidTransaction, "at most 10 MB"):
                self.attach(other["id"], "other")
        document = SupplierKycDocument.objects.filter(kyc_id=kyc["id"]).first()
        self.assertTrue(document.file.name.startswith("supplier-kyc/"))
        self.assertNotIn("document", document.file.name)
        with self.assertRaises(ValidationError):
            document.delete()

    def test_suppliers_opened_before_kyc_are_not_blocked(self):
        self.assertFalse(SupplierKyc.objects.filter(supplier=self.supplier).exists())
        self.commit_to(self.supplier)

    def test_api_upload_download_and_access(self):
        client = APIClient()
        client.force_authenticate(self.it)
        response = client.post("/api/suppliers/kyc/", details(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        kyc_id = response.data["id"]
        upload = SimpleUploadedFile("cr.pdf", PDF, content_type="application/pdf")
        response = client.post(f"/api/suppliers/kyc/{kyc_id}/documents/", {"kind": "cr_certificate", "file": upload})
        self.assertEqual(response.status_code, 201, response.data)
        document_id = response.data["documents"][0]["id"]
        download = client.get(f"/api/suppliers/kyc/{kyc_id}/documents/{document_id}/")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b"".join(download.streaming_content), PDF)
        self.assertIn("attachment", download["Content-Disposition"])
        self.assertEqual(download["X-Content-Type-Options"], "nosniff")
        listing = client.get("/api/suppliers/kyc/")
        self.assertEqual(listing.data["permissions"], {"can_open": True, "can_approve": False})
        self.assertEqual(listing.data["counts"]["draft"], 1)
        outsider = APIClient()
        outsider.force_authenticate(self.outsider)
        self.assertEqual(outsider.get("/api/suppliers/kyc/").status_code, 403)
        self.assertEqual(outsider.get(f"/api/suppliers/kyc/{kyc_id}/documents/{document_id}/").status_code, 403)
        self.assertEqual(APIClient().get(f"/api/suppliers/kyc/{kyc_id}/documents/{document_id}/").status_code, 403)
        manager = APIClient()
        manager.force_authenticate(self.manager)
        self.assertEqual(manager.post(f"/api/suppliers/kyc/{kyc_id}/decision/", {"approve": True}, format="json").status_code, 400)

    def test_supplier_permissions_are_set_on_the_permissions_screen_company_wide(self):
        newcomer = get_user_model().objects.create_user(username="test_new_manager", password="unused-test-password")
        DemoUserMarker.objects.create(user=newcomer)
        result = set_transfer_permission(
            actor=self.sysadmin, business_unit=self.unit, user_id=newcomer.pk,
            action=Action.APPROVE_SUPPLIER, enabled=True,
        )
        self.assertTrue(result["changed"])
        overview = transfer_permission_overview(actor=self.sysadmin, business_unit=self.roche)
        row = next(user for user in overview["users"] if user["id"] == newcomer.pk)
        self.assertIn("approve_supplier", row["actions"])
        self.assertIn("company", {item["scope"] for item in overview["actions"]})
        self.assertFalse(set_transfer_permission(
            actor=self.sysadmin, business_unit=self.roche, user_id=newcomer.pk,
            action=Action.APPROVE_SUPPLIER, enabled=True,
        )["changed"])
        self.assertTrue(set_transfer_permission(
            actor=self.sysadmin, business_unit=self.roche, user_id=newcomer.pk,
            action=Action.APPROVE_SUPPLIER, enabled=False,
        )["changed"])
        self.assertFalse(UserActionGrant.objects.filter(
            user=newcomer, action=Action.APPROVE_SUPPLIER, revoked_at__isnull=True,
        ).exists())
        self.assertEqual(
            list(TransferPermissionAudit.objects.filter(target_user=newcomer).order_by("pk").values_list("change", flat=True)),
            ["granted", "revoked"],
        )
