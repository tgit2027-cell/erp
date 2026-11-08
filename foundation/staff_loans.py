"""Staff loans and salary advances: asked for by HR, approved by someone else, paid out by
finance, and taken back a month at a time through payroll.

Tivoli's rules (from Sam, 4 Nov 2026):

- The amount and the monthly instalment are agreed by management case by case and entered
  on the request once approved; the system then counts the instalments down.
- The only fixed limit: everything a person owes, this request included, cannot be more
  than the end of service they have earned so far plus the coming month's salary, so it can
  always be recovered when they leave.
- Finance pays it out from a bank account or cash box, not through WPS.

Provisional until confirmed:

- Each person can have one advance and one loan open at a time, and nobody serving notice
  or who has left can get a new one.
- The instalment comes off the payroll from the first month chosen. Qatar Labour Law caps
  all deductions together at half the month's pay; if the cap cuts an instalment, the rest
  stays owed and the loan runs a month longer.
- When someone leaves, whatever is still owed comes off the end-of-service settlement, as
  far as the settlement covers it. Anything left over is shown so HR can recover it.

Who: someone with "Set salaries and prepare payroll" records the request; someone else with
"Approve and post payroll" approves or rejects it, never for themselves; someone with
"Record bank and cash" pays it out from a bank account or cash box. The payout is booked to
staff loans and advances (an asset), and each instalment taken through payroll reduces it.
"""

import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .cash_accounts import _account as _cash_account, _past_day, _reconciled, _text, can_manage as can_manage_cash
from .leave import _require_demo_user
from .models import CashAccount, CashLineAssignment, Employee, JournalEntry, PayrollRun, StaffLoan
from .pay_records import can_manage_pay, can_view_pay, current_pay, total
from .payroll import _round, _text_money, can_approve_pay
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import _name
from .transaction_services import InvalidTransaction, _begin_idempotent_operation, _complete, _post_journal

S = StaffLoan.Status
K = StaffLoan.Kind
ZERO = Decimal("0.00")
LOAN_ACCOUNT = "TEST_STAFF_LOANS"
CASH_CODE = "TEST_CASH"
KINDS = (K.ADVANCE, K.LOAN)
OPEN = (S.REQUESTED, S.APPROVED, S.REPAYING)
SHOWN_DAYS = 365
POLICY_LABEL = ("TEST ONLY Tivoli rules: amount and instalment agreed by management case by case; everything owed stays within "
                "the end of service earned so far plus the coming month's salary; paid out by finance. Provisional: one of each "
                "open per person; instalments inside the half-pay deductions cap; what is still owed comes off the "
                "end-of-service settlement.")


def _first(day):
    return day.replace(day=1)


def _next(month):
    return (month.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)


def next_payroll_month(business_unit):
    """The first month, from this one on, whose payroll is not yet approved and posted."""
    month = _first(timezone.localdate())
    posted = set(PayrollRun.objects.filter(business_unit=business_unit, status=PayrollRun.Status.POSTED, month__gte=month)
                 .values_list("month", flat=True))
    while month in posted:
        month = _next(month)
    return month


def repaid(loan):
    return loan.repayments.aggregate(total=Sum("amount"))["total"] or ZERO


def balance(loan):
    """What is still owed. Nothing is owed until the money is paid out."""
    if loan.status not in (S.REPAYING, S.REPAID):
        return ZERO
    return loan.amount - repaid(loan)


def _int(value):
    return value if type(value) is int else None


def _amount(value, field):
    try:
        amount = Decimal(str(value if value is not None else "").replace(",", ""))
    except InvalidOperation:
        raise InvalidTransaction(f"Enter the {field}.") from None
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
        raise InvalidTransaction(f"The {field} must be more than zero, with at most two decimals.")
    return amount


def _months_left(loan, owed):
    if owed <= 0:
        return 0
    return int((owed / loan.instalment).to_integral_value(rounding="ROUND_CEILING"))


