"""Overtime: agreed before the work, approved by a second person, and tied to a sales order.

Tivoli's rules (from Sam, 1 Nov 2026):

- The business unit's manager records overtime before it is worked, with the
  sales order (and so the client), the hours and the people.
- Someone else approves it.
- Overtime is paid, and its cost belongs to the sales order. The cost is worked
  out in payroll from each person's pay, so nothing is posted here:
  working day = basic salary / 30 / 8 x hours x 1.25;
  weekends and public holidays = basic salary / 30 / 8 x hours x 1.50.
  Weekends and holidays are the unit's closed weekdays and dates (Friday until
  its work calendar is set up).

Provisional until confirmed (from Qatar's Labour Law):

- At most 2 hours of overtime on a working day, and 10 on a rest day.
- The person's manager (as set on the staff record), or HR, records it. HR approves it,
  never the person who recorded it or anyone working the overtime.
"""

import calendar
import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .cash_accounts import _text
from .general_ledger import _day
from .leave import _closed, _me, _require_demo_user, _team
from .models import Employee, LeaveRequest, OvertimeLine, OvertimeRequest, SalesQuote
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import _name, can_manage, can_view
from .transaction_services import InvalidTransaction

S = OvertimeRequest.Status
ORDER_STATUSES = (SalesQuote.Status.CONFIRMED, SalesQuote.Status.READY, SalesQuote.Status.DELIVERED)
NORMAL_RATE, REST_DAY_RATE = 125, 150
MAX_HOURS = {False: Decimal("2"), True: Decimal("10")}
MAX_PEOPLE = 50
POLICY_LABEL = ("TEST ONLY Tivoli rules: the unit's manager records overtime before the work, with the sales order, hours and "
                "people; someone else approves it; its cost goes to the sales order and is worked out in payroll as basic salary "
                "÷ 30 ÷ 8 × hours × 1.25 on a working day, or × 1.50 on weekends and public holidays. Provisional from the "
                "Qatar Labour Law: at most 2 hours on a working day and 10 on a rest day.")


def _rest_day(business_unit, day):
    weekdays, dates = _closed(business_unit)
    return day.weekday() in weekdays or day in dates


def _team_ids(actor):
    me = _me(actor)
    return (_team(me), me) if me else (set(), None)


def _order_label(order):
    return {"id": order.pk, "label": f"Quote #{order.pk}", "customer": order.customer.name, "status": order.get_status_display()}


def _orders(business_unit):
    return (SalesQuote.objects.filter(business_unit=business_unit, status__in=ORDER_STATUSES)
            .select_related("customer").order_by("-created_at")[:200])


def _row(request, actor, today):
    lines = list(request.lines.all())
    workers = {line.employee.user_id for line in lines}
    hr = can_manage(actor, request.business_unit)
    return {
        "id": request.pk, "number": f"OT-{request.pk:05d}", "unit": request.business_unit.name,
        "order": _order_label(request.sales_order), "work_date": request.work_date.isoformat(), "rest_day": request.rest_day,
        "rate_percent": request.rate_percent, "work": request.work, "status": request.status,
        "status_label": request.get_status_display(),
        "people": [{"id": line.employee_id, "name": line.employee.full_name, "department": line.employee.department,
                    "hours": format(line.hours.normalize(), "f")} for line in lines],
        "total_hours": format(sum((line.hours for line in lines), Decimal("0")).normalize(), "f"),
        "prepared_by": _name(request.prepared_by), "prepared_at": request.prepared_at.isoformat(),
        "decided_by": _name(request.decided_by), "decision_note": request.decision_note,
        "can_decide": (request.status == S.SUBMITTED and hr and actor.pk != request.prepared_by_id and actor.pk not in workers),
        "can_cancel": (request.status in (S.SUBMITTED, S.APPROVED) and request.work_date > today
                       and (hr or actor.pk == request.prepared_by_id)),
    }


def _requests():
    return (OvertimeRequest.objects.select_related("business_unit", "sales_order__customer", "prepared_by", "decided_by")
            .prefetch_related("lines__employee"))


def _month(value, today):
    if value in (None, ""):
        return today.replace(day=1)
    try:
        year, month = (int(part) for part in str(value).split("-"))
        return datetime.date(year, month, 1)
    except (TypeError, ValueError):
        raise InvalidTransaction("Give the month as YYYY-MM.") from None


