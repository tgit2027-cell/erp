"""Factory planning (Phase 2): the production schedule of every BOQ item (JD), a Gantt view and a dashboard.

Sam, 5 Nov 2026: show each JD through all its phases (drawings, materials, production in joinery,
paint and the other sections, ready for delivery) with the team that works on it and the section;
a list view like ClickUp's, a Gantt view, and a dashboard of items in production, completed, late
work, bottlenecks, cost and payment issues. Tags (door, cabinet, cladding, glass...) are kept by
the Factory Manager and put on each JD, to count work over the years.

Provisional until confirmed (asked 5 Nov): the stages are drawings and cutting list -> materials
ready -> saw/CNC -> joinery -> paint -> metal -> ready for delivery, and any stage can be switched
off for a JD; a section's team leader starts a stage, marks it done or stuck (with a reason); each
stage has a lead and helpers from the factory staff; a payment issue is a job whose sales order
still has money owed, flagged red once the work is ready for delivery. Auto-planning counts working
days and skips Fridays.
"""

import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .access_levels import is_master_admin
from .factory import (
    HUNDRED,
    OPEN,
    ZERO,
    S,
    _actual,
    _day,
    _f,
    _int,
    _limit,
    _require_user,
    _require_viewer,
    _round,
    _text,
    can_approve,
    can_run,
    factory_people,
    material_shortages,
    person_row,
)
from .factory_machines import machine_alerts
from .models import (
    FactoryTag,
    ProductionBoqLine,
    ProductionOrder,
    ProductionStage,
    QuotePayment,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction

K = ProductionStage.Kind
T = ProductionStage.Status
STAGES = [kind for kind, _ in K.choices]
SECTION = {
    K.DRAWINGS: "Drawing office", K.MATERIALS: "Raw materials store", K.SAW: "CNC (wood and metal)",
    K.JOINERY: "Joinery", K.PAINT: "Paint", K.METAL: "Metal", K.READY: "Dispatch",
}
# The factory section whose people do each stage (their names come first in the stage's team list).
STAGE_SECTION = {K.DRAWINGS: "drawings", K.MATERIALS: "store", K.SAW: "cnc", K.JOINERY: "joinery", K.PAINT: "paint",
                 K.METAL: "metal", K.READY: "dispatch"}
DAYS = {K.DRAWINGS: 3, K.MATERIALS: 3, K.SAW: 1, K.JOINERY: 6, K.PAINT: 6, K.METAL: 3, K.READY: 1}
WORKSHOP_STAGES = (K.SAW, K.JOINERY, K.PAINT, K.METAL)
SHOWN_DAYS = 180
TAG_YEARS = 3


def _either(user, business_unit):
    return can_run(user, business_unit) or can_approve(user, business_unit)


# Keeping the stages in step with the BOQ.

def snapshot(order):
    """Planning already done on a job's BOQ items, kept by JD code when the draft's BOQ is saved again."""
    kept = {}
    for stage in ProductionStage.objects.filter(line__order=order).select_related("line").prefetch_related("helpers"):
        kept.setdefault(stage.line.code, {})[stage.kind] = {
            "status": stage.status, "planned_start": stage.planned_start, "planned_end": stage.planned_end,
            "started_on": stage.started_on, "finished_on": stage.finished_on, "lead_id": stage.lead_id,
            "stuck_reason": stage.stuck_reason, "helpers": [person.pk for person in stage.helpers.all()],
        }
    return kept


def ensure_stages(order, kept=None):
    """Give every BOQ item its stages; metal is off unless the job plans metal hours."""
    kept = kept or {}
    for line in order.boq_lines.all():
        have = set(line.stages.values_list("kind", flat=True))
        for position, kind in enumerate(STAGES):
            if kind in have:
                continue
            earlier = kept.get(line.code, {}).get(kind)
            if earlier:
                helpers = earlier.pop("helpers")
                stage = ProductionStage.objects.create(line=line, kind=kind, position=position, **earlier)
                stage.helpers.set(helpers)
                continue
            needed = kind != K.METAL or order.planned_hours_metal > 0
            ProductionStage.objects.create(line=line, kind=kind, position=position,
                                           status=T.NOT_STARTED if needed else T.SKIPPED)


# Reading.

def _working_days(start, days):
    """The day a stage of `days` working days ends, skipping Fridays."""
    day, left = start, max(days, 1) - 1
    while day.weekday() == 4:
        day += datetime.timedelta(days=1)
    while left:
        day += datetime.timedelta(days=1)
        if day.weekday() != 4:
            left -= 1
    return day


def _late(stage, today):
    return stage.status not in (T.DONE, T.SKIPPED) and stage.planned_end is not None and stage.planned_end < today


def _person(employee):
    return {"id": employee.pk, "name": employee.full_name, "job_title": employee.job_title} if employee else None


def _stage_row(stage, today, editable):
    return {
        "id": stage.pk, "kind": stage.kind, "label": stage.get_kind_display(), "section": SECTION[stage.kind],
        "section_id": STAGE_SECTION[stage.kind],
        "status": stage.status, "status_label": stage.get_status_display(),
        "planned_start": stage.planned_start.isoformat() if stage.planned_start else "",
        "planned_end": stage.planned_end.isoformat() if stage.planned_end else "",
        "started_on": stage.started_on.isoformat() if stage.started_on else "",
        "finished_on": stage.finished_on.isoformat() if stage.finished_on else "",
        "lead": _person(stage.lead), "helpers": [_person(person) for person in stage.helpers.all()],
        "stuck_reason": stage.stuck_reason, "late": _late(stage, today),
        "days_late": (today - stage.planned_end).days if _late(stage, today) else 0,
        "by_hand": stage.planned_by_hand, "can_update": editable,
    }


def _rollup(stages):
    """One status for the JD: done when every needed stage is, stuck when any is, otherwise how far it got."""
    needed = [stage for stage in stages if stage.status != T.SKIPPED]
    if needed and all(stage.status == T.DONE for stage in needed):
        return T.DONE
    if any(stage.status == T.STUCK for stage in needed):
        return T.STUCK
    if any(stage.status in (T.IN_PROGRESS, T.DONE) for stage in needed):
        return T.IN_PROGRESS
    return T.NOT_STARTED


def _current(stages):
    return next((stage for stage in stages if stage.status not in (T.DONE, T.SKIPPED)), None)


def _line_row(line, today, editable):
    stages = list(line.stages.all())
    needed = [stage for stage in stages if stage.status != T.SKIPPED]
    current = _current(stages)
    starts = [stage.planned_start for stage in needed if stage.planned_start]
    ends = [stage.planned_end for stage in needed if stage.planned_end]
    finished = [stage.finished_on for stage in needed if stage.finished_on]
    status = _rollup(stages)
    team = {}
    for stage in needed:
        for person in [stage.lead, *stage.helpers.all()]:
            if person:
                team[person.pk] = {"id": person.pk, "name": person.full_name}
    return {
        "id": line.pk, "code": line.code, "description": line.description, "quantity": _f(line.quantity), "unit": line.unit,
        "area": line.area, "floor": line.floor, "zone": line.zone,
        "tags": [{"id": tag.pk, "name": tag.name, "color": tag.color} for tag in line.tags.all()],
        "status": status, "status_label": T(status).label,
        "current": ({"kind": current.kind, "label": current.get_kind_display(), "section": SECTION[current.kind]}
                    if current else None),
        "start": min(starts).isoformat() if starts else "", "end": max(ends).isoformat() if ends else "",
        "completed_on": max(finished).isoformat() if status == T.DONE and finished else "",
        "late": any(_late(stage, today) for stage in needed) or (status != T.DONE and line.order.due_date < today),
        "team": list(team.values()),
        "stages": [_stage_row(stage, today, editable) for stage in stages],
    }


def _paid(quote):
    if quote is None:
        return None
    paid = quote.payments.exclude(kind=QuotePayment.Kind.STORAGE).aggregate(total=Sum("amount"))["total"] or ZERO
    total = quote.total_quote_value
    return {"total": _f(total), "paid": _f(paid), "balance": _f(max(total - paid, ZERO)),
            "percent": _f(_round(paid * HUNDRED / total)) if total else "0.00"}


def _jobs(business_unit):
    since = timezone.now() - datetime.timedelta(days=SHOWN_DAYS)
    return [order for order in ProductionOrder.objects.filter(business_unit=business_unit).exclude(status=S.CANCELLED)
            .select_related("quote__customer", "variant__item").prefetch_related(
                "boq_lines__tags", "boq_lines__stages__lead", "boq_lines__stages__helpers", "quote__payments")
            .order_by("due_date", "pk")
            if order.status in OPEN or order.status in (S.COST_CHECK, S.STORED, S.ON_THE_WAY)
            or (order.finished_at or order.created_at) >= since]


def _job_row(order, actor, today):
    editable = _either(actor, order.business_unit)
    lines = [_line_row(line, today, editable) for line in order.boq_lines.all()]
    payment = _paid(order.quote)
    ready = order.status in (S.STORED, S.ON_THE_WAY, S.DELIVERED) or (lines and all(
        any(stage["kind"] == K.READY and stage["status"] == T.DONE for stage in line["stages"]) for line in lines))
    return {
        "id": order.pk, "number": order.number, "status": order.status, "status_label": order.get_status_display(),
        "purpose_label": order.get_purpose_display(), "project_name": order.project_name,
        "customer": order.quote.customer.name if order.quote_id else "",
        "product": f"{_f(order.quantity)} x {order.variant.sku}" if order.variant_id else "",
        "due_date": order.due_date.isoformat(), "late": order.status in OPEN and order.due_date < today,
        "payment": payment,
        "payment_issue": bool(payment and Decimal(payment["balance"]) > 0 and ready),
        "lines": lines,
    }


def planning_overview(*, actor, business_unit):
    _require_viewer(actor, business_unit)
    today = timezone.localdate()
    jobs = [_job_row(order, actor, today) for order in _jobs(business_unit)]
    people = [person_row(person) for person in factory_people(business_unit)]
    return {
        "today": today.isoformat(), "jobs": jobs, "people": people,
        "tags": _tag_rows(business_unit), "stages": [{"id": kind, "label": label, "section": SECTION[kind],
                                                      "days": DAYS[kind]} for kind, label in K.choices],
        "can_update": _either(actor, business_unit),
        "can_keep_tags": can_approve(actor, business_unit) or is_master_admin(actor),
    }


def _tag_rows(business_unit):
    return [{"id": tag.pk, "name": tag.name, "color": tag.color, "is_active": tag.is_active}
            for tag in FactoryTag.objects.filter(legal_entity=business_unit.legal_entity)]


# Changing the plan and reporting progress.

def _load_stage(business_unit, stage_id):
    stage = (ProductionStage.objects.select_for_update(of=("self",))
             .filter(pk=_int(stage_id), line__order__business_unit=business_unit)
             .select_related("line__order").first())
    if stage is None:
        raise InvalidTransaction("That stage was not found.")
    if stage.line.order.status == S.CANCELLED:
        raise InvalidTransaction(f"{stage.line.order.number} is cancelled.")
    return stage


def _employee(business_unit, value, field):
    person = factory_people(business_unit).filter(pk=_int(value)).first()
    if person is None:
        raise InvalidTransaction(f"Choose the {field} from the factory's staff.")
    return person


def _date_or_none(value, field):
    return None if value in (None, "") else _day(value, field)


@transaction.atomic
def update_stage(*, actor, business_unit, stage_id, data):
    """Plan a stage (dates, lead, helpers) or report progress on it: start, done, stuck, reopen, skip, needed."""
    _require_user(actor, business_unit)
    if not _either(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'Run factory jobs' or 'Approve factory jobs' permission in this unit.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send what to change on the stage.")
    stage = _load_stage(business_unit, stage_id)
    order, today = stage.line.order, timezone.localdate()
    label = f"{stage.line.code} {stage.get_kind_display().lower()}"
    if "planned_start" in data or "planned_end" in data:
        start = _date_or_none(data.get("planned_start", stage.planned_start and stage.planned_start.isoformat()),
                              "planned start")
        end = _date_or_none(data.get("planned_end", stage.planned_end and stage.planned_end.isoformat()), "planned end")
        if start and end and end < start:
            raise InvalidTransaction(f"{label}: the end cannot be before the start.")
        stage.planned_start, stage.planned_end = start, end
        stage.planned_by_hand = True
    if data.get("planned_by_hand") is False:
        stage.planned_by_hand = False
    if "lead_id" in data:
        stage.lead = None if data["lead_id"] in (None, "") else _employee(business_unit, data["lead_id"], "lead")
    if "helper_ids" in data:
        ids = data["helper_ids"]
        if not isinstance(ids, list) or len(ids) > 20:
            raise InvalidTransaction("Choose up to 20 helpers.")
        helpers = [_employee(business_unit, value, "helper") for value in ids]
    else:
        helpers = None
    action = data.get("action")
    if action:
        on = _date_or_none(data.get("date"), "date") or today
        if on > today:
            raise InvalidTransaction("Progress cannot be dated in the future.")
        if action in ("start", "done") and stage.kind in WORKSHOP_STAGES and order.status not in (S.IN_PROGRESS,):
            raise InvalidTransaction(f"{order.number} must be approved and in production before the workshops start.")
        if action == "start":
            if stage.status not in (T.NOT_STARTED, T.STUCK):
                raise InvalidTransaction(f"{label} is {stage.get_status_display().lower()}.")
            stage.status, stage.started_on, stage.stuck_reason = T.IN_PROGRESS, stage.started_on or on, ""
        elif action == "done":
            if stage.status not in (T.NOT_STARTED, T.IN_PROGRESS, T.STUCK):
                raise InvalidTransaction(f"{label} is {stage.get_status_display().lower()}.")
            stage.started_on = stage.started_on or on
            if on < stage.started_on:
                raise InvalidTransaction(f"{label} cannot finish before it started ({stage.started_on}).")
            stage.status, stage.finished_on, stage.stuck_reason = T.DONE, on, ""
        elif action == "stuck":
            if stage.status in (T.DONE, T.SKIPPED):
                raise InvalidTransaction(f"{label} is {stage.get_status_display().lower()}.")
            stage.stuck_reason = _text(data.get("reason"), "reason it is stuck", 200, required=True)
            stage.status = T.STUCK
        elif action == "reopen":
            if stage.status != T.DONE:
                raise InvalidTransaction(f"{label} is not done.")
            stage.status, stage.finished_on = T.IN_PROGRESS, None
        elif action == "skip":
            if stage.status != T.NOT_STARTED:
                raise InvalidTransaction(f"{label} has started, so it cannot be switched off.")
            stage.status = T.SKIPPED
        elif action == "needed":
            if stage.status != T.SKIPPED:
                raise InvalidTransaction(f"{label} is already part of the plan.")
            stage.status = T.NOT_STARTED
        else:
            raise InvalidTransaction("Choose start, done, stuck, reopen, skip or needed.")
    stage.updated_by, stage.updated_at = actor, timezone.now()
    stage.save()
    if helpers is not None:
        stage.helpers.set(helpers)
    return _job_row(_reload(order), actor, today)


def _reload(order):
    return next(job for job in ProductionOrder.objects.filter(pk=order.pk).select_related(
        "quote__customer", "variant__item", "business_unit").prefetch_related(
        "boq_lines__tags", "boq_lines__stages__lead", "boq_lines__stages__helpers", "quote__payments"))


@transaction.atomic
def plan_line(*, actor, business_unit, line_id, data):
    """Lay out a JD's stages one after another from a start date, using each stage's usual days; one lead for all."""
    _require_user(actor, business_unit)
    if not _either(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'Run factory jobs' or 'Approve factory jobs' permission in this unit.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the start date.")
    line = (ProductionBoqLine.objects.select_for_update(of=("self",))
            .filter(pk=_int(line_id), order__business_unit=business_unit).select_related("order").first())
    if line is None:
        raise InvalidTransaction("That BOQ item was not found.")
    start = _day(data.get("start"), "start date")
    days = data.get("days") if isinstance(data.get("days"), dict) else {}
    day = start
    for stage in line.stages.select_for_update().order_by("position"):
        if stage.status in (T.SKIPPED, T.DONE):
            continue
        length = days.get(stage.kind, DAYS[stage.kind])
        if type(length) is not int or not 1 <= length <= 60:
            raise InvalidTransaction(f"{stage.get_kind_display()}: give between 1 and 60 working days.")
        stage.planned_start = _working_days(day, 1)
        stage.planned_end = _working_days(stage.planned_start, length)
        stage.planned_by_hand = False
        stage.updated_by, stage.updated_at = actor, timezone.now()
        stage.save(update_fields=["planned_start", "planned_end", "planned_by_hand", "updated_by", "updated_at"])
        day = stage.planned_end + datetime.timedelta(days=1)
    return _job_row(_reload(line.order), actor, timezone.localdate())


@transaction.atomic
def update_line(*, actor, business_unit, line_id, data):
    """Area, floor, zone and tags of a JD can be changed at any time, to keep the records right."""
    _require_user(actor, business_unit)
    if not _either(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'Run factory jobs' or 'Approve factory jobs' permission in this unit.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send what to change on the BOQ item.")
    line = (ProductionBoqLine.objects.select_for_update(of=("self",))
            .filter(pk=_int(line_id), order__business_unit=business_unit).select_related("order").first())
    if line is None:
        raise InvalidTransaction("That BOQ item was not found.")
    for field in ("area", "floor", "zone"):
        if field in data:
            setattr(line, field, _text(data[field], field, 80))
    line.save()
    if "tag_ids" in data:
        line.tags.set(tags_from(business_unit, data["tag_ids"]))
    return _job_row(_reload(line.order), actor, timezone.localdate())


def tags_from(business_unit, value):
    """Tags by id (from the screen) or by name (from an Excel column); only the Factory Manager's list counts."""
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > 10:
        raise InvalidTransaction("Give at most 10 tags per BOQ item.")
    found = []
    known = list(FactoryTag.objects.filter(legal_entity=business_unit.legal_entity, is_active=True))
    for item in value:
        tag = next((tag for tag in known if (type(item) is int and tag.pk == item)
                    or (isinstance(item, str) and tag.name.lower() == item.strip().lower())), None)
        if tag is None:
            raise InvalidTransaction(f"'{item}' is not on the Factory Manager's tag list.")
        if tag not in found:
            found.append(tag)
    return found


@transaction.atomic
def save_tag(*, actor, business_unit, data):
    """The Factory Manager keeps the tag list (door, cabinet, cladding, glass...)."""
    _require_user(actor, business_unit)
    if not (can_approve(actor, business_unit) or is_master_admin(actor)):
        raise WorkflowPermissionDenied("You need the 'Approve factory jobs' permission in this unit.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the tag's name.")
    name = _text(data.get("name"), "tag name", 40, required=True)
    color = data.get("color") or "#8a5a2b"
    if not isinstance(color, str) or len(color) != 7 or color[0] != "#" \
            or any(char not in "0123456789abcdefABCDEF" for char in color[1:]):
        raise InvalidTransaction("Choose the tag colour.")
    active = data.get("is_active", True)
    if type(active) is not bool:
        raise InvalidTransaction("Say whether the tag is in use.")
    tag_id = data.get("id")
    clash = FactoryTag.objects.filter(legal_entity=business_unit.legal_entity, name__iexact=name)
    if tag_id is None:
        if clash.exists():
            raise InvalidTransaction(f"There is already a '{name}' tag.")
        FactoryTag.objects.create(legal_entity=business_unit.legal_entity, name=name, color=color, is_active=active,
                                  created_by=actor)
    else:
        tag = FactoryTag.objects.filter(pk=_int(tag_id), legal_entity=business_unit.legal_entity).first()
        if tag is None:
            raise InvalidTransaction("That tag was not found.")
        if clash.exclude(pk=tag.pk).exists():
            raise InvalidTransaction(f"There is already a '{name}' tag.")
        tag.name, tag.color, tag.is_active = name, color, active
        tag.save()
    return {"tags": _tag_rows(business_unit)}


# Dashboard.

def factory_dashboard(*, actor, business_unit):
    _require_viewer(actor, business_unit)
    today = timezone.localdate()
    orders = _jobs(business_unit)
    jobs = [_job_row(order, actor, today) for order in orders]
    lines = [line | {"job": job["number"], "job_id": job["id"]} for job in jobs for line in job["lines"]]
    open_lines = [line for line in lines if line["status"] != T.DONE]
    counts = {status: sum(1 for line in lines if line["status"] == status)
              for status in (T.NOT_STARTED, T.IN_PROGRESS, T.STUCK, T.DONE)}

    # Where the work queues up: the stage each unfinished JD is at, and how many of those are late or stuck.
    stages = []
    for kind, label in K.choices:
        at = [line for line in open_lines if line["current"] and line["current"]["kind"] == kind]
        rows = [stage for line in lines for stage in line["stages"] if stage["kind"] == kind]
        took = [(datetime.date.fromisoformat(stage["finished_on"]) - datetime.date.fromisoformat(stage["started_on"])).days + 1
                for stage in rows if stage["status"] == T.DONE and stage["started_on"]]
        planned = [(datetime.date.fromisoformat(stage["planned_end"]) - datetime.date.fromisoformat(stage["planned_start"])).days + 1
                   for stage in rows if stage["planned_start"] and stage["planned_end"]]
        stages.append({"kind": kind, "label": label, "section": SECTION[kind], "waiting": len(at),
                       "late": sum(1 for stage in rows if stage["late"]),
                       "stuck": sum(1 for stage in rows if stage["status"] == T.STUCK),
                       "average_days": _f(Decimal(sum(took)) / len(took)) if took else "",
                       "planned_days": _f(Decimal(sum(planned)) / len(planned)) if planned else ""})
    worst = max(stages, key=lambda row: (row["late"] + row["stuck"], row["waiting"]))
    bottleneck = worst["kind"] if worst["late"] + worst["stuck"] + worst["waiting"] else ""

    late = sorted(([{"job": line["job"], "job_id": line["job_id"], "code": line["code"], "description": line["description"],
                     "stage": stage["label"], "section": stage["section"], "planned_end": stage["planned_end"],
                     "days_late": stage["days_late"], "lead": stage["lead"]["name"] if stage["lead"] else ""}
                    for line in open_lines for stage in line["stages"] if stage["late"]]),
                  key=lambda row: -row["days_late"])
    stuck = [{"job": line["job"], "code": line["code"], "description": line["description"], "stage": stage["label"],
              "reason": stage["stuck_reason"]}
             for line in open_lines for stage in line["stages"] if stage["status"] == T.STUCK]

    people = {}
    for line in open_lines:
        for stage in line["stages"]:
            if stage["status"] in (T.IN_PROGRESS, T.STUCK, T.NOT_STARTED) and stage["lead"]:
                found = people.setdefault(stage["lead"]["id"], {"name": stage["lead"]["name"], "active": 0, "planned": 0,
                                                                "late": 0})
                found["active" if stage["status"] != T.NOT_STARTED else "planned"] += 1
                found["late"] += 1 if stage["late"] else 0

    costs = []
    for order in orders:
        if order.status not in OPEN and order.status != S.COST_CHECK:
            continue
        materials, bought, labour = _actual(order)
        overhead = _round((materials + bought + labour) * order.overhead_percent / HUNDRED)
        estimate, limit = _limit(order)
        spent = materials + bought + labour + overhead
        costs.append({"job": order.number, "job_id": order.pk, "estimate": _f(estimate), "limit": _f(limit),
                      "spent": _f(spent), "percent": _f(_round(spent * HUNDRED / estimate)) if estimate else "",
                      "over": bool(estimate) and spent > limit})

    payments = [{"job": job["number"], "job_id": job["id"], "customer": job["customer"], **job["payment"],
                 "issue": job["payment_issue"]} for job in jobs if job["payment"]]

    return {
        "today": today.isoformat(),
        "items": {"total": len(lines), **counts, "late": sum(1 for line in open_lines if line["late"])},
        "jobs": {"in_production": sum(1 for job in jobs if job["status"] == S.IN_PROGRESS),
                 "waiting": sum(1 for job in jobs if job["status"] in (S.DRAFT, S.WAITING)),
                 "finished": sum(1 for job in jobs if job["status"] in (S.COST_CHECK, S.STORED, S.ON_THE_WAY,
                                                                       S.DELIVERED, S.IN_STOCK)),
                 "late": sum(1 for job in jobs if job["late"])},
        "stages": stages, "bottleneck": bottleneck, "late": late[:30], "late_count": len(late), "stuck": stuck[:30],
        "team": sorted(people.values(), key=lambda row: (-row["late"], -row["active"], row["name"])),
        "costs": sorted(costs, key=lambda row: -Decimal(row["percent"] or "0")),
        "work_in_progress": _f(sum((Decimal(row["spent"]) for row in costs), ZERO)),
        "payments": sorted(payments, key=lambda row: (not row["issue"], -Decimal(row["balance"]))),
        "tags": _tag_history(business_unit, today),
        "workload": _workload(actor, business_unit),
        "to_order": material_shortages(business_unit)["materials"][:30],
        "machines": machine_alerts(business_unit),
    }


def _workload(actor, business_unit):
    from .factory_team import team_workload

    found = team_workload(actor=actor, business_unit=business_unit)
    return {"summary": found["summary"],
            "people": [{key: row[key] for key in ("id", "name", "section_label", "load", "load_label", "active",
                                                  "planned_soon", "late", "assigned", "hours_week", "hours_month")}
                       for row in found["people"]]}


def _tag_history(business_unit, today):
    """For each tag: JD items finished in each of the last three years, and how many are in production now."""
    years = [today.year - offset for offset in range(TAG_YEARS - 1, -1, -1)]
    since = datetime.date(years[0], 1, 1)
    rows = []
    for tag in FactoryTag.objects.filter(legal_entity=business_unit.legal_entity).order_by("name"):
        done = {year: {"items": 0, "quantity": ZERO} for year in years}
        in_production = 0
        for line in (tag.boq_lines.filter(order__business_unit=business_unit).exclude(order__status=S.CANCELLED)
                     .select_related("order").prefetch_related("stages")):
            stages = list(line.stages.all())
            if _rollup(stages) == T.DONE:
                finished = [stage.finished_on for stage in stages if stage.finished_on]
                when = max(finished) if finished else (line.order.finished_at and timezone.localtime(line.order.finished_at).date())
            elif line.order.status in (S.STORED, S.ON_THE_WAY, S.DELIVERED, S.IN_STOCK):
                when = timezone.localtime(line.order.finished_at).date()
            else:
                in_production += 1
                continue
            if when and when >= since:
                done[when.year]["items"] += 1
                done[when.year]["quantity"] += line.quantity
        rows.append({"id": tag.pk, "name": tag.name, "color": tag.color, "in_production": in_production,
                     "years": [{"year": year, "items": done[year]["items"], "quantity": _f(done[year]["quantity"])}
                               for year in years],
                     "total": sum(done[year]["items"] for year in years)})
    return {"years": years, "rows": rows}
