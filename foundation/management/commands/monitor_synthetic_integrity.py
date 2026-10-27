import json

from django.core.management.base import BaseCommand, CommandError

from foundation.operations_monitoring import summarize_synthetic_integrity


class Command(BaseCommand):
    help = "Summarize three local synthetic integrity checks; read-only, no notifications or repairs."

    def handle(self, *args, **options):
        result = summarize_synthetic_integrity()
        self.stdout.write(json.dumps(result, sort_keys=True))
        if result["failed_check_count"]:
            raise CommandError("Synthetic checks failed to run; see the summary. Nothing was repaired.")
        if result["completed_check_discrepancy_count"]:
            raise CommandError("Synthetic discrepancies found; see the summary. Nothing was repaired.")
