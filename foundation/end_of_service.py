"""End-of-service settlement: what is owed to someone who leaves, approved and then paid through payroll.

Tivoli's rules (from Sam, 1 Nov 2026), the same as payroll already uses:

- End of service pay = basic x years of service x 21 / 30, nothing before 1 full year,
  unpaid leave not counted as service.
- Unused annual leave is paid at basic / 30 a day (Sam, 3 Nov 2026). The days are those
  credited at the last work anniversary (plus carried days and HR changes) less what was
  taken by the last day, plus leave earned pro rata from the last anniversary to the last
  working day: 30 days x days since the anniversary / 365, to the nearest half day. Leave
  taken beyond what was earned is deducted at the same rate.

Provisional until confirmed:

- Nothing is paid for leave before the first work anniversary, as annual leave is earned
  only once the first year is complete.
- HR can add other amounts (for example an air ticket) or deductions (for example
  equipment not returned), each with a reason. The total cannot go below zero.
- End of service already paid in an earlier payroll is taken off, so it is never paid twice.
- Staff loans and salary advances still owed are taken off automatically, as far as the
  settlement covers them; anything left over is shown so HR can recover it another way.

Who and how: someone with "Set salaries and prepare payroll" prepares it once the person is
serving notice or has left; someone else with "Approve and post payroll" approves it, never
for themselves. It is then paid in the payroll of the month of the last day, or the next
month whose payroll is not yet posted, as a WPS "Final Settlement".
"""

import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .cash_accounts import _text
from .leave import ANNUAL_DAYS, _half, _require_demo_user, _year_figures, leave_year
from .models import Employee, EndOfServiceSettlement, PayrollLine, PayrollRun
from .pay_records import can_manage_pay, can_view_pay, current_pay
from .payroll import EOS_DAYS_PER_YEAR, _round, _service_days, _text_money, can_approve_pay
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import _name
from .transaction_services import InvalidTransaction

S = EndOfServiceSettlement.Status
ZERO = Decimal("0.00")
MAX_ITEMS = 10
MAX_AMOUNT = Decimal("1000000")
SHOWN_DAYS = 365
POLICY_LABEL = ("TEST ONLY Tivoli rule: end of service = basic x years x 21 / 30, nothing before 1 full year, unpaid leave "
                "not counted; unused leave, including leave earned since the last anniversary, paid at basic / 30 a day. "
                "Provisional: extra amounts and deductions need a reason; paid in the payroll of the last day's month as a Final Settlement.")


def _first(day):
    return day.replace(day=1)


def _next(month):
    return (month.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)


def pay_month(employee, last_day):
    """The month of the last day, or the next month whose payroll is not posted yet."""
    month = _first(last_day)
    posted = set(PayrollRun.objects.filter(business_unit=employee.business_unit, status=PayrollRun.Status.POSTED,
                                           month__gte=month).values_list("month", flat=True))
    while month in posted:
        month = _next(month)
    return month


def _items(value, word):
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise InvalidTransaction(f"Send at most {MAX_ITEMS} {word}.")
    items = []
    for row in value:
        if not isinstance(row, dict):
            raise InvalidTransaction(f"Each of the {word} needs a reason and an amount.")
        label = _text(row.get("label"), "reason", 120)
        if not label:
            raise InvalidTransaction(f"Give a reason for each of the {word}.")
        try:
            amount = Decimal(str(row.get("amount", "")).replace(",", ""))
        except InvalidOperation:
            raise InvalidTransaction(f"Enter the amount for “{label}”.") from None
        if not amount.is_finite() or amount <= 0 or amount >= MAX_AMOUNT or amount != amount.quantize(Decimal("0.01")):
            raise InvalidTransaction(f"The amount for “{label}” must be more than zero, with at most two decimals.")
        items.append({"label": label, "amount": _text_money(amount)})
    return items


