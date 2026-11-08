from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone

from audit.models import AuditEvent

from .models import ChatAccessLog, ChatAccessRequest, Message
from .tests import ChatFixture

EXPLANATION = "HR case 2026-14: complaint about messages sent to a colleague in October."


class AccessReviewTests(ChatFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.hr = cls.make_user("hr_lead", "Hana", "Rahman")
        cls.owner = cls.make_user("owner", "Sam", "Owner")
        cls.it_admin = cls.make_user("it_admin", "Ivan", "Tech")
        cls.it_admin.is_superuser = True
        cls.it_admin.save()
        call_command("set_chat_review_role", "--username", "hr_lead", "--role", "requester", stdout=StringIO())
        call_command("set_chat_review_role", "--username", "owner", "--role", "approver", stdout=StringIO())

    def setUp(self):
        amal, badr = self.client_for(self.amal), self.client_for(self.badr)
        self.chat = self.direct(amal, self.badr)
        self.say(amal, self.chat, "inside the period")
        self.group_chat = self.group(badr, "Unrelated", self.carla)
        self.say(badr, self.group_chat, "Amal is not in this group")
        old = self.say(amal, self.chat, "before the period")
        Message.objects.filter(pk=old).update(created_at=timezone.now() - timedelta(days=60))
        self.today = timezone.localdate()

    def ask(self, client=None, **overrides):
        body = {"subject_id": self.amal.pk, "reason": "hr_investigation", "explanation": EXPLANATION,
                "period_start": (self.today - timedelta(days=7)).isoformat(), "period_end": self.today.isoformat()}
        body.update(overrides)
        return (client or self.client_for(self.hr)).post("/api/chat/access-review/", body, format="json")

    def test_nobody_reads_without_the_named_roles(self):
        for user in (self.it_admin, self.badr):
            client = self.client_for(user)
            self.assertEqual(client.get("/api/chat/access-review/").status_code, 403)
            self.assertEqual(self.ask(client).status_code, 403)
        self.assertEqual(self.client_for(self.it_admin).get("/api/chat/access-review/me/").data,
                         {"can_request": False, "can_approve": False})

    def test_request_needs_a_reason_explanation_and_sensible_period(self):
        bad = [{"reason": "curiosity"}, {"explanation": "too short"}, {"subject_id": self.hr.pk},
               {"period_start": self.today.isoformat(), "period_end": (self.today - timedelta(days=1)).isoformat()},
               {"period_end": (self.today + timedelta(days=1)).isoformat()},
               {"period_start": (self.today - timedelta(days=400)).isoformat()}]
        for overrides in bad:
            self.assertEqual(self.ask(**overrides).status_code, 400, overrides)
        self.assertFalse(ChatAccessRequest.objects.exists())

    def test_full_approved_review_is_limited_and_recorded(self):
        hr, owner = self.client_for(self.hr), self.client_for(self.owner)
        access = self.ask().data["id"]
        # Not readable until approved.
        self.assertEqual(hr.get(f"/api/chat/access-review/{access}/conversations/").status_code, 403)
        # The requester cannot approve, even holding both roles.
        call_command("set_chat_review_role", "--username", "hr_lead", "--role", "approver", stdout=StringIO())
        self.assertEqual(hr.post(f"/api/chat/access-review/{access}/decision/", {"decision": "approve"}, format="json").status_code, 403)
        approved = owner.post(f"/api/chat/access-review/{access}/decision/", {"decision": "approve", "note": "OK for case 14"}, format="json")
        self.assertEqual(approved.status_code, 200)
        self.assertTrue(approved.data["access_until"])

        listing = hr.get(f"/api/chat/access-review/{access}/conversations/").data
        self.assertEqual([row["id"] for row in listing["conversations"]], [self.chat])
        self.assertEqual(listing["conversations"][0]["messages_in_period"], 1)
        messages = hr.get(f"/api/chat/access-review/{access}/conversations/{self.chat}/messages/").data["messages"]
        self.assertEqual([m["body"] for m in messages], ["inside the period"])
        # A chat the person reviewed was never in stays closed.
        self.assertEqual(hr.get(f"/api/chat/access-review/{access}/conversations/{self.group_chat}/messages/").status_code, 404)
        # The approver cannot read; only the requester can.
        self.assertEqual(owner.get(f"/api/chat/access-review/{access}/conversations/").status_code, 404)

        events = list(ChatAccessLog.objects.filter(request_id=access).values_list("event", flat=True))
        self.assertEqual(events, ["requested", "approved", "listed", "read"])
        log = owner.get(f"/api/chat/access-review/{access}/log/").data["log"]
        self.assertEqual([row["event"] for row in log], events)
        audited = AuditEvent.objects.filter(detail__has_key="chat_access_review")
        self.assertEqual(audited.filter(detail__chat_access_review__event="read").count(), 1)
        self.assertNotIn("inside the period", str(list(audited.values_list("detail", flat=True))))

    def test_reading_time_ends(self):
        hr = self.client_for(self.hr)
        access = self.ask().data["id"]
        self.client_for(self.owner).post(f"/api/chat/access-review/{access}/decision/", {"decision": "approve"}, format="json")
        ChatAccessRequest.objects.filter(pk=access).update(access_until=timezone.now() - timedelta(minutes=1))
        self.assertEqual(hr.get(f"/api/chat/access-review/{access}/conversations/").status_code, 403)

    def test_role_removed_stops_reading(self):
        hr = self.client_for(self.hr)
        access = self.ask().data["id"]
        self.client_for(self.owner).post(f"/api/chat/access-review/{access}/decision/", {"decision": "approve"}, format="json")
        call_command("set_chat_review_role", "--username", "hr_lead", "--role", "requester", "--remove", stdout=StringIO())
        self.assertEqual(hr.get(f"/api/chat/access-review/{access}/conversations/").status_code, 403)

    def test_reject_needs_a_note_and_withdraw(self):
        owner, hr = self.client_for(self.owner), self.client_for(self.hr)
        access = self.ask().data["id"]
        self.assertEqual(owner.post(f"/api/chat/access-review/{access}/decision/", {"decision": "reject"}, format="json").status_code, 400)
        rejected = owner.post(f"/api/chat/access-review/{access}/decision/", {"decision": "reject", "note": "Not specific enough"}, format="json")
        self.assertEqual(rejected.data["status"], "rejected")
        self.assertEqual(owner.post(f"/api/chat/access-review/{access}/decision/", {"decision": "approve"}, format="json").status_code, 400)
        second = self.ask().data["id"]
        self.assertEqual(self.client_for(self.owner).post(f"/api/chat/access-review/{second}/withdraw/").status_code, 403)
        self.assertEqual(hr.post(f"/api/chat/access-review/{second}/withdraw/").data["status"], "withdrawn")

    def test_subject_cannot_decide_their_own_review(self):
        call_command("set_chat_review_role", "--username", "amal", "--role", "approver", stdout=StringIO())
        access = self.ask().data["id"]
        response = self.client_for(self.amal).post(f"/api/chat/access-review/{access}/decision/", {"decision": "approve"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_requesters_see_only_their_own_requests(self):
        self.ask()
        call_command("set_chat_review_role", "--username", "dana", "--role", "requester", stdout=StringIO())
        self.assertEqual(self.client_for(self.dana).get("/api/chat/access-review/").data["requests"], [])
        self.assertEqual(len(self.client_for(self.owner).get("/api/chat/access-review/").data["requests"]), 1)

    def test_role_through_a_group_counts_and_role_changes_are_audited(self):
        group = Group.objects.create(name="Chat approvers")
        group.permissions.add(Permission.objects.get(codename="approve_chat_access"))
        self.dana.groups.add(group)
        access = self.ask().data["id"]
        self.assertEqual(self.client_for(self.dana).post(f"/api/chat/access-review/{access}/decision/",
                                                         {"decision": "approve"}, format="json").status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(kind=AuditEvent.Kind.ACCOUNT_CHANGED, detail__chat_review_role="approver").exists())

    def test_log_is_append_only(self):
        self.ask()
        entry = ChatAccessLog.objects.first()
        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ValidationError):
            entry.delete()
