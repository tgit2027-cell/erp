from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .customers import normalize_qid, register_customer, update_customer
from .models import CustomerContactChange, DemoUserMarker, SyntheticCounterparty, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction


def details(**overrides):
    data = {"qid": "28463400123", "name": "Al Sadd family", "name_ar": "عائلة السد",
            "contact_en": "+974 5555 0101", "contact_ar": ""}
    data.update(overrides)
    return data


@override_settings(APP_ENV="demo")
class CustomerQidTests(PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        cls.outsider = get_user_model().objects.create_user(username="test_no_quotes", password="unused-test-password")
        DemoUserMarker.objects.create(user=cls.outsider)
        cls.roche_seller = get_user_model().objects.create_user(username="test_roche_seller", password="unused-test-password")
        DemoUserMarker.objects.create(user=cls.roche_seller)
        UserActionGrant.objects.create(user=cls.roche_seller, business_unit=cls.roche, action=UserActionGrant.Action.CREATE_ORDER)

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.sales)
        self.token = self.client.get("/api/auth/csrf/").data["csrfToken"]

    def post(self, data):
        return self.client.post("/api/customers/", data, format="json", HTTP_X_CSRFTOKEN=self.token)

    def patch(self, customer_id, data):
        return self.client.patch(f"/api/customers/{customer_id}/", data, format="json", HTTP_X_CSRFTOKEN=self.token)

    def test_a_customer_cannot_be_registered_without_a_valid_qid(self):
        for qid in ("", None, "1234567890", "123456789012", "18463400123", "2846340012A"):
            response = self.post(details(qid=qid))
            self.assertEqual(response.status_code, 400, qid)
            self.assertIn("QID", response.data["detail"])
        self.assertEqual(normalize_qid(" 284-6340 0123 "), "28463400123")
        with self.assertRaises(IntegrityError), transaction.atomic():
            SyntheticCounterparty.objects.create(party_type="customer", code="TEST-CUST-NO-QID", name="TEST ONLY no QID")

    def test_registration_uses_the_qid_and_refuses_a_second_customer_with_it(self):
        response = self.post(details(qid="284 6340 0123"))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["qid"], "28463400123")
        self.assertRegex(response.data["client_number"], r"^C-\d{6}$")
        self.assertEqual(response.data["code"], f"TEST-CUST-{response.data['client_number']}")
        self.assertNotIn("28463400123", response.data["code"], "The QID is not repeated in the code")
        self.assertEqual(response.data["name"], "TEST ONLY Al Sadd family")
        again = self.post(details(name="Someone else", contact_en="+974 5555 9999"))
        self.assertEqual(again.status_code, 400)
        self.assertIn("already registered to TEST ONLY Al Sadd family", again.data["detail"])
        self.assertEqual(SyntheticCounterparty.objects.filter(qid="28463400123").count(), 1)

    def test_phone_can_change_but_the_qid_cannot(self):
        customer = self.post(details()).data
        changed = self.patch(customer["id"], {"contact_en": "+974 6666 0202", "qid": customer["qid"]})
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(changed.data["contact_en"], "+974 6666 0202")
        self.assertEqual(changed.data["contact_changes"][0]["fields"],
                         [{"field": "contact_en", "before": "+974 5555 0101", "after": "+974 6666 0202"}])
        refused = self.patch(customer["id"], {"qid": "28463400999"})
        self.assertEqual(refused.status_code, 400)
        self.assertIn("QID cannot be changed", refused.data["detail"])
        record = SyntheticCounterparty.objects.get(pk=customer["id"])
        self.assertEqual(record.qid, "28463400123")
        # The database refuses it too, whatever code path tries.
        with self.assertRaises(Exception) as caught, transaction.atomic():
            SyntheticCounterparty.objects.filter(pk=record.pk).update(qid="28463400999")
        self.assertIn("QID cannot be changed", str(caught.exception))
        with self.assertRaises(Exception), transaction.atomic():
            SyntheticCounterparty.objects.filter(pk=record.pk).update(qid=None)
        self.assertEqual(CustomerContactChange.objects.filter(customer=record).count(), 1)

    def test_saving_unchanged_details_records_no_change(self):
        customer = self.post(details()).data
        self.assertEqual(self.patch(customer["id"], {"contact_en": "+974 5555 0101"}).status_code, 200)
        self.assertFalse(CustomerContactChange.objects.exists())

    def test_history_follows_the_qid_after_the_phone_changes(self):
        self.make_receipt(key="qid-history-receipt")
        self.make_quote(key="qid-history-quote")
        update_customer(actor=self.sales, customer_id=self.customer.pk, data={"contact_en": "+974 7777 0303"})
        found = self.client.get("/api/customers/", {"search": self.customer.qid}).data["customers"]
        self.assertEqual([row["id"] for row in found], [self.customer.pk])
        detail = self.client.get(f"/api/customers/{self.customer.pk}/").data
        self.assertEqual(detail["quote_count"], 1)
        self.assertEqual(detail["quotes"][0]["business_unit"], self.unit.code)
        self.assertEqual(detail["contact_en"], "+974 7777 0303")
        # Someone who quotes only in another business unit finds the customer but not this unit's history.
        self.client.force_login(self.roche_seller)
        other = self.client.get(f"/api/customers/{self.customer.pk}/")
        self.assertEqual(other.status_code, 200, other.data)
        other = other.data
        self.assertEqual(other["qid"], self.customer.qid)
        self.assertEqual(other["quote_count"], 0)

    def test_search_by_qid_name_or_phone(self):
        self.post(details())
        self.post(details(qid="29163400777", name="Lusail villa", contact_en="+974 3333 1212"))
        def names(term):
            return [row["name"] for row in self.client.get("/api/customers/", {"search": term}).data["customers"]]
        self.assertEqual(names("2846"), ["TEST ONLY Al Sadd family"])
        self.assertEqual(names("3333 1212"), ["TEST ONLY Lusail villa"])
        self.assertEqual(names("lusail"), ["TEST ONLY Lusail villa"])
        quote_search = self.client.get("/api/purchasing/quotation-customers/", {
            "business_unit_id": self.unit.pk, "search": "29163400777"}).data["rows"]
        self.assertEqual([row["qid"] for row in quote_search], ["29163400777"])

    def test_only_people_who_quote_can_register_or_look_up_customers(self):
        with self.assertRaises(WorkflowPermissionDenied):
            register_customer(actor=self.outsider, data=details())
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get("/api/customers/").status_code, 403)
        self.assertEqual(self.client.get(f"/api/customers/{self.customer.pk}/").status_code, 403)

    def test_suppliers_have_no_qid(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SyntheticCounterparty.objects.create(party_type="supplier", code="TEST-SUP-QID", name="TEST ONLY s", qid="28463400555")
        with self.assertRaises(InvalidTransaction):
            update_customer(actor=self.sales, customer_id=self.supplier.pk, data={"contact_en": "x"})


@override_settings(APP_ENV="demo")
class CustomerWithoutQidTests(PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.sales)
        self.token = self.client.get("/api/auth/csrf/").data["csrfToken"]

    def post(self, data):
        return self.client.post("/api/customers/", data, format="json", HTTP_X_CSRFTOKEN=self.token)

    def patch(self, customer_id, data):
        return self.client.patch(f"/api/customers/{customer_id}/", data, format="json", HTTP_X_CSRFTOKEN=self.token)

    def test_a_customer_who_declines_is_registered_with_a_number_only(self):
        response = self.post(details(qid="", qid_declined=True, name="Walk-in buyer"))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data["qid"])
        self.assertTrue(response.data["qid_declined"])
        self.assertRegex(response.data["client_number"], r"^C-\d{6}$")
        second = self.post(details(qid=None, qid_declined=True, name="Another walk-in", contact_en="+974 4444 0000"))
        self.assertEqual(second.status_code, 201, "Many customers can decline; only given QIDs must be unique")
        self.assertNotEqual(second.data["client_number"], response.data["client_number"])
        found = self.client.get("/api/customers/", {"search": response.data["client_number"].lower()}).data["customers"]
        self.assertEqual([row["id"] for row in found], [response.data["id"]])

    def test_declining_needs_a_way_to_find_the_customer_again(self):
        refused = self.post(details(qid="", qid_declined=True, contact_en="", contact_ar=""))
        self.assertEqual(refused.status_code, 400)
        self.assertIn("phone number", refused.data["detail"])
        unclear = self.post(details(qid="", qid_declined="yes"))
        self.assertEqual(unclear.status_code, 400, "Declining must be an explicit choice")
        self.assertIn("declined", unclear.data["detail"])

    def test_a_qid_can_be_added_later_and_then_never_changes(self):
        customer = self.post(details(qid="", qid_declined=True)).data
        added = self.patch(customer["id"], {"qid": "291 6340 0888"})
        self.assertEqual(added.status_code, 200, added.data)
        self.assertEqual(added.data["qid"], "29163400888")
        self.assertFalse(added.data["qid_declined"])
        self.assertEqual(added.data["client_number"], customer["client_number"], "The client number stays")
        self.assertIn({"field": "qid", "before": "", "after": "29163400888"}, added.data["contact_changes"][0]["fields"])
        refused = self.patch(customer["id"], {"qid": "29163400999"})
        self.assertEqual(refused.status_code, 400)
        self.assertIn("QID cannot be changed", refused.data["detail"])
        taken = self.post(details(qid="", qid_declined=True, name="Third")).data
        duplicate = self.patch(taken["id"], {"qid": "29163400888"})
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("already registered", duplicate.data["detail"])

    def test_the_database_keeps_the_rules(self):
        customer = SyntheticCounterparty.objects.get(pk=self.post(details(qid="", qid_declined=True)).data["id"])
        with self.assertRaises(Exception) as caught, transaction.atomic():
            SyntheticCounterparty.objects.filter(pk=customer.pk).update(client_number="C-999999")
        self.assertIn("client or project number cannot be changed", str(caught.exception))
        with self.assertRaises(IntegrityError), transaction.atomic():
            SyntheticCounterparty.objects.filter(pk=customer.pk).update(qid_declined=False)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SyntheticCounterparty.objects.filter(pk=self.customer.pk).update(qid_declined=True)
        self.assertRegex(self.customer.client_number, r"^C-\d{6}$", "Existing customers are numbered too")
        self.assertIsNone(self.supplier.client_number)



