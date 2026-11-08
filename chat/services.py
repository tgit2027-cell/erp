"""Rules for the internal chat. Views stay thin and call these."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from foundation.access import active_grants, holders_filter

from .attachments import MAX_FILES_PER_MESSAGE, attachment_row, check_upload
from .models import (
    MAX_GROUP_MEMBERS,
    MAX_MESSAGE_LENGTH,
    MAX_TITLE_LENGTH,
    ChatAttachment,
    Conversation,
    Message,
    Participant,
)

PAGE_SIZE = 50
PREVIEW_LENGTH = 120


def display_name(user):
    return user.get_full_name().strip() or user.get_username()


def person_row(user):
    return {"id": user.pk, "username": user.get_username(), "name": display_name(user)}


def chat_people():
    """Everyone who can use the ERP: an active account with at least one current grant.

    Decided by the business owner: every ERP user can message every other ERP user, across all companies.
    """
    return get_user_model().objects.filter(is_active=True).filter(
        Q(is_superuser=True) | holders_filter(),
    ).distinct().order_by("first_name", "last_name", "username")


def can_chat(user):
    return bool(user.is_active and (user.is_superuser or active_grants(user).exists()))


def require_chat_user(user):
    if not can_chat(user):
        raise PermissionDenied("Chat is available to ERP users only.")


def _people_by_id(ids, *, exclude=None):
    try:
        wanted = {int(value) for value in ids}
    except (TypeError, ValueError):
        raise ValidationError({"detail": "People must be given by their id."}) from None
    if exclude is not None:
        wanted.discard(exclude.pk)
    found = list(chat_people().filter(pk__in=wanted))
    if len(found) != len(wanted):
        raise ValidationError({"detail": "One or more people cannot be found or cannot use chat."})
    return found


def membership(user, conversation_id):
    """The caller's current place in a conversation, or 404 so outsiders learn nothing."""
    participant = (
        Participant.objects.select_related("conversation")
        .filter(conversation_id=conversation_id, user=user, left_at__isnull=True)
        .first()
    )
    if participant is None:
        raise NotFound("Conversation not found.")
    return participant


def open_direct(user, other_id):
    (other,) = _people_by_id([other_id])
    if other.pk == user.pk:
        raise ValidationError({"detail": "Choose someone other than yourself."})
    key = Conversation.key_for(user.pk, other.pk)
    conversation = Conversation.objects.filter(direct_key=key).first()
    if conversation is None:
        try:
            with transaction.atomic():
                conversation = Conversation.objects.create(kind=Conversation.Kind.DIRECT, direct_key=key, created_by=user)
                Participant.objects.bulk_create([
                    Participant(conversation=conversation, user=user),
                    Participant(conversation=conversation, user=other),
                ])
        except IntegrityError:
            # Both people opened the chat at the same moment; use the one that won.
            conversation = Conversation.objects.get(direct_key=key)
    return conversation


def _clean_title(title):
    if not isinstance(title, str) or not title.strip():
        raise ValidationError({"title": "Give the group a name."})
    title = " ".join(title.split())
    if len(title) > MAX_TITLE_LENGTH:
        raise ValidationError({"title": f"Keep the group name to {MAX_TITLE_LENGTH} characters."})
    return title


@transaction.atomic
def create_group(user, title, member_ids):
    title = _clean_title(title)
    if not isinstance(member_ids, list) or not member_ids:
        raise ValidationError({"member_ids": "Add at least one other person."})
    members = _people_by_id(member_ids, exclude=user)
    if not members:
        raise ValidationError({"member_ids": "Add at least one other person."})
    if len(members) + 1 > MAX_GROUP_MEMBERS:
        raise ValidationError({"member_ids": f"A group can have up to {MAX_GROUP_MEMBERS} people."})
    conversation = Conversation.objects.create(kind=Conversation.Kind.GROUP, title=title, created_by=user)
    Participant.objects.bulk_create(
        [Participant(conversation=conversation, user=user)]
        + [Participant(conversation=conversation, user=member) for member in members]
    )
    return conversation


def _require_group(conversation):
    if conversation.kind == Conversation.Kind.TEAM:
        raise ValidationError({"detail": "A team chat follows its team. Its name and members are changed under Teams."})
    if conversation.kind != Conversation.Kind.GROUP:
        raise ValidationError({"detail": "Only group chats have members to change."})


