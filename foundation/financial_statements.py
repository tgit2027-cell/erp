"""Financial statements and month-end close, built on the posted general ledger.

The profit and loss statement and the balance sheet read the same posted
journal lines as the trial balance, by document date, for the selected business
unit or the whole company; viewing them needs the view_ledger permission, just
like the ledger. There is no equity account yet, so the balance sheet shows
equity as the profit kept from earlier years plus the profit for this year: the
two sides always agree because every posted entry balances.

Month-end close is per business unit. A finance manager with the close_month
permission closes an ended month once every earlier month is closed and every
entry in it balances; after that nothing dated in that month can be posted for
the unit. Only the latest closed month can be reopened, with a reason, and both
steps are kept in the close history.

Provisional defaults, to confirm with Sam:
- Months are closed per business unit, not per company.
- Sales are delivery revenue less sales returns; cost of sales is cost of goods sold
  plus stock count differences; everything else is other income or other expenses.
- Supplier payments waiting for approval and unfinished stock counts are shown
  as warnings when closing, but do not block it.

Month-end checklist (provisional, from the roadmap Sam agreed on 2 Nov 2026): before a month is
closed, the screen checks what usually goes wrong at month end.

- Must be done: every bank account and cash box open in the month has an approved bank
  reconciliation for it; depreciation is posted for the month when there are assets in use;
  payroll is approved and posted for the month when anyone is on the payroll; nothing dated in
  the month is still waiting for approval (journal entries, expenses, supplier payments and
  advances, credit and debit notes); every delivery from a supplier in the month has its
  supplier's bill recorded (made a must from Bilal's review, 3 Nov 2026).
- Worth checking: unfinished stock counts.
- Someone with "Close months" can still close with checks not done, but must say why. The
  reason and the checklist as it stood are kept in the close history.
"""

import datetime
from decimal import Decimal

