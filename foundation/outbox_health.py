"""Local synthetic outbox metadata only; never executes or retries the worker."""

from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.models import Case, CharField, Count, Value, When

from .management.commands.snapshot_integrity import Command as IntegritySnapshotCommand
from .models import NotificationOutboxIntent
from .notification_outbox import MAX_DRY_RUN_ATTEMPTS, NOTIFICATION_EVENT_KINDS


def summarize_notification_outbox_health():
    IntegritySnapshotCommand._require_local_demo_database()
    if connection.in_atomic_block:
        raise CommandError(
            "Run outbox health outside an existing transaction to establish its read-only snapshot."
        )
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        intents = NotificationOutboxIntent.objects.all()
        if intents.exclude(
            is_synthetic=True,
            workflow_event__is_synthetic=True,
            workflow_event__kind__in=NOTIFICATION_EVENT_KINDS,
            workflow_event__quote__is_synthetic=True,
            workflow_event__quote__business_unit__is_synthetic=True,
            workflow_event__quote__business_unit__legal_entity__is_synthetic=True,
            workflow_event__quote__customer__is_synthetic=True,
        ).exists():
            raise CommandError("Refusing outbox facts outside the supported synthetic notification scope.")

        # Classify failure codes inside SQL; never retrieve arbitrary raw strings
        # or event message/details, identities, counterparties, or quote values.
        grouped = intents.order_by().annotate(safe_error_code=Case(
            When(last_error_code="", then=Value("")),
            When(last_error_code="FAKE_SINK_FAILURE", then=Value("FAKE_SINK_FAILURE")),
            default=Value("REDACTED_UNRECOGNIZED"),
            output_field=CharField(),
        )).values("status", "attempt_count", "safe_error_code").annotate(count=Count("pk"))
        counts = {status: 0 for status in NotificationOutboxIntent.Status.values}
        attempts = {str(number): 0 for number in range(MAX_DRY_RUN_ATTEMPTS + 1)}
        failure_codes = {}
        total_attempts = 0
        for row in grouped:
            counts[row["status"]] += row["count"]
            attempts[str(row["attempt_count"])] += row["count"]
            total_attempts += row["attempt_count"] * row["count"]
            if row["safe_error_code"]:
                code = row["safe_error_code"]
                failure_codes[code] = failure_codes.get(code, 0) + row["count"]
        return {
            "check": "synthetic_notification_outbox_health",
            "label": "SYNTHETIC DATA ONLY — dry-run intent records, not externally delivered notifications.",
            "read_only": True,
            "snapshot_scope": "One REPEATABLE READ, READ ONLY PostgreSQL transaction.",
            "status": "recorded_metadata" if sum(counts.values()) else "empty",
            "intent_count": sum(counts.values()),
            "counts_by_status": counts,
            "attempts": {
                "total_recorded_attempts": total_attempts,
                "intent_counts_by_attempt_count": attempts,
                "existing_worker_attempt_limit": MAX_DRY_RUN_ATTEMPTS,
            },
            "failure_code_counts": dict(sorted(failure_codes.items())),
            "external_notification_sent_by_this_command": False,
            "external_delivery_verified": False,
        }
