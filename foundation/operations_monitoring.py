"""Local synthetic operator summary; no scheduling, persistence or remediation."""

from django.core.management.base import CommandError
from django.db import connection

from .journal_verification import verify_posted_journal_balances
from .management.commands.snapshot_integrity import Command as IntegritySnapshotCommand
from .stock_reconciliation import reconcile_stock_quantities
from .transfer_verification import verify_stock_transfer_pairs


def summarize_synthetic_integrity():
    # Refuse unsafe targets before running any check, even if a check is replaced.
    IntegritySnapshotCommand._require_local_demo_database()
    if connection.in_atomic_block:
        raise CommandError(
            "Run operations monitoring outside an existing transaction so each "
            "diagnostic can establish its read-only snapshot."
        )
    checks = []
    for name, diagnostic in (
        ("stock_quantities", reconcile_stock_quantities),
        ("transfer_pairs", verify_stock_transfer_pairs),
        ("posted_journals", verify_posted_journal_balances),
    ):
        try:
            result = diagnostic()
        except Exception as error:
            # Never echo arbitrary exception messages (SQL, credentials or data).
            # A failed check is not evidence of consistency; continue the others.
            checks.append({
                "name": name,
                "execution": "failed_to_run",
                "outcome": "unavailable",
                "result": None,
                "error": {
                    "type": type(error).__name__,
                    "message": "Diagnostic execution failed; no consistency conclusion is available.",
                },
            })
        else:
            checks.append({
                "name": name,
                "execution": "passed",
                "outcome": (
                    "discrepancies" if result["discrepancy_count"]
                    else ("empty" if result["status"] == "empty" else "no_discrepancies")
                ),
                "result": result,
                "error": None,
            })
    failures = sum(check["execution"] == "failed_to_run" for check in checks)
    discrepancies = sum(
        check["result"]["discrepancy_count"]
        for check in checks if check["execution"] == "passed"
    )
    return {
        "check": "synthetic_operations_integrity_summary",
        "label": "SYNTHETIC DATA ONLY — local read-only diagnostic, not production acceptance",
        "read_only": True,
        "shared_snapshot": False,
        "snapshot_scope": "Separate REPEATABLE READ, READ ONLY transaction per diagnostic, in listed order.",
        "status": "failed_to_run" if failures else ("discrepancies" if discrepancies else "passed"),
        "failed_check_count": failures,
        "completed_check_discrepancy_count": discrepancies,
        "checks": checks,
    }
