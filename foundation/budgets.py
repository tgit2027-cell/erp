"""Budgets: what a business unit plans to sell and spend each month, compared with what actually happened (synthetic only).

A budget belongs to one business unit and one calendar year. It has an amount
per line per month. The lines follow the profit and loss statement:

    Sales                 sales revenue less sales returns
    Cost of sales         cost of goods sold and stock count differences
    one line per expense category (rent, electricity and water, ...)
    Provisions            amounts set aside for likely costs (end of service, doubtful debts)
    Other running costs   the "other" expense category plus every other expense account (bank charges, ...)
    Bank interest         interest the bank pays us
    Other income          every other income account (exchange gains, ...)

Actual figures come straight from the posted general ledger, so they match the
financial statements. Someone with prepare_budget fills it in and sends it for
approval; a different person with approve_budget approves it or sends it back.
An approved budget is locked; reopening it needs a reason. Every step is kept.
Each line is also compared with the same months of the two previous years.
Provisional: calendar years, QAR only.
"""

import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce, ExtractMonth
from django.utils import timezone

from .access import active_grants
from .financial_statements import COST_OF_SALES_ACCOUNTS, SALES_ACCOUNTS
from .general_ledger import _posted_lines, can_view_ledger
from .models import Budget, BudgetAmount, BudgetEvent, DemoUserMarker, Expense, LedgerAccount, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction, _require_demo

ZERO = Decimal("0.00")
PREPARE = UserActionGrant.Action.PREPARE_BUDGET
APPROVE = UserActionGrant.Action.APPROVE_BUDGET
MAX_AMOUNT = Decimal("1000000000.00")
CATEGORY_LINES = [(value, label) for value, label in Expense.Category.choices if value != Expense.Category.OTHER]
LINES = (
    [("sales", "Sales", "income"), ("cost_of_sales", "Cost of sales", "cost")]
    + [(value, label, "cost") for value, label in CATEGORY_LINES]
    + [("provisions", "Provisions", "cost"), ("other", "Other running costs", "cost"),
       ("bank_interest", "Bank interest", "income"), ("other_income", "Other income", "income")]
)
INCOME_LINES = {key for key, _, kind in LINES if kind == "income"}
INTEREST_ACCOUNTS = {"TEST_INTEREST_INCOME"}
PROVISION_ACCOUNTS = {"TEST_EXP_PROVISIONS"}
PREVIOUS_YEARS = 2
LINE_KEYS = [key for key, _, _ in LINES]
CATEGORY_ACCOUNTS = {f"TEST_EXP_{value.upper()}": value for value, _ in CATEGORY_LINES}
POLICY_LABEL = (
    "TEST ONLY provisional rules: calendar-year budgets per business unit; one person prepares, a different person "
    "approves; actual figures come from the posted ledger; bank interest and other income are budgeted on their own lines; "
    "each line is compared with the same months of the two previous years; QAR only."
)


def _granted(user, business_unit, action):
    return active_grants(user, action).filter(business_unit=business_unit).exists()


def can_prepare(user, business_unit):
    return _granted(user, business_unit, PREPARE)


def can_approve(user, business_unit):
    return _granted(user, business_unit, APPROVE)


def _require_user(user, business_unit):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Budgets run only in the synthetic demo environment.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can use budgets.")
    _require_demo(business_unit, business_unit.legal_entity)
    if not (can_prepare(user, business_unit) or can_approve(user, business_unit) or can_view_ledger(user, business_unit)):
        raise WorkflowPermissionDenied("You need permission to prepare, approve or view budgets in this business unit.")


def _year(value):
    try:
        year = int(value) if value not in (None, "") else timezone.localdate().year
    except (TypeError, ValueError):
        raise InvalidTransaction("Choose a year.") from None
    if not 2000 <= year <= 2100:
        raise InvalidTransaction("Choose a year between 2000 and 2100.")
    return year