@transaction.atomic
def add_members(user, conversation_id, member_ids):
    """Provisional: any member of a group can add people."""
    participant = membership(user, conversation_id)
    conversation = participant.conversation
    _require_group(conversation)
    if not isinstance(member_ids, list) or not member_ids:
        raise ValidationError({"member_ids": "Choose who to add."})
    people = _people_by_id(member_ids, exclude=user)
    current = Participant.objects.filter(conversation=conversation, left_at__isnull=True).count()
    newest = conversation.messages.order_by("-id").values_list("id", flat=True).first() or 0
    added = 0
    for person in people:
        row, created = Participant.objects.get_or_create(conversation=conversation, user=person)
        if created:
            added += 1
        elif row.left_at is not None:
            # Coming back: they see the group from now on, not what they missed while away.
            row.left_at = None
            row.joined_at = timezone.now()
            row.last_read_message_id = newest
            row.save(update_fields=["left_at", "joined_at", "last_read_message_id"])
            added += 1
    if current + added > MAX_GROUP_MEMBERS:
        raise ValidationError({"member_ids": f"A group can have up to {MAX_GROUP_MEMBERS} people."})
    return conversation


@transaction.atomic
def remove_member(user, conversation_id, member_id):
    """A person can always leave; only the group's creator can remove someone else."""
    participant = membership(user, conversation_id)
    conversation = participant.conversation
    _require_group(conversation)
    try:
        member_id = int(member_id)
    except (TypeError, ValueError):
        raise ValidationError({"detail": "People must be given by their id."}) from None
    if member_id != user.pk and conversation.created_by_id != user.pk:
        raise PermissionDenied("Only the person who started this group can remove others.")
    updated = Participant.objects.filter(
        conversation=conversation, user_id=member_id, left_at__isnull=True,
    ).update(left_at=timezone.now())
    if not updated:
        raise NotFound("That person is not in this group.")
    return conversation


def rename_group(user, conversation_id, title):
    participant = membership(user, conversation_id)
    conversation = participant.conversation
    _require_group(conversation)
    conversation.title = _clean_title(title)
    conversation.save(update_fields=["title"])
    return conversation


@transaction.atomic
def send_message(user, conversation_id, body, uploads=()):
    participant = membership(user, conversation_id)
    uploads = list(uploads or ())
    body = body.strip() if isinstance(body, str) else ""
    if not body and not uploads:
        raise ValidationError({"body": "Type a message or attach a file first."})
    if len(body) > MAX_MESSAGE_LENGTH:
        raise ValidationError({"body": f"Keep a message to {MAX_MESSAGE_LENGTH} characters."})
    if len(uploads) > MAX_FILES_PER_MESSAGE:
        raise ValidationError({"files": f"Share up to {MAX_FILES_PER_MESSAGE} files at a time."})
    checked = [(upload, check_upload(upload)) for upload in uploads]
    message = Message.objects.create(conversation=participant.conversation, sender=user, body=body)
    for upload, (name, content_type, size, sha256) in checked:
        attachment = ChatAttachment(message=message, original_name=name, content_type=content_type, size=size,
                                    sha256=sha256, uploaded_by=user)
        attachment.file.save(name, upload, save=False)
        attachment.save()
    Conversation.objects.filter(pk=participant.conversation_id).update(last_message_at=message.created_at)
    # Your own message counts as read by you.
    Participant.objects.filter(pk=participant.pk, last_read_message_id__lt=message.pk).update(
        last_read_message_id=message.pk,
    )
    return message


def mark_read(user, conversation_id, message_id):
    participant = membership(user, conversation_id)
    try:
        message_id = int(message_id)
    except (TypeError, ValueError):
        raise ValidationError({"message_id": "Give the id of the newest message you have seen."}) from None
    newest = participant.conversation.messages.order_by("-id").values_list("id", flat=True).first() or 0
    message_id = min(message_id, newest)
    Participant.objects.filter(pk=participant.pk, last_read_message_id__lt=message_id).update(
        last_read_message_id=message_id,
    )
    return message_id


def message_row(message):
    return {
        "id": message.pk,
        "sender": person_row(message.sender),
        "body": message.body,
        "created_at": message.created_at.isoformat(),
        "attachments": [attachment_row(item) for item in message.attachments.all()],
    }


def attachment_for_member(user, attachment_id):
    """A shared file, only for people who can see its message; 404 otherwise."""
    attachment = ChatAttachment.objects.select_related("message").filter(pk=attachment_id).first()
    if attachment is None:
        raise NotFound("File not found.")
    try:
        participant = membership(user, attachment.message.conversation_id)
    except NotFound:
        raise NotFound("File not found.") from None
    if (participant.conversation.kind != Conversation.Kind.DIRECT
            and attachment.message.created_at < participant.joined_at):
        raise NotFound("File not found.")
    return attachment


