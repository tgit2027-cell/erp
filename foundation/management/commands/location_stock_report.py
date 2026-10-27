import json
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Sum

from foundation.models import Location, StockBalance, StockMovement
from foundation.stock_reconciliation import reconcile_stock_quantities


def location_totals():
    """Stock per location, keyed by the location's database id (which a rename keeps)."""
    balances = {
        row["location_id"]: row
        for row in StockBalance.objects.values("location_id").annotate(
            balances=Count("pk"), on_hand=Sum("on_hand_quantity"), value=Sum("on_hand_value"),
            reserved=Sum("reserved_quantity"),
        )
    }
    movements = dict(StockMovement.objects.values_list("balance__location_id").annotate(count=Count("pk")))
    rows = {}
    for location in Location.objects.order_by("code"):
        row = balances.get(location.pk, {})
        rows[str(location.pk)] = {
            "code": location.code,
            "kind": location.kind,
            "is_active": location.is_active,
            "balances": row.get("balances", 0),
            "on_hand_quantity": format(row.get("on_hand") or Decimal("0.00"), "f"),
            "on_hand_value": format(row.get("value") or Decimal("0.00"), "f"),
            "reserved_quantity": format(row.get("reserved") or Decimal("0.00"), "f"),
            "movements": movements.get(location.pk, 0),
        }
    return rows


class Command(BaseCommand):
    help = (
        "Read-only stock report per location for checking a location migration: run it before and after, "
        "then pass the earlier file with --compare. Nothing is changed."
    )

    def add_arguments(self, parser):
        parser.add_argument("--output", help="Write the report to this JSON file as well.")
        parser.add_argument("--compare", help="A report saved before the migration; every location must match.")

    def handle(self, *args, **options):
        totals = location_totals()
        quantities = reconcile_stock_quantities()
        stranded = [row["code"] for row in totals.values() if not row["is_active"] and row["on_hand_quantity"] != "0.00"]
        report = {"locations": totals, "stock_reconciliation_discrepancies": quantities["discrepancy_count"],
                  "retired_locations_holding_stock": stranded}
        problems = []
        if options["compare"]:
            with open(options["compare"], encoding="utf-8") as handle:
                before = json.load(handle)["locations"]
            fields = ("balances", "on_hand_quantity", "on_hand_value", "reserved_quantity", "movements")
            for location_id, earlier in before.items():
                now = totals.get(location_id)
                if now is None:
                    if earlier["balances"] or earlier["movements"]:
                        problems.append(f"{earlier['code']} had stock or history and no longer exists")
                    continue
                changed = [field for field in fields if now[field] != earlier[field]]
                if changed:
                    problems.append(f"{earlier['code']} -> {now['code']}: {', '.join(changed)} changed")
            report["compared_with"] = options["compare"]
            report["differences"] = problems
        text = json.dumps(report, indent=2, sort_keys=True)
        if options["output"]:
            with open(options["output"], "w", encoding="utf-8") as handle:
                handle.write(text)
        self.stdout.write(text)
        if quantities["discrepancy_count"] or problems:
            raise CommandError("Stock does not reconcile; see the report. Nothing was changed.")
