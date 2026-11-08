"""Cost sharing: moving part of a cost booked in one business unit to other units of the same company.

For example the head office pays the rent for a building that the showroom and
the warehouse also use. Each unit's profit should carry its own part.

Provisional rules (synthetic demo only, waiting for Finance's approval):

- Sharing uses the journal permissions of the unit that booked the cost: whoever
  prepares journal entries there prepares a share, and a different person with
  the approve permission approves it. The receiving units need no action.
- Only running costs (the expense categories, bank charges and depreciation) can
  be shared, and only between units of the same company. Sharing between
  companies waits for Finance's decision on inter-company accounts.
- The split is in percentages. The unit that booked the cost keeps whatever is
  not given away. Each unit's amount is rounded to the dirham and any rounding
  stays with the unit that booked the cost.
- A share cannot move more than the cost booked on that account in that month,
  less what is already shared or waiting to be shared.
- Approval posts one journal in the unit that booked the cost (the cost leaves
  it) and one in each receiving unit (the cost arrives), through a sharing
  account that nets to zero across the company. The month must be open in every
  unit involved.
"""

import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .cash_accounts import _past_day, _text
from .manual_journals import _require_user as _require_journal_user
from .manual_journals import can_approve, can_prepare
from .models import BusinessUnit, ClosedMonth, CostShare, CostShareLine, JournalEntry, JournalLine
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import (
    _TEST_ACCOUNTS, InvalidTransaction, _begin_idempotent_operation, _complete, _money, _post_journal,
)

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")
MAX_UNITS = 20
S = CostShare.Status
SHAREABLE = [code for code in _TEST_ACCOUNTS if code.startswith("TEST_EXP_")] + ["TEST_BANK_CHARGES", "TEST_DEPRECIATION"]
POLICY_LABEL = ("TEST ONLY provisional rules: a second person approves every share; running costs only, between units "
                "of the same company; percentages, with rounding kept by the unit that booked the cost; QAR only.")


def _name(user):
    return (user.get_full_name() or user.username) if user else ""


def _fmt(value):
    return format((value or ZERO).quantize(Decimal("0.01")), "f")


def _account_name(code):
    return _TEST_ACCOUNTS[code][0].replace("TEST ONLY — ", "") if code in _TEST_ACCOUNTS else code


def _month_range(day):
    start = day.replace(day=1)
    return start, (start.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)


def _require(actor, business_unit):
    _require_journal_user(actor, business_unit)


def _related_units(business_unit):
    return (BusinessUnit.objects.filter(legal_entity=business_unit.legal_entity, is_active=True, is_synthetic=True)
            .exclude(pk=business_unit.pk).order_by("name"))


def available(business_unit, code, day, *, exclude_share=None):
    """The cost booked on an account in a unit for the month of day, less shares waiting for approval."""
    start, end = _month_range(day)
    sums = JournalLine.objects.filter(
        entry__business_unit=business_unit, entry__status=JournalEntry.Status.POSTED, account__code=code,
        entry__document_date__gte=start, entry__document_date__lt=end,
    ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
    booked = (sums["debit"] or ZERO) - (sums["credit"] or ZERO)
    waiting = CostShare.objects.filter(business_unit=business_unit, account_code=code, status=S.SUBMITTED,
                                       share_date__gte=start, share_date__lt=end)
    if exclude_share is not None:
        waiting = waiting.exclude(pk=exclude_share.pk)
    moving = CostShareLine.objects.filter(share__in=waiting).aggregate(total=Sum("amount"))["total"] or ZERO
    return booked - moving


def _row(share, actor, unit):
    lines = list(share.lines.all())
    moved = sum((line.amount for line in lines), ZERO)
    mine = share.business_unit_id == unit.pk
    received = next((line for line in lines if line.business_unit_id == unit.pk), None)
    return {
        "id": share.pk, "number": share.number, "date": share.share_date.isoformat(),
        "account_code": share.account_code, "account": _account_name(share.account_code),
        "description": share.description, "amount": _fmt(share.amount), "moved": _fmt(moved),
        "kept_percent": format(share.kept_percent, "f"), "kept": _fmt(share.amount - moved),
        "from_unit": {"id": share.business_unit_id, "name": share.business_unit.name},
        "direction": "sent" if mine else "received",
        "received": _fmt(received.amount) if received else None,
        "lines": [{"unit": {"id": line.business_unit_id, "name": line.business_unit.name},
                   "percent": format(line.percent, "f"), "amount": _fmt(line.amount),
                   "journal_entry_id": line.journal_entry_id} for line in lines],
        "status": share.status, "status_label": share.get_status_display(),
        "prepared_by": _name(share.prepared_by), "decided_by": _name(share.decided_by),
        "decision_note": share.decision_note, "journal_entry_id": share.journal_entry_id,
        "can_decide": (mine and share.status == S.SUBMITTED and share.prepared_by_id != actor.pk
                       and can_approve(actor, share.business_unit)),
    }


def _shares(unit):
    return (CostShare.objects.filter(Q(business_unit=unit) | Q(lines__business_unit=unit)).distinct()
            .select_related("business_unit", "prepared_by", "decided_by").prefetch_related("lines__business_unit"))


def sharing_overview(*, actor, business_unit, status="", month=None):
    _require(actor, business_unit)
    day = _past_day(f"{month}-01", "month") if month else timezone.localdate()
    start, end = _month_range(day)
    shares = _shares(business_unit).filter(share_date__gte=start, share_date__lt=end)
    if status in S.values:
        shares = shares.filter(status=status)
    month_rows = CostShare.objects.filter(share_date__gte=start, share_date__lt=end, status=S.APPROVED)
    sent = (CostShareLine.objects.filter(share__in=month_rows.filter(business_unit=business_unit))
            .aggregate(total=Sum("amount"))["total"] or ZERO)
    received = (CostShareLine.objects.filter(share__in=month_rows, business_unit=business_unit)
                .aggregate(total=Sum("amount"))["total"] or ZERO)
    waiting = CostShare.objects.filter(business_unit=business_unit, status=S.SUBMITTED)
    preparer = can_prepare(actor, business_unit)
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "month": start.strftime("%Y-%m"), "month_label": start.strftime("%B %Y"),
        "shares": [_row(share, actor, business_unit) for share in shares[:200]],
        "totals": {"sent": _fmt(sent), "received": _fmt(received), "waiting": waiting.count(),
                   "waiting_for_me": waiting.exclude(prepared_by=actor).count() if can_approve(actor, business_unit) else 0},
        "accounts": [{"code": code, "name": _account_name(code), "available": _fmt(available(business_unit, code, day))}
                     for code in SHAREABLE] if preparer else [],
        "units": [{"id": unit.pk, "name": unit.name} for unit in _related_units(business_unit)] if preparer else [],
        "can_prepare": preparer, "can_approve": can_approve(actor, business_unit),
        "currency": "QAR", "policy": POLICY_LABEL,
    }


