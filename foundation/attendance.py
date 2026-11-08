"""Attendance: who was in, late or absent on each working day, and HR's monthly confirmation.

Provisional rules (synthetic demo only) until HR gives Tivoli's own policy:

- Attendance is kept for working days only: the unit's closed weekdays and dates
  are skipped (Friday only, until the unit's work calendar is set up).
- A day of approved leave shows as leave and cannot also be marked present or absent.
- The person's manager (as set on the staff record) records their team's attendance,
  up to 7 days back. HR records for anyone in the unit, for any day not yet confirmed.
  Nobody records their own attendance.
- Present, late or absent. Late needs the time the person arrived. Absent has no times.
- Changing a day that was already recorded is kept on the person's staff record history.
- After the month ends, HR confirms it. Every working day must be recorded or on leave
  first. A confirmed month cannot change; HR can reopen it with a reason.
- Nothing here changes pay. Payroll will read confirmed months once Tivoli's pay rules are set.
"""

import calendar
import datetime

from django.db import transaction
from django.utils import timezone

from .cash_accounts import _text
from .general_ledger import _day
from .leave import _me, _require_demo_user, _team, _working_days
from .models import AttendanceMonth, AttendanceRecord, Employee, EmployeeEvent, LeaveRequest
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import _name, can_manage, can_view
from .transaction_services import InvalidTransaction

A = AttendanceRecord.Status
MANAGER_DAYS_BACK = 7
MAX_ENTRIES = 300
POLICY_LABEL = ("TEST ONLY provisional rules: managers record their team's attendance up to 7 days back and HR any day not "
                "yet confirmed; nobody records their own; approved leave shows on its own; HR confirms each month after it "
                "ends. Nothing here changes pay yet.")
CODES = {A.PRESENT: "P", A.LATE: "L", A.ABSENT: "A", LeaveRequest.Kind.ANNUAL: "AL", LeaveRequest.Kind.SICK: "SL",
         LeaveRequest.Kind.UNPAID: "UL"}


def _month(value, today):
    if value in (None, ""):
        return today.replace(day=1)
    try:
        year, month = (int(part) for part in str(value).split("-"))
        return datetime.date(year, month, 1)
    except (TypeError, ValueError):
        raise InvalidTransaction("Give the month as YYYY-MM.") from None


def _last(month):
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def _employed(employee, day):
    return employee.joined_on <= day and (employee.last_day is None or day <= employee.last_day)


def _locked(business_unit_id, day):
    return AttendanceMonth.objects.filter(business_unit_id=business_unit_id, month=day.replace(day=1),
                                          status=AttendanceMonth.Status.CONFIRMED).exists()


def _can_record(actor, employee, day, today):
    """Why this actor cannot record this person's day, or an empty string when they can."""
    if employee.user_id == actor.pk:
        return "Nobody records their own attendance."
    if can_manage(actor, employee.business_unit):
        return ""
    me = _me(actor)
    if me is None or employee.pk not in _team(me):
        return "Only the person's manager or HR can record their attendance."
    if (today - day).days > MANAGER_DAYS_BACK:
        return f"Managers can record up to {MANAGER_DAYS_BACK} days back. Ask HR to record older days."
    return ""


def _people(actor, business_unit, start, end):
    """Who this actor may see, employed at some point in the period."""
    me = _me(actor)
    visible = set()
    if business_unit is not None and can_view(actor, business_unit):
        visible |= set(Employee.objects.filter(business_unit=business_unit).values_list("pk", flat=True))
    if me is not None:
        visible |= _team(me) | {me.pk}
    if not visible:
        raise WorkflowPermissionDenied("Your ERP sign-in is not linked to a staff record, so there is no attendance to show. "
                                       "Ask HR to link it.")
    rows = (Employee.objects.filter(pk__in=visible, joined_on__lte=end).exclude(last_day__lt=start)
            .select_related("business_unit").order_by("department", "full_name"))
    return me, list(rows)


def _leave_days(people, start, end):
    found = {}
    requests = (LeaveRequest.objects.filter(employee__in=people, status=LeaveRequest.Status.APPROVED, start__lte=end, end__gte=start)
                .select_related("employee__business_unit"))
    for request in requests:
        for day in _working_days(request.employee.business_unit, max(request.start, start), min(request.end, end)):
            found[(request.employee_id, day)] = request.kind
    return found