def _line_for(account):
    if account.code in SALES_ACCOUNTS:
        return "sales"
    if account.account_class == LedgerAccount.AccountClass.REVENUE:
        return "bank_interest" if account.code in INTEREST_ACCOUNTS else "other_income"
    if account.account_class != LedgerAccount.AccountClass.EXPENSE:
        return None
    if account.code in COST_OF_SALES_ACCOUNTS:
        return "cost_of_sales"
    if account.code in PROVISION_ACCOUNTS:
        return "provisions"
    return CATEGORY_ACCOUNTS.get(account.code, "other")


def actuals(business_unit, year):
    """Actual amount per line per month (index 0 is January), from the posted ledger."""
    result = {key: [ZERO] * 12 for key in LINE_KEYS}
    rows = (
        _posted_lines(business_unit, "unit")
        .filter(entry__document_date__year=year,
                account__account_class__in=[LedgerAccount.AccountClass.REVENUE, LedgerAccount.AccountClass.EXPENSE])
        .annotate(month=ExtractMonth("entry__document_date"))
        .values("account_id", "month")
        .annotate(debit=Coalesce(Sum("debit"), Value(ZERO)), credit=Coalesce(Sum("credit"), Value(ZERO)))
    )
    accounts = {account.pk: account for account in LedgerAccount.objects.filter(pk__in={row["account_id"] for row in rows})}
    for row in rows:
        line = _line_for(accounts[row["account_id"]])
        if line is None:
            continue
        amount = row["credit"] - row["debit"] if line in INCOME_LINES else row["debit"] - row["credit"]
        result[line][row["month"] - 1] += amount
    return result


def _planned(budget):
    result = {key: [ZERO] * 12 for key in LINE_KEYS}
    if budget is not None:
        for amount in budget.amounts.all():
            if amount.line in result:
                result[amount.line][amount.month - 1] = amount.amount
    return result


def _f(value):
    return format(value, "f")


def _compare(kind, budget, actual, happened=True):
    """happened is False when the period has not ended or started yet, so falling short is not a warning."""
    difference = actual - budget
    if not happened and kind == "income":
        flag = ""
    elif kind == "income":
        flag = "below" if budget and actual < budget else ""
    else:
        flag = "over" if actual > budget else ""
    used = int((actual * 100 / budget).quantize(Decimal("1"))) if budget else None
    return {"budget": _f(budget), "actual": _f(actual), "difference": _f(difference), "used_percent": used, "flag": flag}


def _previous(year, month, to_date, full_year, now_month, now_to_date):
    """The same months of an earlier year, and how this year's actual figures moved against them."""
    def change(now, then):
        now, then = sum(now, ZERO), sum(then, ZERO)
        percent = int(((now - then) * 100 / abs(then)).quantize(Decimal("1"))) if then else None
        return {"actual": _f(then), "change": _f(now - then), "change_percent": percent}
    return {"year": year, "month": change(now_month, month), "to_date": change(now_to_date, to_date),
            "full_year": _f(sum(full_year, ZERO))}


def _person(user):
    return (user.get_full_name() or user.username) if user else ""


def _sum_lines(values, month_slice):
    income = sum((sum(values[key][month_slice], ZERO) for key, _, kind in LINES if kind == "income"), ZERO)
    cost = sum((sum(values[key][month_slice], ZERO) for key, _, kind in LINES if kind == "cost"), ZERO)
    gross_cost = sum(values["cost_of_sales"][month_slice], ZERO)
    sales = sum(values["sales"][month_slice], ZERO)
    return sales - gross_cost, income - cost


