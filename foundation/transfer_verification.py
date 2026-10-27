"""Local synthetic operator diagnostic: transfer structure, never repairs or value."""

from collections import defaultdict

from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.models import Q

from .management.commands.snapshot_integrity import Command as IntegritySnapshotCommand
from .models import StockMovement, StockTransfer


TRANSFER_KINDS = (StockMovement.Kind.TRANSFER_OUT, StockMovement.Kind.TRANSFER_IN)
BUCKET_FIELDS = ("legal_entity_id", "business_unit_id", "variant_id", "location_id")
SYNTHETIC_BUCKET_PATHS = (
    "is_synthetic", "legal_entity__is_synthetic", "business_unit__is_synthetic",
    "business_unit__legal_entity__is_synthetic", "location__is_synthetic",
    "location__legal_entity__is_synthetic", "variant__is_synthetic",
    "variant__item__is_synthetic", "variant__item__brand__is_synthetic",
)
HEADER_FIELDS = (
    "pk", "quantity", "idempotency_id", "idempotency__operation", "idempotency__status",
    "source_balance_id", "destination_balance_id",
    *(f"{side}_balance__{field}" for side in ("source", "destination") for field in BUCKET_FIELDS),
)
MOVEMENT_FIELDS = ("pk", "transfer_id", "balance_id", "idempotency_id", "kind", "quantity_delta")


def _synthetic_bucket(prefix):
    return {f"{prefix}__{path}": True for path in SYNTHETIC_BUCKET_PATHS}


def _verify_pair(header, movements, discrepancies):
    def report(code, **details):
        discrepancies.append({"transfer_id": header["pk"], "code": code, **details})

    if header["idempotency__operation"] != "transfer":
        report("operation_reference", field="operation", expected="transfer",
               actual=header["idempotency__operation"])
    if header["idempotency__status"] != "complete":
        report("operation_reference", field="status", expected="complete",
               actual=header["idempotency__status"])
    # Existing transfers keep company, unit and variant; only location changes.
    for field in BUCKET_FIELDS[:-1]:
        source = header[f"source_balance__{field}"]
        destination = header[f"destination_balance__{field}"]
        if source != destination:
            report("bucket_dimensions", field=field, source_id=source, destination_id=destination)
    if header["source_balance__location_id"] == header["destination_balance__location_id"]:
        report("same_location", location_id=header["source_balance__location_id"])

    if len(movements) != 2:
        report("movement_count", expected=2, actual=len(movements))
    by_kind = defaultdict(list)
    for movement in movements:
        by_kind[movement["kind"]].append(movement)
        if movement["kind"] not in TRANSFER_KINDS:
            report("movement_kind", movement_id=movement["pk"])
            continue
        outbound = movement["kind"] == StockMovement.Kind.TRANSFER_OUT
        expected_balance = header["source_balance_id" if outbound else "destination_balance_id"]
        if movement["balance_id"] != expected_balance:
            report("balance_reference", movement_id=movement["pk"],
                   expected_balance_id=expected_balance, actual_balance_id=movement["balance_id"])
        if movement["idempotency_id"] != header["idempotency_id"]:
            report("idempotency_reference", movement_id=movement["pk"],
                   expected_idempotency_id=header["idempotency_id"],
                   actual_idempotency_id=movement["idempotency_id"])
        expected_quantity = -header["quantity"] if outbound else header["quantity"]
        if movement["quantity_delta"] != expected_quantity:
            report("movement_quantity", movement_id=movement["pk"],
                   expected_quantity=format(expected_quantity, ".2f"),
                   actual_quantity=format(movement["quantity_delta"], ".2f"))
    for kind in TRANSFER_KINDS:
        if len(by_kind[kind]) != 1:
            report("side_count", side=kind, expected=1, actual=len(by_kind[kind]))
    outbound, inbound = (by_kind[kind] for kind in TRANSFER_KINDS)
    if len(outbound) == len(inbound) == 1 and (
        outbound[0]["quantity_delta"] != -inbound[0]["quantity_delta"]
    ):
        report("opposite_quantities",
               outbound_quantity=format(outbound[0]["quantity_delta"], ".2f"),
               inbound_quantity=format(inbound[0]["quantity_delta"], ".2f"))


def verify_stock_transfer_pairs():
    """Return deterministic, quantity/reference-only discrepancies in one snapshot."""
    IntegritySnapshotCommand._require_local_demo_database()
    if connection.in_atomic_block:
        raise CommandError(
            "Run transfer verification outside an existing transaction so its read-only snapshot can be established."
        )
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")

        transfers = StockTransfer.objects.filter(is_synthetic=True).order_by("pk")
        # Fail closed, not silently skip a transfer with non-synthetic related facts.
        if transfers.exclude(
            idempotency__is_synthetic=True,
            **_synthetic_bucket("source_balance"),
            **_synthetic_bucket("destination_balance"),
        ).exists():
            raise CommandError("Refusing transfer facts with non-synthetic related records.")
        headers = list(transfers.values(*HEADER_FIELDS))
        movements = StockMovement.objects.filter(is_synthetic=True).filter(
            Q(transfer_id__in=transfers.values("pk")) | Q(kind__in=TRANSFER_KINDS),
        ).order_by("pk")
        if movements.exclude(
            idempotency__is_synthetic=True, **_synthetic_bucket("balance"),
        ).exists():
            raise CommandError("Refusing movement facts with non-synthetic related records.")
        rows = list(movements.values(*MOVEMENT_FIELDS))
        grouped = defaultdict(list)
        discrepancies = []
        header_ids = {header["pk"] for header in headers}
        for movement in rows:
            if movement["transfer_id"] not in header_ids:
                discrepancies.append({
                    "transfer_id": movement["transfer_id"], "movement_id": movement["pk"],
                    "code": "missing_transfer_reference",
                })
            else:
                grouped[movement["transfer_id"]].append(movement)
        for header in headers:
            _verify_pair(header, grouped[header["pk"]], discrepancies)
        discrepancies.sort(key=lambda row: (
            row.get("transfer_id") or 0, row.get("movement_id") or 0,
            row["code"], row.get("field", ""), row.get("side", ""),
        ))
        return {
            "check": "synthetic_stock_transfer_pair_verification",
            "status": "mismatch" if discrepancies else ("consistent" if headers or rows else "empty"),
            "read_only": True,
            "checked_transfers": len(headers),
            "checked_movements": len(rows),
            "discrepancy_count": len(discrepancies),
            "discrepancies": discrepancies,
            "basis": (
                "TEST ONLY: one outbound/inbound per transfer; signed quantities match the header; "
                "bucket and idempotency references agree. No repairs, valuation or access changes."
            ),
        }