def _leave(employee, last_day):
    """(unused annual days on the last day, explanation). Negative when more was taken than earned."""
    year = leave_year(employee, last_day)
    if year is None:
        return Decimal("0"), "Leaves before the first work anniversary, so no annual leave was earned yet."
    figures = _year_figures(employee, year[0])
    since = (last_day - figures["start"]).days + 1
    earned = _half(Decimal(ANNUAL_DAYS) * since / 365)
    days = Decimal(ANNUAL_DAYS + figures["carried"]) + figures["adjusted"] - figures["taken"] + earned
    parts = [f"{ANNUAL_DAYS} days from {figures['start'].day} {figures['start']:%b %Y}"]
    if figures["carried"]:
        parts.append(f"{figures['carried']} carried over")
    if figures["adjusted"]:
        parts.append(f"{figures['adjusted']:+} changed by HR")
    parts.append(f"{format(earned.normalize(), 'f')} earned since then over {since} days")
    parts.append(f"{figures['taken']} taken")
    return days, ", ".join(parts)


def _already_paid(employee):
    lines = PayrollLine.objects.filter(employee=employee, run__status=PayrollRun.Status.POSTED, end_of_service__gt=0)
    return sum((line.end_of_service for line in lines if not (line.detail.get("end_of_service") or {}).get("settlement")), ZERO)


def work_out(employee, *, additions=(), deductions=()):
    """The settlement figures for this person, as they stand today."""
    if employee.status not in (Employee.Status.NOTICE, Employee.Status.LEFT) or employee.last_day is None:
        raise InvalidTransaction(f"{employee.full_name} is not leaving. Record the last day on the staff record first.")
    last_day = employee.last_day
    pay = current_pay(employee, last_day)
    if pay is None:
        raise InvalidTransaction(f"Enter the pay for {employee.full_name} on the Salaries page first.")
    service = _service_days(employee, last_day)
    unpaid = (last_day - employee.joined_on).days + 1 - service
    years = Decimal(service) / Decimal("365")
    gratuity = _round(pay.basic * years * EOS_DAYS_PER_YEAR / 30) if years >= 1 else ZERO
    already = min(_already_paid(employee), gratuity)
    days, leave_note = _leave(employee, last_day)
    day_rate = pay.basic / 30
    leave_pay = _round(day_rate * days)
    added = sum((Decimal(row["amount"]) for row in additions), ZERO)
    taken = sum((Decimal(row["amount"]) for row in deductions), ZERO)
    total = gratuity - already + leave_pay + added - taken
    from .staff_loans import settlement_rows

    loan_rows, notes = settlement_rows(employee, total)
    deductions = list(deductions) + loan_rows
    total -= sum((Decimal(row["amount"]) for row in loan_rows), ZERO)
    if years < 1:
        notes.append("Leaves before 1 full year of service, so no end of service pay.")
    if already:
        notes.append(f"{_text_money(already)} QAR end of service was already paid in an earlier payroll and is taken off.")
    if days < 0:
        notes.append(f"Took {abs(days)} more leave days than earned; they are deducted.")
    return {
        "last_day": last_day, "service_days": service, "unpaid_days": unpaid, "years": years, "basic": pay.basic,
        "gratuity": gratuity, "already_paid": already, "leave_days": days, "leave_pay": leave_pay, "additions": list(additions),
        "deductions": list(deductions), "total": total,
        "detail": {"years": format(years.quantize(Decimal("0.01")), "f"), "day_rate": _text_money(day_rate), "leave": leave_note,
                   "joined_on": employee.joined_on.isoformat(), "notes": notes},
    }


def _figures(figures):
    return {
        "last_day": figures["last_day"].isoformat(), "service_days": figures["service_days"], "unpaid_days": figures["unpaid_days"],
        "years": figures["detail"]["years"], "basic": _text_money(figures["basic"]), "gratuity": _text_money(figures["gratuity"]),
        "already_paid": _text_money(figures["already_paid"]), "leave_days": format(Decimal(figures["leave_days"]).normalize(), "f"),
        "leave_pay": _text_money(figures["leave_pay"]), "additions": figures["additions"], "deductions": figures["deductions"],
        "total": _text_money(figures["total"]), "day_rate": figures["detail"]["day_rate"], "leave": figures["detail"]["leave"],
        "joined_on": figures["detail"]["joined_on"], "notes": figures["detail"]["notes"],
    }


