"""Security audit log, kept apart from business records.

Events are append-only: the model refuses updates and deletes, and a
database trigger refuses them for any other writer too. Sessions record when
each signed-in session started, was last active and ended, so usage time per
user can be reported.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class AuditEvent(models.Model):
    class Kind(models.TextChoices):
        SIGN_IN = "sign_in", "Signed in"
        SIGN_IN_FAILED = "sign_in_failed", "Failed sign-in"
        SIGN_IN_REFUSED = "sign_in_refused", "Sign-in refused: no access granted"
        SIGN_IN_LOCKED = "sign_in_locked", "Sign-in blocked after repeated failures"
        SIGN_OUT = "sign_out", "Signed out"
        ACCOUNT_CREATED = "account_created", "Account created"
        ACCOUNT_CHANGED = "account_changed", "Account access changed"
        ACTION = "action", "Changed data"
        ACCESS_DENIED = "access_denied", "Access denied"
        SYSTEM_JOB = "system_job", "Background job"

    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    kind = models.CharField(max_length=24, choices=Kind.choices, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events",
    )
    # The name typed or held at the time, kept even when no account matches.
    username = models.CharField(max_length=150, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    method = models.CharField(max_length=8, blank=True)
    path = models.CharField(max_length=300, blank=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    session_hash = models.CharField(max_length=64, blank=True, db_index=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-occurred_at", "-pk"]
        indexes = [models.Index(fields=["user", "-occurred_at"], name="audit_event_user_time")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Audit events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit events are append-only.")


class UserSession(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="audit_sessions")
    session_hash = models.CharField(max_length=64, unique=True)
    started_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-started_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(last_seen_at__gte=models.F("started_at")), name="audit_session_seen_after_start"),
            models.CheckConstraint(
                condition=models.Q(ended_at__isnull=True) | models.Q(ended_at__gte=models.F("started_at")),
                name="audit_session_end_after_start",
            ),
        ]

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit sessions are kept.")
