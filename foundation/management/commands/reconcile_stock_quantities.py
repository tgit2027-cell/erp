import json

from django.core.management.base import BaseCommand, CommandError

from foundation.stock_reconciliation import reconcile_stock_quantities


class Command(BaseCommand):
    help = "Read-only local synthetic stock quantity reconciliation; no repairs, values, or permission changes."

    def handle(self, *args, **options):
        result = reconcile_stock_quantities()
        self.stdout.write(json.dumps(result, sort_keys=True))
        if result["discrepancy_count"]:
            raise CommandError("Synthetic stock quantity discrepancies found. Nothing was repaired.")