def owed_by(employee):
    """Everything this person owes or has asked for: open requests in full, paid-out loans by what is left."""
    owed = ZERO
    for loan in StaffLoan.objects.filter(employee=employee, status__in=OPEN):
        owed += balance(loan) if loan.status == S.REPAYING else loan.amount
    return owed


def limits(employee, pay):
    """What this person could borrow now, and why."""
    from .payroll import EOS_DAYS_PER_YEAR, _service_days

    today = timezone.localdate()
    years = Decimal(_service_days(employee, today)) / Decimal("365") if employee.joined_on <= today else Decimal("0")
    eos = _round(pay.basic * years * EOS_DAYS_PER_YEAR / 30) if years >= 1 else ZERO
    month_pay = total(pay)
    owed = owed_by(employee)
    room = max(eos + month_pay - owed, ZERO)
    return {"eos": eos, "month_pay": month_pay, "owed": owed, "room": room}


def _row(loan, actor):
    unit = loan.business_unit
    owed = balance(loan)
    repayments = list(loan.repayments.select_related("run").order_by("month", "pk"))
    return {
        "id": loan.pk, "kind": loan.kind, "kind_label": loan.get_kind_display(),
        "employee": {"id": loan.employee_id, "name": loan.employee.full_name, "staff_number": loan.employee.staff_number,
                     "department": loan.employee.department},
        "amount": _text_money(loan.amount), "instalment": _text_money(loan.instalment),
        "first_month": loan.first_month.strftime("%Y-%m"), "first_month_label": loan.first_month.strftime("%B %Y"),
        "reason": loan.reason, "status": loan.status, "status_label": loan.get_status_display(),
        "repaid": _text_money(loan.amount - owed) if loan.status in (S.REPAYING, S.REPAID) else "0.00",
        "balance": _text_money(owed), "months_left": _months_left(loan, owed),
        "repayments": [{"month": row.month.strftime("%B %Y"), "amount": _text_money(row.amount),
                        "from_settlement": row.from_settlement} for row in repayments],
        "requested_by": _name(loan.requested_by), "requested_at": loan.requested_at.isoformat(),
        "decided_by": _name(loan.decided_by), "decided_at": loan.decided_at.isoformat() if loan.decided_at else "",
        "decision_note": loan.decision_note,
        "paid_from": loan.paid_from.name if loan.paid_from_id else "", "paid_on": loan.paid_on.isoformat() if loan.paid_on else "",
        "paid_by": _name(loan.paid_by), "payout_entry": loan.payout_entry_id,
        "can_decide": (loan.status == S.REQUESTED and can_approve_pay(actor, unit) and actor.pk != loan.requested_by_id
                       and loan.employee.user_id != actor.pk),
        "can_pay": loan.status == S.APPROVED and can_manage_cash(actor, unit) and loan.employee.user_id != actor.pk,
        "can_cancel": loan.status in (S.REQUESTED, S.APPROVED) and can_manage_pay(actor, unit),
    }


def _require_viewer(actor, business_unit):
    _require_demo_user(actor)
    if business_unit is None or not (can_view_pay(actor, business_unit) or can_manage_cash(actor, business_unit)):
        raise WorkflowPermissionDenied("Staff loans are shown only to people with payroll or bank and cash access in this unit.")


