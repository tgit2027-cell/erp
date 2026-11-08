"""The factory planner (Sam, 7 Nov): prepare the production schedule of every open JD by itself.

It plans from what the ERP already knows:
- past work: the usual working days of each stage for JDs with the same tags (factory_learning);
- materials: free stock in the stores and at the factory, then open purchase orders (ordered, partly delivered,
  paid or not) and requests to purchasing, earliest job first; workshop stages wait for the materials;
- the team: each stage goes to its lead, or to the person of the stage's section who can start it soonest, and
  nobody works on more stages at once than the factory allows.

A factory admin can still change any JD: dates set by hand are kept and the planner works around them.
The planner proposes; the Factory Manager or a factory admin applies.

Provisional until confirmed (asked 7 Nov): goods arrive `order_lead_days` (21) after ordering unless purchasing
gives an expected arrival; a request purchasing has not ordered yet adds ASK_DAYS; a person works on at most
`stages_per_person` (2) stages at once; jobs in production and approved jobs come before drafts, then by due date.
"""

import datetime
from collections import Counter

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .access import active_grants
from .access_levels import is_master_admin
from .factory import (
    OPEN,
    ZERO,
    S,
    _day,
    _f,
    _free_stock,
    _int,
    _require_user,
    _require_viewer,
    _still_needed,
    _text,
    can_approve,
    can_buy,
    can_run,
    factory_people,
    factory_settings,
)
from .factory_learning import _finished_lines, _match, _profile
from .factory_planning import (
    DAYS,
    SECTION,
    STAGE_SECTION,
    WORKSHOP_STAGES,
    K,
    T,
    _working_days,
)
from .models import (
    FactoryPlanBrief,
    FactoryPlanRun,
    ProductionOrder,
    ProductionStage,
    PurchaseArrival,
    PurchaseCommitment,
    PurchaseCommitmentLine,
    PurchaseReceiptLine,
    ReorderRequest,
    SupplierAdvance,
    SupplierPayment,
    UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction

ASK_DAYS = 3
LOOK_AHEAD = 240
ONE = datetime.timedelta(days=1)


def _when(day):
    return day.isoformat() if day else ""


def _days(count):
    return f"{count} working day{'' if count == 1 else 's'}"


def _short(day):
    return f"{day.day} {day.strftime('%b')}"


def _next_work_day(day):
    while day.weekday() == 4:
        day += ONE
    return day


def _work_days(start, end):
    day = start
    while day <= end:
        if day.weekday() != 4:
            yield day
        day += ONE


def can_plan(user, business_unit):
    return can_run(user, business_unit) or can_approve(user, business_unit) or is_master_admin(user)


# Materials: what is free now, what is coming, and when each job has everything.

def _payment(commitment, paid_receipts, advances):
    if commitment.pk in paid_receipts:
        return "paid", "Paid"
    if any(advance.supplier_id == commitment.supplier_id and advance.paid_on >= commitment.created_at.date()
           for advance in advances):
        return "advance", "Advance paid"
    return "unpaid", "Not paid yet"


def _supply(business_unit, variant_ids, lead_days, today):
    """Everything on its way for these materials: open purchase orders, then requests purchasing has not ordered."""
    supply = {}
    lines = list(PurchaseCommitmentLine.objects.filter(commitment__business_unit=business_unit, variant_id__in=variant_ids)
                 .select_related("commitment__supplier", "variant__item"))
    received = {row["commitment_line_id"]: row["total"] for row in PurchaseReceiptLine.objects.filter(
        commitment_line__in=lines).values("commitment_line_id").annotate(total=Sum("quantity"))}
    commitment_ids = {line.commitment_id for line in lines}
    arrivals = {row.commitment_id: row for row in PurchaseArrival.objects.filter(commitment_id__in=commitment_ids)}
    paid = set(SupplierPayment.objects.filter(receipt__purchase_commitment_id__in=commitment_ids, charge__isnull=True,
                                              status=SupplierPayment.Status.APPROVED)
               .values_list("receipt__purchase_commitment_id", flat=True))
    advances = list(SupplierAdvance.objects.filter(business_unit=business_unit, status=SupplierAdvance.Status.APPROVED))
    for line in lines:
        got = received.get(line.pk, ZERO)
        left = line.ordered_quantity - got
        if left <= 0:
            continue
        commitment = line.commitment
        ordered_on = commitment.created_at.date()
        arrival = arrivals.get(commitment.pk)
        expected = arrival.expected_on if arrival else ordered_on + datetime.timedelta(days=lead_days)
        payment, payment_label = _payment(commitment, paid, advances)
        supply.setdefault(line.variant_id, []).append({
            "kind": "order", "order_id": commitment.pk, "supplier": commitment.supplier.name,
            "status": "partly" if got > 0 else "ordered", "status_label": "Partly delivered" if got > 0 else "Ordered",
            "ordered_on": ordered_on, "expected_on": expected, "expected_given": arrival is not None,
            "overdue": expected < today, "payment": payment, "payment_label": payment_label,
            "quantity": left, "left": left})
    asked = ReorderRequest.objects.filter(business_unit=business_unit, variant_id__in=variant_ids,
                                          status=ReorderRequest.Status.SENT).select_related("supplier")
    for request in asked:
        ordered = PurchaseCommitmentLine.objects.filter(commitment__business_unit=business_unit, variant_id=request.variant_id,
                                                        commitment__created_at__gte=request.requested_at).exists()
        if ordered:
            continue
        supply.setdefault(request.variant_id, []).append({
            "kind": "request", "order_id": None, "supplier": request.supplier.name if request.supplier_id else "",
            "status": "asked", "status_label": "Asked purchasing to order", "ordered_on": None,
            "expected_on": today + datetime.timedelta(days=lead_days + ASK_DAYS), "expected_given": False,
            "overdue": False, "payment": "", "payment_label": "", "quantity": request.quantity, "left": request.quantity})
    for rows in supply.values():
        rows.sort(key=lambda row: (row["expected_on"], row["kind"] != "order"))
    return supply


def material_plan(business_unit, today=None, lead_days=None):
    """When each open job has all its materials, and the materials to watch, earliest job first."""
    today = today or timezone.localdate()
    if lead_days is None:
        lead_days = factory_settings(business_unit.legal_entity).order_lead_days
    needs = _still_needed(business_unit)
    ids = {material.pk for _, material, _ in needs}
    free = _free_stock(business_unit, ids)
    left = dict(free)
    supply = _supply(business_unit, ids, lead_days, today)
    unordered_by = today + datetime.timedelta(days=lead_days + ASK_DAYS)
    jobs, materials = {}, {}
    for order, material, quantity in needs:
        want = quantity
        take = min(left.get(material.pk, ZERO), want)
        left[material.pk] = left.get(material.pk, ZERO) - take
        want -= take
        ready, source = today, "In stock"
        for row in supply.get(material.pk, []):
            if want <= 0:
                break
            if row["left"] <= 0:
                continue
            use = min(row["left"], want)
            row["left"] -= use
            want -= use
            arrives = max(row["expected_on"], today)
            if arrives >= ready:
                ready = arrives
                source = (f"{row['status_label']}{' from ' + row['supplier'] if row['supplier'] else ''}, "
                          f"{'overdue since' if row['overdue'] else 'expected'} {_short(row['expected_on'])}")
        if want > 0:
            ready, source = max(ready, unordered_by), f"{_f(want).removesuffix('.00')} not ordered yet"
        job = jobs.setdefault(order.pk, {"ready_on": today, "waiting_for": [], "not_ordered": []})
        if ready > today:
            job["waiting_for"].append({"sku": material.sku, "name": material.item.description, "ready_on": _when(ready),
                                       "source": source})
        if want > 0:
            job["not_ordered"].append({"sku": material.sku, "name": material.item.description, "quantity": _f(want)})
        job["ready_on"] = max(job["ready_on"], ready)
        row = materials.setdefault(material.pk, {
            "variant_id": material.pk, "sku": material.sku, "name": material.item.description,
            "unit": material.item.get_unit_of_measure_display(), "needed": ZERO, "free": free.get(material.pk, ZERO),
            "not_ordered": ZERO, "jobs": []})
        row["needed"] += quantity
        row["not_ordered"] += want
        row["jobs"].append({"job": order.number, "job_id": order.pk, "ready_on": _when(ready)})
    rows = []
    for variant_id, row in materials.items():
        coming = [{"kind": found["kind"], "order_id": found["order_id"], "supplier": found["supplier"],
                   "status": found["status"], "status_label": found["status_label"],
                   "ordered_on": _when(found["ordered_on"]), "expected_on": _when(found["expected_on"]),
                   "expected_given": found["expected_given"], "overdue": found["overdue"],
                   "payment": found["payment"], "payment_label": found["payment_label"],
                   "quantity": _f(found["quantity"])} for found in supply.get(variant_id, [])]
        covered = row["free"] >= row["needed"]
        rows.append(row | {"needed": _f(row["needed"]), "free": _f(row["free"]), "not_ordered": _f(row["not_ordered"]),
                           "coming": coming, "covered_by_stock": covered})
    rows.sort(key=lambda row: (row["covered_by_stock"], row["sku"]))
    return {"jobs": jobs, "materials": rows}


# The schedule.

class _Team:
    """Who works on what, day by day, so nobody gets more stages at once than the factory allows."""

    def __init__(self, people, capacity):
        self.people = {person.pk: person for person in people}
        self.capacity = capacity
        self.days = {}

    def section(self, section):
        return [pk for pk, person in self.people.items()
                if getattr(getattr(person, "factory_worker", None), "section", "") == section]

    def book(self, person_ids, start, end):
        for pk in person_ids:
            counts = self.days.setdefault(pk, Counter())
            for day in _work_days(start, end):
                counts[day] += 1

    def free(self, pk, start, end):
        counts = self.days.get(pk, Counter())
        return all(counts[day] < self.capacity for day in _work_days(start, end))

    def load(self, pk, today):
        return sum(1 for day, count in self.days.get(pk, Counter()).items() if day >= today and count)

    def busy_until(self, pk, start, end):
        counts = self.days.get(pk, Counter())
        last = None
        for day in _work_days(start, end):
            if counts[day] >= self.capacity:
                last = day
        return last

    def name(self, pk):
        person = self.people.get(pk)
        return person.full_name if person else ""


def _earliest(team, person_id, earliest, length):
    day = _next_work_day(earliest)
    for _ in range(LOOK_AHEAD):
        end = _working_days(day, length)
        if person_id is None or team.free(person_id, day, end):
            return day, end
        day = _next_work_day(day + ONE)
    return _next_work_day(earliest), _working_days(_next_work_day(earliest), length)


def _jobs(business_unit):
    rank = {S.IN_PROGRESS: 0, S.WAITING: 1, S.DRAFT: 2}
    orders = list(ProductionOrder.objects.filter(business_unit=business_unit, status__in=OPEN)
                  .select_related("plan_brief")
                  .prefetch_related("boq_lines__tags", "boq_lines__stages__lead", "boq_lines__stages__helpers"))
    orders.sort(key=lambda order: (rank.get(order.status, 3), order.due_date, order.pk))
    return orders


def _brief(order):
    return getattr(order, "plan_brief", None)


def _floors(lines):
    """The floors named on a job's BOQ, in the order they first appear."""
    seen = []
    for line in lines:
        floor = " ".join((line.floor or "").split())
        if floor and floor.lower() not in [found.lower() for found in seen]:
            seen.append(floor)
    return seen


def _ordered_lines(lines, brief):
    """The JDs in the order the admin asked for: the JDs to do first, then floor by floor, then as in the BOQ."""
    floors = [floor.lower() for floor in (brief.floor_order if brief else [])]
    first = {code.upper() for code in (brief.first_codes if brief else [])}

    def key(pair):
        index, line = pair
        floor = " ".join((line.floor or "").split()).lower()
        return (line.code.upper() not in first, floors.index(floor) if floor in floors else len(floors), index)

    return [line for _, line in sorted(enumerate(lines), key=key)]


def _fixed(stage):
    return stage.status in (T.IN_PROGRESS, T.STUCK) or (stage.status == T.NOT_STARTED and stage.planned_by_hand)


def _stage_out(stage, start, end, lead_id, team, reasons, kept, planned=True):
    changed = planned and (not kept) and (start != stage.planned_start or end != stage.planned_end
                                          or lead_id != stage.lead_id)
    return {"id": stage.pk, "kind": stage.kind, "label": stage.get_kind_display(), "section": SECTION[stage.kind],
            "status": stage.status, "status_label": stage.get_status_display(), "kept": kept, "changed": changed,
            "by_hand": stage.planned_by_hand,
            "now": {"start": _when(stage.planned_start), "end": _when(stage.planned_end),
                    "lead": stage.lead.full_name if stage.lead else ""},
            "plan": {"start": _when(start), "end": _when(end), "lead_id": lead_id,
                     "lead": team.name(lead_id) if lead_id else ""},
            "reasons": reasons}


def _brief_row(brief):
    if brief is None:
        return None
    return {"start_on": _when(brief.start_on), "floor_order": brief.floor_order, "first_codes": brief.first_codes,
            "finish_by": _when(brief.finish_by), "notes": brief.notes,
            "given_by": (brief.given_by.get_full_name() or brief.given_by.get_username()),
            "given_at": brief.given_at.isoformat()}


class _Planner:
    def __init__(self, business_unit, today):
        self.today = today
        found = factory_settings(business_unit.legal_entity)
        self.found = found
        people = list(factory_people(business_unit))
        self.team = _Team(people, found.stages_per_person)
        self.materials = material_plan(business_unit, today, found.order_lead_days)
        self.finished = _finished_lines(business_unit)
        self.active = {person.pk: person.full_name for person in people}
        self.learned = {}

    def profile(self, line):
        key = tuple(sorted(tag.pk for tag in line.tags.all()))
        if key not in self.learned:
            chosen, basis = _match(self.finished, list(key))
            self.learned[key] = (_profile(chosen, self.active), basis)
        return self.learned[key]

    def ready(self, order):
        return self.materials["jobs"].get(order.pk, {"ready_on": self.today, "waiting_for": [], "not_ordered": []})

    def line(self, line, ready, start_day, planned):
        """Plan one JD's open stages one after another from start_day; returns its rows and when it ends."""
        team, today = self.team, self.today
        profile, basis = self.profile(line)
        stages = sorted(line.stages.all(), key=lambda stage: stage.position)
        materials_first = any(stage.kind == K.MATERIALS and stage.status not in (T.DONE, T.SKIPPED) for stage in stages)
        workshop_waits = ready["ready_on"] > today
        cursor, rows, line_end = start_day, [], None
        for stage in stages:
            if stage.status in (T.DONE, T.SKIPPED):
                continue
            if _fixed(stage):
                why = ["Set by hand, so it stays."] if stage.planned_by_hand else [f"{stage.get_status_display()}."]
                end = stage.planned_end or today
                rows.append(_stage_out(stage, stage.planned_start, stage.planned_end, stage.lead_id, team, why, True,
                                       planned))
                cursor = max(cursor, _next_work_day(max(end, today) + ONE))
                if stage.kind in WORKSHOP_STAGES:
                    workshop_waits = False
                line_end = max(line_end or end, end)
                continue
            reasons = []
            learned = profile["stages"][stage.kind] if profile else None
            if learned and learned["days"]:
                length = learned["days"]
                reasons.append(f"{_days(length)}, as in {profile['samples']} finished JDs ({basis}).")
            elif stage.planned_start and stage.planned_end and stage.kind != K.MATERIALS:
                length = max(sum(1 for _ in _work_days(stage.planned_start, stage.planned_end)), 1)
                reasons.append(f"{_days(length)}, as planned before.")
            else:
                length = DAYS[stage.kind]
                reasons.append(f"{_days(length)}, the usual for this stage.")
            length = min(max(length, 1), 60)
            earliest = cursor
            if workshop_waits and stage.kind in WORKSHOP_STAGES:
                after = _next_work_day(ready["ready_on"] + ONE)
                if after > earliest:
                    earliest = after
                    first = ready["waiting_for"][0] if ready["waiting_for"] else None
                    reasons.append(f"Starts after the materials arrive ({_short(ready['ready_on'])})"
                                   + (f": {first['name']}, {first['source']}." if first else "."))
                workshop_waits = False
            # Who does it: the lead already chosen, else the person of the section who can start soonest.
            if stage.lead_id:
                candidates, chosen_by = [stage.lead_id], "lead"
            else:
                section = team.section(STAGE_SECTION[stage.kind])
                usual = profile["leads"].get(stage.kind) if profile else None
                candidates = sorted(section, key=lambda pk: (not (usual and usual["id"] == pk), team.load(pk, today)))
                chosen_by = "section"
            best = None
            for pk in candidates or [None]:
                start, end = _earliest(team, pk, earliest, length)
                if stage.kind == K.MATERIALS and materials_first and ready["ready_on"] > end:
                    end = _next_work_day(ready["ready_on"])
                if best is None or start < best[0]:
                    best = (start, end, pk)
            start, end, lead_id = best
            if stage.kind == K.MATERIALS and ready["ready_on"] > today and end == _next_work_day(ready["ready_on"]):
                first = ready["waiting_for"][0] if ready["waiting_for"] else None
                reasons.append(f"Waits for the last materials ({_short(ready['ready_on'])})"
                               + (f": {first['name']}, {first['source']}." if first else "."))
            if lead_id and start > earliest:
                busy = team.busy_until(lead_id, earliest, start - ONE)
                if busy:
                    reasons.append(f"{team.name(lead_id)} is on other work until {_short(busy)}.")
            if lead_id and chosen_by == "section":
                reasons.append(f"{team.name(lead_id)} from {SECTION[stage.kind]} can start soonest.")
            elif not lead_id:
                reasons.append(f"Nobody is in the {SECTION[stage.kind]} section yet, so no lead is set.")
            team.book([pk for pk in [lead_id, *[person.pk for person in stage.helpers.all()]] if pk], start, end)
            rows.append(_stage_out(stage, start, end, lead_id, team, reasons, False, planned))
            cursor = _next_work_day(end + ONE)
            line_end = max(line_end or end, end)
        return rows, line_end, profile

    def job(self, order, planned):
        brief = _brief(order)
        ready = self.ready(order)
        all_lines = list(order.boq_lines.all())
        start_day = _next_work_day(self.today)
        if brief and brief.start_on > start_day:
            start_day = _next_work_day(brief.start_on)
        lines, job_end, first_start = [], None, None
        for line in _ordered_lines(all_lines, brief):
            rows, line_end, profile = self.line(line, ready, start_day, planned)
            if not rows:
                continue
            for row in rows:
                if row["plan"]["start"] and (first_start is None or row["plan"]["start"] < first_start):
                    first_start = row["plan"]["start"]
            job_end = max(job_end or line_end, line_end) if line_end else job_end
            lines.append({"id": line.pk, "code": line.code, "description": line.description, "floor": line.floor,
                          "tags": [tag.name for tag in line.tags.all()],
                          "learned_from": profile["samples"] if profile else 0, "stages": rows})
        if not lines:
            return None
        finish_by = (brief.finish_by if brief and brief.finish_by else None) or order.due_date
        late_by = (job_end - finish_by).days if job_end and job_end > finish_by else 0
        row = {"id": order.pk, "number": order.number, "project": order.project_name, "status": order.status,
               "status_label": order.get_status_display(), "due_date": _when(order.due_date),
               "finish_by": _when(finish_by), "finishes_on": _when(job_end), "late_by": late_by,
               "materials_ready_on": _when(ready["ready_on"]), "waiting_for": ready["waiting_for"],
               "not_ordered": ready["not_ordered"], "planned": planned, "brief": _brief_row(brief), "lines": lines}
        if not planned:
            started = any(stage.status in (T.IN_PROGRESS, T.STUCK, T.DONE)
                          for line in all_lines for stage in line.stages.all())
            suggested = _next_work_day(self.today) if started else datetime.date.fromisoformat(first_start)
            why = ("Work on this job has already started." if started else
                   "The first day the team can take it" + (f"; the workshops wait for materials until "
                                                           f"{_short(ready['ready_on'])}." if ready["waiting_for"] else "."))
            row["questions"] = {
                "floors": _floors(all_lines), "codes": [line.code for line in all_lines],
                "suggested": {"start_on": _when(suggested), "start_why": why, "floor_order": _floors(all_lines),
                              "finish_by": _when(order.due_date), "first_codes": []}}
        return row


def build_plan(business_unit, today=None):
    """The plan of every open job. Jobs with a brief are planned for real; for the others the planner suggests how to
    answer its questions (they are worked out last, so they never push a planned job)."""
    today = today or timezone.localdate()
    planner = _Planner(business_unit, today)
    orders = _jobs(business_unit)
    # Work that stays where it is: stages under way and dates set by hand, on every open job.
    for order in orders:
        for line in order.boq_lines.all():
            for stage in line.stages.all():
                if _fixed(stage) and stage.planned_end and stage.planned_end >= today:
                    ids = [pk for pk in [stage.lead_id, *[person.pk for person in stage.helpers.all()]] if pk]
                    planner.team.book(ids, max(stage.planned_start or today, today), stage.planned_end)
    jobs = []
    for planned in (True, False):
        for order in orders:
            if (_brief(order) is not None) != planned:
                continue
            row = planner.job(order, planned)
            if row:
                jobs.append(row)
    planned_jobs = [job for job in jobs if job["planned"]]
    changes = sum(stage["changed"] for job in planned_jobs for line in job["lines"] for stage in line["stages"])
    found = planner.found
    return {"today": _when(today), "jobs": jobs, "materials": planner.materials["materials"],
            "summary": {"jobs": len(planned_jobs), "to_brief": len(jobs) - len(planned_jobs), "changes": changes,
                        "late_jobs": [{"number": job["number"], "late_by": job["late_by"]}
                                      for job in planned_jobs if job["late_by"]],
                        "waiting_materials": sum(1 for job in planned_jobs if job["waiting_for"]),
                        "not_ordered": sum(1 for row in planner.materials["materials"] if row["not_ordered"] != "0.00")},
            "rules": {"order_lead_days": found.order_lead_days, "stages_per_person": found.stages_per_person,
                      "ask_days": ASK_DAYS}}


def _run_row(run):
    return {"id": run.pk, "ran_on": _when(run.ran_on), "ran_at": run.ran_at.isoformat(),
            "by": (run.ran_by.get_full_name() or run.ran_by.get_username()) if run.ran_by else "The planner",
            "reason": run.reason, "changes": run.changes, "moves": run.moves}


def _range(start, end):
    if not start:
        return "not planned"
    return _short(datetime.date.fromisoformat(start)) + ("" if start == end else
                                                         f" to {_short(datetime.date.fromisoformat(end))}")


def _why(row):
    """The reason that explains a move best: materials or a busy person, else the stage before it."""
    for reason in row["reasons"]:
        if "materials" in reason or "other work" in reason:
            return reason
    if row["now"]["lead"] != row["plan"]["lead"] and row["plan"]["lead"]:
        return f"{row['plan']['lead']} can start it soonest."
    return "It follows the stage before it, which moved."


def run_planner(business_unit, actor=None, reason="", today=None):
    """Plan every job that has a brief and save it, keeping dates set by hand; record what moved and why."""
    today = today or timezone.localdate()
    list(ProductionStage.objects.select_for_update().filter(line__order__business_unit=business_unit,
                                                            line__order__status__in=OPEN).values_list("pk", flat=True))
    plan = build_plan(business_unit, today)
    people = {person.pk: person for person in factory_people(business_unit)}
    now, moves, targets = timezone.now(), [], {}
    for job in plan["jobs"]:
        if not job["planned"]:
            continue
        for line in job["lines"]:
            for row in line["stages"]:
                if not row["changed"]:
                    continue
                targets[row["id"]] = row["plan"]
                if row["now"]["start"] or row["now"]["lead"] != row["plan"]["lead"]:
                    moves.append({"job": job["number"], "code": line["code"], "stage": row["label"],
                                  "from": _range(row["now"]["start"], row["now"]["end"]),
                                  "to": _range(row["plan"]["start"], row["plan"]["end"]),
                                  "lead": row["plan"]["lead"], "why": _why(row)})
    for stage in ProductionStage.objects.filter(pk__in=targets):
        target = targets[stage.pk]
        stage.planned_start = datetime.date.fromisoformat(target["start"])
        stage.planned_end = datetime.date.fromisoformat(target["end"])
        if target["lead_id"] and stage.lead_id is None:
            stage.lead = people.get(target["lead_id"])
        stage.planned_by_hand = False
        stage.updated_by, stage.updated_at = actor, now
        stage.save(update_fields=["planned_start", "planned_end", "lead", "planned_by_hand", "updated_by", "updated_at"])
    run = FactoryPlanRun.objects.create(business_unit=business_unit, ran_on=today, ran_by=actor, reason=reason[:120],
                                        changes=len(targets), moves=moves[:300])
    return run


def replan_if_due(business_unit, today=None):
    """The planner re-plans by itself once a day, the first time anyone looks at the factory's schedule that day."""
    today = today or timezone.localdate()
    if not FactoryPlanBrief.objects.filter(order__business_unit=business_unit, order__status__in=OPEN).exists():
        return None
    if FactoryPlanRun.objects.filter(business_unit=business_unit, ran_on=today, ran_by__isnull=True).exists():
        return None
    with transaction.atomic():
        return run_planner(business_unit, None, "Morning re-plan", today)


def plan_overview(*, actor, business_unit):
    _require_viewer(actor, business_unit)
    replan_if_due(business_unit)
    plan = build_plan(business_unit)
    runs = FactoryPlanRun.objects.filter(business_unit=business_unit).select_related("ran_by")[:10]
    return plan | {"can_plan": can_plan(actor, business_unit), "runs": [_run_row(run) for run in runs]}


def _require_planner(actor, business_unit):
    _require_user(actor, business_unit)
    if not can_plan(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'Run factory jobs' or 'Approve factory jobs' permission in this unit.")


@transaction.atomic
def replan_now(*, actor, business_unit, data=None):
    """A factory admin asks the planner to plan again now (after changing something by hand, for example)."""
    _require_planner(actor, business_unit)
    run = run_planner(business_unit, actor, "Asked to plan again")
    return plan_overview(actor=actor, business_unit=business_unit) | {"applied": run.changes}


def _names(value, allowed, field):
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > 200 or not all(isinstance(item, str) for item in value):
        raise InvalidTransaction(f"Choose the {field} from the list.")
    known = {item.lower(): item for item in allowed}
    chosen = []
    for item in value:
        found = known.get(" ".join(item.split()).lower())
        if found is None:
            raise InvalidTransaction(f"{item} is not one of this job's {field}.")
        if found not in chosen:
            chosen.append(found)
    return chosen


@transaction.atomic
def save_brief(*, actor, business_unit, order_id, data):
    """The admin answers the planner's questions for a job; the planner then plans the job by itself."""
    _require_planner(actor, business_unit)
    if not isinstance(data, dict):
        raise InvalidTransaction("Answer the planner's questions.")
    order = (ProductionOrder.objects.select_for_update().filter(pk=_int(order_id), business_unit=business_unit)
             .prefetch_related("boq_lines").first())
    if order is None:
        raise InvalidTransaction("That job was not found.")
    if order.status not in OPEN:
        raise InvalidTransaction(f"{order.number} is {order.get_status_display().lower()}, so there is nothing to plan.")
    lines = list(order.boq_lines.all())
    start = _day(data.get("start_on"), "start date")
    if start < timezone.localdate():
        raise InvalidTransaction("The start date cannot be in the past.")
    finish_by = None if data.get("finish_by") in (None, "") else _day(data["finish_by"], "finish-by date")
    if finish_by and finish_by < start:
        raise InvalidTransaction("The job cannot have to finish before it starts.")
    FactoryPlanBrief.objects.update_or_create(order=order, defaults={
        "start_on": start, "floor_order": _names(data.get("floor_order"), _floors(lines), "floors"),
        "first_codes": _names(data.get("first_codes"), [line.code for line in lines], "JDs"),
        "finish_by": finish_by, "notes": _text(data.get("notes", ""), "notes", 500),
        "given_by": actor, "given_at": timezone.now()})
    run = run_planner(business_unit, actor, f"{order.number} planned")
    return plan_overview(actor=actor, business_unit=business_unit) | {"applied": run.changes}


# Expected arrival of a purchase order.

def _can_set_arrival(user, business_unit):
    return (can_plan(user, business_unit) or can_buy(user, business_unit)
            or active_grants(user, UserActionGrant.Action.CREATE_ORDER).filter(business_unit=business_unit).exists())


@transaction.atomic
def set_arrival(*, actor, business_unit, data):
    """Purchasing (or the factory) records when an order's goods are expected; empty clears it."""
    _require_user(actor, business_unit)
    if not _can_set_arrival(actor, business_unit):
        raise WorkflowPermissionDenied("Only purchasing or the factory team can give an order's expected arrival.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the order and its expected arrival.")
    commitment = PurchaseCommitment.objects.filter(pk=_int(data.get("order_id")), business_unit=business_unit).first()
    if commitment is None:
        raise InvalidTransaction("That purchase order was not found.")
    if data.get("expected_on") in (None, ""):
        PurchaseArrival.objects.filter(commitment=commitment).delete()
    else:
        expected = _day(data["expected_on"], "expected arrival")
        if expected < commitment.created_at.date():
            raise InvalidTransaction("The goods cannot arrive before they were ordered.")
        PurchaseArrival.objects.update_or_create(commitment=commitment, defaults={
            "expected_on": expected, "note": _text(data.get("note", ""), "note", 160),
            "updated_by": actor, "updated_at": timezone.now()})
    return plan_overview(actor=actor, business_unit=business_unit)