@override_settings(APP_ENV="demo")
class CustomerNumbersAndTitlesTests(PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.sales)
        self.token = self.client.get("/api/auth/csrf/").data["csrfToken"]

    def post(self, data):
        return self.client.post("/api/customers/", data, format="json", HTTP_X_CSRFTOKEN=self.token)

    def patch(self, customer_id, data):
        return self.client.patch(f"/api/customers/{customer_id}/", data, format="json", HTTP_X_CSRFTOKEN=self.token)

    def test_customers_register_for_retail_projects_or_both(self):
        retail = self.post(details(qid="28463400201", register_for="client")).data
        project = self.post(details(qid="28463400202", register_for="project")).data
        both = self.post(details(qid="28463400203", register_for="both")).data
        self.assertRegex(retail["client_number"], r"^C-\d{6}$")
        self.assertIsNone(retail["project_number"])
        self.assertIsNone(project["client_number"])
        self.assertRegex(project["project_number"], r"^P-\d{6}$")
        self.assertRegex(both["client_number"], r"^C-\d{6}$")
        self.assertRegex(both["project_number"], r"^P-\d{6}$")
        short = both["client_number"].replace("-", "").replace("C0", "c", 1)  # typed without the dash, e.g. c00012
        for number in (both["client_number"], both["project_number"].lower(), short):
            found = self.client.get("/api/customers/", {"search": number}).data["customers"]
            self.assertEqual([row["id"] for row in found], [both["id"]], number)
        self.assertEqual(self.post(details(qid="28463400204", register_for="factory")).status_code, 400)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SyntheticCounterparty.objects.filter(pk=retail["id"]).update(client_number=None)

    def test_a_retail_customer_can_be_given_a_project_number_and_keeps_both(self):
        customer = self.post(details(qid="28463400211")).data
        added = self.patch(customer["id"], {"add_number": "project"})
        self.assertEqual(added.status_code, 200, added.data)
        self.assertEqual(added.data["client_number"], customer["client_number"])
        self.assertRegex(added.data["project_number"], r"^P-\d{6}$")
        self.assertIn({"field": "project_number", "before": "", "after": added.data["project_number"]},
                      added.data["contact_changes"][0]["fields"])
        again = self.patch(customer["id"], {"add_number": "project"})
        self.assertEqual(again.status_code, 400)
        self.assertIn("already has a project number", again.data["detail"])
        with self.assertRaises(Exception) as caught, transaction.atomic():
            SyntheticCounterparty.objects.filter(pk=customer["id"]).update(project_number="P-999999")
        self.assertIn("cannot be changed", str(caught.exception))

    def test_titles_come_from_a_fixed_list_and_are_counted(self):
        sheikh = self.post(details(qid="28463400221", title="sheikh", name="Sheikh test")).data
        self.post(details(qid="28463400222", title="sheikha", name="Sheikha test"))
        self.post(details(qid="28463400223", title="mr", name="Mr test"))
        self.assertEqual(sheikh["title"], "sheikh")
        self.assertEqual(sheikh["title_label"], "Sheikh")
        self.assertEqual(sheikh["title_ar"], "الشيخ")
        self.assertEqual(self.post(details(qid="28463400224", title="Sir")).status_code, 400)
        listed = self.client.get("/api/customers/", {"title": "sheikh"}).data
        self.assertEqual([row["id"] for row in listed["customers"]], [sheikh["id"]])
        counts = {row["title"]: row for row in listed["title_counts"]}
        self.assertEqual(counts["sheikh"]["customers"], 1)
        self.assertEqual(counts["sheikh"]["bought"], 0)
        self.assertIn("he", [title["code"] for title in listed["titles"]])
        self.assertEqual(self.client.get("/api/customers/", {"title": "sir"}).status_code, 400)
        changed = self.patch(sheikh["id"], {"title": "he"})
        self.assertEqual(changed.data["title"], "he")
        self.assertIn({"field": "title", "before": "sheikh", "after": "he"}, changed.data["contact_changes"][0]["fields"])

    def test_customer_type_defaults_to_individual_and_can_change(self):
        person = self.post(details(qid="28463400231", name="Type test")).data
        self.assertEqual((person["customer_type"], person["customer_type_label"]), ("individual", "Individual"))
        ministry = self.post(details(qid="28463400232", name="Ministry test", customer_type="government")).data
        self.assertEqual(ministry["customer_type"], "government")
        self.assertEqual(self.post(details(qid="28463400233", customer_type="charity")).status_code, 400)
        changed = self.patch(person["id"], {"customer_type": "company"})
        self.assertEqual(changed.data["customer_type"], "company")
        self.assertIn({"field": "customer_type", "before": "individual", "after": "company"},
                      changed.data["contact_changes"][0]["fields"])
        listed = self.client.get("/api/customers/").data
        self.assertEqual([row["code"] for row in listed["customer_types"]], ["individual", "company", "government"])

    def test_bought_counts_follow_invoices(self):
        from .models import SalesQuote
        from .purchasing_services import deliver_sales_quote, notify_quote_ready

        self.make_receipt(key="title-receipt")
        SyntheticCounterparty.objects.filter(pk=self.customer.pk).update(title="sheikha")
        self.post(details(qid="28463400231", title="sheikha", name="Sheikha who has not bought"))
        counts = lambda: {row["title"]: row for row in self.client.get("/api/customers/").data["title_counts"]}
        self.assertEqual((counts()["sheikha"]["customers"], counts()["sheikha"]["bought"]), (2, 0))
        quote = SalesQuote.objects.get(pk=self.make_quote(key="title-quote")["quote_id"])
        self.pay(quote, key="title-pay", amount=str(quote.total_quote_value))
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key="title-ready")
        deliver_sales_quote(actor=self.sales, quote=quote, idempotency_key="title-deliver")
        self.assertEqual((counts()["sheikha"]["customers"], counts()["sheikha"]["bought"]), (2, 1))
