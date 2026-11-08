"""Month-end revaluation of what we still owe suppliers in foreign currency; synthetic only.

A delivery priced in euros (or another foreign currency) is booked in QAR at the rate on the
receipt day. Until it is paid, the QAR we really owe moves with the rate. At month end Finance
enters the month-end rate for each currency and the difference is posted as an unrealised
exchange gain or loss, dated the last day of the month. The same entry is reversed on the first
day of the next month, so payments keep clearing at the receipt rate and the realised gain or
loss is still worked out in full when the supplier is paid.

What is revalued: goods and landed charges in a foreign currency, received by the month end,
less payments dated by the month end. Advances paid to suppliers are not revalued: they are
prepayments that will be used against goods, not money to be received back (provisional).
"""

import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .financial_statements import _label, _month, _next_month, _person, can_close_months
from .general_ledger import _money, _require_viewer
from .models import (
    BASE_CURRENCY, ClosedMonth, FxRevaluation, JournalEntry, PurchaseReceipt, PurchaseReceiptCharge, SupplierPayment,
)
from .purchasing_services import WorkflowPermissionDenied, _to_base
from .supplier_payments import _payable
from .transaction_services import InvalidTransaction, _begin_idempotent_operation, _complete, _post_journal

ZERO = Decimal("0.00")
PNL_ACCOUNT = "TEST_FX_UNREALISED"
MONTHS_SHOWN = 6
POLICY = "TEST ONLY synthetic revaluation, not Tivoli-approved accounts"
# QAR per one unit. Wide enough for any currency we buy in; catches a rate typed the wrong way round.
MIN_RATE, MAX_RATE = Decimal("0.01"), Decimal("100")


def _month_end(month):
    return _next_month(month) - datetime.timedelta(days=1)


def _paid_by(item, last_day):
    payments = item.payments((SupplierPayment.Status.APPROVED,)).filter(paid_on__lte=last_day)
    amount = sum((payment.amount for payment in payments), ZERO)
    cleared = sum((payment.cleared_value or ZERO for payment in payments), ZERO)
    return amount, cleared


def _open_items(business_unit, month):
    """Foreign-currency goods and charges still owed at the month end, with the QAR they are carried at."""
    last_day = _month_end(month)
    receipts = (PurchaseReceipt.objects.filter(business_unit=business_unit, created_at__date__lte=last_day)
                .select_related("supplier").order_by("pk"))
    items = []
    for receipt in receipts:
        things = [None] if receipt.currency != BASE_CURRENCY else []
        things += list(PurchaseReceiptCharge.objects.filter(receipt=receipt).exclude(currency=BASE_CURRENCY).order_by("pk"))
        for charge in things:
            item = _payable(receipt, charge)
            paid, cleared = _paid_by(item, last_day)
            owed = item.payable - paid
            if owed <= 0:
                continue
            items.append({
                "item": item, "owed": owed, "carried": item.carried - cleared,
                "receipt_id": receipt.pk, "supplier": receipt.supplier.name, "what": item.label,
                "account": item.account, "currency": item.currency,
            })
    return items


def _last_rates(business_unit, currencies, last_day):
    """The latest rate we used for each currency up to the month end, as a starting point."""
    found = {}
    for code in currencies:
        rows = [
            SupplierPayment.objects.filter(business_unit=business_unit, currency=code, rate_date__lte=last_day)
            .exclude(rate_date__isnull=True).order_by("-rate_date", "-pk").values("exchange_rate", "rate_date", "rate_source").first(),
            PurchaseReceipt.objects.filter(business_unit=business_unit, currency=code, rate_date__lte=last_day)
            .exclude(rate_date__isnull=True).order_by("-rate_date", "-pk").values("exchange_rate", "rate_date", "rate_source").first(),
        ]
        rows = [row for row in rows if row]
        if rows:
            best = max(rows, key=lambda row: row["rate_date"])
            found[code] = {"rate": format(best["exchange_rate"].normalize(), "f"), "date": best["rate_date"].isoformat(),
                           "source": best["rate_source"]}
    return found


def _rate(value, code):
    try:
        rate = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise InvalidTransaction(f"Enter the {code} rate as QAR for one {code}, for example 3.9850.") from None
    if not rate.is_finite() or rate < MIN_RATE or rate > MAX_RATE:
        raise InvalidTransaction(f"The {code} rate looks wrong. Enter QAR for one {code}, between {MIN_RATE} and {MAX_RATE}.")
    if rate.as_tuple().exponent < -8:
        raise InvalidTransaction(f"The {code} rate can have at most 8 decimal places.")
    return rate