def budget_view(*, actor, business_unit, year=None, month=None):
    _require_user(actor, business_unit)
    year = _year(year)
    today = timezone.localdate()
    default_month = today.month if year == today.year else 12 if year < today.year else 1
    try:
        month = int(month) if month not in (None, "") else default_month
    except (TypeError, ValueError):
        raise InvalidTransaction("Choose a month.") from None
    if not 1 <= month <= 12:
        raise InvalidTransaction("Choose a month from 1 to 12.")
    budget = Budget.objects.filter(business_unit=business_unit, year=year).select_related(
        "prepared_by", "approved_by").prefetch_related("amounts").first()
    planned = _planned(budget)
    actual = actuals(business_unit, year)
    earlier = {back: actuals(business_unit, year - back) for back in range(1, PREVIOUS_YEARS + 1)}
    last_year = earlier[1]
    this, upto = slice(month - 1, month), slice(0, month)
    # A month counts as happened once it has ended; the current month and later ones are still running.
    happened = (year, month) < (today.year, today.month)
    lines = []
    for key, label, kind in LINES:
        lines.append({
            "line": key, "label": label, "kind": kind,
            "budget": [_f(value) for value in planned[key]], "actual": [_f(value) for value in actual[key]],
            "last_year": [_f(value) for value in last_year[key]],
            "year_budget": _f(sum(planned[key], ZERO)),
            "month": _compare(kind, planned[key][month - 1], actual[key][month - 1], happened),
            "to_date": _compare(kind, sum(planned[key][upto], ZERO), sum(actual[key][upto], ZERO), happened),
            "previous": [_previous(year - back, values[key][this], values[key][upto], values[key], actual[key][this], actual[key][upto])
                         for back, values in earlier.items()],
        })
    profit = {}
    for name, index in (("gross_profit", 0), ("net_profit", 1)):
        profit[name] = {
            "month": _compare("income", _sum_lines(planned, this)[index], _sum_lines(actual, this)[index], happened),
            "to_date": _compare("income", _sum_lines(planned, upto)[index], _sum_lines(actual, upto)[index], happened),
            "year_budget": _f(_sum_lines(planned, slice(0, 12))[index]),
            "previous": [
                _previous(year - back, [_sum_lines(values, this)[index]], [_sum_lines(values, upto)[index]],
                          [_sum_lines(values, slice(0, 12))[index]],
                          [_sum_lines(actual, this)[index]], [_sum_lines(actual, upto)[index]])
                for back, values in earlier.items()],
        }
    status = budget.status if budget else ""
    prepare, approve = can_prepare(actor, business_unit), can_approve(actor, business_unit)
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "year": year, "month": month,
        "years": sorted({today.year - 1, today.year, today.year + 1, year,
                         *Budget.objects.filter(business_unit=business_unit).values_list("year", flat=True)}),
        "budget": None if budget is None else {
            "id": budget.pk, "status": budget.status, "status_label": budget.get_status_display(), "note": budget.note,
            "prepared_by": _person(budget.prepared_by), "approved_by": _person(budget.approved_by),
            "approved_at": budget.approved_at.isoformat() if budget.approved_at else "",
            "updated_at": budget.updated_at.isoformat(),
        },
        "lines": lines, **profit,
        "history": [
            {"at": event.at.isoformat(), "by": _person(event.by), "what": event.get_kind_display(), "note": event.note,
             "total": _f(event.total)}
            for event in (budget.events.select_related("by")[:30] if budget else [])
        ],
        "can_prepare": prepare, "can_approve": approve,
        "can_edit": prepare and status in ("", Budget.Status.DRAFT),
        "can_submit": prepare and status == Budget.Status.DRAFT,
        "can_decide": approve and status == Budget.Status.SUBMITTED and budget.prepared_by_id != actor.pk,
        "can_reopen": approve and status == Budget.Status.APPROVED,
        "policy_label": POLICY_LABEL,
    }


