"""Read-side reports over the audit log: usage per user, one user's activity, security alerts."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Max, Q
from django.utils import timezone

from .models import AuditEvent, UserSession

ALERT_KINDS = [
    AuditEvent.Kind.SIGN_IN_FAILED,
    AuditEvent.Kind.SIGN_IN_REFUSED,
    AuditEvent.Kind.SIGN_IN_LOCKED,
    AuditEvent.Kind.ACCESS_DENIED,
    AuditEvent.Kind.ACCOUNT_CREATED,
    AuditEvent.Kind.ACCOUNT_CHANGED,
]


def _seconds(session, now):
    end = session.ended_at or session.last_seen_at
    return max(int((min(end, now) - session.started_at).total_seconds()), 0)


def event_row(event):
    return {
        "id": event.pk,
        "occurred_at": event.occurred_at.isoformat(),
        "kind": event.kind,
        "label": AuditEvent.Kind(event.kind).label,
        "username": event.username,
        "user_id": event.user_id,
        "ip_address": event.ip_address,
        "user_agent": event.user_agent,
        "method": event.method,
        "path": event.path,
        "status_code": event.status_code,
        "detail": event.detail,
    }


def usage_by_user(*, start, end):
    now = timezone.now()
    sessions = UserSession.objects.filter(started_at__lt=end).filter(
        Q(ended_at__isnull=True, last_seen_at__gte=start) | Q(ended_at__gte=start)
    ).select_related("user")
    per_user = {}
    for session in sessions:
        row = per_user.setdefault(session.user_id, {"sessions": 0, "seconds": 0})
        row["sessions"] += 1
        row["seconds"] += _seconds(session, now)
    events = (
        AuditEvent.objects.filter(occurred_at__gte=start, occurred_at__lt=end, user__isnull=False)
        .values("user_id")
        .annotate(
            actions=Count("pk", filter=Q(kind=AuditEvent.Kind.ACTION)),
            failed=Count("pk", filter=Q(kind=AuditEvent.Kind.SIGN_IN_FAILED)),
            denied=Count("pk", filter=Q(kind=AuditEvent.Kind.ACCESS_DENIED)),
            last_sign_in=Max("occurred_at", filter=Q(kind=AuditEvent.Kind.SIGN_IN)),
        )
    )
    counts = {row["user_id"]: row for row in events}
    users = get_user_model().objects.filter(pk__in=set(per_user) | set(counts)).order_by("username")
    rows = []
    for user in users:
        usage = per_user.get(user.pk, {"sessions": 0, "seconds": 0})
        count = counts.get(user.pk, {})
        last_ip = (
            AuditEvent.objects.filter(user=user, kind=AuditEvent.Kind.SIGN_IN)
            .values_list("ip_address", flat=True).first()
        )
        rows.append({
            "user_id": user.pk,
            "username": user.get_username(),
            "name": user.get_full_name(),
            "is_active": user.is_active,
            "sessions": usage["sessions"],
            "active_seconds": usage["seconds"],
            "actions": count.get("actions", 0),
            "failed_sign_ins": count.get("failed", 0),
            "access_denied": count.get("denied", 0),
            "last_sign_in": count["last_sign_in"].isoformat() if count.get("last_sign_in") else None,
            "last_address": last_ip,
        })
    rows.sort(key=lambda row: (-row["active_seconds"], row["username"]))
    return rows


def security_summary(*, start, end):
    window = AuditEvent.objects.filter(occurred_at__gte=start, occurred_at__lt=end)
    by_kind = dict(window.values_list("kind").annotate(total=Count("pk")).values_list("kind", "total"))
    failed = window.filter(kind=AuditEvent.Kind.SIGN_IN_FAILED)
    return {
        "sign_ins": by_kind.get(AuditEvent.Kind.SIGN_IN, 0),
        "failed_sign_ins": by_kind.get(AuditEvent.Kind.SIGN_IN_FAILED, 0),
        "unknown_account_attempts": failed.filter(detail__unknown_account=True).count(),
        "lockouts": by_kind.get(AuditEvent.Kind.SIGN_IN_LOCKED, 0),
        "refused_sign_ins": by_kind.get(AuditEvent.Kind.SIGN_IN_REFUSED, 0),
        "access_denied": by_kind.get(AuditEvent.Kind.ACCESS_DENIED, 0),
        "accounts_created": by_kind.get(AuditEvent.Kind.ACCOUNT_CREATED, 0),
        "accounts_changed": by_kind.get(AuditEvent.Kind.ACCOUNT_CHANGED, 0),
        "sign_ins_from_new_address": window.filter(kind=AuditEvent.Kind.SIGN_IN, detail__new_address=True).count(),
        "failed_by_address": [
            {"ip_address": row["ip_address"], "attempts": row["total"]}
            for row in failed.values("ip_address").annotate(total=Count("pk")).order_by("-total")[:10]
        ],
        "recent_alerts": [event_row(event) for event in window.filter(kind__in=ALERT_KINDS)[:50]],
        "background_jobs": by_kind.get(AuditEvent.Kind.SYSTEM_JOB, 0),
        "recent_jobs": [event_row(event) for event in window.filter(kind=AuditEvent.Kind.SYSTEM_JOB)[:20]],
    }


def default_window(params):
    from datetime import date, datetime, time
    now = timezone.now()
    tz = timezone.get_current_timezone()

    def parse(name):
        value = params.get(name)
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"Give {name} as YYYY-MM-DD.") from None

    start_day, end_day = parse("from"), parse("to")
    start = timezone.make_aware(datetime.combine(start_day, time.min), tz) if start_day else now - timedelta(days=30)
    end = timezone.make_aware(datetime.combine(end_day, time.min), tz) + timedelta(days=1) if end_day else now + timedelta(seconds=1)
    if end <= start:
        raise ValueError("The end date must be on or after the start date.")
    return start, end
