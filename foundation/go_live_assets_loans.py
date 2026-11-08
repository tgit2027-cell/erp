"""Go-live import of the fixed asset register and of staff loans, one asset and one loan at a time.

Part of the go-live imports (go_live_imports.py). Tivoli's rule (Bilal, 5 Nov 2026): fixed assets
come in asset by asset and staff loans loan by employee, never as one total per account.
Provisional rules until Tivoli confirms them:

- Fixed assets: one row per asset, with its cost, the depreciation the old system has already
  taken, its useful life in months and the purchase date. Each becomes an asset in use under
  Fixed assets. The ERP keeps the old system's monthly depreciation (Bilal, 5 Nov 2026), from the
  month after the balances day until nothing is left; a blank monthly amount means the cost less
  the value at the end, divided by the useful life. Old asset numbers are kept; a blank number gets
  the next ERP number.
- Staff loans: one row per loan or salary advance still being repaid, with the staff number,
  what is still owed and the monthly instalment. Payroll takes the instalments from the month
  given (by default the month after the balances day).
- Approving the file books, on the balances day, fixed assets and their depreciation so far, or
  staff loans, against the "opening balances" account, which the trial balance left waiting.
"""

import datetime
import math
from collections import defaultdict
from decimal import Decimal

from django.utils import timezone

from .fixed_assets import USUAL_LIFE_MONTHS, _months_used, _next_month, _next_number
from .go_live_balances import OPENING, _amount, _unit
from .models import BusinessUnit, Employee, FixedAsset, JournalEntry, StaffLoan
from .transaction_services import _begin_idempotent_operation, _complete, _post_journal

ZERO = Decimal("0.00")
CATEGORIES = {**{value: value for value in FixedAsset.Category.values},
              **{label.lower(): value for value, label in FixedAsset.Category.choices},
              "furniture": "furniture", "fit-out": "fitout", "fit out": "fitout", "vehicle": "vehicles", "car": "vehicles",
              "computer": "computers", "it": "computers", "machine": "machinery", "tools": "machinery", "other": "other"}
KINDS = {"loan": "loan", "staff loan": "loan", "advance": "advance", "salary advance": "advance"}

ASSET_COLUMNS = [
    ("Business unit", "business_unit", True, "The business unit code, for example TVL.", "TVL"),
    ("Asset number", "number", False, "The old system's asset number. Blank gives the next ERP number.", "TVL-FA-0042"),
    ("Name", "name", True, "What the asset is.", "Showroom lighting, West Bay"),
    ("Category", "category", True, "Furniture, Fit-out, Vehicles, Computers, Machinery or Other.", "Fit-out"),
    ("Purchase date", "purchase_date", True, "When it was bought or came into use.", "2024-03-10"),
    ("Cost QAR", "cost", True, "What it cost, in QAR.", "180000"),
    ("Depreciation to date QAR", "depreciation", False, "Depreciation the old system has taken up to the balances day. "
     "Blank means none.", "57000"),
    ("Useful life months", "life_months", False, "Its whole useful life, in months. Blank means the usual for the category.", "60"),
    ("Value at the end QAR", "residual_value", False, "What it is expected to be worth at the end. Blank means zero.", ""),
    ("Monthly depreciation QAR", "monthly", False, "What the old system depreciates each month. The ERP carries on with it. "
     "Blank means the cost less the value at the end, divided by the useful life.", "3000"),
    ("Supplier", "supplier", False, "Optional.", "Doha Lighting WLL"),
]
LOAN_COLUMNS = [
    ("Business unit", "business_unit", True, "The business unit code, for example TVL.", "TVL"),
    ("Staff number", "staff_number", True, "The person's staff number, as on the Staff page.", "TVL-0007"),
    ("Kind", "kind", False, "Loan or Advance. Blank means Loan.", "Loan"),
    ("Still owed QAR", "amount", True, "What the person still owes on it, in QAR.", "6000"),
    ("Monthly instalment QAR", "instalment", True, "What payroll takes back each month.", "1000"),
    ("Next instalment month", "first_month", False, "The first month payroll takes it, as YYYY-MM. Blank means the month "
     "after the balances day.", "2026-12"),
    ("Reason", "reason", False, "Optional.", "Housing deposit"),
]


def _text(values, key, label, size, problems, required=False):
    text = " ".join(str(values.get(key, "") or "").split())
    if required and not text:
        problems.append(f"{label} is missing.")
    if len(text) > size:
        problems.append(f"{label} can be at most {size} characters.")
    return text[:size]


def _date(value, label, day, problems):
    text = str(value or "").strip()
    if not text:
        problems.append(f"{label} is missing.")
        return None
    for form in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            found = datetime.datetime.strptime(text[:10], form).date()
            break
        except ValueError:
            found = None
    if found is None:
        problems.append(f"{label} must be a date, for example 2024-03-10 or 10/03/2024.")
    elif day and found > day:
        problems.append(f"{label} is after the balances day ({day:%d %b %Y}).")
        return None
    return found