def _percent(value, number):
    try:
        percent = Decimal(str(value).strip())
    except Exception:  # noqa: BLE001 - any unreadable value gets the same message
        raise InvalidTransaction(f"Unit {number}: give the percentage as a number.") from None
    if not percent.is_finite() or percent != percent.quantize(Decimal("0.01")):
        raise InvalidTransaction(f"Unit {number}: use at most two decimals in the percentage.")
    if not ZERO < percent <= HUNDRED:
        raise InvalidTransaction(f"Unit {number}: the percentage must be above 0 and at most 100.")
    return percent


def _check_open(unit, day):
    if ClosedMonth.objects.filter(business_unit=unit, month=day.replace(day=1)).exists():
        raise InvalidTransaction(f"{day:%B %Y} is closed for {unit.name}. Use a date in an open month, or ask Finance to reopen it.")


def _next_number(business_unit, day):
    prefix = f"CS-{business_unit.code}-{day.year}-"
    last = CostShare.objects.select_for_update().filter(number__startswith=prefix).order_by("-number").first()
    following = int(last.number.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{following:05d}"


@transaction.atomic
def prepare_share(*, actor, business_unit, data):
    """Prepare a share. Nothing moves until someone else approves it."""
    _require(actor, business_unit)
    if not can_prepare(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'prepare journal entries' permission for this unit to share a cost.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the share's details.")
    code = data.get("account_code")
    if code not in SHAREABLE:
        raise InvalidTransaction("Choose the kind of cost to share.")
    day = _past_day(data.get("date"), "date")
    description = _text(data.get("description"), "description", 200, required=True)
    amount = _money(data.get("amount"), "amount")
    if amount <= 0:
        raise InvalidTransaction("Give the cost to share, above zero.")
    raw = data.get("units")
    if not isinstance(raw, list) or not raw:
        raise InvalidTransaction("Add at least one unit to share the cost with.")
    if len(raw) > MAX_UNITS:
        raise InvalidTransaction(f"A cost can be shared with up to {MAX_UNITS} units.")
    allowed = {unit.pk: unit for unit in _related_units(business_unit)}
    lines, seen = [], set()
    for number, item in enumerate(raw, start=1):
        unit_id = item.get("business_unit_id") if isinstance(item, dict) else None
        unit = allowed.get(unit_id) if type(unit_id) is int else None
        if unit is None:
            raise InvalidTransaction(f"Unit {number}: choose another active unit of {business_unit.legal_entity.name}.")
        if unit.pk in seen:
            raise InvalidTransaction(f"{unit.name} is listed twice.")
        seen.add(unit.pk)
        percent = _percent(item.get("percent"), number)
        lines.append(CostShareLine(business_unit=unit, percent=percent,
                                   amount=(amount * percent / HUNDRED).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)))
    given = sum((line.percent for line in lines), ZERO)
    if given > HUNDRED:
        raise InvalidTransaction(f"The percentages add up to {given}%, more than 100%.")
    if given == HUNDRED:
        # Rounding stays with the unit that booked the cost; with nothing kept, the last unit takes it.
        lines[-1].amount += amount - sum((line.amount for line in lines), ZERO)
    if any(line.amount <= 0 for line in lines):
        raise InvalidTransaction("Each unit's part must be at least 0.01 QAR.")
    moved = sum((line.amount for line in lines), ZERO)
    left = available(business_unit, code, day)
    if moved > left:
        raise InvalidTransaction(
            f"{business_unit.name} has {max(left, ZERO):,.2f} QAR of {_account_name(code).lower()} in {day:%B %Y} "
            f"that is not already shared, less than the {moved:,.2f} QAR to move.")
    for unit in [business_unit, *(line.business_unit for line in lines)]:
        _check_open(unit, day)
    share = CostShare.objects.create(
        business_unit=business_unit, number=_next_number(business_unit, day), share_date=day, account_code=code,
        description=description, amount=amount, kept_percent=HUNDRED - given, prepared_by=actor,
    )
    for line in lines:
        line.share = share
    CostShareLine.objects.bulk_create(lines)
    return _row(_load(business_unit, share.pk), actor, business_unit)


def _load(business_unit, share_id, *, lock=False):
    rows = CostShare.objects.filter(business_unit=business_unit, pk=share_id if type(share_id) is int else None)
    if lock:
        rows = rows.select_for_update(of=("self",))
    share = rows.select_related("business_unit__legal_entity", "prepared_by", "decided_by").first()
    if share is None:
        raise InvalidTransaction("That share was not found in this business unit.")
    return share


def share_detail(*, actor, business_unit, share_id):
    _require(actor, business_unit)
    share = _shares(business_unit).filter(pk=share_id if type(share_id) is int else None).first()
    if share is None:
        raise InvalidTransaction("That share was not found in this business unit.")
    return _row(share, actor, business_unit)


@transaction.atomic
def decide_share(*, actor, business_unit, share_id, action, reason=""):
    _require(actor, business_unit)
    share = _load(business_unit, share_id, lock=True)
    if share.status != S.SUBMITTED:
        raise InvalidTransaction(f"{share.number} is already {share.get_status_display().lower()}.")
    if not can_approve(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'approve journal entries' permission for this unit to approve a share.")
    if share.prepared_by_id == actor.pk:
        raise WorkflowPermissionDenied("You prepared this share, so someone else must approve it.")
    share.decided_by, share.decided_at = actor, timezone.now()
    if action == "reject":
        share.status, share.decision_note = S.REJECTED, _text(reason, "reason", 200, required=True)
        share.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        return _row(_load(business_unit, share.pk), actor, business_unit)
    if action != "approve":
        raise InvalidTransaction("Approve or reject.")
    lines = list(share.lines.select_related("business_unit__legal_entity"))
    moved = sum((line.amount for line in lines), ZERO)
    left = available(business_unit, share.account_code, share.share_date, exclude_share=share)
    if moved > left:
        raise InvalidTransaction(
            f"Only {max(left, ZERO):,.2f} QAR of this cost is still unshared in {share.share_date:%B %Y}, "
            f"less than the {moved:,.2f} QAR to move. Reject this share and prepare a new one.")
    for line in lines:
        if line.business_unit.legal_entity_id != business_unit.legal_entity_id or not line.business_unit.is_active:
            raise InvalidTransaction(f"{line.business_unit.name} can no longer receive shared costs.")
    entity, account = business_unit.legal_entity, _account_name(share.account_code).lower()
    operation, _ = _begin_idempotent_operation(f"cost-share-{share.pk}-out", "cost_share", {"share": share.pk})
    share.journal_entry = _post_journal(
        legal_entity=entity, business_unit=business_unit, idempotency=operation, kind=JournalEntry.Kind.COST_SHARE,
        description=f"TEST ONLY: {share.number} {account} shared with other units: {share.description}"[:240],
        lines=[("TEST_UNIT_SHARING", moved, ZERO, f"{share.number} given to other units"[:200]),
               (share.account_code, ZERO, moved, f"{share.number} {share.description}"[:200])],
        document_date=share.share_date,
    )
    _complete(operation, {"share": share.pk, "journal_entry_id": share.journal_entry.pk})
    for line in lines:
        incoming, _ = _begin_idempotent_operation(f"cost-share-{share.pk}-in-{line.business_unit_id}", "cost_share",
                                                  {"share": share.pk, "unit": line.business_unit_id})
        line.journal_entry = _post_journal(
            legal_entity=entity, business_unit=line.business_unit, idempotency=incoming, kind=JournalEntry.Kind.COST_SHARE,
            description=f"TEST ONLY: {share.number} {line.percent}% of {account} from {business_unit.name}: {share.description}"[:240],
            lines=[(share.account_code, line.amount, ZERO, f"{share.number} {share.description}"[:200]),
                   ("TEST_UNIT_SHARING", ZERO, line.amount, f"{share.number} from {business_unit.name}"[:200])],
            document_date=share.share_date,
        )
        _complete(incoming, {"share": share.pk, "journal_entry_id": line.journal_entry.pk})
        line.save(update_fields=["journal_entry"])
    share.status = S.APPROVED
    share.save(update_fields=["status", "decided_by", "decided_at", "journal_entry"])
    return _row(_load(business_unit, share.pk), actor, business_unit)