from django.db import connection, transaction
from django.db.models import F, Max, Min, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .access import active_grants
from .access_levels import is_master_admin, require_master_admin
from .general_ledger import (
    ZERO, _day, _money, _period, _posted_entries, _posted_lines, _require_viewer, _scope, _totals,
)
from .models import (
    AdjustmentNote, BankReconciliation, CashAccount, ClosedMonth, DepreciationRun, Employee, EmployeePay, Expense, FixedAsset,
    JournalEntry, LedgerAccount, ManualJournal, MonthCloseEvent, PayrollRun, StockCount, SupplierAdvance, SupplierPayment,
    UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction

REVENUE = LedgerAccount.AccountClass.REVENUE
EXPENSE = LedgerAccount.AccountClass.EXPENSE
ASSET = LedgerAccount.AccountClass.ASSET
LIABILITY = LedgerAccount.AccountClass.LIABILITY
# Sales returns (customer refunds) is a revenue account debited by returns, so it nets against sales.
SALES_ACCOUNTS = {"TEST_REVENUE", "TEST_SALES_RETURNS"}
COST_OF_SALES_ACCOUNTS = {"TEST_COGS", "TEST_STOCK_COUNT"}
MONTHS_SHOWN = 24
HISTORY = 30
POLICY = "TEST ONLY synthetic statements, not Tivoli-approved accounts"


def _row(account, amounts):
    return {"account_id": account.pk, "code": account.code, "name": account.name,
            **{key: _money(value) for key, value in amounts.items()}}


def _net(totals, account, sign):
    debit, credit = totals.get(account.pk, (ZERO, ZERO))
    return (credit - debit) if sign == "credit" else (debit - credit)


def _section(key, label, accounts, columns, sign):
    rows, sums = [], dict.fromkeys(columns, ZERO)
    for account in accounts:
        amounts = {column: _net(totals, account, sign) for column, totals in columns.items()}
        if any(amounts.values()):
            rows.append(_row(account, amounts))
            for column, value in amounts.items():
                sums[column] += value
    return {"key": key, "label": label, "rows": rows, "total": {k: _money(v) for k, v in sums.items()}}, sums


def income_statement(*, actor, business_unit, start=None, end=None, scope="unit"):
    scope = _require_viewer(actor, business_unit, scope)
    start, end = _period(start, end)
    year_start = end.replace(month=1, day=1)
    lines = _posted_lines(business_unit, scope).filter(account__account_class__in=[REVENUE, EXPENSE])
    columns = {
        "period": _totals(lines.filter(entry__document_date__gte=start, entry__document_date__lte=end)),
        "year_to_date": _totals(lines.filter(entry__document_date__gte=year_start, entry__document_date__lte=end)),
    }
    used = set(columns["period"]) | set(columns["year_to_date"])
    accounts = sorted(LedgerAccount.objects.filter(pk__in=used), key=lambda account: account.code)
    pick = lambda cls, codes, inside: [a for a in accounts if a.account_class == cls and (a.code in codes) == inside]
    sales, sales_sum = _section("sales", "Sales", pick(REVENUE, SALES_ACCOUNTS, True), columns, "credit")
    cost, cost_sum = _section("cost_of_sales", "Cost of sales", pick(EXPENSE, COST_OF_SALES_ACCOUNTS, True), columns, "debit")
    other_income, income_sum = _section("other_income", "Other income", pick(REVENUE, SALES_ACCOUNTS, False), columns, "credit")
    other_expenses, expense_sum = _section("other_expenses", "Other expenses", pick(EXPENSE, COST_OF_SALES_ACCOUNTS, False), columns, "debit")
    gross = {key: sales_sum[key] - cost_sum[key] for key in columns}
    net = {key: gross[key] + income_sum[key] - expense_sum[key] for key in columns}
    margin = lambda key: (
        format((gross[key] * 100 / sales_sum[key]).quantize(Decimal("0.1")), "f") if sales_sum[key] else None
    )
    return {
        **_scope(business_unit, scope),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "year_start": year_start.isoformat(),
        "currency": "QAR",
        "sections": [sales, cost, other_income, other_expenses],
        "gross_profit": {key: _money(value) for key, value in gross.items()},
        "gross_margin_percent": {key: margin(key) for key in columns},
        "net_profit": {key: _money(value) for key, value in net.items()},
        "policy_label": POLICY,
    }


def balance_sheet(*, actor, business_unit, as_of=None, scope="unit"):
    scope = _require_viewer(actor, business_unit, scope)
    as_of = _day(as_of, timezone.localdate(), "balance sheet")
    year_start = as_of.replace(month=1, day=1)
    lines = _posted_lines(business_unit, scope).filter(entry__document_date__lte=as_of)
    totals = {"balance": _totals(lines)}
    accounts = sorted(LedgerAccount.objects.filter(pk__in=set(totals["balance"])), key=lambda account: account.code)
    assets, asset_sum = _section("assets", "Assets", [a for a in accounts if a.account_class == ASSET], totals, "debit")
    liabilities, liability_sum = _section(
        "liabilities", "Liabilities", [a for a in accounts if a.account_class == LIABILITY], totals, "credit",
    )

    def profit(query):
        sums = query.filter(account__account_class__in=[REVENUE, EXPENSE]).aggregate(
            debit=Coalesce(Sum("debit"), Value(ZERO)), credit=Coalesce(Sum("credit"), Value(ZERO)),
        )
        return sums["credit"] - sums["debit"]

    earlier = profit(lines.filter(entry__document_date__lt=year_start))
    this_year = profit(lines.filter(entry__document_date__gte=year_start))
    equity_total = earlier + this_year
    equity = {
        "key": "equity", "label": "Equity",
        "rows": [
            {"code": "", "name": f"Profit kept from years before {as_of.year}", "balance": _money(earlier)},
            {"code": "", "name": f"Profit for {as_of.year} to date", "balance": _money(this_year)},
        ],
        "total": {"balance": _money(equity_total)},
    }
    difference = asset_sum["balance"] - liability_sum["balance"] - equity_total
    return {
        **_scope(business_unit, scope),
        "as_of": as_of.isoformat(),
        "currency": "QAR",
        "sections": [assets, liabilities, equity],
        "total_assets": _money(asset_sum["balance"]),
        "total_liabilities_and_equity": _money(liability_sum["balance"] + equity_total),
        "balanced": difference == ZERO,
        "difference": _money(difference),
        "policy_label": POLICY,
    }


# Month-end close.

def can_close_months(user, business_unit):
    return active_grants(user, UserActionGrant.Action.CLOSE_MONTH).filter(business_unit=business_unit).exists()


def _month(value):
    try:
        year, month = (int(part) for part in str(value or "").split("-"))
        return datetime.date(year, month, 1)
    except (TypeError, ValueError):
        raise InvalidTransaction("Give the month as YYYY-MM.") from None


def _next_month(month):
    return (month + datetime.timedelta(days=32)).replace(day=1)


def _label(month):
    return f"{month:%B %Y}"


def _person(user):
    return user.get_full_name() or user.get_username()


def _month_entries(business_unit, month):
    return _posted_entries(business_unit, "unit").filter(document_date__gte=month, document_date__lt=_next_month(month))


def _month_figures(business_unit, month):
    entries = _month_entries(business_unit, month)
    sums = _posted_lines(business_unit, "unit").filter(
        entry__document_date__gte=month, entry__document_date__lt=_next_month(month),
    ).aggregate(debit=Coalesce(Sum("debit"), Value(ZERO)), credit=Coalesce(Sum("credit"), Value(ZERO)))
    unbalanced = (
        entries.annotate(d=Coalesce(Sum("lines__debit"), Value(ZERO)), c=Coalesce(Sum("lines__credit"), Value(ZERO)))
        .exclude(d=F("c")).count()
    )
    return {"entry_count": entries.count(), "debit": sums["debit"], "credit": sums["credit"], "unbalanced": unbalanced}


def _warnings(business_unit, month):
    month_end = _next_month(month)
    counts = StockCount.objects.filter(
        business_unit=business_unit, opened_at__date__lt=month_end,
    ).exclude(status__in=[StockCount.Status.CLOSED, StockCount.Status.CANCELLED]).count()
    payments = SupplierPayment.objects.filter(
        business_unit=business_unit, status=SupplierPayment.Status.REQUESTED, paid_on__lt=month_end,
    ).count()
    warnings = []
    if counts:
        warnings.append(f"{counts} stock count{'s are' if counts > 1 else ' is'} not finished. "
                        "Their write-offs and adjustments will be posted in a later month.")
    if payments:
        warnings.append(f"{payments} supplier payment{'s are' if payments > 1 else ' is'} waiting for approval with a "
                        f"payment date in or before {_label(month)}. Approve or reject them before closing.")
    return warnings


def _check(key, label, done, detail, required=True, screen=""):
    return {"key": key, "label": label, "done": done, "detail": detail, "required": required, "screen": screen}


def _plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


def _checklist(business_unit, month):
    """What should be finished before a month is closed. Required checks need a reason to close without them."""
    end = _next_month(month)
    last_day = end - datetime.timedelta(days=1)
    checks = []
    accounts = list(CashAccount.objects.filter(business_unit=business_unit, opening_date__lte=last_day, is_synthetic=True)
                    .order_by("name"))
    if accounts:
        approved = set(BankReconciliation.objects.filter(account__in=accounts, month=month,
                                                         status=BankReconciliation.Status.APPROVED).values_list("account_id", flat=True))
        missing = [account.name for account in accounts if account.pk not in approved]
        checks.append(_check("bank_rec", "Bank and cash reconciled", not missing,
                             f"Not approved yet: {', '.join(missing)}." if missing
                             else f"All {_plural(len(accounts), 'account')} reconciled and approved.", screen="finance/bank-rec"))
    assets = FixedAsset.objects.filter(business_unit=business_unit, status=FixedAsset.Status.IN_USE, purchase_date__lt=end).count()
    if assets:
        run = DepreciationRun.objects.filter(business_unit=business_unit, month=month).exists()
        checks.append(_check("depreciation", "Depreciation posted", run,
                             f"Posted for {_plural(assets, 'asset')} in use." if run
                             else f"{_label(month)} depreciation is not posted yet.", screen="finance/assets"))
    paid = (EmployeePay.objects.filter(employee__business_unit=business_unit, effective_from__lt=end)
            .exclude(employee__status=Employee.Status.LEFT).values("employee").distinct().count())
    if paid:
        posted = PayrollRun.objects.filter(business_unit=business_unit, month=month, status=PayrollRun.Status.POSTED).exists()
        checks.append(_check("payroll", "Payroll approved and posted", posted,
                             "Posted." if posted else f"{_label(month)} payroll is not approved and posted yet.", screen="hr/payroll"))
    waiting = [
        (ManualJournal.objects.filter(business_unit=business_unit, status=ManualJournal.Status.SUBMITTED,
                                      entry_date__gte=month, entry_date__lt=end).count(), "journal entry", "journal entries"),
        (Expense.objects.filter(business_unit=business_unit, status=Expense.Status.SUBMITTED,
                                expense_date__gte=month, expense_date__lt=end).count(), "expense", "expenses"),
        (SupplierPayment.objects.filter(business_unit=business_unit, status=SupplierPayment.Status.REQUESTED,
                                        paid_on__lt=end).count(), "supplier payment", "supplier payments"),
        (SupplierAdvance.objects.filter(business_unit=business_unit, status=SupplierAdvance.Status.REQUESTED,
                                        paid_on__lt=end).count(), "supplier advance", "supplier advances"),
        (AdjustmentNote.objects.filter(business_unit=business_unit, status=AdjustmentNote.Status.SUBMITTED,
                                       note_date__gte=month, note_date__lt=end).count(), "credit or debit note", "credit or debit notes"),
    ]
    open_items = [f"{count} {one if count == 1 else many}" for count, one, many in waiting if count]
    checks.append(_check("approvals", "Nothing waiting for approval", not open_items,
                         f"Still waiting: {', '.join(open_items)}. Approve or reject them before closing." if open_items
                         else f"Nothing dated in {_label(month)} is waiting for approval."))
    counts = StockCount.objects.filter(business_unit=business_unit, opened_at__date__lt=end).exclude(
        status__in=[StockCount.Status.CLOSED, StockCount.Status.CANCELLED]).count()
    checks.append(_check("stock_counts", "Stock counts finished", not counts,
                         f"{_plural(counts, 'stock count')} not finished." if counts else "No stock count left open.",
                         required=False, screen="inventory/counts"))
    from .fixed_assets import _unbilled

    unbilled = _unbilled(business_unit, month)
    checks.append(_check("bills", "Supplier bills recorded", not unbilled,
                         f"{len(unbilled)} {'delivery' if len(unbilled) == 1 else 'deliveries'} still waiting for the supplier's bill."
                         if unbilled else "Every delivery has its supplier's bill.", screen="finance/supplier-bills"))
    return checks


def _first_month(business_unit):
    earliest = _posted_entries(business_unit, "unit").aggregate(day=Min("document_date"))["day"]
    closed = ClosedMonth.objects.filter(business_unit=business_unit).aggregate(month=Min("month"))["month"]
    candidates = [day.replace(day=1) for day in (earliest, closed) if day]
    return min(candidates) if candidates else None


def _close_problem(business_unit, month, figures, current):
    if month >= current:
        return "This month has not ended yet."
    if ClosedMonth.objects.filter(business_unit=business_unit, month=month).exists():
        return f"{_label(month)} is already closed."
    first = _first_month(business_unit)
    if first and first < month:
        closed = set(ClosedMonth.objects.filter(business_unit=business_unit, month__lt=month).values_list("month", flat=True))
        walk = first
        while walk < month:
            if walk not in closed:
                return f"Close {_label(walk)} first. Months are closed in order."
            walk = _next_month(walk)
    if figures["unbalanced"]:
        return f"{figures['unbalanced']} entries in {_label(month)} do not balance."
    return ""


def _require_closer(actor, business_unit):
    _require_viewer(actor, business_unit, "unit")
    if not can_close_months(actor, business_unit):
        raise WorkflowPermissionDenied("You do not have permission to close or reopen months for this business unit.")


def month_close_overview(*, actor, business_unit):
    _require_viewer(actor, business_unit, "unit")
    current = timezone.localdate().replace(day=1)
    first = _first_month(business_unit) or current
    first = max(first, (current - datetime.timedelta(days=31 * (MONTHS_SHOWN - 1))).replace(day=1))
    closed = {row.month: row for row in ClosedMonth.objects.filter(business_unit=business_unit).select_related("closed_by")}
    latest_closed = max(closed) if closed else None
    allowed = can_close_months(actor, business_unit)
    months, walk = [], first
    while walk <= current:
        figures = _month_figures(business_unit, walk)
        row = closed.get(walk)
        problem = "" if row else _close_problem(business_unit, walk, figures, current)
        months.append({
            "month": walk.strftime("%Y-%m"),
            "label": _label(walk),
            "status": "closed" if row else ("current" if walk == current else "open"),
            "entry_count": figures["entry_count"],
            "debit": _money(figures["debit"]),
            "credit": _money(figures["credit"]),
            "closed_by": _person(row.closed_by) if row else "",
            "closed_at": row.closed_at.isoformat() if row else "",
            "can_close": allowed and not row and not problem,
            "close_blocked": problem,
            "warnings": _warnings(business_unit, walk) if not row and not problem else [],
            "checklist": _checklist(business_unit, walk) if not row and not problem else [],
            "can_reopen": allowed and is_master_admin(actor) and bool(row) and walk == latest_closed,
        })
        walk = _next_month(walk)
    events = MonthCloseEvent.objects.filter(business_unit=business_unit).select_related("actor")[:HISTORY]
    return {
        **_scope(business_unit, "unit"),
        "currency": "QAR",
        "can_close": allowed,
        "months": list(reversed(months)),
        "history": [{
            "month": _label(event.month), "action": event.action, "action_label": event.get_action_display(),
            "actor": _person(event.actor), "at": event.created_at.isoformat(), "reason": event.reason,
            "checks_not_done": [check["label"] for check in event.checklist if check.get("required") and not check.get("done")],
            "entry_count": event.entry_count, "total_debit": _money(event.total_debit),
        } for event in events],
        "policy_label": POLICY,
    }


def _lock_postings():
    # Hold back new journal entries until this close commits, so the figures it records are final.
    with connection.cursor() as cursor:
        cursor.execute(f'LOCK TABLE "{JournalEntry._meta.db_table}" IN SHARE ROW EXCLUSIVE MODE')


@transaction.atomic
def close_month(*, actor, business_unit, month, reason=""):
    _require_closer(actor, business_unit)
    month = _month(month)
    _lock_postings()
    figures = _month_figures(business_unit, month)
    problem = _close_problem(business_unit, month, figures, timezone.localdate().replace(day=1))
    if problem:
        raise InvalidTransaction(problem)
    checklist = _checklist(business_unit, month)
    not_done = [check["label"].lower() for check in checklist if check["required"] and not check["done"]]
    reason = " ".join(reason.split()) if isinstance(reason, str) else ""
    if len(reason) > 240:
        raise InvalidTransaction("Keep the reason under 240 characters.")
    if not_done and len(reason) < 10:
        raise InvalidTransaction(f"Not done yet: {', '.join(not_done)}. Finish them, or give the reason for closing anyway "
                                 "(at least 10 characters).")
    ClosedMonth.objects.create(
        legal_entity=business_unit.legal_entity, business_unit=business_unit, month=month, closed_by=actor,
        entry_count=figures["entry_count"], total_debit=figures["debit"],
    )
    MonthCloseEvent.objects.create(
        legal_entity=business_unit.legal_entity, business_unit=business_unit, month=month,
        action=MonthCloseEvent.Action.CLOSE, actor=actor, reason=reason if not_done else "",
        entry_count=figures["entry_count"], total_debit=figures["debit"],
        checklist=[{"label": check["label"], "done": check["done"], "required": check["required"], "detail": check["detail"]}
                   for check in checklist],
    )
    return month_close_overview(actor=actor, business_unit=business_unit)


@transaction.atomic
def reopen_month(*, actor, business_unit, month, reason):
    _require_closer(actor, business_unit)
    require_master_admin(actor, "reopen a closed month")
    month = _month(month)
    reason = " ".join(reason.split()) if isinstance(reason, str) else ""
    if len(reason) < 10:
        raise InvalidTransaction("Say why the month is being reopened (at least 10 characters).")
    if len(reason) > 240:
        raise InvalidTransaction("Keep the reason under 240 characters.")
    row = ClosedMonth.objects.select_for_update().filter(business_unit=business_unit, month=month).first()
    if row is None:
        raise InvalidTransaction(f"{_label(month)} is not closed.")
    latest = ClosedMonth.objects.filter(business_unit=business_unit).aggregate(month=Max("month"))["month"]
    if latest != month:
        raise InvalidTransaction(f"Reopen {_label(latest)} first. Only the latest closed month can be reopened.")
    figures = _month_figures(business_unit, month)
    row.delete()
    MonthCloseEvent.objects.create(
        legal_entity=business_unit.legal_entity, business_unit=business_unit, month=month,
        action=MonthCloseEvent.Action.REOPEN, actor=actor, reason=reason,
        entry_count=figures["entry_count"], total_debit=figures["debit"],
    )
    return month_close_overview(actor=actor, business_unit=business_unit)