def overtime_overview(*, actor, business_unit=None, month=None):
    _require_demo_user(actor)
    today = timezone.localdate()
    start = _month(month, today)
    end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
    team, me = _team_ids(actor)
    hr_view = business_unit is not None and can_view(actor, business_unit)
    hr = business_unit is not None and can_manage(actor, business_unit)
    if not hr_view and me is None:
        raise WorkflowPermissionDenied("Your ERP sign-in is not linked to a staff record, so there is no overtime to show. "
                                       "Ask HR to link it.")
    seen = Q(prepared_by=actor) | Q(lines__employee_id__in=team | ({me.pk} if me else set()))
    if hr_view:
        seen |= Q(business_unit=business_unit)
    rows = [_row(request, actor, today) for request in
            _requests().filter(seen, work_date__gte=start, work_date__lte=end).distinct()]
    waiting = [_row(request, actor, today) for request in _requests().filter(seen, status=S.SUBMITTED).distinct()
               if request.work_date < start or request.work_date > end]
    by_order = {}
    for row in rows:
        if row["status"] != S.APPROVED:
            continue
        entry = by_order.setdefault(row["order"]["id"], {**row["order"], "normal_hours": Decimal("0"), "rest_hours": Decimal("0"), "people": set()})
        entry["rest_hours" if row["rest_day"] else "normal_hours"] += Decimal(row["total_hours"])
        entry["people"] |= {person["id"] for person in row["people"]}
    allowed = Employee.objects.exclude(status=Employee.Status.LEFT)
    allowed = allowed.filter(business_unit=business_unit) if hr else allowed.filter(pk__in=team)
    if business_unit is not None and not hr:
        allowed = allowed.filter(business_unit=business_unit) if allowed.filter(business_unit=business_unit).exists() else allowed
    allowed = list(allowed.select_related("business_unit").order_by("department", "full_name"))
    people = [{"id": person.pk, "name": person.full_name, "department": person.department, "job_title": person.job_title}
              for person in allowed]
    order_unit = business_unit if hr or not allowed else allowed[0].business_unit
    return {
        "unit": {"id": business_unit.pk, "name": business_unit.name} if business_unit else None,
        "month": start.strftime("%Y-%m"), "month_label": start.strftime("%B %Y"), "today": today.isoformat(),
        "rows": waiting + rows,
        "by_order": [{**entry, "normal_hours": format(entry["normal_hours"].normalize(), "f"),
                      "rest_hours": format(entry["rest_hours"].normalize(), "f"), "people": len(entry["people"])}
                     for entry in by_order.values()],
        "orders": [_order_label(order) for order in _orders(order_unit)] if order_unit and people else [],
        "people": people, "can_prepare": bool(people), "is_hr": hr,
        "max_hours": {"working_day": str(MAX_HOURS[False]), "rest_day": str(MAX_HOURS[True])},
        "rates": {"working_day": NORMAL_RATE, "rest_day": REST_DAY_RATE},
        "policy": POLICY_LABEL,
    }


def _hours(value, name):
    try:
        hours = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise InvalidTransaction(f"{name}: give the hours as a number, for example 2 or 1.5.") from None
    if not hours.is_finite() or hours <= 0 or hours * 2 != (hours * 2).to_integral_value():
        raise InvalidTransaction(f"{name}: give whole or half hours, more than zero.")
    return hours