def _amount(value, label):
    if value in (None, ""):
        return ZERO
    try:
        amount = Decimal(str(value).replace(",", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise InvalidTransaction(f"{label} is not a number.") from None
    if amount < 0 or amount > MAX_AMOUNT:
        raise InvalidTransaction(f"{label} must be between 0 and 1,000,000,000.")
    return amount


def _load(business_unit, year, *, lock=True):
    rows = Budget.objects.filter(business_unit=business_unit, year=year)
    budget = (rows.select_for_update() if lock else rows).first()
    if budget is None:
        raise InvalidTransaction(f"There is no {year} budget yet.")
    return budget


def _total(budget):
    return budget.amounts.aggregate(total=Coalesce(Sum("amount"), Value(ZERO)))["total"]


def _note(value, label="note", required=False):
    text = " ".join(str(value or "").split())
    if required and not text:
        raise InvalidTransaction(f"Give a {label}.")
    if len(text) > 300:
        raise InvalidTransaction(f"Keep the {label} to 300 characters.")
    return text


@transaction.atomic
def save_budget(*, actor, business_unit, year, amounts, note=""):
    """Create or replace the amounts of a budget that is still being prepared."""
    _require_user(actor, business_unit)
    if not can_prepare(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'prepare budgets' permission for this unit.")
    year = _year(year)
    if not isinstance(amounts, dict) or set(amounts) - set(LINE_KEYS):
        raise InvalidTransaction("Send an amount per month for each budget line.")
    labels = {key: label for key, label, _ in LINES}
    rows = []
    for key, months in amounts.items():
        if not isinstance(months, list) or len(months) != 12:
            raise InvalidTransaction(f"{labels[key]} needs 12 monthly amounts.")
        for index, value in enumerate(months):
            amount = _amount(value, f"{labels[key]} for {datetime.date(year, index + 1, 1):%B}")
            if amount:
                rows.append((key, index + 1, amount))
    budget, created = Budget.objects.select_for_update().get_or_create(
        business_unit=business_unit, year=year, defaults={"prepared_by": actor})
    if not created and budget.status != Budget.Status.DRAFT:
        raise InvalidTransaction(f"The {year} budget is {budget.get_status_display().lower()}; it cannot be changed now.")
    budget.amounts.all().delete()
    BudgetAmount.objects.bulk_create([BudgetAmount(budget=budget, line=key, month=month, amount=amount)
                                      for key, month, amount in rows])
    budget.prepared_by, budget.updated_at, budget.note = actor, timezone.now(), _note(note)
    budget.save(update_fields=["prepared_by", "updated_at", "note"])
    BudgetEvent.objects.create(budget=budget, kind=BudgetEvent.Kind.SAVED, by=actor, total=_total(budget))
    return budget_view(actor=actor, business_unit=business_unit, year=year)


@transaction.atomic
def decide_budget(*, actor, business_unit, year, action, reason=""):
    """Send for approval, approve, send back or reopen."""
    _require_user(actor, business_unit)
    year = _year(year)
    budget = _load(business_unit, year)
    status, kind = budget.status, None
    if action == "submit":
        if not can_prepare(actor, business_unit):
            raise WorkflowPermissionDenied("You need the 'prepare budgets' permission for this unit.")
        if status != Budget.Status.DRAFT:
            raise InvalidTransaction(f"The {year} budget is already {budget.get_status_display().lower()}.")
        if not _total(budget):
            raise InvalidTransaction("Fill in at least one amount before sending the budget for approval.")
        budget.status, kind = Budget.Status.SUBMITTED, BudgetEvent.Kind.SUBMITTED
    elif action in ("approve", "return", "reopen"):
        if not can_approve(actor, business_unit):
            raise WorkflowPermissionDenied("You need the 'approve budgets' permission for this unit.")
        if action == "reopen":
            if status != Budget.Status.APPROVED:
                raise InvalidTransaction(f"Only an approved budget can be reopened; the {year} budget is {budget.get_status_display().lower()}.")
            reason = _note(reason, "reason for reopening it", required=True)
            budget.status, budget.approved_by, budget.approved_at = Budget.Status.DRAFT, None, None
            kind = BudgetEvent.Kind.REOPENED
        else:
            if status != Budget.Status.SUBMITTED:
                raise InvalidTransaction(f"The {year} budget is not waiting for approval.")
            if budget.prepared_by_id == actor.pk:
                raise WorkflowPermissionDenied("Someone other than the person who prepared the budget must approve it.")
            if action == "approve":
                budget.status, budget.approved_by, budget.approved_at = Budget.Status.APPROVED, actor, timezone.now()
                kind = BudgetEvent.Kind.APPROVED
            else:
                reason = _note(reason, "reason for sending it back", required=True)
                budget.status, kind = Budget.Status.DRAFT, BudgetEvent.Kind.RETURNED
    else:
        raise InvalidTransaction("Send for approval, approve, send back or reopen.")
    budget.updated_at = timezone.now()
    budget.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    BudgetEvent.objects.create(budget=budget, kind=kind, by=actor, note=reason if action in ("return", "reopen") else "",
                               total=_total(budget))
    return budget_view(actor=actor, business_unit=business_unit, year=year)