def _minutes(time):
    return time.hour * 60 + time.minute


def timing(record, hours):
    """(minutes late, minutes left early) against the person's working hours; nothing when they have none."""
    if hours is None or record.status == A.ABSENT:
        return 0, 0
    late = _minutes(record.check_in) - _minutes(hours.starts_at) if record.check_in else 0
    late = late if late > hours.grace_minutes else 0
    early = _minutes(hours.ends_at) - _minutes(record.check_out) if record.check_out else 0
    return max(late, 0), max(early, 0)


def hours_label(schedule):
    if schedule is None:
        return ""
    return f"{schedule.name}, {schedule.starts_at:%H:%M} to {schedule.ends_at:%H:%M}"


def _hours_by_person(people):
    return {person.pk: person.work_schedule for person in Employee.objects.filter(pk__in=[p.pk for p in people]).select_related("work_schedule")}


def _record_row(record, hours=None):
    late, early = timing(record, hours)
    return {"status": record.status, "status_label": record.get_status_display(),
            "check_in": record.check_in.strftime("%H:%M") if record.check_in else "",
            "check_out": record.check_out.strftime("%H:%M") if record.check_out else "", "note": record.note,
            "excused": record.excused, "minutes_late": late, "minutes_early": early,
            "recorded_by": _name(record.recorded_by)}


def _grid(people, start, end, today):
    """Each person's day-by-day attendance with totals."""
    records = {(record.employee_id, record.day): record for record in
               AttendanceRecord.objects.filter(employee__in=people, day__gte=start, day__lte=end).select_related("recorded_by")}
    leave = _leave_days(people, start, end)
    hours = _hours_by_person(people)
    rows = []
    for person in people:
        working = set(_working_days(person.business_unit, start, end))
        cells, totals = {}, {"present": 0, "late": 0, "absent": 0, "leave": 0, "missing": 0, "late_or_early": 0}
        day = start
        while day <= end:
            key, cell = (person.pk, day), None
            if not _employed(person, day):
                cell = {"code": "", "kind": "not_employed"}
            elif day not in working:
                cell = {"code": "", "kind": "closed"}
            elif key in leave:
                cell = {"code": CODES[leave[key]], "kind": "leave"}
                totals["leave"] += 1
            elif key in records:
                record = records[key]
                cell = {"code": CODES[record.status], "kind": record.status, **_record_row(record, hours.get(person.pk))}
                totals[record.status] += 1
                if (cell["minutes_late"] or cell["minutes_early"]) and not record.excused:
                    totals["late_or_early"] += 1
            elif day > today:
                cell = {"code": "", "kind": "future"}
            else:
                cell = {"code": "?", "kind": "missing"}
                totals["missing"] += 1
            cells[day.isoformat()] = cell
            day += datetime.timedelta(days=1)
        rows.append({"id": person.pk, "name": person.full_name, "staff_number": person.staff_number,
                     "department": person.department, "job_title": person.job_title, "unit_id": person.business_unit_id,
                     "hours": hours_label(hours.get(person.pk)), "cells": cells, "totals": totals})
    return rows


def _month_state(business_unit, month):
    row = (AttendanceMonth.objects.filter(business_unit=business_unit, month=month).select_related("confirmed_by", "reopened_by")
           .first() if business_unit else None)
    if row is None:
        return None
    return {"status": row.status, "confirmed_by": _name(row.confirmed_by), "confirmed_at": row.confirmed_at.isoformat(),
            "reopened_by": _name(row.reopened_by), "reopen_reason": row.reopen_reason}


