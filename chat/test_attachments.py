import shutil
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from .models import ChatAccessLog, ChatAttachment, Message
from .tests import ChatFixture

PDF = b"%PDF-1.4\n% synthetic test document\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def upload(name, content, kind="application/octet-stream"):
    return SimpleUploadedFile(name, content, content_type=kind)


class AttachmentTests(ChatFixture):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.amal_client, self.badr_client = self.client_for(self.amal), self.client_for(self.badr)
        self.chat = self.direct(self.amal_client, self.badr)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def send(self, client, conversation, files, body=""):
        return client.post(f"/api/chat/conversations/{conversation}/messages/", {"body": body, "files": files}, format="multipart")

    def test_share_and_download_a_document(self):
        sent = self.send(self.amal_client, self.chat, [upload("Quote 114.pdf", PDF)], body="Signed quote attached")
        self.assertEqual(sent.status_code, 201, sent.data)
        (row,) = sent.data["attachments"]
        self.assertEqual((row["name"], row["size"], row["content_type"], row["is_picture"]), ("Quote 114.pdf", len(PDF), "application/pdf", False))
        stored = ChatAttachment.objects.get()
        self.assertTrue(stored.file.name.startswith("chat/"))
        self.assertNotIn("Quote", stored.file.name)
        self.assertEqual(len(stored.sha256), 64)

        page = self.badr_client.get(f"/api/chat/conversations/{self.chat}/messages/").data
        self.assertEqual(page["messages"][0]["attachments"][0]["id"], row["id"])
        response = self.badr_client.get(f"/api/chat/attachments/{row['id']}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), PDF)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        # Documents are never shown inline, even when asked.
        self.assertIn("attachment", self.badr_client.get(f"/api/chat/attachments/{row['id']}/", {"inline": "1"})["Content-Disposition"])

    def test_pictures_can_show_inline_and_file_only_message_has_a_preview(self):
        sent = self.send(self.amal_client, self.chat, [upload("sofa.png", PNG)])
        self.assertEqual(sent.status_code, 201, sent.data)
        attachment = sent.data["attachments"][0]
        self.assertTrue(attachment["is_picture"])
        response = self.badr_client.get(f"/api/chat/attachments/{attachment['id']}/", {"inline": "1"})
        self.assertIn("inline", response["Content-Disposition"])
        preview = self.badr_client.get("/api/chat/conversations/").data["conversations"][0]["last_message"]
        self.assertEqual(preview["text"], "Shared sofa.png")

    def test_outsiders_and_late_joiners_cannot_download(self):
        attachment = self.send(self.amal_client, self.chat, [upload("plan.pdf", PDF)]).data["attachments"][0]
        self.assertEqual(self.client_for(self.carla).get(f"/api/chat/attachments/{attachment['id']}/").status_code, 404)
        group = self.group(self.amal_client, "Team", self.badr)
        early = self.send(self.amal_client, group, [upload("early.pdf", PDF)]).data["attachments"][0]
        Message.objects.filter(conversation_id=group).update(created_at=timezone.now() - timedelta(minutes=5))
        self.amal_client.post(f"/api/chat/conversations/{group}/members/", {"member_ids": [self.dana.pk]}, format="json")
        self.assertEqual(self.client_for(self.dana).get(f"/api/chat/attachments/{early['id']}/").status_code, 404)

    def test_refused_files(self):
        cases = [
            [upload("tool.exe", b"MZ" + b"\x00" * 10)],
            [upload("fake.pdf", b"not a pdf at all")],
            [upload("empty.pdf", b"")],
            [upload("binary.txt", b"abc\x00def")],
            [upload("big.pdf", PDF + b"0" * (25 * 1024 * 1024))],
            [upload(f"f{n}.pdf", PDF) for n in range(6)],
        ]
        for files in cases:
            response = self.send(self.amal_client, self.chat, files)
            self.assertEqual(response.status_code, 400, files[0].name)
        self.assertFalse(ChatAttachment.objects.exists())
        self.assertFalse(Message.objects.exists())
        self.assertEqual(self.send(self.amal_client, self.chat, []).status_code, 400)

    def test_office_and_text_files_are_accepted(self):
        files = [upload("prices.xlsx", b"PK\x03\x04rest"), upload("notes.txt", "Délai: jeudi ✓".encode()),
                 upload("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest"), upload("list.csv", b"a,b\n1,2\n")]
        response = self.send(self.amal_client, self.chat, files)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data["attachments"]), 4)

    def test_purge_removes_old_files_from_storage(self):
        old = self.send(self.amal_client, self.chat, [upload("old.pdf", PDF)]).data
        self.send(self.amal_client, self.chat, [upload("new.pdf", PDF)])
        old_path = Path(self.media) / ChatAttachment.objects.get(pk=old["attachments"][0]["id"]).file.name
        self.assertTrue(old_path.exists())
        Message.objects.filter(pk=old["id"]).update(created_at=timezone.now() - timedelta(days=1200))
        out = StringIO()
        call_command("purge_old_chat_messages", stdout=out)
        self.assertIn("1 chat message(s) and 1 shared file(s)", out.getvalue())
        self.assertFalse(old_path.exists())
        self.assertEqual(list(ChatAttachment.objects.values_list("original_name", flat=True)), ["new.pdf"])

    def test_reviewer_downloads_are_recorded(self):
        call_command("set_chat_review_role", "--username", "carla", "--role", "requester", stdout=StringIO())
        call_command("set_chat_review_role", "--username", "dana", "--role", "approver", stdout=StringIO())
        attachment = self.send(self.amal_client, self.chat, [upload("evidence.pdf", PDF)]).data["attachments"][0]
        carla = self.client_for(self.carla)
        today = timezone.localdate().isoformat()
        access = carla.post("/api/chat/access-review/", {
            "subject_id": self.amal.pk, "reason": "legal_case", "period_start": today, "period_end": today,
            "explanation": "Legal claim 2026-3 about a disputed delivery agreement.",
        }, format="json").data["id"]
        url = f"/api/chat/access-review/{access}/attachments/{attachment['id']}/"
        self.assertEqual(carla.get(url).status_code, 403)
        self.client_for(self.dana).post(f"/api/chat/access-review/{access}/decision/", {"decision": "approve"}, format="json")
        messages = carla.get(f"/api/chat/access-review/{access}/conversations/{self.chat}/messages/").data["messages"]
        self.assertEqual(messages[0]["attachments"][0]["name"], "evidence.pdf")
        response = carla.get(url)
        self.assertEqual(b"".join(response.streaming_content), PDF)
        self.assertTrue(ChatAccessLog.objects.filter(request_id=access, event="downloaded", detail__attachment=attachment["id"]).exists())
