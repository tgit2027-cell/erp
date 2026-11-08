"""Factory team: who works in which section, and each person's workload and work record.

Sam, 7 Nov 2026: the team on each JD is picked from the company's employees under the Factory, listed by section
(joinery, paint, metal...). The dashboard shows each person's workload, the hours they completed on each JD and
everything they have done or are assigned to, so the Factory Manager can see who has little work and spread it.

Provisional until confirmed: the factory's people are the employees in the "Factory" department (or anyone the
Factory Manager gives a section); the Factory Manager sets each person's section. A person is "Free" with nothing in
progress and nothing planned in the next 14 days, "Busy" with 3 or more stages in progress or any late stage, and
"Normal" otherwise.
"""

import datetime

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .access_levels import is_master_admin
from .factory import (
    ZERO,
    _f,
    _int,
    _require_user,
    _require_viewer,
    can_approve,
    factory_people,
    person_row,
)
from .models import (
    Employee,
    FactorySection,
    FactoryWorker,
    ProductionLabour,
    ProductionStage,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction

T = ProductionStage.Status
LOOK_AHEAD = 14
BUSY_STAGES = 3
HISTORY = 40


def can_keep_team(user, business_unit):
    return can_approve(user, business_unit) or is_master_admin(user)


def _load(active, planned, late):
    if late or active >= BUSY_STAGES:
        return "busy", "Busy"
    if not active and not planned:
        return "free", "Free"
    return "normal", "Normal"


def _assignment(stage, role, today):
    line, order = stage.line, stage.line.order
    late = stage.status not in (T.DONE, T.SKIPPED) and stage.planned_end is not None and stage.planned_end < today
    return {"job": order.number, "job_id": order.pk, "project": order.project_name, "code": line.code,
            "description": line.description, "stage": stage.get_kind_display(), "role": role,
            "status": stage.status, "status_label": stage.get_status_display(),
            "planned_start": stage.planned_start.isoformat() if stage.planned_start else "",
            "planned_end": stage.planned_end.isoformat() if stage.planned_end else "",
            "finished_on": stage.finished_on.isoformat() if stage.finished_on else "", "late": late}


def team_workload(*, actor, business_unit, person_id=None):
    """Everyone in the factory with their section, current work, hours, and (for one person) their full record."""
    _require_viewer(actor, business_unit)
    today = timezone.localdate()
    people = list(factory_people(business_unit))
    if person_id is not None:
        wanted = int(person_id) if isinstance(person_id, str) and person_id.isdigit() else _int(person_id)
        people = [person for person in people if person.pk == wanted]
        if not people:
            raise InvalidTransaction("That person is not on the factory's staff.")
    ids = [person.pk for person in people]
    stages = (ProductionStage.objects.filter(line__order__business_unit=business_unit)
              .exclude(line__order__status="cancelled").select_related("line__order", "lead")
              .prefetch_related("helpers"))
    open_work, done_work = {}, {}
    for stage in stages.filter(status__in=(T.NOT_STARTED, T.IN_PROGRESS, T.STUCK)):
        for person, role in [(stage.lead, "Lead"), *((helper, "Helper") for helper in stage.helpers.all())]:
            if person and person.pk in ids:
                open_work.setdefault(person.pk, []).append(_assignment(stage, role, today))
    for stage in stages.filter(status=T.DONE, finished_on__gte=today - datetime.timedelta(days=365)):
        for person, role in [(stage.lead, "Lead"), *((helper, "Helper") for helper in stage.helpers.all())]:
            if person and person.pk in ids:
                done_work.setdefault(person.pk, []).append(_assignment(stage, role, today))
    month_start, week_start = today.replace(day=1), today - datetime.timedelta(days=today.weekday())
    labour = ProductionLabour.objects.filter(employee_id__in=ids, order__business_unit=business_unit)
    totals = {}
    for key, since in (("week", week_start), ("month", month_start), ("year", today - datetime.timedelta(days=365))):
        for row in labour.filter(work_date__gte=since).values("employee_id").annotate(total=Sum("hours")):
            totals.setdefault(row["employee_id"], {})[key] = row["total"]
    rows = []
    soon = today + datetime.timedelta(days=LOOK_AHEAD)
    for person in people:
        work = open_work.get(person.pk, [])
        active = sum(1 for row in work if row["status"] in (T.IN_PROGRESS, T.STUCK))
        planned = sum(1 for row in work if row["status"] == T.NOT_STARTED and row["planned_start"]
                      and row["planned_start"] <= soon.isoformat())
        late = sum(1 for row in work if row["late"])
        load, load_label = _load(active, planned, late)
        hours = totals.get(person.pk, {})
        row = person_row(person) | {
            "active": active, "planned_soon": planned, "assigned": len(work), "late": late,
            "load": load, "load_label": load_label,
            "hours_week": _f(hours.get("week", ZERO)), "hours_month": _f(hours.get("month", ZERO)),
            "hours_year": _f(hours.get("year", ZERO)),
            "completed_year": len(done_work.get(person.pk, [])),
            "work": sorted(work, key=lambda row: (row["planned_start"] or "9999", row["job"], row["code"])),
        }
        if person_id is not None:
            by_line = {}
            for entry in labour.filter(employee=person).select_related("order", "line").order_by("-work_date", "-pk"):
                key = (entry.order.number, entry.line.code if entry.line_id else "")
                found = by_line.setdefault(key, {"job": entry.order.number, "code": key[1] or "Whole job",
                                                 "description": entry.line.description if entry.line_id else "",
                                                 "hours": ZERO, "overtime": ZERO, "last": entry.work_date.isoformat()})
                found["hours"] += entry.hours
                found["overtime"] += entry.hours if entry.overtime else ZERO
            row["hours_by_jd"] = [found | {"hours": _f(found["hours"]), "overtime": _f(found["overtime"])}
                                  for found in list(by_line.values())[:HISTORY]]
            row["done"] = sorted(done_work.get(person.pk, []), key=lambda row: row["finished_on"], reverse=True)[:HISTORY]
        rows.append(row)
    order = {"busy": 0, "normal": 1, "free": 2}
    rows.sort(key=lambda row: (row["section"] or "zz", order[row["load"]], row["name"]))
    return {
        "today": today.isoformat(), "people": rows,
        "sections": [{"id": value, "label": label} for value, label in FactorySection.choices],
        "summary": {"people": len(rows), "busy": sum(1 for row in rows if row["load"] == "busy"),
                    "free": sum(1 for row in rows if row["load"] == "free"),
                    "no_section": sum(1 for row in rows if not row["section"])},
        "can_keep_team": can_keep_team(actor, business_unit),
        "staff": ([{"id": person.pk, "name": person.full_name, "department": person.department}
                   for person in Employee.objects.filter(business_unit__legal_entity=business_unit.legal_entity,
                                                         status=Employee.Status.ACTIVE)
                   .exclude(pk__in=[person.pk for person in factory_people(business_unit)]).order_by("full_name")[:300]]
                  if can_keep_team(actor, business_unit) and person_id is None else []),
    }


@transaction.atomic
def save_worker(*, actor, business_unit, data):
    """The Factory Manager puts a person in a section (or takes the section off)."""
    _require_user(actor, business_unit)
    if not can_keep_team(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'Approve factory jobs' permission in this unit.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the person and their section.")
    person = Employee.objects.filter(pk=_int(data.get("employee_id")), status=Employee.Status.ACTIVE,
                                     business_unit__legal_entity=business_unit.legal_entity).first()
    if person is None:
        raise InvalidTransaction("Choose the person from the company's active staff.")
    section = data.get("section")
    if section in (None, ""):
        FactoryWorker.objects.filter(employee=person).delete()
    elif section not in FactorySection.values:
        raise InvalidTransaction("Choose the section they work in.")
    else:
        FactoryWorker.objects.update_or_create(employee=person, defaults={
            "section": section, "updated_by": actor, "updated_at": timezone.now()})
    return team_workload(actor=actor, business_unit=business_unit)