def attendance_month(*, actor, business_unit=None, month=None):
    _require_demo_user(actor)
    today = timezone.localdate()
    start = _month(month, today)
    end = _last(start)
    me, people = _people(actor, business_unit, start, end)
    rows = _grid(people, start, end, today)
    recordable = {person.pk for person in people if not _can_record(actor, person, min(today, end), today)}
    for row in rows:
        row["can_record"] = row["id"] in recordable
    days, day = [], start
    while day <= end:
        days.append({"date": day.isoformat(), "weekday": day.strftime("%a"), "day": day.day})
        day += datetime.timedelta(days=1)
    state = _month_state(business_unit, start)
    hr = business_unit is not None and can_manage(actor, business_unit)
    return {
        "unit": {"id": business_unit.pk, "name": business_unit.name} if business_unit else None,
        "month": start.strftime("%Y-%m"), "month_label": start.strftime("%B %Y"), "today": today.isoformat(),
        "days": days, "people": rows, "me_id": me.pk if me else None,
        "confirmation": state,
        "locked": bool(state and state["status"] == AttendanceMonth.Status.CONFIRMED),
        "can_confirm": hr and end < today and not (state and state["status"] == AttendanceMonth.Status.CONFIRMED),
        "can_reopen": hr and bool(state and state["status"] == AttendanceMonth.Status.CONFIRMED),
        "is_hr": hr, "manager_days_back": MANAGER_DAYS_BACK,
        "statuses": [{"value": value, "label": label} for value, label in A.choices],
        "policy": POLICY_LABEL,
    }


def _time(value, field):
    if value in (None, ""):
        return None
    try:
        if isinstance(value, str) and len(value) == 5:
            return datetime.time.fromisoformat(value)
    except ValueError:
        pass
    raise InvalidTransaction(f"Give the {field} time as HH:MM, for example 07:45.")


@transaction.atomic
def record_attendance(*, actor, data):
    """Record or correct one day for one or more people."""
    _require_demo_user(actor)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the attendance.")
    today = timezone.localdate()
    day = _day(data.get("day"), None, "attendance") if data.get("day") else None
    if day is None:
        raise InvalidTransaction("Choose the day.")
    if day > today:
        raise InvalidTransaction("Attendance cannot be recorded for a day that has not happened yet.")
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        raise InvalidTransaction("Choose at least one person.")
    if len(entries) > MAX_ENTRIES:
        raise InvalidTransaction(f"Record at most {MAX_ENTRIES} people at a time.")
    ids = [entry.get("employee_id") if isinstance(entry, dict) else None for entry in entries]
    if any(type(value) is not int for value in ids) or len(set(ids)) != len(ids):
        raise InvalidTransaction("Each person can appear once.")
    people = {person.pk: person for person in
              Employee.objects.select_for_update(of=("self",)).filter(pk__in=ids).select_related("business_unit")}
    leave = _leave_days(list(people.values()), day, day)
    existing = {record.employee_id: record for record in AttendanceRecord.objects.select_for_update().filter(employee_id__in=ids, day=day)}
    saved = changed = 0
    for entry in entries:
        person = people.get(entry["employee_id"])
        if person is None:
            raise InvalidTransaction("That person was not found.")
        why = _can_record(actor, person, day, today)
        if why:
            raise WorkflowPermissionDenied(f"{person.full_name}: {why}")
        if not _employed(person, day):
            raise InvalidTransaction(f"{person.full_name} did not work here on {day:%d %b %Y}.")
        if day not in _working_days(person.business_unit, day, day):
            raise InvalidTransaction(f"{day:%A %d %b} is a day off for {person.business_unit.name}.")
        if (person.pk, day) in leave:
            raise InvalidTransaction(f"{person.full_name} is on approved leave on {day:%d %b}. Cancel the leave first to record attendance.")
        if _locked(person.business_unit_id, day):
            raise InvalidTransaction(f"{day:%B %Y} is confirmed for {person.business_unit.name}. HR must reopen it first.")
        status = entry.get("status")
        if status not in A.values:
            raise InvalidTransaction(f"{person.full_name}: choose present, late or absent.")
        check_in, check_out = _time(entry.get("check_in"), "arrival"), _time(entry.get("check_out"), "leaving")
        if status == A.ABSENT and (check_in or check_out):
            raise InvalidTransaction(f"{person.full_name} is marked absent, so leave the times empty.")
        if status == A.LATE and check_in is None:
            raise InvalidTransaction(f"{person.full_name} is marked late: give the time they arrived.")
        if check_in and check_out and check_out <= check_in:
            raise InvalidTransaction(f"{person.full_name}: the leaving time must be after the arrival time.")
        note = _text(entry.get("note"), "note", 200)
        excused = entry.get("excused", False)
        if type(excused) is not bool:
            raise InvalidTransaction(f"{person.full_name}: say whether the late arrival or early leaving was approved.")
        if excused and status == A.ABSENT:
            raise InvalidTransaction(f"{person.full_name} is marked absent. Record approved time off as leave instead.")
        old = existing.get(person.pk)
        values = {"status": status, "check_in": check_in, "check_out": check_out, "note": note, "excused": excused}
        if old is not None:
            before = _record_row(old)
            if all(getattr(old, field) == value for field, value in values.items()):
                continue
            for field, value in values.items():
                setattr(old, field, value)
            old.recorded_by, old.recorded_at = actor, timezone.now()
            old.save()
            after = _record_row(old)
            EmployeeEvent.objects.create(employee=person, actor=actor, change="attendance", what=(
                f"{day:%d %b %Y} changed from {before['status_label'].lower()}"
                f"{' ' + before['check_in'] if before['check_in'] else ''} to {after['status_label'].lower()}"
                f"{' ' + after['check_in'] if after['check_in'] else ''}")[:400])
            changed += 1
        else:
            AttendanceRecord.objects.create(employee=person, day=day, recorded_by=actor, **values)
        saved += 1
    return {"day": day.isoformat(), "saved": saved, "changed": changed}