def loans_overview(*, actor, business_unit):
    _require_viewer(actor, business_unit)
    since = timezone.now() - datetime.timedelta(days=SHOWN_DAYS)
    loans = list(StaffLoan.objects.filter(business_unit=business_unit).select_related(
        "business_unit", "employee", "requested_by", "decided_by", "paid_from", "paid_by"))
    shown = [loan for loan in loans if loan.status in OPEN or (loan.decided_at or loan.requested_at) >= since]
    rows = [_row(loan, actor) for loan in shown]
    order = {S.REQUESTED: 0, S.APPROVED: 1, S.REPAYING: 2, S.REPAID: 3, S.REJECTED: 4, S.CANCELLED: 5}
    rows.sort(key=lambda row: (order[row["status"]], row["employee"]["name"]))
    owed = sum((Decimal(row["balance"]) for row in rows), ZERO)
    can_request = can_manage_pay(actor, business_unit)
    people = []
    if can_request:
        for person in Employee.objects.filter(business_unit=business_unit, status=Employee.Status.ACTIVE).order_by("full_name"):
            pay = current_pay(person)
            row = {"id": person.pk, "name": person.full_name, "department": person.department, "limits": None}
            if pay is not None:
                found = limits(person, pay)
                row["limits"] = {key: _text_money(value) for key, value in found.items()} | {"basic": _text_money(pay.basic)}
            people.append(row)
    accounts = []
    if can_manage_cash(actor, business_unit):
        accounts = [{"id": account.pk, "name": account.name, "kind_label": account.get_kind_display()}
                    for account in CashAccount.objects.filter(business_unit=business_unit, is_synthetic=True).order_by("kind", "name")]
    return {
        "unit": {"id": business_unit.pk, "name": business_unit.name}, "today": timezone.localdate().isoformat(),
        "next_month": next_payroll_month(business_unit).strftime("%Y-%m"),
        "tiles": {
            "owed": _text_money(owed),
            "repaying": sum(1 for row in rows if row["status"] == S.REPAYING),
            "waiting": sum(1 for row in rows if row["status"] == S.REQUESTED),
            "to_pay": sum(1 for row in rows if row["status"] == S.APPROVED),
        },
        "loans": rows, "people": people, "accounts": accounts,
        "can_request": can_request, "policy": POLICY_LABEL,
    }


@transaction.atomic
def request_loan(*, actor, business_unit, data):
    """Record a request for a loan or an advance, to be approved by someone else."""
    _require_demo_user(actor)
    if business_unit is None or not can_manage_pay(actor, business_unit):
        raise WorkflowPermissionDenied("Only people who prepare payroll in this unit can record a loan or an advance.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the person, the amount and the monthly instalment.")
    kind = data.get("kind")
    if kind not in KINDS:
        raise InvalidTransaction("Choose a staff loan or a salary advance.")
    employee = Employee.objects.select_for_update().filter(pk=_int(data.get("employee_id")), business_unit=business_unit).first()
    if employee is None:
        raise InvalidTransaction("Choose someone from this unit.")
    if employee.status != Employee.Status.ACTIVE:
        raise InvalidTransaction(f"{employee.full_name} is {employee.get_status_display().lower()}, so cannot get a new loan or advance.")
    if employee.user_id == actor.pk:
        raise WorkflowPermissionDenied("Someone else must record a loan or an advance for you.")
    pay = current_pay(employee)
    if pay is None:
        raise InvalidTransaction(f"Enter the pay for {employee.full_name} on the Salaries page first.")
    word = "advance" if kind == K.ADVANCE else "loan"
    if StaffLoan.objects.filter(employee=employee, kind=kind, status__in=OPEN).exists():
        raise InvalidTransaction(f"{employee.full_name} already has an open {word}. It must be repaid or cancelled first.")
    amount = _amount(data.get("amount"), "amount")
    found = limits(employee, pay)
    if amount > found["room"]:
        already = f", less {_text_money(found['owed'])} QAR already owed or asked for" if found["owed"] else ""
        raise InvalidTransaction(f"{employee.full_name} can borrow at most {_text_money(found['room'])} QAR now: end of service "
                                 f"earned so far {_text_money(found['eos'])} plus the coming month's salary "
                                 f"{_text_money(found['month_pay'])}{already}.")
    instalment = _amount(data.get("instalment") if data.get("instalment") not in (None, "") else amount, "monthly instalment")
    if instalment > amount:
        raise InvalidTransaction("The monthly instalment cannot be more than the amount.")
    earliest = next_payroll_month(business_unit)
    first = data.get("first_month")
    if first in (None, ""):
        first_month = earliest
    else:
        try:
            year, month = (int(part) for part in str(first).split("-"))
            first_month = datetime.date(year, month, 1)
        except (TypeError, ValueError):
            raise InvalidTransaction("Give the first month as YYYY-MM.") from None
    if first_month < earliest:
        raise InvalidTransaction(f"The first instalment can come off the {earliest:%B %Y} payroll at the earliest.")
    if first_month > _next(_next(_next(earliest))):
        raise InvalidTransaction("Start the instalments within the next three payrolls.")
    reason = _text(data.get("reason"), "reason", 200, required=True)
    loan = StaffLoan.objects.create(business_unit=business_unit, employee=employee, kind=kind, amount=amount,
                                    instalment=instalment, first_month=first_month, reason=reason, requested_by=actor)
    return _row(loan, actor)