@transaction.atomic
def prepare_overtime(*, actor, business_unit, data):
    _require_demo_user(actor)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the overtime details.")
    # The overtime belongs to the unit the people work in, whichever unit is selected on screen:
    # a manager may have no access to their team's unit.
    first = data.get("lines")[0] if isinstance(data.get("lines"), list) and data.get("lines") else None
    employee_id = first.get("employee_id") if isinstance(first, dict) else None
    found = Employee.objects.filter(pk=employee_id if type(employee_id) is int else None).select_related("business_unit").first()
    if found is None:
        raise InvalidTransaction("Choose at least one person.")
    business_unit = found.business_unit
    today = timezone.localdate()
    day = _day(data.get("work_date"), None, "overtime") if data.get("work_date") else None
    if day is None:
        raise InvalidTransaction("Choose the day of the overtime.")
    if day < today:
        raise InvalidTransaction("Overtime must be recorded before it is worked. This day has already passed.")
    if day > today + datetime.timedelta(days=90):
        raise InvalidTransaction("Record overtime at most 90 days ahead.")
    order_id = data.get("sales_order_id")
    order = (SalesQuote.objects.filter(pk=order_id if type(order_id) is int else None, business_unit=business_unit)
             .select_related("customer").first())
    if order is None:
        raise InvalidTransaction("Choose a sales order of this business unit.")
    if order.status not in ORDER_STATUSES:
        raise InvalidTransaction(f"Quote #{order.pk} is {order.get_status_display().lower()}. Overtime can only be charged to a "
                                 "confirmed sales order.")
    work = _text(data.get("work"), "work to be done", 200, required=True)
    lines = data.get("lines")
    if not isinstance(lines, list) or not lines:
        raise InvalidTransaction("Choose at least one person.")
    if len(lines) > MAX_PEOPLE:
        raise InvalidTransaction(f"Choose at most {MAX_PEOPLE} people on one request.")
    ids = [line.get("employee_id") if isinstance(line, dict) else None for line in lines]
    if any(type(value) is not int for value in ids) or len(set(ids)) != len(ids):
        raise InvalidTransaction("Each person can appear once.")
    rest_day = _rest_day(business_unit, day)
    limit = MAX_HOURS[rest_day]
    people = {person.pk: person for person in Employee.objects.select_for_update(of=("self",)).filter(pk__in=ids)}
    team, _ = _team_ids(actor)
    hr = can_manage(actor, business_unit)
    on_leave = set(LeaveRequest.objects.filter(employee_id__in=ids, status=LeaveRequest.Status.APPROVED, start__lte=day,
                                               end__gte=day).values_list("employee_id", flat=True))
    booked = set(OvertimeLine.objects.filter(employee_id__in=ids, request__work_date=day,
                                             request__status__in=(S.SUBMITTED, S.APPROVED)).values_list("employee_id", flat=True))
    checked = []
    for line in lines:
        person = people.get(line["employee_id"])
        if person is None or person.business_unit_id != business_unit.pk:
            raise InvalidTransaction("Everyone on the request must work in this business unit.")
        if person.user_id == actor.pk:
            raise WorkflowPermissionDenied("Nobody records overtime for themselves.")
        if not hr and person.pk not in team:
            raise WorkflowPermissionDenied(f"{person.full_name} is not in your team. Ask their manager or HR.")
        if person.status == Employee.Status.LEFT or person.joined_on > day or (person.last_day and person.last_day < day):
            raise InvalidTransaction(f"{person.full_name} is not working here on {day:%d %b %Y}.")
        if person.pk in on_leave:
            raise InvalidTransaction(f"{person.full_name} is on approved leave on {day:%d %b}.")
        if person.pk in booked:
            raise InvalidTransaction(f"{person.full_name} already has overtime on {day:%d %b}.")
        hours = _hours(line.get("hours"), person.full_name)
        if hours > limit:
            raise InvalidTransaction(f"{person.full_name}: at most {limit.normalize()} hours of overtime on a "
                                     f"{'rest day' if rest_day else 'working day'}.")
        checked.append((person, hours))
    request = OvertimeRequest.objects.create(
        business_unit=business_unit, sales_order=order, work_date=day, rest_day=rest_day,
        rate_percent=REST_DAY_RATE if rest_day else NORMAL_RATE, work=work, prepared_by=actor)
    OvertimeLine.objects.bulk_create([OvertimeLine(request=request, employee=person, hours=hours) for person, hours in checked])
    return _row(_requests().get(pk=request.pk), actor, today)


@transaction.atomic
def decide_overtime(*, actor, request_id, action, note=""):
    _require_demo_user(actor)
    today = timezone.localdate()
    request = _requests().select_for_update(of=("self",)).filter(pk=request_id if type(request_id) is int else None).first()
    team, me = _team_ids(actor)
    if request is None or not (can_view(actor, request.business_unit) or request.prepared_by_id == actor.pk
                               or request.lines.filter(employee_id__in=team | ({me.pk} if me else set())).exists()):
        raise InvalidTransaction("That overtime request was not found.")
    row = _row(request, actor, today)
    if action == "cancel":
        if not row["can_cancel"]:
            raise WorkflowPermissionDenied("Only the person who recorded it, or HR, can cancel overtime, and only before the day.")
        request.decision_note = _text(note, "reason", 200)
    elif action in ("approve", "reject"):
        if request.status != S.SUBMITTED:
            raise InvalidTransaction(f"This overtime is already {request.get_status_display().lower()}.")
        if actor.pk == request.prepared_by_id:
            raise WorkflowPermissionDenied("Someone other than the person who recorded the overtime must approve it.")
        if not row["can_decide"]:
            raise WorkflowPermissionDenied("Only HR can approve overtime, and never someone working it.")
        request.decision_note = _text(note, "reason", 200, required=action == "reject")
    else:
        raise InvalidTransaction("Approve, reject or cancel.")
    request.status = {"approve": S.APPROVED, "reject": S.REJECTED, "cancel": S.CANCELLED}[action]
    request.decided_by, request.decided_at = actor, timezone.now()
    request.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    return _row(_requests().get(pk=request.pk), actor, today)
