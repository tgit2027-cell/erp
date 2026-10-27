from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from ...notification_outbox import (
    DRY_RUN_NOTICE,
    InMemoryFakeNotificationSink,
    MAX_DRY_RUN_BATCH,
    process_notification_outbox_dry_run,
)


class Command(BaseCommand):
    help = "Process synthetic notification intents through an in-memory dry-run sink only."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10)

    def handle(self, *args, **options):
        self._require_local_demo_database()
        limit = options["limit"]
        if not 1 <= limit <= MAX_DRY_RUN_BATCH:
            raise CommandError(f"--limit must be from 1 through {MAX_DRY_RUN_BATCH}.")

        sink = InMemoryFakeNotificationSink()
        outcomes = process_notification_outbox_dry_run(sink=sink, limit=limit)
        self.stdout.write(f"{DRY_RUN_NOTICE} No email, WhatsApp, SMS, or network delivery is configured.")
        if not outcomes:
            self.stdout.write("No pending synthetic notification intents.")
            return
        for outcome in outcomes:
            self.stdout.write(
                f"Intent {outcome['intent_id']}: {outcome['status']} "
                f"(attempt {outcome['attempt_count']}); external_notification_sent=false."
            )

    @staticmethod
    def _require_local_demo_database():
        if settings.APP_ENV != "demo":
            raise CommandError("Refusing to run unless APP_ENV=demo is explicitly set.")
        if connection.vendor != "postgresql":
            raise CommandError("The dry-run outbox worker requires PostgreSQL.")
        database = connection.settings_dict
        if database.get("NAME") not in {"tivoli_erp_demo", "test_tivoli_erp_demo"}:
            raise CommandError("Refusing an unrecognized database; expected the local synthetic demo database.")
        if database.get("HOST", "") not in {"", "localhost", "127.0.0.1", "::1"}:
            raise CommandError("Refusing a non-local database.")
