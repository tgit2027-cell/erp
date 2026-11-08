from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditEvent
from foundation.models import BusinessUnit, LegalEntity, UserActionGrant

from .models import Conversation, Message, Participant


class ChatFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        entity = LegalEntity.objects.create(name="Chat test company", code="chat-test")
        cls.unit = BusinessUnit.objects.create(legal_entity=entity, name="Chat unit", code="CHT")
        users = get_user_model().objects
        cls.amal = cls.make_user("amal", "Amal", "Haddad")
        cls.badr = cls.make_user("badr", "Badr", "Saleh")
        cls.carla = cls.make_user("carla", "Carla", "Mendes")
        cls.dana = cls.make_user("dana", "Dana", "Odeh")
        # An account with no ERP access must not appear in chat or use it.
        cls.no_access = users.create_user(username="no_access", password="unused-test-password")
        cls.inactive = cls.make_user("inactive", "Old", "Account")
        cls.inactive.is_active = False
        cls.inactive.save()

    @classmethod
    def make_user(cls, username, first, last):
        user = get_user_model().objects.create_user(
            username=username, first_name=first, last_name=last, password="unused-test-password",
        )
        UserActionGrant.objects.create(user=user, business_unit=cls.unit, action=UserActionGrant.Action.READ_STOCK)
        return user

    def client_for(self, user):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(user)
        token = client.get("/api/auth/csrf/").data["csrfToken"]
        client.credentials(HTTP_X_CSRFTOKEN=token)
        return client

    def direct(self, client, other):
        response = client.post("/api/chat/conversations/", {"kind": "direct", "user_id": other.pk}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def group(self, client, title, *members):
        response = client.post("/api/chat/conversations/",
                               {"kind": "group", "title": title, "member_ids": [m.pk for m in members]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def say(self, client, conversation_id, body):
        response = client.post(f"/api/chat/conversations/{conversation_id}/messages/", {"body": body}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]


class DirectChatTests(ChatFixture):
    def test_people_lists_erp_users_only(self):
        names = [row["username"] for row in self.client_for(self.amal).get("/api/chat/people/").data["people"]]
        self.assertEqual(names, ["badr", "carla", "dana"])
        found = self.client_for(self.amal).get("/api/chat/people/", {"q": "men"}).data["people"]
        self.assertEqual([row["name"] for row in found], ["Carla Mendes"])

    def test_users_without_erp_access_are_refused(self):
        client = APIClient()
        client.force_login(self.no_access)
        self.assertEqual(client.get("/api/chat/conversations/").status_code, 403)
        self.assertEqual(APIClient().get("/api/chat/unread/").status_code, 403)
        amal = self.client_for(self.amal)
        for person in (self.no_access, self.inactive, self.amal):
            response = amal.post("/api/chat/conversations/", {"kind": "direct", "user_id": person.pk}, format="json")
            self.assertEqual(response.status_code, 400)

    def test_two_people_share_one_direct_chat(self):
        first = self.direct(self.client_for(self.amal), self.badr)
        second = self.direct(self.client_for(self.badr), self.amal)
        self.assertEqual(first, second)
        self.assertEqual(Conversation.objects.filter(kind="direct").count(), 1)

    def test_messages_unread_counts_and_reading(self):
        amal, badr = self.client_for(self.amal), self.client_for(self.badr)
        chat = self.direct(amal, self.badr)
        self.say(amal, chat, "  Has the Natuzzi container cleared customs?  ")
        last = self.say(amal, chat, "Need it for Thursday's delivery.")
        self.assertEqual(amal.get("/api/chat/unread/").data, {"total": 0, "conversations": 0})
        self.assertEqual(badr.get("/api/chat/unread/").data, {"total": 2, "conversations": 1})

        listing = badr.get("/api/chat/conversations/").data["conversations"]
        self.assertEqual(listing[0]["title"], "Amal Haddad")
        self.assertEqual(listing[0]["unread"], 2)
        self.assertEqual(listing[0]["last_message"]["sender"], "Amal Haddad")
        self.assertEqual(amal.get("/api/chat/conversations/").data["conversations"][0]["last_message"]["sender"], "You")

        page = badr.get(f"/api/chat/conversations/{chat}/messages/").data
        self.assertEqual([m["body"] for m in page["messages"]],
                         ["Has the Natuzzi container cleared customs?", "Need it for Thursday's delivery."])
        read = badr.post(f"/api/chat/conversations/{chat}/read/", {"message_id": last + 999}, format="json")
        self.assertEqual(read.data["last_read_message_id"], last)
        self.assertEqual(read.data["unread"], {"total": 0, "conversations": 0})

        reply = self.say(badr, chat, "Yes, cleared this morning.")
        newer = amal.get(f"/api/chat/conversations/{chat}/messages/", {"after": last}).data["messages"]
        self.assertEqual([m["id"] for m in newer], [reply])

    def test_outsiders_cannot_read_or_write(self):
        chat = self.direct(self.client_for(self.amal), self.badr)
        self.say(self.client_for(self.amal), chat, "private")
        carla = self.client_for(self.carla)
        self.assertEqual(carla.get(f"/api/chat/conversations/{chat}/messages/").status_code, 404)
        self.assertEqual(carla.post(f"/api/chat/conversations/{chat}/messages/", {"body": "hi"}, format="json").status_code, 404)
        self.assertEqual(carla.get("/api/chat/conversations/").data["conversations"], [])

    def test_empty_or_long_messages_are_refused(self):
        amal = self.client_for(self.amal)
        chat = self.direct(amal, self.badr)
        url = f"/api/chat/conversations/{chat}/messages/"
        self.assertEqual(amal.post(url, {"body": "   "}, format="json").status_code, 400)
        self.assertEqual(amal.post(url, {"body": "x" * 4001}, format="json").status_code, 400)

    def test_paging_back_through_history(self):
        amal = self.client_for(self.amal)
        chat = self.direct(amal, self.badr)
        conversation = Conversation.objects.get(pk=chat)
        Message.objects.bulk_create([Message(conversation=conversation, sender=self.amal, body=f"m{n}") for n in range(60)])
        newest = amal.get(f"/api/chat/conversations/{chat}/messages/").data
        self.assertEqual(len(newest["messages"]), 50)
        self.assertTrue(newest["has_more"])
        self.assertEqual(newest["messages"][-1]["body"], "m59")
        older = amal.get(f"/api/chat/conversations/{chat}/messages/", {"before": newest["messages"][0]["id"]}).data
        self.assertEqual([m["body"] for m in older["messages"]], [f"m{n}" for n in range(10)])
        self.assertFalse(older["has_more"])

    def test_message_text_stays_out_of_the_audit_log(self):
        amal = self.client_for(self.amal)
        chat = self.direct(amal, self.badr)
        self.say(amal, chat, "salary figures for the team")
        events = AuditEvent.objects.filter(kind=AuditEvent.Kind.ACTION, path__startswith="/api/chat/")
        self.assertTrue(events.exists())
        self.assertNotIn("salary", str(list(events.values_list("detail", flat=True))))


class GroupChatTests(ChatFixture):
    def test_group_members_messages_and_unread(self):
        amal, badr, carla = (self.client_for(u) for u in (self.amal, self.badr, self.carla))
        group = self.group(amal, "  Warehouse   team ", self.badr, self.carla)
        detail = amal.get(f"/api/chat/conversations/{group}/").data
        self.assertEqual(detail["title"], "Warehouse team")
        self.assertEqual([m["username"] for m in detail["members"]], ["amal", "badr", "carla"])
        self.assertTrue(detail["can_remove_members"])
        self.assertFalse(badr.get(f"/api/chat/conversations/{group}/").data["can_remove_members"])
        self.say(amal, group, "Stock count starts at 8.")
        self.say(badr, group, "Noted.")
        self.assertEqual(carla.get("/api/chat/unread/").data["total"], 2)
        self.assertEqual(amal.get("/api/chat/unread/").data["total"], 1)

    def test_group_needs_a_name_and_a_member(self):
        amal = self.client_for(self.amal)
        bad = [{"kind": "group", "title": "", "member_ids": [self.badr.pk]},
               {"kind": "group", "title": "Solo", "member_ids": []},
               {"kind": "group", "title": "Solo", "member_ids": [self.amal.pk]},
               {"kind": "group", "title": "X", "member_ids": [self.no_access.pk]},
               {"kind": "other"}]
        for body in bad:
            self.assertEqual(amal.post("/api/chat/conversations/", body, format="json").status_code, 400, body)

    def test_new_member_sees_only_messages_from_joining(self):
        amal, dana = self.client_for(self.amal), self.client_for(self.dana)
        group = self.group(amal, "Showroom", self.badr)
        self.say(amal, group, "before Dana joined")
        Message.objects.filter(conversation_id=group).update(created_at=timezone.now() - timedelta(minutes=5))
        response = self.client_for(self.badr).post(f"/api/chat/conversations/{group}/members/",
                                                   {"member_ids": [self.dana.pk]}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(dana.get(f"/api/chat/conversations/{group}/messages/").data["messages"], [])
        self.assertEqual(dana.get("/api/chat/unread/").data["total"], 0)
        self.say(amal, group, "welcome Dana")
        self.assertEqual([m["body"] for m in dana.get(f"/api/chat/conversations/{group}/messages/").data["messages"]],
                         ["welcome Dana"])

    def test_leaving_and_removing(self):
        amal, badr, carla = (self.client_for(u) for u in (self.amal, self.badr, self.carla))
        group = self.group(amal, "Finance", self.badr, self.carla)
        # Only the creator removes others.
        self.assertEqual(badr.delete(f"/api/chat/conversations/{group}/members/{self.carla.pk}/").status_code, 403)
        self.assertEqual(amal.delete(f"/api/chat/conversations/{group}/members/{self.carla.pk}/").status_code, 200)
        self.assertEqual(carla.get(f"/api/chat/conversations/{group}/messages/").status_code, 404)
        # Anyone can leave.
        self.assertEqual(badr.delete(f"/api/chat/conversations/{group}/members/{self.badr.pk}/").status_code, 204)
        self.assertEqual(badr.get("/api/chat/conversations/").data["conversations"], [])
        self.assertEqual(Participant.objects.filter(conversation_id=group, left_at__isnull=True).count(), 1)

    def test_direct_chats_have_no_member_changes(self):
        amal = self.client_for(self.amal)
        chat = self.direct(amal, self.badr)
        response = amal.post(f"/api/chat/conversations/{chat}/members/", {"member_ids": [self.carla.pk]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(amal.patch(f"/api/chat/conversations/{chat}/", {"title": "x"}, format="json").status_code, 400)

    def test_rename_group(self):
        badr = self.client_for(self.badr)
        group = self.group(self.client_for(self.amal), "Old name", self.badr)
        self.assertEqual(badr.patch(f"/api/chat/conversations/{group}/", {"title": "New name"}, format="json").data["title"],
                         "New name")


class RetentionTests(ChatFixture):
    def test_purge_deletes_only_old_messages(self):
        amal = self.client_for(self.amal)
        chat = self.direct(amal, self.badr)
        old = self.say(amal, chat, "old")
        kept = self.say(amal, chat, "two and a half years")
        self.say(amal, chat, "new")
        Message.objects.filter(pk=kept).update(created_at=timezone.now() - timedelta(days=900))
        Message.objects.filter(pk=old).update(created_at=timezone.now() - timedelta(days=1200))
        out = StringIO()
        call_command("purge_old_chat_messages", "--dry-run", stdout=out)
        self.assertIn("Would delete 1 chat message(s)", out.getvalue())
        self.assertEqual(Message.objects.count(), 3)
        call_command("purge_old_chat_messages", stdout=StringIO())
        self.assertEqual(list(Message.objects.values_list("body", flat=True)), ["two and a half years", "new"])
