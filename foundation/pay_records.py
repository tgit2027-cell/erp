"""Pay records: each person's basic pay and allowances, their bank account for WPS, and each unit's working hours.

Tivoli's rules (from Sam, 1 Nov 2026): pay is basic plus housing, transport, mobile and
other allowances, paid on the 1st of each month through WPS.

- Only people with "See salaries and payroll" see amounts; "Set salaries and prepare payroll"
  changes them. Nobody changes their own pay.
- A change applies from the 1st of a month and is kept as a new row, so the history stays.
  It can start this month or later; only someone's first pay can start earlier, from the
  month they joined.
- The bank account must be a valid Qatar IBAN (checked with the standard IBAN check digits).
- HR (Add and change staff records) sets the unit's working hours (one set per work group or
  shift, with grace minutes) and gives each person theirs. Attendance uses them to count minutes
  late or left early. Tivoli's standard hours (from Sam, 1 Nov 2026) can be added in one step.
"""

import datetime
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .access import active_grants
from .cash_accounts import _text
from .leave import _require_demo_user
from .models import Employee, EmployeePay, UserActionGrant, WorkSchedule
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import _name, can_manage
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action
PARTS = ("basic", "housing", "transport", "mobile", "other")
MAX_AMOUNT = Decimal("1000000")
IBAN = re.compile(r"^QA[0-9]{2}[A-Z]{4}[A-Z0-9]{21}$")
TIVOLI_HOURS = (
    ("Back office, Admin and Interiors", "08:00", "17:30"),
    ("Workshop and Warehouse", "07:00", "16:00"),
    ("Home Delivery", "08:00", "17:00"),
    ("Fabric Workshop", "08:00", "17:00"),
    ("Showroom morning shift", "09:00", "17:30"),
    ("Showroom mid shift", "10:30", "19:00"),
    ("Showroom evening shift", "12:30", "21:00"),
)


def _granted(user, business_unit, action):
    return active_grants(user, action).filter(business_unit=business_unit).exists()


def can_view_pay(user, business_unit):
    return any(_granted(user, business_unit, action) for action in (A.VIEW_PAY, A.MANAGE_PAY, A.APPROVE_PAY))


def can_manage_pay(user, business_unit):
    return _granted(user, business_unit, A.MANAGE_PAY)


def _money(value):
    return format(value.quantize(Decimal("0.01")), "f")


def current_pay(employee, on=None):
    """The pay row in force on this day, or None."""
    on = (on or timezone.localdate()).replace(day=1)
    return employee.pay.filter(effective_from__lte=on).order_by("-effective_from", "-pk").first()


def total(pay):
    return sum((getattr(pay, part) for part in PARTS), Decimal("0"))


def _mask(iban):
    return f"{iban[:4]} •••• {iban[-4:]}" if iban else ""


def _pay_row(pay):
    if pay is None:
        return None
    return {"id": pay.pk, "effective_from": pay.effective_from.strftime("%Y-%m"),
            **{part: _money(getattr(pay, part)) for part in PARTS}, "other_label": pay.other_label,
            "total": _money(total(pay)), "bank_name": pay.bank_name, "iban": _mask(pay.iban), "reason": pay.reason,
            "set_by": _name(pay.set_by), "set_at": pay.set_at.isoformat()}


def _hours_row(hours, people=None):
    if hours is None:
        return None
    return {"id": hours.pk, "name": hours.name, "starts_at": hours.starts_at.strftime("%H:%M"), "ends_at": hours.ends_at.strftime("%H:%M"),
            "grace_minutes": hours.grace_minutes, "people": people, "set_by": _name(hours.set_by), "set_at": hours.set_at.isoformat()}