def months_left(purchase_date, life_months, day):
    """Months of useful life left after the balances day."""
    probe = FixedAsset(purchase_date=purchase_date, life_months=life_months)
    used = _months_used(probe, day.replace(day=1))
    return max(0, life_months - math.ceil(used))


# Fixed assets
def check_asset_row(values, seen, problems):
    day = seen.get("day")
    row = {"business_unit": _unit(values, problems)}
    number = _text(values, "number", "Asset number", 24, problems).upper()
    if number.startswith("FA-"):
        problems.append("Asset numbers starting with FA- are the ERP's own. Leave it blank to get one, or use the old number.")
    elif number and number in seen["asset"]:
        problems.append(f"Asset number {number} is also on row {seen['asset'][number]}.")
    elif number and FixedAsset.objects.filter(number=number).exists():
        problems.append(f"Asset number {number} is already in the ERP.")
    row["number"] = number
    row["name"] = _text(values, "name", "Name", 120, problems, required=True)
    given = " ".join(str(values.get("category", "") or "").split()).lower()
    row["category"] = CATEGORIES.get(given, "")
    if not given:
        problems.append("Category is missing.")
    elif not row["category"]:
        problems.append("Category must be Furniture, Fit-out, Vehicles, Computers, Machinery or Other.")
    bought = _date(values.get("purchase_date"), "Purchase date", day, problems)
    row["purchase_date"] = bought.isoformat() if bought else ""
    row["cost"] = _amount(values.get("cost", ""), "Cost QAR", problems, positive=True)
    if not values.get("cost"):
        problems.append("Cost QAR is missing.")
    row["depreciation"] = _amount(values.get("depreciation", ""), "Depreciation to date QAR", problems) or "0.00"
    row["residual_value"] = _amount(values.get("residual_value", ""), "Value at the end QAR", problems) or "0.00"
    life = str(values.get("life_months", "") or "").strip()
    if life:
        row["life_months"] = int(life) if life.isdigit() and 1 <= int(life) <= 600 else 0
        if not row["life_months"]:
            problems.append("Useful life months must be a whole number from 1 to 600.")
    else:
        row["life_months"] = USUAL_LIFE_MONTHS.get(row["category"], 60)
    row["monthly"] = _amount(values.get("monthly", ""), "Monthly depreciation QAR", problems, positive=True)
    row["supplier"] = _text(values, "supplier", "Supplier", 120, problems)
    if row["cost"]:
        cost, residual, taken = Decimal(row["cost"]), Decimal(row["residual_value"]), Decimal(row["depreciation"])
        if residual >= cost:
            problems.append("Value at the end must be less than the cost.")
        elif taken > cost - residual:
            problems.append("Depreciation to date cannot be more than the cost less the value at the end.")
        elif row["monthly"] and Decimal(row["monthly"]) > cost - residual:
            problems.append("Monthly depreciation cannot be more than the cost less the value at the end.")
        elif not row["monthly"] and bought and day and row["life_months"]:
            left = months_left(bought, row["life_months"], day)
            row["months_left"] = left
            if left == 0 and taken < cost - residual:
                problems.append("Its useful life is over by the balances day, so the depreciation to date should be the "
                                "cost less the value at the end.")
    return row


def monthly_amount(row):
    """The old system's monthly depreciation, or straight line over the whole life when none was given."""
    if row.get("monthly"):
        return Decimal(row["monthly"])
    depreciable = Decimal(row["cost"]) - Decimal(row["residual_value"])
    return max(Decimal("0.01"), (depreciable / row["life_months"]).quantize(Decimal("0.01")))


def remember_asset(row, number, seen):
    if row["number"]:
        seen["asset"].setdefault(row["number"], number)


def create_assets(batch, actor):
    """Each asset in use, with its own journal against opening balances. Returns the new asset ids."""
    units = {unit.code: unit for unit in BusinessUnit.objects.filter(code__in={row["business_unit"] for row in batch.rows})}
    start, now, ids = _next_month(batch.balances_on.replace(day=1)), timezone.now(), []
    for index, row in enumerate(batch.rows):
        unit = units[row["business_unit"]]
        cost, taken = Decimal(row["cost"]), Decimal(row["depreciation"])
        bought = datetime.date.fromisoformat(row["purchase_date"])
        asset = FixedAsset(
            business_unit=unit, number=row["number"] or _next_number(unit), name=row["name"], category=row["category"],
            supplier=row["supplier"], purchase_date=bought, cost=cost, residual_value=Decimal(row["residual_value"]),
            life_months=row["life_months"], source=FixedAsset.Source.OPENING, opening_depreciation=taken,
            depreciation_from=start, opening_months_left=months_left(bought, row["life_months"], batch.balances_on),
            opening_monthly=monthly_amount(row),
            status=FixedAsset.Status.IN_USE, submitted_by=batch.prepared_by, submitted_at=batch.prepared_at,
            decided_by=actor, decided_at=now)
        operation, _ = _begin_idempotent_operation(f"go-live-asset-{batch.pk}-{index}", "go_live_fixed_asset",
                                                   {"batch": batch.pk, "row": index})
        memo = f"{asset.number} {asset.name}"[:240]
        lines = [("TEST_FIXED_ASSETS", cost, ZERO, memo)]
        if taken > 0:
            lines.append(("TEST_ACCUM_DEPRECIATION", ZERO, taken, f"{memo}: depreciation before go-live"[:240]))
        if cost - taken > 0:
            lines.append((OPENING, ZERO, cost - taken, f"{memo}: brought in"[:240]))
        asset.journal_entry = _post_journal(
            legal_entity=unit.legal_entity, business_unit=unit, idempotency=operation, kind=JournalEntry.Kind.OPENING_BALANCE,
            document_date=batch.balances_on, description=f"TEST ONLY fixed asset {asset.number} at go-live (import {batch.pk})"[:240],
            lines=lines)
        asset.save()
        _complete(operation, {"batch": batch.pk, "asset": asset.pk, "journal": asset.journal_entry_id})
        ids.append(asset.pk)
    return ids