def messages_page(user, conversation_id, *, after=None, before=None):
    participant = membership(user, conversation_id)
    messages = (Message.objects.filter(conversation_id=participant.conversation_id)
                .select_related("sender").prefetch_related("attachments"))
    if participant.conversation.kind != Conversation.Kind.DIRECT:
        # Someone added later sees the group from when they joined.
        messages = messages.filter(created_at__gte=participant.joined_at)
    try:
        if after is not None:
            rows = list(messages.filter(id__gt=int(after)).order_by("id")[:PAGE_SIZE + 1])
            has_more = len(rows) > PAGE_SIZE
            rows = rows[:PAGE_SIZE]
        else:
            if before is not None:
                messages = messages.filter(id__lt=int(before))
            rows = list(messages.order_by("-id")[:PAGE_SIZE + 1])
            has_more = len(rows) > PAGE_SIZE
            rows = list(reversed(rows[:PAGE_SIZE]))
    except ValueError:
        raise ValidationError({"detail": "after and before must be message ids."}) from None
    return {
        "messages": [message_row(message) for message in rows],
        "has_more": has_more,
        "last_read_message_id": participant.last_read_message_id,
    }


def _annotated_participations(user):
    unread = (
        Message.objects.filter(conversation_id=OuterRef("conversation_id"), id__gt=OuterRef("last_read_message_id"))
        .exclude(sender=user)
        .filter(created_at__gte=OuterRef("joined_at"))
        .order_by()
        .values("conversation_id")
        .annotate(total=Count("id"))
        .values("total")
    )
    return Participant.objects.filter(user=user, left_at__isnull=True).annotate(unread=Subquery(unread))


def unread_summary(user):
    counts = [row or 0 for row in _annotated_participations(user).values_list("unread", flat=True)]
    return {"total": sum(counts), "conversations": sum(1 for count in counts if count)}


def conversation_list(user):
    participations = list(
        _annotated_participations(user)
        .select_related("conversation", "conversation__created_by")
        .order_by("-conversation__last_message_at", "-conversation__created_at")
    )
    ids = [row.conversation_id for row in participations]
    members = {}
    for row in Participant.objects.filter(conversation_id__in=ids, left_at__isnull=True).select_related("user"):
        members.setdefault(row.conversation_id, []).append(row.user)
    latest = {}
    latest_ids = (
        Message.objects.filter(conversation_id=OuterRef("pk")).order_by("-id").values("id")[:1]
    )
    for message in Message.objects.filter(
        id__in=Conversation.objects.filter(pk__in=ids).annotate(latest=Subquery(latest_ids)).values("latest"),
    ).select_related("sender").prefetch_related("attachments"):
        latest[message.conversation_id] = message
    rows = []
    for participation in participations:
        conversation = participation.conversation
        people = members.get(conversation.pk, [])
        message = latest.get(conversation.pk)
        if message and conversation.kind != Conversation.Kind.DIRECT and message.created_at < participation.joined_at:
            message = None
        rows.append(conversation_row(user, conversation, people, participation.unread or 0, message))
    return rows


def conversation_row(user, conversation, people, unread, latest):
    others = [person for person in people if person.pk != user.pk]
    if conversation.kind == Conversation.Kind.DIRECT:
        title = display_name(others[0]) if others else "Former colleague"
    else:
        title = conversation.title
    preview = None
    if latest is not None:
        text = " ".join(latest.body.split())
        if not text:
            names = [item.original_name for item in latest.attachments.all()]
            text = "Shared " + (names[0] if len(names) == 1 else f"{len(names)} files")
        preview = {
            "sender": "You" if latest.sender_id == user.pk else display_name(latest.sender),
            "text": text if len(text) <= PREVIEW_LENGTH else text[:PREVIEW_LENGTH - 1] + "…",
            "created_at": latest.created_at.isoformat(),
        }
    return {
        "id": conversation.pk,
        "kind": conversation.kind,
        "title": title,
        "members": [person_row(person) for person in sorted(people, key=display_name)],
        "started_by": conversation.created_by_id,
        "can_remove_members": conversation.kind == Conversation.Kind.GROUP and conversation.created_by_id == user.pk,
        "unread": unread,
        "last_message": preview,
    }


def conversation_detail(user, conversation_id):
    participant = membership(user, conversation_id)
    for row in conversation_list(user):
        if row["id"] == participant.conversation_id:
            return row
    raise NotFound("Conversation not found.")
