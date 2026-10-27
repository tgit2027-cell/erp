"""Local operator diagnostic only: quantity checks, never repairs or valuation."""

from decimal import Decimal

from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.models import Sum

from .management.commands.snapshot_integrity import Command as IntegritySnapshotCommand
from .models import StockBalance, StockMovement, StockReservation


def reconcile_stock_quantities():
    # Reuse the established local synthetic-demo/restore operator boundary.
    # This is not an HTTP service and does not confer any ERP-user capability.
    IntegritySnapshotCommand._require_local_demo_database()
    if connection.in_atomic_block:
        raise CommandError("Run reconciliation outside an existing transaction so its read-only snapshot can be established.")

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")

        balances = StockBalance.objects.filter(
            is_synthetic=True,
            legal_entity__is_synthetic=True,
            business_unit__is_synthetic=True,
            location__is_synthetic=True,
            location__legal_entity__is_synthetic=True,
            variant__is_synthetic=True,
            variant__item__is_synthetic=True,
            variant__item__brand__is_synthetic=True,
        ).order_by("pk")
        rows = list(balances.values("pk", "on_hand_quantity", "reserved_quantity"))
        movement_totals = dict(
            StockMovement.objects.filter(
                is_synthetic=True, balance_id__in=balances.values("pk"),
            ).order_by().values("balance_id").annotate(
                quantity=Sum("quantity_delta"),
            ).values_list("balance_id", "quantity")
        )
        reservation_totals = dict(
            StockReservation.objects.filter(
                is_synthetic=True,
                balance_id__in=balances.values("pk"),
                status=StockReservation.Status.ACTIVE,
            ).order_by().values("balance_id").annotate(
                quantity_total=Sum("quantity"),
            ).values_list("balance_id", "quantity_total")
        )
        discrepancies = []
        for row in rows:
            for field, totals in (
                ("on_hand_quantity", movement_totals),
                ("reserved_quantity", reservation_totals),
            ):
                stored = row[field]
                derived = totals.get(row["pk"], Decimal("0.00"))
                if stored != derived:
                    discrepancies.append({
                        "balance_id": row["pk"],
                        "field": field,
                        "stored_quantity": format(stored, ".2f"),
                        "derived_quantity": format(derived, ".2f"),
                        "difference": format(stored - derived, ".2f"),
                    })
        return {
            "check": "synthetic_stock_quantity_reconciliation",
            "status": "mismatch" if discrepancies else ("consistent" if rows else "empty"),
            "read_only": True,
            "checked_buckets": len(rows),
            "discrepancy_count": len(discrepancies),
            "discrepancies": discrepancies,
            "basis": "TEST ONLY: on hand = movement quantity sum; reserved = active reservation sum. No repairs or valuation.",
        }
