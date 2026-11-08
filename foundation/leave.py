"""Leave: requests, approvals and balances.

Tivoli's annual leave rules (given by Sam, 1 Nov 2026), with the other rules
provisional from the minimums in Qatar's Labour Law:

- Annual leave: 30 calendar days, earned once the person completes 1 year of
  service. The days are credited in full on each work anniversary, and the leave
  year runs from one anniversary to the day before the next.
- The year's leave can be taken in at most 2 parts, of any length.
- At the anniversary, up to 15 unused days carry over to the new leave year and
  must be used in it; anything above 15 is lost. Carried days are used first.
- Nobody can be away for more than 30 days in a row, counting every kind of leave.
- HR can add or remove days in the current leave year, with a reason.
- Sick leave: the first 14 days in a year on full pay, the next 28 on half pay
  and the next 42 unpaid. Someone with less than 3 months' service is shown as
  not yet covered.
- Unpaid leave has no limit, but still needs approval.
- Sick and unpaid leave are counted in working days: the unit's closed weekdays and closed dates
  are skipped (Friday only, until the unit's work calendar is set up).
- A person asks for their own leave, or HR asks on their behalf. Their manager
  (as set on the staff record) or HR approves it, but never the person who asked
  or the person taking the leave. Annual leave cannot go past what is left.
- A request can be cancelled before it starts by the person, or at any time by HR.
"""

import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .cash_accounts import _text
from .general_ledger import _day
from .models import BusinessUnitWorkCalendar, DemoUserMarker, Employee, EmployeeEvent, LeaveAdjustment, LeaveRequest
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import _name, can_manage, can_view
from .transaction_services import InvalidTransaction

K, S = LeaveRequest.Kind, LeaveRequest.Status
ANNUAL_DAYS, PARTS_PER_YEAR, CARRY_OVER_DAYS, MAX_DAYS_IN_A_ROW = 30, 2, 15, 30
SICK_BANDS = [(14, "full pay"), (28, "half pay"), (42, "unpaid")]
SICK_QUALIFYING_MONTHS = 3
MAX_DAYS = 120
POLICY_LABEL = ("TEST ONLY Tivoli rules: 30 calendar days of annual leave a year once 1 year of service is complete, "
                "credited on each work anniversary; taken in at most 2 parts; up to 15 unused days carry over and must be "
                "used in the next leave year; never more than 30 days away in a row; people at the same level in the same "
                "department and unit are not away together, with a week between one coming back and the next leaving; "
                "sick leave 14 days full pay, 28 half pay, 42 unpaid; unpaid leave needs approval. The manager or HR "
                "approves, never the person who asked.")
LIVE = (S.REQUESTED, S.APPROVED)
GAP = datetime.timedelta(days=7)


def _closed(business_unit):
    calendar = BusinessUnitWorkCalendar.objects.filter(business_unit=business_unit, is_configured=True).first()
    weekdays, dates = {4}, set()
    if calendar and isinstance(calendar.closed_weekdays, list):
        weekdays = {day for day in calendar.closed_weekdays if type(day) is int and 0 <= day <= 6}
        for value in calendar.closed_dates if isinstance(calendar.closed_dates, list) else []:
            try:
                dates.add(datetime.date.fromisoformat(value))
            except (TypeError, ValueError):
                continue
    return weekdays, dates


def _working_days(business_unit, start, end):
    weekdays, dates = _closed(business_unit)
    days, day = [], start
    while day <= end:
        if day.weekday() not in weekdays and day not in dates:
            days.append(day)
        day += datetime.timedelta(days=1)
    return days


def _years_of_service(employee, on):
    years = on.year - employee.joined_on.year
    if (on.month, on.day) < (employee.joined_on.month, employee.joined_on.day):
        years -= 1
    return years


def _days(value):
    """Days as text without a needless .0, e.g. 4 or 4.5."""
    value = Decimal(value)
    return format(value.quantize(Decimal("1")) if value == value.to_integral_value() else value.normalize(), "f")