@transaction.atomic
def confirm_month(*, actor, business_unit, data):
    """HR confirms a finished month, or reopens a confirmed one with a reason."""
    _require_demo_user(actor)
    if business_unit is None or not can_manage(actor, business_unit):
        raise WorkflowPermissionDenied("Only HR can confirm or reopen a month of attendance.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the month.")
    today = timezone.localdate()
    start = _month(data.get("month"), today)
    end = _last(start)
    row = AttendanceMonth.objects.select_for_update().filter(business_unit=business_unit, month=start).first()
    action = data.get("action")
    if action == "reopen":
        if row is None or row.status != AttendanceMonth.Status.CONFIRMED:
            raise InvalidTransaction(f"{start:%B %Y} is not confirmed.")
        row.reopen_reason = _text(data.get("reason"), "reason", 200, required=True)
        row.status, row.reopened_by, row.reopened_at = AttendanceMonth.Status.REOPENED, actor, timezone.now()
        row.save(update_fields=["status", "reopened_by", "reopened_at", "reopen_reason"])
        return {"month": start.strftime("%Y-%m"), "status": row.status}
    if action != "confirm":
        raise InvalidTransaction("Confirm or reopen.")
    if end >= today:
        raise InvalidTransaction(f"{start:%B %Y} has not ended yet. Confirm it from the 1st of next month.")
    if row is not None and row.status == AttendanceMonth.Status.CONFIRMED:
        raise InvalidTransaction(f"{start:%B %Y} is already confirmed.")
    people = list(Employee.objects.filter(business_unit=business_unit, joined_on__lte=end).exclude(last_day__lt=start)
                  .select_related("business_unit").order_by("full_name"))
    grid = _grid(people, start, end, today)
    gaps = [(person["name"], person["totals"]["missing"]) for person in grid if person["totals"]["missing"]]
    if gaps:
        total = sum(count for _, count in gaps)
        names = ", ".join(f"{name} ({count})" for name, count in gaps[:3]) + (" and others" if len(gaps) > 3 else "")
        raise InvalidTransaction(f"{total} working day{'s are' if total != 1 else ' is'} not recorded yet: {names}. "
                                 "Record them, or the leave, before confirming.")
    summary = {str(person["id"]): person["totals"] for person in grid}
    if row is None:
        row = AttendanceMonth.objects.create(business_unit=business_unit, month=start, confirmed_by=actor, summary=summary)
    else:
        # The last reopening stays on the row so the history is not lost.
        row.status, row.confirmed_by, row.confirmed_at, row.summary = AttendanceMonth.Status.CONFIRMED, actor, timezone.now(), summary
        row.save(update_fields=["status", "confirmed_by", "confirmed_at", "summary"])
    return {"month": start.strftime("%Y-%m"), "status": row.status, "people": len(grid)}
