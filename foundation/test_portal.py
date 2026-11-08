"""The HR Portal: who posts, who sees what, must-read confirmations, files, suggestions and the off switch; synthetic only."""

import base64
from io import BytesIO
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import BusinessUnit, PortalSuggestion, UserActionGrant
from .portal import act_on_post, answer_suggestion, create_post, photo_file, portal_overview, post_detail, post_file, \
    send_portal_emails, send_suggestion, suggestions_overview
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import save_employee, staff_detail
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action


def png():
    from PIL import Image

    out = BytesIO()
    Image.new("RGB", (20, 10), (200, 30, 30)).save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


@override_settings(APP_ENV="demo", HR_PORTAL_ENABLED=True)
class HrPortalTests(PurchasingFixtureMixin, TestCase):
    """Admin is HR in Tivoli with the portal permission; demo_multi (Aisha) is staff; demo_roche works only in Roche Bobois."""

    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        for action in (A.MANAGE_STAFF, A.POST_PORTAL):
            UserActionGrant.objects.get_or_create(user=cls.admin, business_unit=cls.unit, action=action)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)

    def post(self, **extra):
        return create_post(actor=self.admin, data={"kind": "procedure", "title": "TEST ONLY Leave request procedure",
                                                   "body": "Ask two weeks before.", "unit_id": self.unit.pk, **extra})

    def test_only_the_permitted_hr_team_posts(self):
        UserActionGrant.objects.filter(user=self.admin, action=A.POST_PORTAL).delete()
        with self.assertRaisesMessage(WorkflowPermissionDenied, "Post on the HR Portal"):
            self.post()  # HR staff records alone is not enough (Sam, 5 Nov 2026)
        with self.assertRaises(WorkflowPermissionDenied):
            create_post(actor=self.sales, data={"kind": "memo", "title": "x", "body": "y", "unit_id": self.unit.pk})

    def test_staff_see_posts_of_their_units_and_group_posts_need_every_unit(self):
        self.post()
        with self.assertRaisesMessage(WorkflowPermissionDenied, "every business unit"):
            create_post(actor=self.admin, data={"kind": "announcement", "title": "TEST ONLY Eid", "body": "Closed."})
        for unit in BusinessUnit.objects.filter(is_active=True):
            UserActionGrant.objects.get_or_create(user=self.admin, business_unit=unit, action=A.POST_PORTAL)
        create_post(actor=self.admin, data={"kind": "announcement", "title": "TEST ONLY Eid holiday", "body": "Closed."})

        roche = portal_overview(actor=self.roche_user, section="library")
        self.assertEqual(roche["posts"], [], "A Tivoli procedure is not shown to someone only in Roche Bobois")
        self.assertEqual([row["title"] for row in portal_overview(actor=self.roche_user)["posts"]], ["TEST ONLY Eid holiday"])
        self.assertEqual(portal_overview(actor=self.roche_user)["posts"][0]["for_label"], "Everyone in the group")
        self.assertEqual(len(portal_overview(actor=self.sales, section="library")["posts"]), 1)

    def test_must_read_is_confirmed_and_shown_on_the_staff_record(self):
        aisha = save_employee(actor=self.admin, business_unit=self.unit, data={
            "full_name": "TEST ONLY Aisha Rahman", "job_title": "Sales Consultant", "department": "Sales",
            "nationality": "Filipino", "joined_on": "2021-03-14", "user_id": self.sales.pk})
        post = self.post(must_read=True)
        self.assertEqual([row["id"] for row in portal_overview(actor=self.sales)["to_read"]], [post["id"]])
        act_on_post(actor=self.sales, post_id=post["id"], action="read")
        act_on_post(actor=self.sales, post_id=post["id"], action="read")  # twice is harmless
        self.assertEqual(portal_overview(actor=self.sales)["to_read"], [])

        detail = post_detail(actor=self.admin, post_id=post["id"])
        self.assertGreaterEqual(detail["reads"]["total"], 2)
        self.assertEqual(detail["reads"]["done"], 1)
        names = {row["name"]: row["read_at"] for row in detail["readers"]}
        self.assertTrue(any(at for at in names.values()))
        self.assertNotIn("readers", post_detail(actor=self.sales, post_id=post["id"]), "Staff do not see who read it")

        record = staff_detail(actor=self.admin, business_unit=self.unit, employee_id=aisha["id"])
        self.assertEqual([row["title"] for row in record["portal_reads"]], ["TEST ONLY Leave request procedure"])

    def test_taking_a_post_down_needs_a_reason_and_hides_it_from_staff(self):
        post = self.post()
        with self.assertRaises(WorkflowPermissionDenied):
            act_on_post(actor=self.sales, post_id=post["id"], action="withdraw", reason="x")
        with self.assertRaisesMessage(InvalidTransaction, "reason"):
            act_on_post(actor=self.admin, post_id=post["id"], action="withdraw")
        act_on_post(actor=self.admin, post_id=post["id"], action="withdraw", reason="TEST ONLY replaced by a new version")
        self.assertEqual(portal_overview(actor=self.sales, section="library")["posts"], [])
        self.assertEqual(portal_overview(actor=self.admin, section="library")["posts"][0]["status"], "withdrawn")
        with self.assertRaises(InvalidTransaction):
            act_on_post(actor=self.sales, post_id=post["id"], action="read")

    def test_events_need_a_date_and_show_as_upcoming(self):
        with self.assertRaisesMessage(InvalidTransaction, "date"):
            create_post(actor=self.admin, data={"kind": "event", "title": "TEST ONLY Family day", "body": "Fun.", "unit_id": self.unit.pk})
        create_post(actor=self.admin, data={"kind": "event", "title": "TEST ONLY Family day", "body": "Fun.", "unit_id": self.unit.pk,
                                            "event_on": "2099-01-15", "event_place": "TEST ONLY Aspire Park"})
        upcoming = portal_overview(actor=self.sales)["upcoming"]
        self.assertEqual(upcoming[0]["event_place"], "TEST ONLY Aspire Park")

    def test_a_picture_or_pdf_can_be_added(self):
        post = self.post(file={"name": "flow.png", "data": png()})
        self.assertEqual(post["file"], {"name": "flow.png", "type": "png"})
        name, content_type, data = post_file(actor=self.sales, post_id=post["id"])
        self.assertEqual((name, content_type), ("flow.png", "image/png"))
        self.assertTrue(data.startswith(b"\x89PNG"))
        pdf = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 test").decode()
        self.assertEqual(self.post(file={"name": "p.pdf", "data": pdf})["file"]["type"], "pdf")
        with self.assertRaises(InvalidTransaction):
            self.post(file={"name": "x.png", "data": "data:image/png;base64," + base64.b64encode(b"not a png").decode()})
        with self.assertRaises(InvalidTransaction):
            self.post(file={"name": "x.exe", "data": "data:application/octet-stream;base64,AAAA"})
        with self.assertRaises(InvalidTransaction):
            post_file(actor=self.roche_user, post_id=post["id"])

    def test_suggestions_carry_the_name_and_go_to_hr(self):
        sent = send_suggestion(actor=self.sales, data={"unit_id": self.unit.pk, "topic": "workplace", "text": "TEST ONLY more shade",
                                                      "anonymous": True})
        self.assertEqual(PortalSuggestion.objects.get(pk=sent["id"]).sender, self.sales, "No anonymous suggestions (Sam)")
        inbox = suggestions_overview(actor=self.admin)["inbox"]
        self.assertEqual(inbox[0]["sender"], self.sales.get_full_name() or self.sales.username)
        self.assertEqual(suggestions_overview(actor=self.roche_user)["inbox"], [])
        with self.assertRaises(WorkflowPermissionDenied):
            answer_suggestion(actor=self.roche_user, suggestion_id=sent["id"], action="seen")
        answer_suggestion(actor=self.admin, suggestion_id=sent["id"], action="answer", reply="TEST ONLY Shades go up next month.")
        mine = suggestions_overview(actor=self.sales)["mine"][0]
        self.assertEqual((mine["status"], mine["reply"]), ("closed", "TEST ONLY Shades go up next month."))
        with self.assertRaises(InvalidTransaction):
            send_suggestion(actor=self.roche_user, data={"unit_id": self.unit.pk, "text": "x"})

    def test_the_portal_is_off_until_switched_on(self):
        client = APIClient()
        client.force_authenticate(self.sales)
        self.assertEqual(client.get("/api/portal/posts/").status_code, 200)
        with override_settings(HR_PORTAL_ENABLED=False):
            self.assertEqual(client.get("/api/portal/posts/").status_code, 404)
            self.assertEqual(client.get("/api/portal/suggestions/").status_code, 404)
            with self.assertRaises(WorkflowPermissionDenied):
                portal_overview(actor=self.sales)

    def test_event_photos_and_the_latest_album_on_home(self):
        event = create_post(actor=self.admin, data={"kind": "event", "title": "TEST ONLY Kick-off 2026", "body": "Thank you all.",
                                                    "unit_id": self.unit.pk, "event_on": "2026-01-20"})
        with self.assertRaises(WorkflowPermissionDenied):
            act_on_post(actor=self.sales, post_id=event["id"], action="add_photo", photo={"data": png()})
        pdf = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 test").decode()
        with self.assertRaises(InvalidTransaction):
            act_on_post(actor=self.admin, post_id=event["id"], action="add_photo", photo={"data": pdf})
        row = act_on_post(actor=self.admin, post_id=event["id"], action="add_photo", photo={"data": png(), "caption": "TEST ONLY Team"})
        self.assertEqual(row["photos"][0]["caption"], "TEST ONLY Team")
        self.assertEqual(portal_overview(actor=self.sales)["latest_album"]["id"], event["id"])
        self.assertEqual([post["id"] for post in portal_overview(actor=self.sales, section="photos")["posts"]], [event["id"]])
        name, content_type, data = photo_file(actor=self.sales, post_id=event["id"], photo_id=row["photos"][0]["id"])
        self.assertEqual(content_type, "image/png")

    def test_new_posts_are_emailed_once_to_everyone_they_are_for(self):
        from django.core import mail

        self.sales.email = "aisha@example.invalid"
        self.sales.save(update_fields=["email"])
        self.post(must_read=True)
        emails = send_portal_emails()
        to_aisha = [email for email in emails if email.user_id == self.sales.pk]
        self.assertEqual(len(to_aisha), 1)
        self.assertIn("#/portal/library", to_aisha[0].body)
        self.assertIn("I have read this", to_aisha[0].body)
        self.assertFalse(any(email.user_id == self.admin.pk for email in emails), "The person who posted is not emailed")
        self.assertFalse(any(email.user_id == self.sales.pk for email in send_portal_emails()), "Sent only once")
        self.assertTrue(any("aisha@example.invalid" in message.to for message in mail.outbox))

    def test_hr_gives_a_portal_only_sign_in_from_the_staff_record(self):
        from django.contrib.auth import authenticate

        from .portal import give_portal_sign_in, reset_portal_password

        person = save_employee(actor=self.admin, business_unit=self.unit, data={
            "full_name": "TEST ONLY Omar Haddad", "job_title": "Driver", "department": "Logistics",
            "nationality": "Jordanian", "joined_on": "2022-05-01", "work_email": "omar@example.invalid"})
        with self.assertRaises(WorkflowPermissionDenied):
            give_portal_sign_in(actor=self.sales, business_unit=self.unit, employee_id=person["id"], password="Portal-2026-pass")
        made = give_portal_sign_in(actor=self.admin, business_unit=self.unit, employee_id=person["id"], password="Portal-2026-pass")
        self.assertEqual(made["username"], "omar@example.invalid")
        omar = authenticate(username="omar@example.invalid", password="Portal-2026-pass")
        self.assertIsNotNone(omar)
        self.assertFalse(UserActionGrant.objects.filter(user=omar).exists(), "A portal sign-in carries no permissions")
        self.post()
        self.assertEqual(len(portal_overview(actor=omar, section="library")["posts"]), 1, "He sees his unit's procedures")
        with self.assertRaisesMessage(InvalidTransaction, "already has a sign-in"):
            give_portal_sign_in(actor=self.admin, business_unit=self.unit, employee_id=person["id"], password="Portal-2026-pass")
        reset_portal_password(actor=self.admin, business_unit=self.unit, employee_id=person["id"], password="Another-2026-pass")
        self.assertIsNotNone(authenticate(username="omar@example.invalid", password="Another-2026-pass"))
        aisha = save_employee(actor=self.admin, business_unit=self.unit, data={
            "full_name": "TEST ONLY Aisha Rahman", "job_title": "Sales", "department": "Sales", "nationality": "Filipino",
            "joined_on": "2021-03-14", "user_id": self.sales.pk})
        with self.assertRaisesMessage(WorkflowPermissionDenied, "opens more than the portal"):
            reset_portal_password(actor=self.admin, business_unit=self.unit, employee_id=aisha["id"], password="Another-2026-pass")

    def test_people_change_their_own_password(self):
        client = APIClient()
        self.sales.set_password("Old-password-2026")
        self.sales.save()
        client.force_login(self.sales)
        response = client.post("/api/portal/password/", {"current": "wrong", "new": "New-password-2026"}, format="json")
        self.assertEqual(response.status_code, 400)
        response = client.post("/api/portal/password/", {"current": "Old-password-2026", "new": "New-password-2026"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.sales.refresh_from_db()
        self.assertTrue(self.sales.check_password("New-password-2026"))
        self.assertEqual(client.get("/api/portal/posts/").status_code, 200, "Still signed in after the change")

    def test_a_portal_only_sign_in_can_sign_in_only_while_the_portal_is_on(self):
        from .portal import give_portal_sign_in

        person = save_employee(actor=self.admin, business_unit=self.unit, data={
            "full_name": "TEST ONLY Omar Haddad", "job_title": "Driver", "department": "Logistics",
            "nationality": "Jordanian", "joined_on": "2022-05-01", "work_email": "omar@example.invalid"})
        give_portal_sign_in(actor=self.admin, business_unit=self.unit, employee_id=person["id"], password="Portal-2026-pass")
        login = {"username": "omar@example.invalid", "password": "Portal-2026-pass"}
        with override_settings(HR_PORTAL_ENABLED=False):
            self.assertEqual(APIClient().post("/api/auth/login/", login, format="json").status_code, 400)
        client = APIClient()
        response = client.post("/api/auth/login/", login, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["hr_portal_enabled"])
        self.assertEqual(client.get("/api/portal/posts/").status_code, 200)
