"""The audit log records sign-ins, refusals, changes and usage, and only system administrators read it."""

import os
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.utils.crypto import get_random_string
from rest_framework.test import APIClient

from foundation.models import BusinessUnit, DemoUserMarker, Item, Location, UserActionGrant, UserFavorite

from .changes import Collector, current_changes

from .models import AuditEvent, UserSession


@override_settings(APP_ENV="demo")
class AuditLogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            call_command("seed_demo", stdout=StringIO())
        User = get_user_model()
        cls.password = get_random_string(32)
        cls.user = User.objects.get(username="demo_roche")
        cls.user.set_password(cls.password)
        cls.user.save(update_fields=["password"])
        cls.admin = User.objects.create_user("it_audit_admin", password=cls.password, is_staff=True, is_superuser=True)
        DemoUserMarker.objects.create(user=cls.admin)

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def sign_in(self, username, password, client=None, **extra):
        client = client or APIClient(enforce_csrf_checks=True)
        csrf = client.get("/api/auth/csrf/").data["csrfToken"]
        response = client.post("/api/auth/login/", {"username": username, "password": password},
                               format="json", HTTP_X_CSRFTOKEN=csrf, **extra)
        return client, response

    def kinds(self, **filters):
        return list(AuditEvent.objects.filter(**filters).order_by("pk").values_list("kind", flat=True))

    def test_sign_in_and_sign_out_open_and_close_a_session(self):
        client, response = self.sign_in(self.user.username, self.password, REMOTE_ADDR="10.1.2.3",
                                        HTTP_USER_AGENT="TEST browser")
        self.assertEqual(response.status_code, 200)
        event = AuditEvent.objects.get(kind=AuditEvent.Kind.SIGN_IN)
        self.assertEqual((event.user, event.ip_address, event.user_agent), (self.user, "10.1.2.3", "TEST browser"))
        self.assertTrue(event.detail["first_sign_in"])
        session = UserSession.objects.get(user=self.user)
        self.assertIsNone(session.ended_at)
        csrf = client.get("/api/auth/csrf/").data["csrfToken"]
        client.post("/api/auth/logout/", {}, format="json", HTTP_X_CSRFTOKEN=csrf)
        session.refresh_from_db()
        self.assertIsNotNone(session.ended_at)
        self.assertEqual(self.kinds(user=self.user, kind__in=["sign_in", "sign_out"]), ["sign_in", "sign_out"])
        _, again = self.sign_in(self.user.username, self.password, REMOTE_ADDR="198.51.100.7")
        self.assertEqual(again.status_code, 200)
        latest = AuditEvent.objects.filter(kind=AuditEvent.Kind.SIGN_IN).first()
        self.assertTrue(latest.detail["new_address"])

    @override_settings(LOGIN_FAILURE_LIMIT=2)
    def test_failed_unknown_refused_and_locked_sign_ins_are_recorded_without_passwords(self):
        self.sign_in(self.user.username, "wrong-password")
        self.sign_in("nobody-here", "wrong-password")
        self.sign_in(self.user.username, "wrong-password")
        _, locked = self.sign_in(self.user.username, self.password)
        self.assertEqual(locked.status_code, 429)
        failed = AuditEvent.objects.filter(kind=AuditEvent.Kind.SIGN_IN_FAILED).order_by("pk")
        self.assertEqual([(e.username, e.detail["unknown_account"]) for e in failed],
                         [(self.user.username, False), ("nobody-here", True), (self.user.username, False)])
        self.assertTrue(AuditEvent.objects.filter(kind=AuditEvent.Kind.SIGN_IN_LOCKED, username=self.user.username).exists())
        no_access = get_user_model().objects.create_user("no_grants_user", password=self.password)
        DemoUserMarker.objects.create(user=no_access)
        _, refused = self.sign_in("no_grants_user", self.password)
        self.assertEqual(refused.status_code, 400)
        self.assertTrue(AuditEvent.objects.filter(kind=AuditEvent.Kind.SIGN_IN_REFUSED, user=no_access).exists())
        stored = str(list(AuditEvent.objects.values()))
        self.assertNotIn(self.password, stored)
        self.assertNotIn("wrong-password", stored)

    def test_account_creation_and_access_changes_are_recorded(self):
        user = get_user_model().objects.create_user("new_starter", password=self.password)
        created = AuditEvent.objects.get(kind=AuditEvent.Kind.ACCOUNT_CREATED, user=user)
        self.assertEqual(created.detail["by"], "system")
        user.is_staff = True
        user.set_password(get_random_string(32))
        user.save()
        changed = AuditEvent.objects.get(kind=AuditEvent.Kind.ACCOUNT_CHANGED, user=user)
        self.assertEqual(sorted(changed.detail["changed"]), ["is_staff", "password"])
        self.assertNotIn("password", {key for key in changed.detail if key not in {"changed", "by"}})

    def test_data_changes_and_refused_requests_are_recorded(self):
        client, _ = self.sign_in(self.user.username, self.password)
        csrf = client.get("/api/auth/csrf/").data["csrfToken"]
        denied = client.get("/api/audit/")
        self.assertEqual(denied.status_code, 403)
        client.post("/api/favorites/", {"destination": "inventory.main"}, format="json", HTTP_X_CSRFTOKEN=csrf)
        kinds = self.kinds(user=self.user)
        self.assertIn("access_denied", kinds)
        self.assertIn("action", kinds)
        action = AuditEvent.objects.filter(kind=AuditEvent.Kind.ACTION, user=self.user).first()
        self.assertEqual((action.method, action.path), ("POST", "/api/favorites/"))

    def test_events_cannot_be_edited_or_deleted(self):
        self.sign_in(self.user.username, "wrong-password")
        event = AuditEvent.objects.get(kind=AuditEvent.Kind.SIGN_IN_FAILED)
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()
        with self.assertRaises(DatabaseError), transaction.atomic():
            AuditEvent.objects.filter(pk=event.pk).update(username="someone-else")
        with self.assertRaises(DatabaseError), transaction.atomic():
            AuditEvent.objects.filter(pk=event.pk).delete()

    def test_secrets_and_session_keys_never_reach_the_log(self):
        secret = get_random_string(40)
        client, _ = self.sign_in(self.user.username, self.password)
        csrf = client.get("/api/auth/csrf/").data["csrfToken"]
        client.post(f"/api/favorites/?token={secret}", {"destination": "inventory.main", "password": secret,
                    "token": secret}, format="json", HTTP_X_CSRFTOKEN=csrf, HTTP_AUTHORIZATION=f"Bearer {secret}")
        session_key = client.cookies["sessionid"].value
        stored = str(list(AuditEvent.objects.values())) + str(list(UserSession.objects.values()))
        for value in (secret, self.password, session_key, csrf):
            self.assertNotIn(value, stored)

    def test_client_address_uses_the_proxy_entry_only_when_trusted(self):
        forged = {"REMOTE_ADDR": "10.0.0.5", "HTTP_X_FORWARDED_FOR": "6.6.6.6, 203.0.113.9"}
        self.sign_in("nobody-here", "wrong-password", **forged)
        self.assertEqual(AuditEvent.objects.get(kind=AuditEvent.Kind.SIGN_IN_FAILED).ip_address, "10.0.0.5", "Off by default: the header is ignored")
        with self.settings(AUDIT_TRUST_X_FORWARDED_FOR=True):
            self.sign_in("nobody-else", "wrong-password", **forged)
            self.sign_in("no-header", "wrong-password", REMOTE_ADDR="10.0.0.5")
        latest = AuditEvent.objects.filter(kind=AuditEvent.Kind.SIGN_IN_FAILED).order_by("-pk")
        self.assertEqual([e.ip_address for e in latest[:2]], ["10.0.0.5", "203.0.113.9"],
                         "Only the address the proxy added is kept; the visitor's own entry is ignored")

    def test_reading_pages_writes_nothing_except_a_once_a_minute_last_seen(self):
        client, _ = self.sign_in(self.user.username, self.password)
        client.get("/api/favorites/")

        def writes(path):
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(client.get(path).status_code, 200)
            return [q["sql"] for q in queries.captured_queries if q["sql"].split()[0] in {"INSERT", "UPDATE", "DELETE"}]

        before = AuditEvent.objects.count()
        self.assertEqual(writes("/api/favorites/"), [])
        UserSession.objects.filter(user=self.user).update(started_at=timezone.now() - timedelta(minutes=5),
                                                          last_seen_at=timezone.now() - timedelta(minutes=2))
        written = writes("/api/favorites/")
        self.assertEqual(len(written), 1)
        self.assertIn("audit_usersession", written[0])
        self.assertEqual(writes("/api/favorites/"), [])
        self.assertEqual(AuditEvent.objects.count(), before, "Successful reads are not logged as events")

    def test_a_data_change_lists_the_records_it_touched_and_a_failure_lists_none(self):
        client, _ = self.sign_in(self.user.username, self.password)
        csrf = client.get("/api/auth/csrf/").data["csrfToken"]
        client.post("/api/favorites/", {"destination": "inventory"}, format="json", HTTP_X_CSRFTOKEN=csrf)
        favorite = UserFavorite.objects.get(user=self.user, destination="inventory")
        event = AuditEvent.objects.filter(kind=AuditEvent.Kind.ACTION, user=self.user).first()
        self.assertEqual(event.detail["records"]["foundation.UserFavorite"],
                         {"created": 1, "updated": 0, "deleted": 0, "ids": [favorite.pk]})
        refused = client.post("/api/favorites/", {"destination": "no.such.place"}, format="json", HTTP_X_CSRFTOKEN=csrf)
        self.assertGreaterEqual(refused.status_code, 400)
        failed = AuditEvent.objects.filter(kind=AuditEvent.Kind.ACTION, user=self.user).first()
        self.assertEqual(failed.detail, {"succeeded": False})

    def test_master_data_keeps_old_and_new_values_and_links(self):
        changes = Collector()
        token = current_changes.set(changes)
        try:
            item = Item.objects.first()
            old_name = item.name_en
            item.name_en = "Renamed chair"
            item.save()
            grant = UserActionGrant.objects.create(user=self.user, business_unit=BusinessUnit.objects.first(),
                                                   action=UserActionGrant.Action.READ_STOCK)
            grant.delete()
            location = Location.objects.filter(business_units__isnull=False).first()
            unit = location.business_units.first()
            location.business_units.remove(unit)
            item.save()  # nothing changed, so no entry
        finally:
            current_changes.reset(token)
        detail = changes.summary()
        self.assertEqual(detail["records"]["foundation.Item"]["updated"], 2)
        entries = detail["changes"]
        self.assertEqual(entries[0], {"model": "foundation.Item", "id": item.pk, "action": "updated",
                                      "changed": {"name_en": [old_name, "Renamed chair"]}})
        self.assertEqual([(e["model"], e["action"]) for e in entries[1:]], [
            ("foundation.UserActionGrant", "created"), ("foundation.UserActionGrant", "deleted"),
            ("foundation.Location", "remove"),
        ])
        self.assertEqual(entries[1]["values"]["action"], UserActionGrant.Action.READ_STOCK)
        self.assertEqual(entries[3]["ids"], [unit.pk])

    def test_background_jobs_that_change_data_are_logged_as_system(self):
        seeded = AuditEvent.objects.filter(kind=AuditEvent.Kind.SYSTEM_JOB, detail__command="seed_demo")
        self.assertTrue(seeded.exists(), "The seed in setUpTestData ran as a job")
        job = seeded.first()
        self.assertEqual((job.user, job.username, job.detail["succeeded"]), (None, "system", True))
        self.assertIn("foundation.Item", job.detail["records"])
        before = AuditEvent.objects.filter(kind=AuditEvent.Kind.SYSTEM_JOB).count()
        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            call_command("seed_demo", stdout=StringIO())
        self.assertEqual(AuditEvent.objects.filter(kind=AuditEvent.Kind.SYSTEM_JOB).count(), before + 1)
        call_command("check", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(AuditEvent.objects.filter(kind=AuditEvent.Kind.SYSTEM_JOB).count(), before + 1,
                         "Commands that change nothing leave no event")
        auditor, _ = self.sign_in(self.admin.username, self.password)
        overview = auditor.get("/api/audit/").data["security"]
        self.assertGreaterEqual(overview["background_jobs"], 1)
        self.assertEqual(overview["recent_jobs"][0]["kind"], "system_job")

    def test_report_shows_usage_per_user_and_each_users_activity(self):
        client, _ = self.sign_in(self.user.username, self.password)
        session = UserSession.objects.get(user=self.user)
        UserSession.objects.filter(pk=session.pk).update(
            started_at=timezone.now() - timedelta(minutes=45), last_seen_at=timezone.now() - timedelta(minutes=5),
        )
        self.sign_in("nobody-here", "wrong-password")
        auditor, _ = self.sign_in(self.admin.username, self.password)
        overview = auditor.get("/api/audit/")
        self.assertEqual(overview.status_code, 200, overview.data)
        row = next(r for r in overview.data["users"] if r["username"] == self.user.username)
        self.assertEqual(row["sessions"], 1)
        self.assertEqual(row["active_seconds"], 40 * 60)
        security = overview.data["security"]
        self.assertEqual(security["unknown_account_attempts"], 1)
        self.assertGreaterEqual(security["sign_ins"], 2)
        detail = auditor.get(f"/api/audit/users/{self.user.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["events"][0]["kind"], "sign_in")
        only = auditor.get(f"/api/audit/users/{self.user.pk}/?kind=sign_in")
        self.assertEqual([e["kind"] for e in only.data["events"]], ["sign_in"])
        self.assertEqual(auditor.get(f"/api/audit/users/{self.user.pk}/?kind=bogus").status_code, 400)
        self.assertEqual(auditor.get("/api/audit/?from=2026-13-01").status_code, 400)
        self.assertEqual(client.get(f"/api/audit/users/{self.user.pk}/").status_code, 403)
