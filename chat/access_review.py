"""Approved, recorded access to one person's chats for a specific reason.

Rules (business owner, 31 Oct 2026, with provisional details marked):
- Nobody can read other people's chats by default, including administrators.
- A request names the person, a reason from a short list, a written explanation
  and the period of messages needed.
- A second, very senior person approves or rejects it. Being a system
  administrator is not enough: both roles must be given to the person by name
  (provisional: approver role intended for the business owner only).
- The requester, approver and the person reviewed are three different people.
- After approval the requester can read, read-only, for 72 hours (provisional),
  and only messages inside the approved period.
- Every request, decision, and every chat opened is written to a permanent log
  and to the security audit log. The person reviewed is not told (provisional),
  so an investigation is not compromised.
"""

from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from audit.models import AuditEvent
from audit.recording import record

from .models import ChatAccessLog, ChatAccessRequest, ChatAttachment, Conversation, Message, Participant
from .services import display_name, message_row, person_row

ACCESS_HOURS = 72
MAX_PERIOD_DAYS = 366
MIN_EXPLANATION = 30
REQUEST_PERMISSION = "request_chat_access"
APPROVE_PERMISSION = "approve_chat_access"


def holds(user, codename):
    """True only when the role was given to this person by name or through a group.

    Unlike Django's has_perm, being a superuser does not count.
    """
    if not (user and user.is_authenticated and user.is_active):
        return False
    return Permission.objects.filter(content_type__app_label="chat", codename=codename).filter(
        Q(user=user) | Q(group__user=user),
    ).exists()


def capabilities(user):
    return {"can_request": holds(user, REQUEST_PERMISSION), "can_approve": holds(user, APPROVE_PERMISSION)}


def _require(user, codename):
    if not holds(user, codename):
        raise PermissionDenied("You have not been given this role.")


def _log(access, event, user, request=None, conversation=None, **detail):
    ChatAccessLog.objects.create(request=access, event=event, user=user, conversation=conversation, detail=detail)
    record(AuditEvent.Kind.ACTION, request=request, user=user, status_code=200, detail={
        "chat_access_review": {"request": access.pk, "event": event, "subject": access.subject_id,
                               **({"conversation": conversation.pk} if conversation else {})},
    })


def _parse_day(value, field):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValidationError({field: "Use a date like 2026-10-31."}) from None


@transaction.atomic
def create_request(user, data, request=None):
    _require(user, REQUEST_PERMISSION)
    try:
        subject = get_user_model().objects.get(pk=int(data.get("subject_id")))
    except (TypeError, ValueError, get_user_model().DoesNotExist):
        raise ValidationError({"subject_id": "Choose the person whose chats you need to review."}) from None
    if subject.pk == user.pk:
        raise ValidationError({"subject_id": "You cannot ask to review your own chats."})
    reason = data.get("reason")
    if reason not in ChatAccessRequest.Reason.values:
        raise ValidationError({"reason": "Choose one of the listed reasons."})
    explanation = data.get("explanation")
    if not isinstance(explanation, str) or len(explanation.strip()) < MIN_EXPLANATION:
        raise ValidationError({"explanation": f"Explain the specific case in at least {MIN_EXPLANATION} characters."})
    start, end = _parse_day(data.get("period_start"), "period_start"), _parse_day(data.get("period_end"), "period_end")
    if end < start:
        raise ValidationError({"period_end": "The end date must be on or after the start date."})
    if (end - start).days >= MAX_PERIOD_DAYS:
        raise ValidationError({"period_end": "Ask for one year of messages at most."})
    if end > timezone.localdate():
        raise ValidationError({"period_end": "The period cannot end in the future."})
    access = ChatAccessRequest.objects.create(
        requested_by=user, subject=subject, reason=reason, explanation=explanation.strip()[:2000],
        period_start=start, period_end=end,
    )
    _log(access, ChatAccessLog.Event.REQUESTED, user, request)
    return access


def _pending(access_id):
    access = ChatAccessRequest.objects.select_for_update().filter(pk=access_id).first()
    if access is None:
        raise NotFound("Request not found.")
    if access.status != ChatAccessRequest.Status.PENDING:
        raise ValidationError({"detail": "This request has already been decided."})
    return access


@transaction.atomic
def decide(user, access_id, approve, note, request=None):
    _require(user, APPROVE_PERMISSION)
    access = _pending(access_id)
    if user.pk in (access.requested_by_id, access.subject_id):
        raise PermissionDenied("Someone other than the requester and the person reviewed must decide this request.")
    note = (note or "").strip() if isinstance(note, str) else ""
    if not approve and len(note) < 10:
        raise ValidationError({"note": "Say why the request is rejected."})
    now = timezone.now()
    access.status = ChatAccessRequest.Status.APPROVED if approve else ChatAccessRequest.Status.REJECTED
    access.decided_by, access.decided_at, access.decision_note = user, now, note[:1000]
    access.access_until = now + timedelta(hours=ACCESS_HOURS) if approve else None
    access.save(update_fields=["status", "decided_by", "decided_at", "decision_note", "access_until"])
    _log(access, ChatAccessLog.Event.APPROVED if approve else ChatAccessLog.Event.REJECTED, user, request)
    return access