def pay_overview(*, actor, business_unit):
    _require_demo_user(actor)
    if business_unit is None or not can_view_pay(actor, business_unit):
        raise WorkflowPermissionDenied("Salaries are shown only to people with payroll access in this unit.")
    today = timezone.localdate()
    people = list(Employee.objects.filter(business_unit=business_unit).exclude(status=Employee.Status.LEFT)
                  .select_related("work_schedule").order_by("department", "full_name"))
    rows, sums = [], {part: Decimal("0") for part in PARTS}
    for person in people:
        pay = current_pay(person, today)
        upcoming = person.pay.filter(effective_from__gt=today.replace(day=1)).order_by("effective_from", "pk").last()
        if pay is not None:
            for part in PARTS:
                sums[part] += getattr(pay, part)
        rows.append({"id": person.pk, "name": person.full_name, "staff_number": person.staff_number, "job_title": person.job_title,
                     "department": person.department, "joined_on": person.joined_on.isoformat(), "is_me": person.user_id == actor.pk,
                     "hours_id": person.work_schedule_id,
                     "pay": _pay_row(pay), "upcoming": _pay_row(upcoming),
                     "history": [_pay_row(row) for row in person.pay.select_related("set_by")[:24]]})
    return {
        "unit": {"id": business_unit.pk, "name": business_unit.name}, "month": today.strftime("%Y-%m"),
        "people": rows, "missing": sum(1 for row in rows if row["pay"] is None),
        "totals": {**{part: _money(value) for part, value in sums.items()}, "total": _money(sum(sums.values(), Decimal("0")))},
        "working_hours": [_hours_row(row, sum(1 for person in people if person.work_schedule_id == row.pk))
                          for row in WorkSchedule.objects.filter(business_unit=business_unit).select_related("set_by")],
        "no_hours": sum(1 for person in people if person.work_schedule_id is None),
        "can_manage": can_manage_pay(actor, business_unit), "can_set_hours": can_manage(actor, business_unit),
        "policy": ("TEST ONLY Tivoli pay: basic plus housing, transport, mobile and other allowances, paid on the 1st through WPS. "
                   "A change starts on the 1st of a month and the old pay stays in the history."),
    }


def _amount(value, label, required=False):
    if value in (None, ""):
        if required:
            raise InvalidTransaction(f"Give the {label}.")
        return Decimal("0")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise InvalidTransaction(f"Give the {label} as an amount in QAR, for example 4500.") from None
    if not amount.is_finite() or amount < 0 or amount > MAX_AMOUNT or amount != amount.quantize(Decimal("0.01")):
        raise InvalidTransaction(f"Give the {label} as an amount from 0 to {MAX_AMOUNT:,} QAR with at most 2 decimals.")
    if required and amount == 0:
        raise InvalidTransaction(f"The {label} must be more than zero.")
    return amount


def _iban(value):
    text = re.sub(r"\s+", "", value).upper() if isinstance(value, str) else ""
    if not text:
        return ""
    rearranged = text[4:] + text[:4]
    digits = "".join(str(int(char, 36)) for char in rearranged) if rearranged.isalnum() else ""
    if not IBAN.match(text) or not digits or int(digits) % 97 != 1:
        raise InvalidTransaction("Give a valid Qatar IBAN: QA, 2 check digits, the 4-letter bank code and 21 more characters.")
    return text


@transaction.atomic
def set_pay(*, actor, business_unit, data):
    _require_demo_user(actor)
    if business_unit is None or not can_manage_pay(actor, business_unit):
        raise WorkflowPermissionDenied("Only people who set salaries in this unit can change pay.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the pay details.")
    employee_id = data.get("employee_id")
    employee = (Employee.objects.select_for_update().filter(pk=employee_id if type(employee_id) is int else None,
                                                             business_unit=business_unit).first())
    if employee is None:
        raise InvalidTransaction("That person was not found in this unit.")
    if employee.user_id == actor.pk:
        raise WorkflowPermissionDenied("Nobody changes their own pay. Ask someone else with payroll access.")
    if employee.status == Employee.Status.LEFT:
        raise InvalidTransaction(f"{employee.full_name} has left.")
    try:
        year, month = (int(part) for part in str(data.get("effective_from") or "").split("-"))
        start = datetime.date(year, month, 1)
    except (TypeError, ValueError):
        raise InvalidTransaction("Give the month the pay starts, as YYYY-MM.") from None
    this_month = timezone.localdate().replace(day=1)
    first = not employee.pay.exists()
    if start < employee.joined_on.replace(day=1):
        raise InvalidTransaction(f"{employee.full_name} joined on {employee.joined_on:%d %b %Y}. Pay cannot start before that month.")
    if start < this_month and not first:
        raise InvalidTransaction("A pay change can start this month or later. Past months stay as they were paid.")
    if start > this_month.replace(year=this_month.year + 1):
        raise InvalidTransaction("A pay change can start at most a year ahead.")
    amounts = {part: _amount(data.get(part), {"basic": "basic pay", "other": "other allowance"}.get(part, f"{part} allowance"),
                             required=part == "basic") for part in PARTS}
    other_label = _text(data.get("other_label"), "name of the other allowance", 60, required=amounts["other"] > 0)
    previous = current_pay(employee, start)
    bank_name = _text(data.get("bank_name"), "bank name", 80)
    iban = _iban(data.get("iban")) if data.get("iban") not in (None, "") else (previous.iban if previous else "")
    if iban and not bank_name:
        bank_name = previous.bank_name if previous else ""
    if iban and not bank_name:
        raise InvalidTransaction("Give the bank name with the IBAN.")
    pay = EmployeePay.objects.create(employee=employee, effective_from=start, other_label=other_label if amounts["other"] else "",
                                     bank_name=bank_name, iban=iban, reason=_text(data.get("reason"), "reason", 200, required=True),
                                     set_by=actor, **amounts)
    return _pay_row(pay)


