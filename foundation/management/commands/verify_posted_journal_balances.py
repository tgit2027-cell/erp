import json

from django.core.management.base import BaseCommand, CommandError

from foundation.journal_verification import verify_posted_journal_balances


class Command(BaseCommand):
    help = "Read-only local synthetic posted-journal balance verification; no posting or repair."

    def handle(self, *args, **options):
        result = verify_posted_journal_balances()
        self.stdout.write(json.dumps(result, sort_keys=True))
        if result["discrepancy_count"]:
            raise CommandError("Synthetic posted-journal discrepancies found. Nothing was repaired.")
