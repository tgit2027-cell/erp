"""Write audit records. Nothing here may stop the request it describes."""

import hashlib
import logging
from contextvars import ContextVar
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import AuditEvent, UserSession

log = logging.getLogger(__name__)

# The request being served, so model signals can say who made a change.
current_request = ContextVar("audit_current_request", default=None)

SEEN_INTERVAL = timedelta(seconds=60)


def client_ip(request):
    if request is None:
        return None
    if getattr(settings, "AUDIT_TRUST_X_FORWARDED_FOR", False):
        # The trusted proxy appends the address it saw, so take the last entry.
        # Anything earlier in the header came from the visitor and can be forged.
        forwarded = [part.strip() for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")]
        if forwarded[-1]:
            return forwarded[-1]
    return request.META.get("REMOTE_ADDR") or None


def session_hash(request):
    key = getattr(getattr(request, "session", None), "session_key", None)
    return hashlib.sha256(key.encode()).hexdigest() if key else ""


def _actor(user):
    if user is not None and getattr(user, "is_authenticated", False):
        return user, user.get_username()
    return None, ""


def record(kind, *, request=None, user=None, username="", status_code=None, detail=None):
    """Append one audit event. A failure is logged and never raised."""
    actor, name = _actor(user if user is not None else getattr(request, "user", None))
    try:
        with transaction.atomic():
            return AuditEvent.objects.create(
                kind=kind,
                user=actor,
                username=(username or name)[:150],
                ip_address=client_ip(request),
                user_agent=(request.META.get("HTTP_USER_AGENT", "") if request else "")[:300],
                method=(getattr(request, "method", None) or "")[:8],
                path=(getattr(request, "path", None) or "")[:300],
                status_code=status_code,
                session_hash=session_hash(request) if request else "",
                detail=detail or {},
            )
    except Exception:  # pragma: no cover - the audit trail must not break the app
        log.exception("Could not record audit event %s", kind)
        return None


def start_session(request, user):
    digest = session_hash(request)
    if not digest:
        return None
    now = timezone.now()
    session, _ = UserSession.objects.get_or_create(
        session_hash=digest,
        defaults={
            "user": user, "started_at": now, "last_seen_at": now,
            "ip_address": client_ip(request),
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300],
        },
    )
    return session


def touch_session(request):
    """Mark the session active, at most once a minute so reads stay cheap."""
    digest = session_hash(request)
    if not digest:
        return
    now = timezone.now()
    # Read first so a request inside the interval issues no write at all.
    current = UserSession.objects.filter(session_hash=digest).values("pk", "ended_at", "last_seen_at").first()
    if current is None:
        start_session(request, request.user)
    elif current["ended_at"] is None and current["last_seen_at"] < now - SEEN_INTERVAL:
        UserSession.objects.filter(pk=current["pk"], ended_at__isnull=True).update(last_seen_at=now)


def end_session(request):
    digest = session_hash(request)
    if digest:
        now = timezone.now()
        UserSession.objects.filter(session_hash=digest, ended_at__isnull=True).update(ended_at=now, last_seen_at=now)