def _time(value):
    try:
        return datetime.time.fromisoformat(value) if isinstance(value, str) and len(value) == 5 else None
    except ValueError:
        return None


def _save_hours(actor, business_unit, data):
    schedule = None
    if data.get("id") is not None:
        schedule = WorkSchedule.objects.select_for_update().filter(pk=data["id"] if type(data["id"]) is int else None,
                                                                   business_unit=business_unit).first()
        if schedule is None:
            raise InvalidTransaction("Those working hours were not found in this unit.")
    name = _text(data.get("name"), "name of these hours", 60, required=True)
    starts, ends = _time(data.get("starts_at")), _time(data.get("ends_at"))
    if starts is None or ends is None:
        raise InvalidTransaction("Give the start and finish times as HH:MM, for example 08:00 and 17:00.")
    if ends <= starts:
        raise InvalidTransaction("The working day must finish after it starts.")
    grace = data.get("grace_minutes", 0)
    if type(grace) is not int or not 0 <= grace <= 60:
        raise InvalidTransaction("Give the grace period as whole minutes from 0 to 60.")
    if WorkSchedule.objects.filter(business_unit=business_unit, name__iexact=name).exclude(pk=getattr(schedule, "pk", None)).exists():
        raise InvalidTransaction(f"{business_unit.name} already has working hours called {name}.")
    schedule = schedule or WorkSchedule(business_unit=business_unit)
    schedule.name, schedule.starts_at, schedule.ends_at, schedule.grace_minutes = name, starts, ends, grace
    schedule.set_by, schedule.set_at = actor, timezone.now()
    schedule.save()
    return _hours_row(schedule)


def _assign_hours(business_unit, data):
    employee_id, hours_id = data.get("employee_id"), data.get("hours_id")
    employee = Employee.objects.select_for_update().filter(pk=employee_id if type(employee_id) is int else None,
                                                             business_unit=business_unit).first()
    if employee is None:
        raise InvalidTransaction("That person was not found in this unit.")
    schedule = None
    if hours_id is not None:
        schedule = WorkSchedule.objects.filter(pk=hours_id if type(hours_id) is int else None, business_unit=business_unit).first()
        if schedule is None:
            raise InvalidTransaction("Those working hours were not found in this unit.")
    employee.work_schedule = schedule
    employee.save(update_fields=["work_schedule"])
    return {"employee_id": employee.pk, "hours": _hours_row(schedule)}


@transaction.atomic
def set_working_hours(*, actor, business_unit, data):
    """Add or change a set of working hours, add Tivoli's standard hours, or give a person their hours."""
    _require_demo_user(actor)
    if business_unit is None or not can_manage(actor, business_unit):
        raise WorkflowPermissionDenied("Only HR can set working hours.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the working hours.")
    action = data.get("action", "save")
    if action == "assign":
        return _assign_hours(business_unit, data)
    from .access_levels import require_master_admin
    require_master_admin(actor, "add or change the sets of working hours. HR gives people their hours")
    if action == "standard":
        have = {name.lower() for name in WorkSchedule.objects.filter(business_unit=business_unit).values_list("name", flat=True)}
        added = [_save_hours(actor, business_unit, {"name": name, "starts_at": starts, "ends_at": ends})
                 for name, starts, ends in TIVOLI_HOURS if name.lower() not in have]
        return {"added": added}
    if action != "save":
        raise InvalidTransaction("Choose save, standard or assign.")
    return _save_hours(actor, business_unit, data)