def _rows(items, rates):
    rows = []
    for row in items:
        rate = rates.get(row["currency"])
        value = _to_base(row["owed"], rate) if rate else None
        rows.append({
            "receipt_id": row["receipt_id"], "supplier": row["supplier"], "what": row["what"], "currency": row["currency"],
            "owed": _money(row["owed"]), "carried": _money(row["carried"]),
            "booked_rate": format((row["carried"] / row["owed"]).quantize(Decimal("0.0001")), "f"),
            "revalued": _money(value) if value is not None else None,
            "difference": _money(value - row["carried"]) if value is not None else None,
        })
    return rows


def _summary(rows):
    """Positive difference: we owe more QAR than booked, a loss. Negative: a gain."""
    done = [row for row in rows if row["difference"] is not None]
    losses = sum((Decimal(row["difference"]) for row in done if Decimal(row["difference"]) > 0), ZERO)
    gains = -sum((Decimal(row["difference"]) for row in done if Decimal(row["difference"]) < 0), ZERO)
    return {"loss": _money(losses), "gain": _money(gains), "net": _money(losses - gains), "complete": len(done) == len(rows)}


def _run_row(run):
    return {
        "month": run.month.strftime("%Y-%m"), "label": _label(run.month), "rates": run.rates, "source": run.source,
        "net": _money(run.net), "item_count": len(run.lines), "posted_by": _person(run.posted_by), "posted_at": run.posted_at.isoformat(),
        "journal_entry_id": run.journal_entry_id, "reversal_entry_id": run.reversal_entry_id, "lines": run.lines,
    }


def _problem(business_unit, month, current):
    if month >= current:
        return "This month has not ended yet. Revalue once it is over."
    if FxRevaluation.objects.filter(business_unit=business_unit, month=month).exists():
        return f"{_label(month)} is already revalued."
    closed = set(ClosedMonth.objects.filter(business_unit=business_unit, month__in=[month, _next_month(month)])
                 .values_list("month", flat=True))
    if month in closed:
        return f"{_label(month)} is closed. Revalue before closing the month."
    if _next_month(month) in closed:
        return f"{_label(_next_month(month))} is closed, so the reversal on its first day cannot be posted."
    return ""


def fx_revaluation_overview(*, actor, business_unit, month=None, rates=None):
    _require_viewer(actor, business_unit, "unit")
    current = timezone.localdate().replace(day=1)
    runs = {run.month: run for run in FxRevaluation.objects.filter(business_unit=business_unit).select_related("posted_by")}
    months, walk = [], current
    for _ in range(MONTHS_SHOWN):
        walk = (walk - datetime.timedelta(days=1)).replace(day=1)
        problem = _problem(business_unit, walk, current)
        months.append({"month": walk.strftime("%Y-%m"), "label": _label(walk), "revalued": walk in runs,
                       "blocked": problem if walk not in runs else ""})
    if month:
        chosen = _month(month)
    else:
        ready = [row for row in months if not row["revalued"] and not row["blocked"]]
        chosen = _month((ready[0] if ready else months[0])["month"])
    allowed = can_close_months(actor, business_unit)
    run = runs.get(chosen)
    selected = {"month": chosen.strftime("%Y-%m"), "label": _label(chosen), "last_day": _month_end(chosen).isoformat()}
    if run:
        selected |= {"run": _run_row(run), "can_post": False, "blocked": f"{_label(chosen)} is already revalued."}
    else:
        items = _open_items(business_unit, chosen)
        currencies = sorted({row["currency"] for row in items})
        given = {}
        for code in currencies:
            value = (rates or {}).get(code)
            if value not in (None, ""):
                try:
                    given[code] = _rate(value, code)
                except InvalidTransaction:
                    pass
        rows = _rows(items, given)
        problem = _problem(business_unit, chosen, current)
        selected |= {
            "currencies": currencies, "last_rates": _last_rates(business_unit, currencies, _month_end(chosen)),
            "rows": rows, "summary": _summary(rows), "blocked": problem, "can_post": allowed and not problem and bool(items),
        }
    return {
        "unit": {"id": business_unit.pk, "name": business_unit.name}, "can_post": allowed, "months": months, "selected": selected,
        "history": [_run_row(run) for run in sorted(runs.values(), key=lambda run: run.month, reverse=True)],
        "policy_label": POLICY,
    }


