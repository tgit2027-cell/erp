"""Offline synthetic posted-journal checks; not accounting policy or repair."""

from collections import defaultdict
from decimal import Decimal, localcontext

from django.core.management.base import CommandError
from django.db import connection, transaction

from .management.commands.snapshot_integrity import Command as IntegritySnapshotCommand
from .models import JournalEntry, JournalLine


HEADER_FIELDS = ("pk", "legal_entity_id", "currency")
LINE_FIELDS = (
    "pk", "entry_id", "account__legal_entity_id", "account__currency", "debit", "credit",
)


def _journal_discrepancies(headers, lines):
    """Compare copied facts without SQL, rounding, netting entries or mutations."""
    grouped = defaultdict(list)
    for line in lines:
        grouped[line["entry_id"]].append(line)
    discrepancies = []
    # Numeric(16,2) facts can sum beyond a single column's precision. Do not
    # inherit a caller's reduced Decimal precision or introduce money rounding.
    with localcontext() as context:
        context.prec = max(28, 16 + len(str(len(lines))))
        for header in headers:
            entry_lines = grouped[header["pk"]]

            def report(code, **facts):
                discrepancies.append({
                    "entry_id": header["pk"], "legal_entity_id": header["legal_entity_id"],
                    "code": code, **facts,
                })

            if header["currency"] != "QAR":
                report("journal_currency", expected="QAR", actual=header["currency"])
            if len(entry_lines) < 2:
                report("line_count", expected_minimum=2, actual=len(entry_lines))
            debit = sum((line["debit"] for line in entry_lines), Decimal("0.00"))
            credit = sum((line["credit"] for line in entry_lines), Decimal("0.00"))
            if debit != credit:
                report(
                    "unbalanced", currency=header["currency"],
                    debit_total=format(debit, ".2f"), credit_total=format(credit, ".2f"),
                    difference=format(debit - credit, ".2f"),
                )
            for line in entry_lines:
                if line["account__legal_entity_id"] != header["legal_entity_id"]:
                    report(
                        "account_legal_entity", line_id=line["pk"],
                        expected_legal_entity_id=header["legal_entity_id"],
                        actual_legal_entity_id=line["account__legal_entity_id"],
                    )
                if line["account__currency"] != "QAR":
                    report(
                        "account_currency", line_id=line["pk"],
                        expected="QAR", actual=line["account__currency"],
                    )
    return sorted(discrepancies, key=lambda row: (
        row["entry_id"], row["code"], row.get("line_id", 0),
    ))


def verify_posted_journal_balances():
    """Read existing synthetic posted entries in one database-enforced snapshot."""
    IntegritySnapshotCommand._require_local_demo_database()
    if connection.in_atomic_block:
        raise CommandError(
            "Run journal verification outside an existing transaction so its read-only snapshot can be established."
        )
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        entries = JournalEntry.objects.filter(status=JournalEntry.Status.POSTED).order_by("pk")
        if entries.exclude(is_synthetic=True, legal_entity__is_synthetic=True).exists():
            raise CommandError("Refusing posted journals with non-synthetic related records.")
        headers = list(entries.values(*HEADER_FIELDS))
        entry_lines = JournalLine.objects.filter(entry_id__in=entries.values("pk")).order_by("pk")
        if entry_lines.exclude(
            is_synthetic=True, account__is_synthetic=True, account__legal_entity__is_synthetic=True,
        ).exists():
            raise CommandError("Refusing journal lines with non-synthetic related records.")
        lines = list(entry_lines.values(*LINE_FIELDS))
        discrepancies = _journal_discrepancies(headers, lines)
        return {
            "check": "synthetic_posted_journal_balance_verification",
            "status": "mismatch" if discrepancies else ("consistent" if headers else "empty"),
            "read_only": True,
            "checked_entries": len(headers),
            "checked_lines": len(lines),
            "checked_legal_entities": len({header["legal_entity_id"] for header in headers}),
            "discrepancy_count": len(discrepancies),
            "discrepancies": discrepancies,
            "basis": (
                "TEST ONLY: each posted journal has at least two lines, equal exact debits/credits, "
                "and accounts in its legal entity in QAR. No repairs or accounting-policy approval."
            ),
        }