def _settlement_row(settlement, actor):
    unit = settlement.business_unit
    paid_in = PayrollLine.objects.filter(employee=settlement.employee, run__status=PayrollRun.Status.POSTED,
                                         detail__end_of_service__settlement=settlement.pk).select_related("run").first()
    return {
        "id": settlement.pk, "status": settlement.status, "status_label": settlement.get_status_display(),
        "pay_month": settlement.pay_month.strftime("%Y-%m"), "pay_month_label": settlement.pay_month.strftime("%B %Y"),
        "paid_in": paid_in.run.month.strftime("%B %Y") if paid_in else "",
        "figures": _figures({
            "last_day": settlement.last_day, "service_days": settlement.service_days, "unpaid_days": settlement.unpaid_days,
            "basic": settlement.basic, "gratuity": settlement.gratuity, "already_paid": settlement.already_paid,
            "leave_days": settlement.leave_days, "leave_pay": settlement.leave_pay, "additions": settlement.additions,
            "deductions": settlement.deductions, "total": settlement.total, "detail": settlement.detail,
        }),
        "note": settlement.note,
        "prepared_by": _name(settlement.prepared_by), "prepared_at": settlement.prepared_at.isoformat(),
        "approved_by": _name(settlement.approved_by), "approved_at": settlement.approved_at.isoformat() if settlement.approved_at else "",
        "can_approve": (settlement.status == S.PREPARED and can_approve_pay(actor, unit) and actor.pk != settlement.prepared_by_id
                        and settlement.employee.user_id != actor.pk),
        "can_change": settlement.status == S.PREPARED and can_manage_pay(actor, unit),
        "can_cancel": settlement.status != S.CANCELLED and not paid_in and can_manage_pay(actor, unit),
    }


def _require_viewer(actor, business_unit):
    _require_demo_user(actor)
    if business_unit is None or not can_view_pay(actor, business_unit):
        raise WorkflowPermissionDenied("End-of-service settlements are shown only to people with payroll access in this unit.")


def settlements_overview(*, actor, business_unit):
    _require_viewer(actor, business_unit)
    since = timezone.localdate() - datetime.timedelta(days=SHOWN_DAYS)
    leaving = list(Employee.objects.filter(business_unit=business_unit, status__in=[Employee.Status.NOTICE, Employee.Status.LEFT],
                                           last_day__gte=since).order_by("-last_day", "full_name"))
    live = {row.employee_id: row for row in EndOfServiceSettlement.objects.filter(employee__in=leaving).exclude(status=S.CANCELLED)
            .select_related("business_unit", "employee", "prepared_by", "approved_by")}
    people = []
    for person in leaving:
        row = {"id": person.pk, "name": person.full_name, "staff_number": person.staff_number, "job_title": person.job_title,
               "department": person.department, "status_label": person.get_status_display(), "last_day": person.last_day.isoformat(),
               "leaving_reason": person.leaving_reason, "settlement": None, "preview": None, "problem": ""}
        if person.pk in live:
            row["settlement"] = _settlement_row(live[person.pk], actor)
        else:
            try:
                row["preview"] = _figures(work_out(person))
                row["pay_month_label"] = pay_month(person, person.last_day).strftime("%B %Y")
            except InvalidTransaction as error:
                row["problem"] = str(error)
        people.append(row)
    return {
        "unit": {"id": business_unit.pk, "name": business_unit.name}, "people": people,
        "can_prepare": can_manage_pay(actor, business_unit), "policy": POLICY_LABEL,
    }


def _employee(business_unit, employee_id):
    employee = Employee.objects.select_for_update().filter(
        pk=employee_id if type(employee_id) is int else None, business_unit=business_unit).first()
    if employee is None:
        raise InvalidTransaction("Choose someone from this unit.")
    return employee


