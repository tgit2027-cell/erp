import json

from django.core.management.base import BaseCommand, CommandError

from foundation.transfer_verification import verify_stock_transfer_pairs


class Command(BaseCommand):
    help = "Read-only local synthetic transfer-pair structure verification; no repair or valuation."

    def handle(self, *args, **options):
        result = verify_stock_transfer_pairs()
        self.stdout.write(json.dumps(result, sort_keys=True))
        if result["discrepancy_count"]:
            raise CommandError("Synthetic transfer-pair discrepancies found. Nothing was repaired.")