@transaction.atomic
def withdraw(user, access_id, request=None):
    access = _pending(access_id)
    if access.requested_by_id != user.pk:
        raise PermissionDenied("Only the person who asked can withdraw a request.")
    access.status = ChatAccessRequest.Status.WITHDRAWN
    access.save(update_fields=["status"])
    _log(access, ChatAccessLog.Event.WITHDRAWN, user, request)
    return access


def is_open(access):
    return (access.status == ChatAccessRequest.Status.APPROVED and access.access_until is not None
            and access.access_until > timezone.now())


def request_row(access, viewer):
    return {
        "id": access.pk,
        "requested_by": person_row(access.requested_by),
        "subject": person_row(access.subject),
        "reason": access.reason,
        "reason_label": access.get_reason_display(),
        "explanation": access.explanation,
        "period_start": access.period_start.isoformat(),
        "period_end": access.period_end.isoformat(),
        "created_at": access.created_at.isoformat(),
        "status": access.status,
        "status_label": access.get_status_display(),
        "decided_by": person_row(access.decided_by) if access.decided_by else None,
        "decided_at": access.decided_at.isoformat() if access.decided_at else None,
        "decision_note": access.decision_note,
        "access_until": access.access_until.isoformat() if access.access_until else None,
        "can_read": access.requested_by_id == viewer.pk and is_open(access),
        "can_decide": (access.status == ChatAccessRequest.Status.PENDING and holds(viewer, APPROVE_PERMISSION)
                       and viewer.pk not in (access.requested_by_id, access.subject_id)),
        "can_withdraw": access.status == ChatAccessRequest.Status.PENDING and access.requested_by_id == viewer.pk,
    }


def visible_requests(user):
    """Requesters see their own requests; approvers see every request and its log."""
    caps = capabilities(user)
    if not (caps["can_request"] or caps["can_approve"]):
        raise PermissionDenied("You have not been given a chat review role.")
    rows = ChatAccessRequest.objects.select_related("requested_by", "subject", "decided_by")
    if not caps["can_approve"]:
        rows = rows.filter(requested_by=user)
    return rows


def access_log(user, access_id):
    access = visible_requests(user).filter(pk=access_id).first()
    if access is None:
        raise NotFound("Request not found.")
    return [{
        "event": entry.event, "event_label": entry.get_event_display(), "user": display_name(entry.user),
        "occurred_at": entry.occurred_at.isoformat(), "conversation": entry.conversation_id,
    } for entry in access.log.select_related("user")]


def _open_for(user, access_id):
    access = ChatAccessRequest.objects.select_related("subject").filter(pk=access_id, requested_by=user).first()
    if access is None:
        raise NotFound("Request not found.")
    if not is_open(access):
        raise PermissionDenied("This request is not approved, or its reading time has ended.")
    if not holds(user, REQUEST_PERMISSION):
        raise PermissionDenied("You no longer have the chat review role.")
    return access


def _window(access):
    zone = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(access.period_start, time.min), zone)
    end = timezone.make_aware(datetime.combine(access.period_end + timedelta(days=1), time.min), zone)
    return start, end


def reviewed_conversations(user, access_id, request=None):
    access = _open_for(user, access_id)
    start, end = _window(access)
    ids = Participant.objects.filter(user=access.subject).values("conversation_id")
    conversations = Conversation.objects.filter(pk__in=ids, messages__created_at__gte=start, messages__created_at__lt=end).distinct()
    rows = []
    for conversation in conversations.prefetch_related("participants__user"):
        people = [row.user for row in conversation.participants.all()]
        title = conversation.title if conversation.kind != Conversation.Kind.DIRECT else " and ".join(sorted(display_name(p) for p in people))
        count = conversation.messages.filter(created_at__gte=start, created_at__lt=end).count()
        rows.append({"id": conversation.pk, "kind": conversation.kind, "title": title,
                     "members": [person_row(person) for person in people], "messages_in_period": count})
    _log(access, ChatAccessLog.Event.LISTED, user, request, conversations=len(rows))
    return {"request": request_row(access, user), "conversations": rows}


def reviewed_messages(user, access_id, conversation_id, request=None):
    access = _open_for(user, access_id)
    conversation = Conversation.objects.filter(pk=conversation_id, participants__user=access.subject).first()
    if conversation is None:
        raise NotFound("Conversation not found in this review.")
    start, end = _window(access)
    messages = (Message.objects.filter(conversation=conversation, created_at__gte=start, created_at__lt=end)
                .select_related("sender").prefetch_related("attachments"))
    rows = [message_row(message) for message in messages.order_by("id")[:2000]]
    _log(access, ChatAccessLog.Event.READ, user, request, conversation=conversation, messages=len(rows))
    return {"messages": rows}


def reviewed_attachment(user, access_id, attachment_id, request=None):
    access = _open_for(user, access_id)
    start, end = _window(access)
    attachment = ChatAttachment.objects.select_related("message__conversation").filter(
        pk=attachment_id, message__created_at__gte=start, message__created_at__lt=end,
        message__conversation__participants__user=access.subject,
    ).first()
    if attachment is None:
        raise NotFound("File not found in this review.")
    _log(access, ChatAccessLog.Event.DOWNLOADED, user, request, conversation=attachment.message.conversation,
         attachment=attachment.pk)
    return attachment