def _load(business_unit, loan_id):
    loan = (StaffLoan.objects.select_for_update().filter(pk=_int(loan_id), business_unit=business_unit)
            .select_related("business_unit__legal_entity", "employee").first())
    if loan is None:
        raise InvalidTransaction("That loan or advance was not found.")
    return loan


def _pay_out(actor, loan, account_id, date):
    business_unit = loan.business_unit
    if not can_manage_cash(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'record bank and cash' permission to pay out a loan or an advance.")
    if loan.employee.user_id == actor.pk:
        raise WorkflowPermissionDenied("Someone else must pay out a loan or an advance to you.")
    if loan.status != S.APPROVED:
        raise InvalidTransaction("Only an approved loan or advance can be paid out.")
    if loan.employee.status != Employee.Status.ACTIVE:
        raise InvalidTransaction(f"{loan.employee.full_name} is no longer active. Cancel it instead.")
    account = _cash_account(business_unit, _int(account_id), "account it is paid from")
    day = _past_day(date, "date")
    if day < loan.decided_at.date():
        raise InvalidTransaction("The payout cannot be dated before it was approved.")
    if day < account.opening_date:
        raise InvalidTransaction(f"{account.name} opened on {account.opening_date.isoformat()}, after this date.")
    if _reconciled(account, day):
        raise InvalidTransaction(f"{day:%B %Y} is reconciled and approved for {account.name}. Use a later date.")
    operation, _ = _begin_idempotent_operation(f"staff-loan-{loan.pk}-payout", "staff_loan_payout", {"loan": loan.pk})
    memo = f"{loan.get_kind_display()} to {loan.employee.full_name}"[:240]
    entry = _post_journal(
        legal_entity=business_unit.legal_entity, business_unit=business_unit, idempotency=operation,
        kind=JournalEntry.Kind.PAYROLL, description=f"TEST ONLY: {memo}",
        lines=[(LOAN_ACCOUNT, loan.amount, ZERO, memo), (CASH_CODE, ZERO, loan.amount, memo)], document_date=day,
    )
    CashLineAssignment.objects.create(journal_line=entry.lines.get(credit__gt=0), account=account, assigned_by=actor)
    _complete(operation, {"loan": loan.pk, "journal_entry_id": entry.pk})
    loan.status, loan.paid_from, loan.paid_on, loan.paid_by, loan.payout_entry = S.REPAYING, account, day, actor, entry
    loan.save(update_fields=["status", "paid_from", "paid_on", "paid_by", "payout_entry"])


@transaction.atomic
def decide_loan(*, actor, business_unit, loan_id, action, note="", account_id=None, date=None):
    _require_demo_user(actor)
    if business_unit is None:
        raise WorkflowPermissionDenied("Choose a unit.")
    loan = _load(business_unit, loan_id)
    if action in ("approve", "reject"):
        if loan.status != S.REQUESTED:
            raise InvalidTransaction("Only a request waiting for approval can be approved or rejected.")
        if not can_approve_pay(actor, business_unit):
            raise WorkflowPermissionDenied("You need 'Approve and post payroll' in this unit.")
        if actor.pk == loan.requested_by_id:
            raise WorkflowPermissionDenied("You recorded this request, so someone else must decide it.")
        if loan.employee.user_id == actor.pk:
            raise WorkflowPermissionDenied("This is for you, so someone else must decide it.")
        note = _text(note, "note", 200, required=action == "reject")
        loan.status = S.APPROVED if action == "approve" else S.REJECTED
        loan.decided_by, loan.decided_at, loan.decision_note = actor, timezone.now(), note
        loan.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    elif action == "pay":
        _pay_out(actor, loan, account_id, date)
    elif action == "cancel":
        if loan.status not in (S.REQUESTED, S.APPROVED):
            raise InvalidTransaction("Only a loan or advance not yet paid out can be cancelled.")
        if not can_manage_pay(actor, business_unit):
            raise WorkflowPermissionDenied("Only people who prepare payroll in this unit can cancel it.")
        loan.status, loan.decision_note = S.CANCELLED, _text(note, "reason", 200, required=True)
        loan.save(update_fields=["status", "decision_note"])
    else:
        raise InvalidTransaction("Choose approve, reject, pay or cancel.")
    return _row(loan, actor)


# Used by payroll and the end-of-service settlement.

def due(employee, month):
    """[(loan, what is owed)] for loans being repaid whose instalments have started by this month."""
    loans = StaffLoan.objects.filter(employee=employee, status=S.REPAYING, first_month__lte=month).order_by("first_month", "pk")
    return [(loan, owed) for loan in loans if (owed := balance(loan)) > 0]


def outstanding(employee):
    """[(loan, what is owed)] for every loan or advance paid out and not yet repaid, whatever the month."""
    loans = StaffLoan.objects.filter(employee=employee, status=S.REPAYING).order_by("first_month", "pk")
    return [(loan, owed) for loan in loans if (owed := balance(loan)) > 0]


def take_instalments(worked, employee, month, cap):
    """Add this month's instalments to the payroll line, inside what the deductions cap leaves room for."""
    room = max(cap - worked["deductions"], ZERO)
    rows = []
    for loan, owed in due(employee, month):
        wanted = min(loan.instalment, owed)
        taken = min(wanted, room)
        room -= taken
        rows.append({"loan": loan.pk, "label": loan.get_kind_display(), "due": _text_money(wanted), "amount": _text_money(taken),
                     "balance_after": _text_money(owed - taken)})
        if taken < wanted:
            worked["detail"]["notes"].append(
                f"{loan.get_kind_display()}: only {_text_money(taken)} of the {_text_money(wanted)} QAR instalment fits under "
                "the half-pay cap. The rest stays owed.")
    taken = sum((Decimal(row["amount"]) for row in rows), ZERO)
    worked["deductions"] += taken
    worked["net"] -= taken
    worked["detail"]["loans"] = rows
    return rows


def settlement_rows(employee, room):
    """Deductions for the end-of-service settlement: what is still owed, as far as `room` covers it."""
    rows, notes = [], []
    for loan, owed in outstanding(employee):
        taken = min(owed, max(room, ZERO))
        room -= taken
        if taken > 0:
            rows.append({"label": f"{loan.get_kind_display()} still owed", "amount": _text_money(taken), "loan": loan.pk})
        if taken < owed:
            notes.append(f"{loan.get_kind_display()}: {_text_money(owed - taken)} QAR is still owed after this settlement and "
                         "must be recovered another way.")
    return rows, notes


def record_repayments(run, repayments):
    """Record what an approved payroll took back, and close loans that are now repaid."""
    from .models import StaffLoanRepayment

    for loan_id, amount, from_settlement in repayments:
        loan = StaffLoan.objects.select_for_update().filter(pk=loan_id, business_unit=run.business_unit).select_related("employee").first()
        if loan is None or loan.status != S.REPAYING:
            raise InvalidTransaction("A staff loan in this payroll was changed after it was worked out. Work the payroll out again first.")
        owed = balance(loan)
        if amount > owed:
            where = "end-of-service settlement" if from_settlement else "payroll"
            raise InvalidTransaction(f"{loan.employee.full_name} now owes only {_text_money(owed)} QAR on the "
                                     f"{loan.get_kind_display().lower()}. Work the {where} out again first.")
        StaffLoanRepayment.objects.create(loan=loan, run=run, month=run.month, amount=amount, from_settlement=from_settlement)
        if amount == owed:
            loan.status = S.REPAID
            loan.save(update_fields=["status"])
