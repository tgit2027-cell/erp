from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from .models import NotificationOutboxIntent, WorkflowEvent


DRY_RUN_NOTICE = "DRY-RUN: recorded intent only; no external notification sent."
MAX_DRY_RUN_ATTEMPTS = 3
MAX_DRY_RUN_BATCH = 50
NOTIFICATION_EVENT_KINDS = frozenset(
    {
        WorkflowEvent.Kind.READINESS_NOTIFICATION,
        WorkflowEvent.Kind.STORAGE_CHARGE_NOTIFICATION,
    }
)


class NotificationOutboxError(Exception):
    pass


def enqueue_notification_intent(workflow_event):
    """Persist a minimal notification intent alongside its triggering event."""
    if not connection.in_atomic_block:
        raise NotificationOutboxError("Notification intents must be enqueued in the triggering transaction.")
    if settings.APP_ENV != "demo" or not workflow_event.is_synthetic:
        raise NotificationOutboxError("Notification intents are restricted to synthetic demo events.")
    if workflow_event.kind not in NOTIFICATION_EVENT_KINDS:
        raise NotificationOutboxError("This workflow event kind is not a notification intent.")
    intent, _created = NotificationOutboxIntent.objects.get_or_create(
        workflow_event=workflow_event,
        defaults={
            "status": NotificationOutboxIntent.Status.PENDING,
            "attempt_count": 0,
            "is_synthetic": True,
        },
    )
    return intent


class InMemoryFakeNotificationSink:
    """A test-only sink. It stores dry-run intents in memory and has no I/O adapters."""

    def __init__(self, *, failures_remaining=0):
        if not isinstance(failures_remaining, int) or failures_remaining < 0:
            raise ValueError("failures_remaining must be a nonnegative integer.")
        self.failures_remaining = failures_remaining
        self.attempts = []
        self.recorded_by_intent_id = {}

    def record_intent(self, intent):
        safe_intent = dict(intent)
        if safe_intent.get("dry_run") is not True or safe_intent.get("external_notification_sent") is not False:
            raise NotificationOutboxError("The fake sink accepts dry-run intents only.")
        if "recipient" in safe_intent or "channel" in safe_intent or "message" in safe_intent:
            raise NotificationOutboxError("The fake sink does not accept recipients, channels, or message content.")
        self.attempts.append(safe_intent)
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("Synthetic fake-sink failure.")
        self.recorded_by_intent_id.setdefault(safe_intent["intent_id"], safe_intent)


def _intent_payload(intent):
    event = intent.workflow_event
    return {
        "intent_id": intent.pk,
        "workflow_event_id": event.pk,
        "event_kind": event.kind,
        "quote_id": event.quote_id,
        "dry_run": True,
        "external_notification_sent": False,
        "label": DRY_RUN_NOTICE,
    }


def process_notification_outbox_dry_run(*, sink, limit=10):
    """Attempt each queued intent once using only the fixed in-memory fake sink."""
    if settings.APP_ENV != "demo":
        raise NotificationOutboxError("The notification outbox worker is enabled only in APP_ENV=demo.")
    if connection.in_atomic_block:
        raise NotificationOutboxError("Run the dry-run worker only after the triggering transaction commits.")
    if type(sink) is not InMemoryFakeNotificationSink:
        raise NotificationOutboxError("Only the in-memory fake notification sink is permitted.")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_DRY_RUN_BATCH:
        raise ValueError(f"limit must be an integer from 1 through {MAX_DRY_RUN_BATCH}.")

    eligible = NotificationOutboxIntent.objects.filter(
        status__in=[NotificationOutboxIntent.Status.PENDING, NotificationOutboxIntent.Status.RETRY],
        attempt_count__lt=MAX_DRY_RUN_ATTEMPTS,
        is_synthetic=True,
        workflow_event__is_synthetic=True,
        workflow_event__kind__in=NOTIFICATION_EVENT_KINDS,
    ).order_by("created_at", "pk")
    intent_ids = list(eligible.values_list("pk", flat=True)[:limit])
    outcomes = []

    for intent_id in intent_ids:
        with transaction.atomic():
            intent = (
                NotificationOutboxIntent.objects.select_for_update(skip_locked=True)
                .select_related("workflow_event")
                .filter(pk=intent_id)
                .first()
            )
            if (
                intent is None
                or intent.status not in (NotificationOutboxIntent.Status.PENDING, NotificationOutboxIntent.Status.RETRY)
                or intent.attempt_count >= MAX_DRY_RUN_ATTEMPTS
                or not intent.is_synthetic
                or not intent.workflow_event.is_synthetic
                or intent.workflow_event.kind not in NOTIFICATION_EVENT_KINDS
            ):
                continue

            intent.attempt_count += 1
            intent.last_attempt_at = timezone.now()
            try:
                sink.record_intent(_intent_payload(intent))
            except Exception:
                intent.status = (
                    NotificationOutboxIntent.Status.RETRY
                    if intent.attempt_count < MAX_DRY_RUN_ATTEMPTS
                    else NotificationOutboxIntent.Status.DRY_RUN_FAILED
                )
                intent.last_error_code = "FAKE_SINK_FAILURE"
            else:
                intent.status = NotificationOutboxIntent.Status.DRY_RUN_RECORDED
                intent.last_error_code = ""
            intent.save(
                update_fields=[
                    "attempt_count",
                    "last_attempt_at",
                    "status",
                    "last_error_code",
                    "updated_at",
                ]
            )
            outcomes.append(
                {
                    "intent_id": intent.pk,
                    "status": intent.status,
                    "attempt_count": intent.attempt_count,
                    "label": DRY_RUN_NOTICE,
                    "external_notification_sent": False,
                }
            )

    return outcomes
