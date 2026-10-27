"""Synthetic-only, non-posting Finance draft foundation.

The normal app exposes none of these operations. Only the separate verified
disposable lab HTTP adapter and isolated tests call them. There is no shared
Finance capability or posting service here. They never create or update
LedgerAccount, JournalEntry, JournalLine or operational stock/accounting records.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from .models import (
    BusinessUnit,
    FinanceDraftAccount,
    FinanceDraftDimension,
    FinanceDraftDimensionValue,
    FinanceDraftJournal,
    FinanceDraftJournalLine,
    FinanceDraftJournalLineDimension,
    FinanceDraftJournalRevision,
    LegalEntity,
)


class FinanceDraftValidationError(ValidationError):
    """A draft-only validation failure; it is never an approval/posting error."""


class FinanceDraftConflict(FinanceDraftValidationError):
    def __init__(self, current_revision):
        self.current_revision = current_revision
        super().__init__("This draft changed in another editor. Your inputs are retained; reload the saved version before editing again.")


ACCOUNT_CODE = re.compile(r"^TEST-DRAFT-ACCT-[A-Z0-9-]{1,23}$")
DIMENSION_CODE = re.compile(r"^TEST-DRAFT-DIM-[A-Z0-9-]{1,20}$")
DIMENSION_VALUE_CODE = re.compile(r"^TEST-DRAFT-VAL-[A-Z0-9-]{1,21}$")


def _require_synthetic_demo(entity):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise FinanceDraftValidationError("This draft prototype is restricted to the synthetic demo environment.")
    if (
        not getattr(settings, "FINANCE_DRAFT_TEST_MODE", False)
        or connection.vendor != "postgresql"
        or not str(connection.settings_dict["NAME"]).startswith("test_")
    ):
        raise FinanceDraftValidationError("Finance draft persistence is restricted to explicitly enabled isolated PostgreSQL test databases.")
    if not isinstance(entity, LegalEntity) or not entity.pk or not entity.is_synthetic:
        raise FinanceDraftValidationError("A persisted synthetic legal entity is required.")


def _required_text(value, field_name, *, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise FinanceDraftValidationError(f"{field_name} must be non-empty and no longer than {maximum} characters.")
    return value.strip()


def _draft_code(value, pattern, label):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise FinanceDraftValidationError(f"{label} must use its TEST-DRAFT prefix and uppercase synthetic code format.")
    return value


def _optional_text(value, field_name, *, maximum):
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise FinanceDraftValidationError(f"{field_name} must be text no longer than {maximum} characters.")
    return value.strip()


def _money(value):
    if isinstance(value, bool) or isinstance(value, float):
        raise FinanceDraftValidationError("Amounts must be exact decimal strings or Decimal values, not floating-point values.")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise FinanceDraftValidationError("Amount must be a valid decimal value.") from None
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        raise FinanceDraftValidationError("Amount must be finite, non-negative, and have at most two decimal places.")
    if amount >= Decimal("1000000000000"):
        raise FinanceDraftValidationError("Amount exceeds the synthetic draft field limit.")
    return amount.quantize(Decimal("0.01"))


def create_finance_draft_account(*, legal_entity, code, name_en, name_ar=""):
    """Create a proposed account definition, never an operational ledger account."""
    _require_synthetic_demo(legal_entity)
    account = FinanceDraftAccount(
        legal_entity=legal_entity,
        code=_draft_code(code, ACCOUNT_CODE, "Account code"),
        name_en=_required_text(name_en, "English account name", maximum=160),
        name_ar=_optional_text(name_ar, "Arabic account name", maximum=160),
        is_synthetic=True,
    )
    account.full_clean()
    account.save()
    return account


def create_finance_draft_dimension(*, legal_entity, code, label_en, label_ar=""):
    """Create a proposed dimension label, with no activated mapping semantics."""
    _require_synthetic_demo(legal_entity)
    dimension = FinanceDraftDimension(
        legal_entity=legal_entity,
        code=_draft_code(code, DIMENSION_CODE, "Dimension code"),
        label_en=_required_text(label_en, "English dimension label", maximum=160),
        label_ar=_optional_text(label_ar, "Arabic dimension label", maximum=160),
        is_synthetic=True,
    )
    dimension.full_clean()
    dimension.save()
    return dimension


def create_finance_draft_dimension_value(*, dimension, code, label_en, label_ar=""):
    if not isinstance(dimension, FinanceDraftDimension) or not dimension.pk or not dimension.is_synthetic:
        raise FinanceDraftValidationError("A persisted synthetic draft dimension is required.")
    _require_synthetic_demo(dimension.legal_entity)
    value = FinanceDraftDimensionValue(
        dimension=dimension,
        code=_draft_code(code, DIMENSION_VALUE_CODE, "Dimension value code"),
        label_en=_required_text(label_en, "English dimension value", maximum=160),
        label_ar=_optional_text(label_ar, "Arabic dimension value", maximum=160),
        is_synthetic=True,
    )
    value.full_clean()
    value.save()
    return value


def _prepare_lines(*, legal_entity, title_en, title_ar="", lines=()):
    _require_synthetic_demo(legal_entity)
    if not isinstance(title_ar, str) or len(title_ar.strip()) > 200:
        raise FinanceDraftValidationError("Arabic journal title must be no longer than 200 characters.")
    if not isinstance(lines, (list, tuple)):
        raise FinanceDraftValidationError("Draft journal lines must be provided as a list.")
    prepared = []
    for index, item in enumerate(lines, start=1):
        if not isinstance(item, dict):
            raise FinanceDraftValidationError(f"Draft line {index} must be an object.")
        account = item.get("account")
        if not isinstance(account, FinanceDraftAccount) or not account.pk:
            raise FinanceDraftValidationError(f"Draft line {index} must use a saved draft account.")
        if account.legal_entity_id != legal_entity.pk or not account.is_synthetic:
            raise FinanceDraftValidationError(f"Draft line {index} account must belong to the same synthetic legal entity.")
        business_unit = item.get("business_unit")
        if business_unit is not None:
            if not isinstance(business_unit, BusinessUnit) or not business_unit.pk:
                raise FinanceDraftValidationError(f"Draft line {index} business unit must be an existing synthetic unit.")
            if business_unit.legal_entity_id != legal_entity.pk or not business_unit.is_synthetic:
                raise FinanceDraftValidationError(f"Draft line {index} business unit is outside the synthetic legal entity.")
        dimensions = item.get("dimensions", ())
        if not isinstance(dimensions, (list, tuple)):
            raise FinanceDraftValidationError(f"Draft line {index} dimensions must be a list.")
        if len({getattr(value, "pk", None) for value in dimensions}) != len(dimensions):
            raise FinanceDraftValidationError(f"Draft line {index} contains a duplicate dimension value.")
        for value in dimensions:
            if not isinstance(value, FinanceDraftDimensionValue) or not value.pk:
                raise FinanceDraftValidationError(f"Draft line {index} must use saved draft dimension values.")
            if value.dimension.legal_entity_id != legal_entity.pk or not value.is_synthetic:
                raise FinanceDraftValidationError(f"Draft line {index} dimension is outside the synthetic legal entity.")
        memo_en = item.get("memo_en", "")
        memo_ar = item.get("memo_ar", "")
        if not isinstance(memo_en, str) or len(memo_en) > 240 or not isinstance(memo_ar, str) or len(memo_ar) > 240:
            raise FinanceDraftValidationError(f"Draft line {index} memos must be at most 240 characters.")
        prepared.append({
            "account": account,
            "business_unit": business_unit,
            "dimensions": list(dimensions),
            "debit": _money(item.get("debit", "0.00")),
            "credit": _money(item.get("credit", "0.00")),
            "memo_en": memo_en.strip(),
            "memo_ar": memo_ar.strip(),
        })

    _required_text(title_en, "English journal title")
    return prepared


def _write_lines(journal, prepared):
    for item in prepared:
        line = FinanceDraftJournalLine.objects.create(
            journal=journal, account=item["account"], business_unit=item["business_unit"],
            debit=item["debit"], credit=item["credit"],
            memo_en=item["memo_en"], memo_ar=item["memo_ar"], is_synthetic=True,
        )
        FinanceDraftJournalLineDimension.objects.bulk_create([
            FinanceDraftJournalLineDimension(line=line, value=value, is_synthetic=True)
            for value in item["dimensions"]
        ])


def _opening_date(kind, value):
    if kind != "opening_balance":
        if value not in (None, ""):
            raise FinanceDraftValidationError("An as-of date belongs only to an opening-balance worksheet.")
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise FinanceDraftValidationError("Enter an explicit as-of date in YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise FinanceDraftValidationError("The as-of date must be a valid calendar date.") from None


ADJUSTMENT_KINDS = ("accrual_adjustment", "prepayment_adjustment")


def _adjustment_input(kind, proposal):
    """Validate explicit reviewer inputs, never calculate recognition or posting."""
    if kind not in ADJUSTMENT_KINDS:
        if proposal not in (None, {}):
            raise FinanceDraftValidationError("Adjustment details belong only to an accrual or prepayment proposal.")
        return {}
    if not isinstance(proposal, dict) or set(proposal) != {"date", "explanation"}:
        raise FinanceDraftValidationError("Supply an explicit adjustment date and explanation only; no conventions are inferred.")
    value = proposal["date"]
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise FinanceDraftValidationError("Enter an explicit proposed adjustment date in YYYY-MM-DD format.")
    try:
        proposed_date = date.fromisoformat(value)
    except ValueError:
        raise FinanceDraftValidationError("The proposed adjustment date must be a valid calendar date.") from None
    explanation = _required_text(proposal["explanation"], "Adjustment explanation", maximum=2000)
    return {"date": proposed_date.isoformat(), "explanation": explanation}


def create_finance_draft_journal(*, legal_entity, title_en, title_ar="", lines=(), kind="journal", recurrence_proposal=None, as_of_date=None, adjustment_proposal=None):
    """Persist a draft plus its first immutable revision; never post."""
    prepared = _prepare_lines(legal_entity=legal_entity, title_en=title_en, title_ar=title_ar, lines=lines)
    from .finance_recurrence import validate_proposal
    proposal = validate_proposal(kind, recurrence_proposal)
    opening_date = _opening_date(kind, as_of_date)
    adjustment = _adjustment_input(kind, adjustment_proposal)
    with transaction.atomic():
        journal = FinanceDraftJournal(
            legal_entity=legal_entity,
            title_en=_required_text(title_en, "English journal title"),
            title_ar=title_ar.strip(),
            currency="QAR",
            state=FinanceDraftJournal.State.DRAFT,
            is_synthetic=True,
            revision=1,
            kind=kind, recurrence_proposal=proposal,
            as_of_date=opening_date,
            adjustment_proposal=adjustment,
        )
        journal.full_clean()
        journal.save()
        _write_lines(journal, prepared)
        _record_revision(journal)
    return journal


def update_finance_draft_journal(*, journal_id, legal_entity, expected_revision, title_en, title_ar="", lines=(), kind=None, recurrence_proposal=None, as_of_date=None, adjustment_proposal=None):
    prepared = _prepare_lines(legal_entity=legal_entity, title_en=title_en, title_ar=title_ar, lines=lines)
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise FinanceDraftValidationError("An integer expected revision is required.")
    with transaction.atomic():
        journal = FinanceDraftJournal.objects.select_for_update().get(pk=journal_id, legal_entity=legal_entity)
        if journal.revision != expected_revision:
            raise FinanceDraftConflict(journal.revision)
        if kind is not None and kind != journal.kind:
            raise FinanceDraftValidationError("A saved draft's kind cannot change; create a separate proposal instead.")
        from .finance_recurrence import validate_proposal
        proposal = validate_proposal(journal.kind, journal.recurrence_proposal if recurrence_proposal is None else recurrence_proposal)
        opening_date = _opening_date(journal.kind, as_of_date)
        adjustment = _adjustment_input(journal.kind, journal.adjustment_proposal if adjustment_proposal is None else adjustment_proposal)
        # Preserve a real baseline for older isolated drafts; never invent history.
        if not journal.revisions.exists():
            _record_revision(journal)
        journal.lines.all().delete()
        _write_lines(journal, prepared)
        journal.title_en = title_en.strip()
        journal.title_ar = title_ar.strip()
        journal.revision += 1
        journal.updated_at = timezone.now()
        journal.recurrence_proposal = proposal
        journal.as_of_date = opening_date
        journal.adjustment_proposal = adjustment
        journal.save(update_fields=["title_en", "title_ar", "revision", "updated_at", "recurrence_proposal", "as_of_date", "adjustment_proposal"])
        _record_revision(journal)
        return journal


def finance_draft_snapshot(journal):
    _require_synthetic_demo(journal.legal_entity)
    return {
        "id": journal.pk, "revision": journal.revision,
        "title_en": journal.title_en, "title_ar": journal.title_ar,
        "legal_entity_id": journal.legal_entity_id,
        "state": journal.state, "currency": journal.currency,
        "kind": journal.kind, "recurrence_proposal": journal.recurrence_proposal,
        "as_of_date": journal.as_of_date.isoformat() if journal.as_of_date else None,
        "adjustment_proposal": journal.adjustment_proposal,
        "updated_at": journal.updated_at.isoformat(),
        "lines": [
            {
                "account_id": line.account_id, "account_code": line.account.code,
                "account_name_en": line.account.name_en, "account_name_ar": line.account.name_ar,
                "business_unit_id": line.business_unit_id,
                "business_unit_code": line.business_unit.code if line.business_unit_id else "",
                "dimensions": [{"id": value.pk, "code": value.code, "label_en": value.label_en, "label_ar": value.label_ar}
                               for value in line.dimensions.order_by("pk")],
                "debit": format(line.debit, ".2f"), "credit": format(line.credit, ".2f"),
                "memo_en": line.memo_en, "memo_ar": line.memo_ar,
            }
            for line in journal.lines.select_related("account", "business_unit").prefetch_related("dimensions").order_by("pk")
        ],
        "review": review_finance_draft_journal(journal),
    }


def _record_revision(journal):
    FinanceDraftJournalRevision.objects.create(
        journal=journal, revision=journal.revision, snapshot=finance_draft_snapshot(journal), is_synthetic=True,
    )


def review_finance_draft_journal(journal):
    """Return a deterministic, read-only balance review of an isolated draft."""
    if not isinstance(journal, FinanceDraftJournal) or not journal.pk or not journal.is_synthetic:
        raise FinanceDraftValidationError("A saved synthetic draft journal is required.")
    _require_synthetic_demo(journal.legal_entity)
    lines = list(journal.lines.select_related("account").order_by("pk"))
    debit_total = sum((line.debit for line in lines), Decimal("0.00"))
    credit_total = sum((line.credit for line in lines), Decimal("0.00"))
    issues = []
    if len(lines) < 2:
        issues.append({
            "code": "at_least_two_lines",
            "en": "Add at least two lines before review.",
            "ar": "أضف سطرين على الأقل قبل المراجعة.",
        })
    for line in lines:
        if (line.debit > 0) == (line.credit > 0):
            issues.append({
                "code": f"line_{line.pk}_one_side_only",
                "en": f"Line {line.pk} must contain a positive debit or credit, not both.",
                "ar": f"يجب أن يحتوي السطر {line.pk} على مدين أو دائن موجب، وليس كليهما.",
            })
    if debit_total != credit_total:
        issues.append({
            "code": "totals_not_balanced",
            "en": "Draft totals do not balance; correct the draft before review.",
            "ar": "إجمالي المسودة غير متوازن؛ لا يمكن اعتبارها جاهزة للمراجعة.",
        })
    return {
        "state": FinanceDraftJournal.State.DRAFT,
        "currency": "QAR",
        "synthetic_only": True,
        "posted": False,
        "balanced": not issues,
        "debit_total": format(debit_total, ".2f"),
        "credit_total": format(credit_total, ".2f"),
        "difference": format(debit_total - credit_total, ".2f"),
        "issues": issues,
    }