@transaction.atomic
def prepare_settlement(*, actor, business_unit, data):
    """Work out the settlement, or work it out again with new extras while it waits for approval."""
    _require_demo_user(actor)
    if business_unit is None or not can_manage_pay(actor, business_unit):
        raise WorkflowPermissionDenied("Only people who prepare payroll in this unit can prepare a settlement.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the person and any extra amounts.")
    employee = _employee(business_unit, data.get("employee_id"))
    figures = work_out(employee, additions=_items(data.get("additions"), "extra amounts"),
                       deductions=_items(data.get("deductions"), "deductions"))
    if figures["total"] < 0:
        raise InvalidTransaction(f"The deductions are more than what is owed by {_text_money(-figures['total'])} QAR. "
                                 "Recover the rest another way and lower the deductions.")
    settlement = EndOfServiceSettlement.objects.select_for_update().filter(employee=employee).exclude(status=S.CANCELLED).first()
    if settlement is not None and settlement.status != S.PREPARED:
        raise InvalidTransaction("This settlement is already approved. Cancel it first to change it.")
    if settlement is None:
        settlement = EndOfServiceSettlement(business_unit=business_unit, employee=employee)
    for field in ("last_day", "service_days", "unpaid_days", "basic", "gratuity", "already_paid", "leave_days", "leave_pay",
                  "additions", "deductions", "total", "detail"):
        setattr(settlement, field, figures[field])
    settlement.pay_month = pay_month(employee, employee.last_day)
    settlement.note = _text(data.get("note"), "note", 300)
    settlement.prepared_by, settlement.prepared_at = actor, timezone.now()
    settlement.save()
    return _settlement_row(settlement, actor)


def _settlement(business_unit, settlement_id):
    settlement = (EndOfServiceSettlement.objects.select_for_update()
                  .filter(pk=settlement_id if type(settlement_id) is int else None, business_unit=business_unit)
                  .select_related("business_unit", "employee").first())
    if settlement is None:
        raise InvalidTransaction("That settlement was not found.")
    return settlement


@transaction.atomic
def decide_settlement(*, actor, business_unit, settlement_id, action, reason=""):
    _require_demo_user(actor)
    settlement = _settlement(business_unit, settlement_id)
    if action == "approve":
        if settlement.status != S.PREPARED:
            raise InvalidTransaction("Only a settlement waiting for approval can be approved.")
        if not can_approve_pay(actor, business_unit):
            raise WorkflowPermissionDenied("You need 'Approve and post payroll' in this unit.")
        if actor.pk == settlement.prepared_by_id:
            raise WorkflowPermissionDenied("You prepared this settlement, so someone else must approve it.")
        if settlement.employee.user_id == actor.pk:
            raise WorkflowPermissionDenied("This settlement pays you, so someone else must approve it.")
        if settlement.employee.last_day != settlement.last_day:
            raise InvalidTransaction("The last day changed on the staff record. Work the settlement out again first.")
        settlement.pay_month = pay_month(settlement.employee, settlement.last_day)
        settlement.status, settlement.approved_by, settlement.approved_at = S.APPROVED, actor, timezone.now()
        settlement.save(update_fields=["pay_month", "status", "approved_by", "approved_at"])
    elif action == "cancel":
        if settlement.status == S.CANCELLED:
            raise InvalidTransaction("This settlement is already cancelled.")
        if not can_manage_pay(actor, business_unit):
            raise WorkflowPermissionDenied("Only people who prepare payroll in this unit can cancel a settlement.")
        if PayrollLine.objects.filter(run__status=PayrollRun.Status.POSTED, detail__end_of_service__settlement=settlement.pk).exists():
            raise InvalidTransaction("This settlement is already paid in a posted payroll.")
        reason = _text(reason, "reason", 200)
        if not reason:
            raise InvalidTransaction("Say why the settlement is cancelled.")
        settlement.status, settlement.cancelled_by, settlement.cancelled_at = S.CANCELLED, actor, timezone.now()
        settlement.cancel_reason = reason
        settlement.save(update_fields=["status", "cancelled_by", "cancelled_at", "cancel_reason"])
    else:
        raise InvalidTransaction("Choose approve or cancel.")
    row = _settlement_row(settlement, actor)
    prepared = PayrollRun.objects.filter(business_unit=business_unit, month=settlement.pay_month, status=PayrollRun.Status.PREPARED).exists()
    row["payroll_to_redo"] = prepared
    return row


def approved_for(business_unit, month):
    """Approved settlements to pay in this unit's payroll for this month, by employee."""
    return {row.employee_id: row for row in EndOfServiceSettlement.objects.filter(
        business_unit=business_unit, pay_month=month, status=S.APPROVED).select_related("employee")}
