"""Internal chat between ERP users: one-to-one and group conversations.

Message text is private to the people in a conversation. It is kept out of the
audit log (this app is not in the audit's tracked apps), and no screen or API
lets administrators read other people's chats.
"""

import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

MAX_MESSAGE_LENGTH = 4000
MAX_TITLE_LENGTH = 120
MAX_GROUP_MEMBERS = 200


class Conversation(models.Model):
    class Kind(models.TextChoices):
        DIRECT = "direct", "Direct"
        GROUP = "group", "Group"
        TEAM = "team", "Team"

    kind = models.CharField(max_length=8, choices=Kind.choices)
    title = models.CharField(max_length=MAX_TITLE_LENGTH, blank=True)
    # "<smaller user id>:<larger user id>" for a direct chat, so two people share one chat.
    direct_key = models.CharField(max_length=41, null=True, blank=True, unique=True)
    # A team's own chat: its members follow the team and are changed only under Teams.
    team = models.OneToOneField("foundation.Team", null=True, blank=True, on_delete=models.PROTECT, related_name="chat")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="chat_conversations_started",
    )
    created_at = models.DateTimeField(default=timezone.now)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                name="chat_direct_has_key",
                condition=(models.Q(kind="direct", direct_key__isnull=False, team__isnull=True)
                           | models.Q(kind="group", direct_key__isnull=True, team__isnull=True)
                           | models.Q(kind="team", direct_key__isnull=True, team__isnull=False)),
            ),
        ]

    def clean(self):
        if self.kind != self.Kind.DIRECT and not self.title.strip():
            raise ValidationError({"title": "A group needs a name."})

    @staticmethod
    def key_for(first_user_id, second_user_id):
        low, high = sorted((int(first_user_id), int(second_user_id)))
        return f"{low}:{high}"


class Participant(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="chat_participations")
    joined_at = models.DateTimeField(default=timezone.now)
    # Set when someone leaves or is removed from a group; they then lose access to it.
    left_at = models.DateTimeField(null=True, blank=True)
    # The newest message id this person has seen; anything after it from others is unread.
    last_read_message_id = models.BigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["conversation", "user"], name="chat_one_participant_row"),
        ]
        indexes = [models.Index(fields=["user", "left_at"], name="chat_participant_user")]


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="chat_messages")
    body = models.TextField(max_length=MAX_MESSAGE_LENGTH)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["conversation", "id"], name="chat_message_conversation")]


class ChatAccessRequest(models.Model):
    """A request to read one person's chats for a stated, specific reason.

    Business owner's rule: no one reads other people's chats except with very
    high-level approval, for a specific reason, and every look is recorded.
    The requester and the approver must be two different people, and neither
    can be the person whose chats are being reviewed.
    """

    class Reason(models.TextChoices):
        HR_INVESTIGATION = "hr_investigation", "HR investigation"
        LEGAL_CASE = "legal_case", "Legal case or claim"
        REGULATOR = "regulator", "Request from a regulator or the authorities"
        FRAUD = "fraud", "Suspected fraud or theft"

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="chat_access_requests_made",
    )
    subject = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="chat_access_requests_about",
    )
    reason = models.CharField(max_length=24, choices=Reason.choices)
    explanation = models.TextField(max_length=2000)
    # Only messages sent inside this period can be read.
    period_start = models.DateField()
    period_end = models.DateField()
    created_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="chat_access_requests_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(max_length=1000, blank=True)
    # Reading is allowed only until this moment after approval.
    access_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        permissions = [
            ("request_chat_access", "Can ask to read someone's chats for a stated reason"),
            ("approve_chat_access", "Can approve reading someone's chats"),
        ]
        constraints = [
            models.CheckConstraint(name="chat_access_period_order", condition=models.Q(period_end__gte=models.F("period_start"))),
            models.CheckConstraint(name="chat_access_not_self", condition=~models.Q(requested_by=models.F("subject"))),
        ]


class ChatAccessLog(models.Model):
    """Append-only record of every step and every look under an access request."""

    class Event(models.TextChoices):
        REQUESTED = "requested", "Requested"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"
        LISTED = "listed", "Viewed the list of chats"
        READ = "read", "Read a chat"
        DOWNLOADED = "downloaded", "Downloaded a shared file"

    request = models.ForeignKey(ChatAccessRequest, on_delete=models.PROTECT, related_name="log")
    event = models.CharField(max_length=12, choices=Event.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="chat_access_log")
    occurred_at = models.DateTimeField(default=timezone.now)
    conversation = models.ForeignKey(Conversation, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["occurred_at", "pk"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("The chat access log cannot be changed.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("The chat access log cannot be deleted.")


def _attachment_path(instance, filename):
    # Stored under a random name; the original name is kept on the row for display.
    return f"chat/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class ChatAttachment(models.Model):
    """A file shared in a chat. Kept as long as its message (3 years) and covered by media backups."""

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=_attachment_path, max_length=200)
    original_name = models.CharField(max_length=200)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="chat_attachments")
    uploaded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["pk"]