# Staff loans
def _month(value, problems):
    text = str(value or "").strip()
    try:
        year, month = (int(part) for part in text[:7].split("-"))
        return datetime.date(year, month, 1)
    except (TypeError, ValueError):
        problems.append("Next instalment month must be a month, for example 2026-12.")
        return None


def check_loan_row(values, seen, problems):
    day = seen.get("day")
    row = {"business_unit": _unit(values, problems)}
    number = _text(values, "staff_number", "Staff number", 20, problems, required=True).upper()
    person = Employee.objects.filter(staff_number__iexact=number, business_unit__code=row["business_unit"]).first() if number else None
    if number and person is None:
        problems.append(f"Nobody in {row['business_unit']} has the staff number {number}. Add them on the Staff page first.")
    elif person is not None and person.last_day and day and person.last_day <= day:
        problems.append(f"{person.full_name} left on {person.last_day:%d %b %Y}; settle the loan in their end of service instead.")
    row["staff_number"] = person.staff_number if person else number
    given = " ".join(str(values.get("kind", "") or "").split()).lower()
    row["kind"] = KINDS.get(given or "loan", "")
    if not row["kind"]:
        problems.append("Kind must be Loan or Advance.")
    row["amount"] = _amount(values.get("amount", ""), "Still owed QAR", problems, positive=True)
    if not values.get("amount"):
        problems.append("Still owed QAR is missing.")
    row["instalment"] = _amount(values.get("instalment", ""), "Monthly instalment QAR", problems, positive=True)
    if not values.get("instalment"):
        problems.append("Monthly instalment QAR is missing.")
    if row["amount"] and row["instalment"] and Decimal(row["instalment"]) > Decimal(row["amount"]):
        problems.append("The monthly instalment cannot be more than what is still owed.")
    first = _month(values.get("first_month"), problems) if values.get("first_month") else None
    if first and day and first <= day.replace(day=1):
        problems.append("Next instalment month must be after the balances day; the old system took the earlier ones.")
    row["first_month"] = first.isoformat() if first else ""
    row["reason"] = _text(values, "reason", "Reason", 200, problems)
    return row


def create_loans(batch, actor):
    """The loans, being repaid, and one journal per business unit against opening balances. Returns the loan ids."""
    units = {unit.code: unit for unit in BusinessUnit.objects.filter(code__in={row["business_unit"] for row in batch.rows})}
    default_first, now, ids = _next_month(batch.balances_on.replace(day=1)), timezone.now(), []
    by_unit = defaultdict(list)
    for row in batch.rows:
        by_unit[row["business_unit"]].append(row)
    for code, rows in by_unit.items():
        unit = units[code]
        total = sum((Decimal(row["amount"]) for row in rows), ZERO)
        operation, _ = _begin_idempotent_operation(f"go-live-loans-{batch.pk}-{code}", "go_live_staff_loans", {"batch": batch.pk, "unit": code})
        entry = _post_journal(
            legal_entity=unit.legal_entity, business_unit=unit, idempotency=operation, kind=JournalEntry.Kind.OPENING_BALANCE,
            document_date=batch.balances_on, description=f"TEST ONLY staff loans and advances at go-live (import {batch.pk})",
            lines=[("TEST_STAFF_LOANS", total, ZERO, "Staff loans and advances from the old system"),
                   (OPENING, ZERO, total, "Staff loans and advances brought in")])
        for row in rows:
            loan = StaffLoan.objects.create(
                business_unit=unit, employee=Employee.objects.get(staff_number=row["staff_number"]), kind=row["kind"],
                amount=Decimal(row["amount"]), instalment=Decimal(row["instalment"]),
                first_month=datetime.date.fromisoformat(row["first_month"]) if row["first_month"] else default_first,
                reason=row["reason"] or "Brought over from the old system at go-live", status=StaffLoan.Status.REPAYING,
                requested_by=batch.prepared_by, requested_at=batch.prepared_at, decided_by=actor, decided_at=now,
                decision_note="Approved with the go-live import", paid_on=batch.balances_on, paid_by=actor, payout_entry=entry)
            ids.append(loan.pk)
        _complete(operation, {"batch": batch.pk, "unit": code, "journal": entry.pk})
    return ids