def _journal_lines(items, rates, *, reverse=False):
    by_account, net = {}, ZERO
    for row in items:
        difference = _to_base(row["owed"], rates[row["currency"]]) - row["carried"]
        by_account[row["account"]] = by_account.get(row["account"], ZERO) + difference
        net += difference
    lines = []
    for account, amount in sorted(by_account.items()):
        if amount:
            # We owe more QAR (positive): the supplier balance goes up, a credit.
            lines.append((account, ZERO, amount, "Owed to suppliers revalued at month-end rate") if amount > 0
                         else (account, -amount, ZERO, "Owed to suppliers revalued at month-end rate"))
    if net:
        lines.append((PNL_ACCOUNT, net, ZERO, "Unrealised exchange loss") if net > 0
                     else (PNL_ACCOUNT, ZERO, -net, "Unrealised exchange gain"))
    if reverse:
        lines = [(account, credit, debit, f"Reversal: {memo[0].lower()}{memo[1:]}") for account, debit, credit, memo in lines]
    return lines, net


@transaction.atomic
def post_fx_revaluation(*, actor, business_unit, month, rates, source=""):
    _require_viewer(actor, business_unit, "unit")
    if not can_close_months(actor, business_unit):
        raise WorkflowPermissionDenied("You need the “Close months” permission to revalue foreign currency balances.")
    chosen = _month(month)
    list(ClosedMonth.objects.select_for_update().filter(business_unit=business_unit).values_list("pk", flat=True))
    problem = _problem(business_unit, chosen, timezone.localdate().replace(day=1))
    if problem:
        raise InvalidTransaction(problem)
    source = str(source or "").strip()
    if len(source) > 120:
        raise InvalidTransaction("Keep the rate source under 120 characters.")
    if not source:
        raise InvalidTransaction("Say where the rates come from, for example “QCB closing rates 30 September”.")
    items = _open_items(business_unit, chosen)
    if not items:
        raise InvalidTransaction(f"Nothing in a foreign currency was owed to suppliers at the end of {_label(chosen)}.")
    rates = rates if isinstance(rates, dict) else {}
    used = {}
    for code in sorted({row["currency"] for row in items}):
        if rates.get(code) in (None, ""):
            raise InvalidTransaction(f"Enter the {code} rate at {_month_end(chosen):%d %B %Y}.")
        used[code] = _rate(rates[code], code)
    lines, net = _journal_lines(items, used)
    run = FxRevaluation(business_unit=business_unit, month=chosen, rates={code: format(rate, "f") for code, rate in used.items()},
                        source=source, net=net, lines=_rows(items, used), posted_by=actor)
    if lines:
        legal = business_unit.legal_entity
        operation, _ = _begin_idempotent_operation(f"fx-revaluation-{business_unit.pk}-{chosen:%Y-%m}", "fx_revaluation",
                                                   {"unit": business_unit.pk, "month": chosen.isoformat()})
        run.journal_entry = _post_journal(
            legal_entity=legal, business_unit=business_unit, idempotency=operation, kind=JournalEntry.Kind.FX_REVALUATION,
            description=f"TEST ONLY: foreign currency revaluation at {_month_end(chosen):%d %B %Y} ({source})",
            lines=lines, document_date=_month_end(chosen))
        _complete(operation, {"month": chosen.isoformat(), "journal_entry_id": run.journal_entry.pk})
        back, _ = _begin_idempotent_operation(f"fx-revaluation-reversal-{business_unit.pk}-{chosen:%Y-%m}", "fx_revaluation",
                                              {"unit": business_unit.pk, "month": chosen.isoformat(), "reversal": True})
        run.reversal_entry = _post_journal(
            legal_entity=legal, business_unit=business_unit, idempotency=back, kind=JournalEntry.Kind.FX_REVALUATION,
            description=f"TEST ONLY: reversal of the {_label(chosen)} foreign currency revaluation",
            lines=_journal_lines(items, used, reverse=True)[0], document_date=_next_month(chosen))
        _complete(back, {"month": chosen.isoformat(), "journal_entry_id": run.reversal_entry.pk})
    run.save()
    return fx_revaluation_overview(actor=actor, business_unit=business_unit, month=chosen.strftime("%Y-%m"))