def _half(value):
    return (value * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2


def _anniversary(employee, years):
    joined = employee.joined_on
    try:
        return joined.replace(year=joined.year + years)
    except ValueError:  # Joined on 29 February.
        return joined.replace(year=joined.year + years, day=28)


def leave_year(employee, on):
    """(number, first day, last day) of the leave year that contains this day, or None in the first year."""
    years = _years_of_service(employee, on)
    if years < 1:
        return None
    return years, _anniversary(employee, years), _anniversary(employee, years + 1) - datetime.timedelta(days=1)


def _calendar_days(start, end, first, last):
    start, end = max(start, first), min(end, last)
    return (end - start).days + 1 if end >= start else 0


def _year_figures(employee, number):
    """Annual leave figures for leave year `number`, carrying unused days forward from the years before it."""
    requests = list(employee.leave_requests.filter(kind=K.ANNUAL, status__in=LIVE))
    adjustments = list(employee.leave_adjustments.all())
    carried = 0
    for current in range(1, number + 1):
        first, last = _anniversary(employee, current), _anniversary(employee, current + 1) - datetime.timedelta(days=1)
        adjusted = sum((row.days for row in adjustments if row.year == first.year), Decimal("0"))
        taken = sum(_calendar_days(row.start, row.end, first, last) for row in requests if row.status == S.APPROVED)
        waiting = sum(_calendar_days(row.start, row.end, first, last) for row in requests if row.status == S.REQUESTED)
        parts = sum(1 for row in requests if first <= row.start <= last)
        own_used = max(0, taken + waiting - carried)
        figures = {"number": current, "start": first, "end": last, "carried": carried, "adjusted": adjusted, "taken": taken,
                   "waiting": waiting, "parts": parts, "left": Decimal(ANNUAL_DAYS + carried) + adjusted - taken - waiting}
        carried = int(max(Decimal("0"), min(Decimal(CARRY_OVER_DAYS), Decimal(ANNUAL_DAYS) + adjusted - own_used)))
    return figures


def _days_in_year(request, year):
    if request.start.year == request.end.year == year:
        return request.days
    return len([day for day in _working_days(request.employee.business_unit, request.start, request.end) if day.year == year])


def _used(employee, kind, year, statuses):
    requests = employee.leave_requests.filter(kind=kind, status__in=statuses, start__lte=datetime.date(year, 12, 31),
                                              end__gte=datetime.date(year, 1, 1)).select_related("employee__business_unit")
    return sum((_days_in_year(request, year) for request in requests), 0)


def balance(employee, on=None):
    today = timezone.localdate()
    on = on or today
    year = today.year
    current = leave_year(employee, on)
    annual = _year_figures(employee, current[0]) if current else None
    sick = _used(employee, K.SICK, year, LIVE)
    bands, left = [], sick
    for size, label in SICK_BANDS:
        used = min(left, size)
        bands.append({"label": label, "days": size, "used": used})
        left -= used
    return {
        "year": year, "eligible_from": _anniversary(employee, 1).isoformat(),
        "leave_year": ({"start": annual["start"].isoformat(), "end": annual["end"].isoformat()} if annual else None),
        "entitlement": str(ANNUAL_DAYS if annual else 0), "carried": annual["carried"] if annual else 0,
        "adjusted": _days(annual["adjusted"]) if annual else "0", "taken": annual["taken"] if annual else 0,
        "waiting": annual["waiting"] if annual else 0, "left": _days(annual["left"]) if annual else "0",
        "parts_used": annual["parts"] if annual else 0, "parts_allowed": PARTS_PER_YEAR,
        "sick": {"used": sick, "bands": bands, "beyond": left,
                 "qualifies": _years_of_service(employee, today) > 0
                 or (today - employee.joined_on).days >= SICK_QUALIFYING_MONTHS * 30},
        "unpaid": _used(employee, K.UNPAID, year, LIVE),
    }


def _days_in_a_row(employee, start, end):
    """The longest stretch away this leave would make, joined with any leave right before or after it."""
    live = list(employee.leave_requests.filter(status__in=LIVE).values_list("start", "end"))
    changed = True
    while changed:
        changed = False
        for first, last in live:
            if first <= end + datetime.timedelta(days=1) and last >= start - datetime.timedelta(days=1) and (first < start or last > end):
                start, end, changed = min(start, first), max(end, last), True
    return (end - start).days + 1


def _team(employee):
    """Everyone who reports to this person, directly or through others."""
    found, frontier = set(), {employee.pk}
    while frontier:
        frontier = set(Employee.objects.filter(manager_id__in=frontier).exclude(pk__in=found).values_list("pk", flat=True))
        found |= frontier
    return found


def _me(actor):
    return Employee.objects.filter(user=actor).exclude(status=Employee.Status.LEFT).select_related("business_unit").first()


def _require_demo_user(actor):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Leave runs only in the synthetic demo environment.")
    if not getattr(actor, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=actor, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can use leave.")


def _can_see(actor, employee):
    if employee.user_id == actor.pk or can_view(actor, employee.business_unit):
        return True
    me = _me(actor)
    return me is not None and employee.pk in _team(me)


def _is_manager(actor, employee):
    return employee.manager_id is not None and employee.manager.user_id == actor.pk


def _waiting_for_manager(request):
    """A special approval goes to the line manager first, then HR. Without a linked manager, HR decides alone."""
    employee = request.employee
    return (bool(request.overlap_note) and request.manager_approved_by_id is None and employee.manager_id is not None
            and employee.manager.user_id is not None)


def _can_decide(actor, request):
    employee = request.employee
    if request.status != S.REQUESTED or actor.pk in (request.requested_by_id, employee.user_id):
        return False
    if request.overlap_note:
        if _waiting_for_manager(request):
            return _is_manager(actor, employee)
        return can_manage(actor, employee.business_unit) and actor.pk != request.manager_approved_by_id
    if can_manage(actor, employee.business_unit):
        return True
    return _is_manager(actor, employee)


def _row(request, actor):
    employee = request.employee
    today = timezone.localdate()
    return {
        "id": request.pk, "employee": {"id": employee.pk, "name": employee.full_name, "staff_number": employee.staff_number,
                                       "department": employee.department},
        "kind": request.kind, "kind_label": request.get_kind_display(), "start": request.start.isoformat(),
        "end": request.end.isoformat(), "days": request.days, "reason": request.reason, "status": request.status,
        "status_label": request.get_status_display(), "requested_by": _name(request.requested_by),
        "requested_at": request.requested_at.isoformat(), "decided_by": _name(request.decided_by),
        "decision_note": request.decision_note,
        "special_approval": None if not request.overlap_note else {
            "why": request.overlap_note, "manager": _name(request.manager_approved_by),
            "waiting_for": ("" if request.status != S.REQUESTED else
                            "line manager" if _waiting_for_manager(request) else "HR")},
        "can_decide": _can_decide(actor, request),
        "can_cancel": request.status in LIVE and (can_manage(actor, employee.business_unit)
                                                  or (employee.user_id == actor.pk and request.start > today)),
    }


def _requests():
    return LeaveRequest.objects.select_related("employee__business_unit", "employee__manager", "requested_by", "decided_by")


def _person(employee):
    return {"id": employee.pk, "name": employee.full_name, "staff_number": employee.staff_number, "job_title": employee.job_title,
            "department": employee.department, "joined_on": employee.joined_on.isoformat(), "balance": balance(employee)}


def leave_overview(*, actor, business_unit=None):
    _require_demo_user(actor)
    today = timezone.localdate()
    year = today.year
    me = _me(actor)
    hr_view = business_unit is not None and can_view(actor, business_unit)
    hr_manage = business_unit is not None and can_manage(actor, business_unit)
    if me is None and not hr_view:
        raise WorkflowPermissionDenied("Your ERP sign-in is not linked to a staff record, so there is no leave to show. Ask HR to link it.")
    visible = set()
    if hr_view:
        visible |= set(Employee.objects.filter(business_unit=business_unit).exclude(status=Employee.Status.LEFT).values_list("pk", flat=True))
    if me is not None:
        visible |= _team(me)
    people = list(Employee.objects.filter(pk__in=visible).exclude(status=Employee.Status.LEFT).order_by("full_name"))
    waiting = [request for request in _requests().filter(status=S.REQUESTED, employee_id__in=visible | ({me.pk} if me else set()))
               if _can_decide(actor, request)]
    soon = today + datetime.timedelta(days=30)
    away = _requests().filter(employee_id__in=visible, status__in=LIVE, start__lte=soon, end__gte=today).order_by("start")
    return {
        "unit": {"id": business_unit.pk, "name": business_unit.name} if business_unit else None,
        "year": year, "today": today.isoformat(),
        "me": ({**_person(me), "requests": [_row(request, actor) for request in _requests().filter(employee=me)[:50]]}
               if me else None),
        "to_decide": [_row(request, actor) for request in waiting],
        "away": [_row(request, actor) for request in away],
        "people": [_person(person) for person in people],
        "can_request_for": ([{"id": person.pk, "name": person.full_name} for person in people] if hr_manage else []),
        "can_adjust": hr_manage, "kinds": [{"value": value, "label": label} for value, label in K.choices],
        "policy": POLICY_LABEL,
    }


def _level(employee):
    return (employee.grade or employee.job_title).strip().lower()


def _check_colleagues(employee, start, end, special):
    """People at the same level in the same department and business unit cannot be away together.

    The next one can start only a week after the last one is back. Sick leave is not planned, so it is not held back.
    With special=True the request goes ahead but needs both the line manager and HR; the clash is returned as a note.
    """
    peers = [person.pk for person in Employee.objects.select_for_update().filter(
        business_unit=employee.business_unit, department=employee.department).exclude(pk=employee.pk).exclude(
        status=Employee.Status.LEFT) if _level(person) == _level(employee)]
    clash = (LeaveRequest.objects.filter(employee_id__in=peers, status__in=LIVE, start__lte=end + GAP,
                                         end__gte=start - GAP).exclude(kind=K.SICK).select_related("employee")
             .order_by("start").first())
    if clash is None:
        return ""
    note = (f"{clash.employee.full_name}, at the same level in {employee.department}, is away from {clash.start:%d %b} to "
            f"{clash.end:%d %b %Y} ({clash.get_status_display().lower()}).")
    if special:
        return note
    back = clash.end + datetime.timedelta(days=1)
    raise InvalidTransaction(
        f"{note} Leave at the same level must be a week apart: it can start from {back + GAP:%d %b %Y}, or end by "
        f"{clash.start - GAP - datetime.timedelta(days=1):%d %b %Y}. Otherwise ask for a special approval from the line "
        "manager and HR, with the reason.")


@transaction.atomic
def request_leave(*, actor, data):
    _require_demo_user(actor)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the leave details.")
    employee_id = data.get("employee_id")
    me = _me(actor)
    if employee_id in (None, "") or (me is not None and employee_id == me.pk):
        employee = me
        if employee is None:
            raise WorkflowPermissionDenied("Your ERP sign-in is not linked to a staff record. Ask HR to link it.")
    else:
        employee = Employee.objects.filter(pk=employee_id if type(employee_id) is int else None).select_related("business_unit").first()
        if employee is None or not can_manage(actor, employee.business_unit):
            raise WorkflowPermissionDenied("Only HR can ask for leave on someone else's behalf.")
    employee = Employee.objects.select_for_update(of=("self",)).select_related("business_unit", "manager").get(pk=employee.pk)
    if employee.status == Employee.Status.LEFT:
        raise InvalidTransaction(f"{employee.full_name} has left.")
    kind = data.get("kind")
    if kind not in K.values:
        raise InvalidTransaction("Choose annual, sick or unpaid leave.")
    start = _day(data.get("start"), None, "first day") if data.get("start") else None
    end = _day(data.get("end"), None, "last day") if data.get("end") else None
    if start is None or end is None:
        raise InvalidTransaction("Give the first and last day of the leave.")
    if end < start:
        raise InvalidTransaction("The last day cannot be before the first day.")
    if (end - start).days > 366:
        raise InvalidTransaction("Ask for at most a year at a time.")
    if start < employee.joined_on or (employee.last_day and end > employee.last_day):
        raise InvalidTransaction("The leave must fall between the joining date and the last day of work.")
    if kind != K.SICK and start < timezone.localdate() and not can_manage(actor, employee.business_unit):
        raise InvalidTransaction("Only sick leave can be asked for after it has started. HR can record past leave.")
    clash = employee.leave_requests.filter(status__in=LIVE, start__lte=end, end__gte=start).first()
    if clash is not None:
        raise InvalidTransaction(f"{employee.full_name} already has {clash.get_kind_display().lower()} from {clash.start:%d %b} to {clash.end:%d %b}.")
    overlap = ""
    if kind != K.SICK:
        overlap = _check_colleagues(employee, start, end, special=data.get("special_approval") is True)
        if overlap and not _text(data.get("reason"), "reason", 200):
            raise InvalidTransaction("Give the reason for the special approval.")
    in_a_row = _days_in_a_row(employee, start, end)
    if in_a_row > MAX_DAYS_IN_A_ROW:
        raise InvalidTransaction(f"That would make {in_a_row} days away in a row. Nobody can be away for more than "
                                 f"{MAX_DAYS_IN_A_ROW} days in a row.")
    if kind == K.ANNUAL:
        current = leave_year(employee, start)
        if current is None:
            raise InvalidTransaction(f"{employee.full_name} completes 1 year of service on {_anniversary(employee, 1):%d %b %Y}. "
                                     "Annual leave can be taken from then.")
        if end > current[2]:
            raise InvalidTransaction(f"Annual leave cannot run past the end of the leave year on {current[2]:%d %b %Y}. "
                                     "Ask for the days after it separately.")
        figures = _year_figures(employee, current[0])
        if figures["parts"] >= PARTS_PER_YEAR:
            raise InvalidTransaction(f"{employee.full_name} has already asked for annual leave {PARTS_PER_YEAR} times in the leave "
                                     f"year to {current[2]:%d %b %Y}. It can be taken in at most {PARTS_PER_YEAR} parts.")
        wanted = (end - start).days + 1
        if wanted > figures["left"]:
            raise InvalidTransaction(f"{employee.full_name} has {_days(figures['left'])} days of annual leave left in the "
                                     f"leave year to {current[2]:%d %b %Y}, fewer than the {wanted} asked for.")
        count = wanted
    else:
        days = _working_days(employee.business_unit, start, end)
        if not days:
            raise InvalidTransaction("Those dates are all days off. There is nothing to ask for.")
        if len(days) > MAX_DAYS:
            raise InvalidTransaction(f"Ask for at most {MAX_DAYS} working days at a time.")
        count = len(days)
    request = LeaveRequest.objects.create(employee=employee, kind=kind, start=start, end=end, days=count,
                                          reason=_text(data.get("reason"), "reason", 200), requested_by=actor,
                                          overlap_note=overlap)
    return _row(_requests().get(pk=request.pk), actor)


@transaction.atomic
def decide_leave(*, actor, request_id, action, note=""):
    _require_demo_user(actor)
    request = (_requests().select_for_update(of=("self",)).filter(pk=request_id if type(request_id) is int else None).first())
    if request is None or not _can_see(actor, request.employee):
        raise InvalidTransaction("That leave request was not found.")
    if action == "cancel":
        row = _row(request, actor)
        if not row["can_cancel"]:
            raise WorkflowPermissionDenied("Only HR, or the person before the leave starts, can cancel it.")
        request.status, request.cancelled_by, request.cancelled_at = S.CANCELLED, actor, timezone.now()
        request.save(update_fields=["status", "cancelled_by", "cancelled_at"])
        return _row(request, actor)
    if action not in ("approve", "reject"):
        raise InvalidTransaction("Approve, reject or cancel.")
    if request.status != S.REQUESTED:
        raise InvalidTransaction(f"This request is already {request.get_status_display().lower()}.")
    if actor.pk in (request.requested_by_id, request.employee.user_id):
        raise WorkflowPermissionDenied("Someone other than the person who asked, and the person taking the leave, must decide.")
    if not _can_decide(actor, request):
        raise WorkflowPermissionDenied("Only the person's manager or HR can decide on this leave.")
    if action == "approve" and _waiting_for_manager(request):
        request.manager_approved_by, request.manager_approved_at = actor, timezone.now()
        request.save(update_fields=["manager_approved_by", "manager_approved_at"])
        return _row(request, actor)
    if action == "reject":
        request.decision_note = _text(note, "reason", 200, required=True)
        request.status = S.REJECTED
    else:
        request.decision_note = _text(note, "note", 200)
        request.status = S.APPROVED
    request.decided_by, request.decided_at = actor, timezone.now()
    request.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    return _row(request, actor)


@transaction.atomic
def adjust_leave(*, actor, data):
    _require_demo_user(actor)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the adjustment.")
    employee_id = data.get("employee_id")
    employee = Employee.objects.filter(pk=employee_id if type(employee_id) is int else None).select_related("business_unit").first()
    if employee is None or not can_manage(actor, employee.business_unit):
        raise WorkflowPermissionDenied("Only HR can change leave balances.")
    current = leave_year(employee, timezone.localdate())
    if current is None:
        raise InvalidTransaction(f"{employee.full_name} has no annual leave until {_anniversary(employee, 1):%d %b %Y}.")
    year = current[1].year
    try:
        days = Decimal(str(data.get("days")))
    except Exception:  # noqa: BLE001 - any unreadable value gets the same message
        raise InvalidTransaction("Give the days as a number, for example 5 or -2.5.") from None
    if not days.is_finite() or days == 0 or days != _half(days) or abs(days) > 60:
        raise InvalidTransaction("Give whole or half days, from -60 to 60, not zero.")
    reason = _text(data.get("reason"), "reason", 200, required=True)
    LeaveAdjustment.objects.create(employee=employee, year=year, days=days, reason=reason, added_by=actor)
    EmployeeEvent.objects.create(employee=employee, actor=actor, change="leave balance",
                                 what=f"{days:+} days of annual leave for the leave year from {current[1]:%d %b %Y}: {reason}"[:400])
    return _person(employee)
