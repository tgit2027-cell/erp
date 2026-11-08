"""What the factory learns from finished work, to fill new JDs that are usually made the same way.

Sam, 7 Nov 2026: an assistant for the factory that learns the production time of each item, the materials used and
how things are made. For a door: the type of door, the materials needed, joinery and paint time, the number of
accessories, who usually makes doors. It should help automate the JDs that are usually done the same way.

How it learns (agreed with Sam on 7 Nov): from every JD finished in the last three years, grouped by its tags, so
"Door + Fire rated" learns from fire-rated doors first and falls back to all doors. For each group it keeps the
working days each stage took (Fridays not counted), the materials and accessories per unit (from the BOQ's
Materials sheet), the hours per unit booked to the JD, the stages usually not needed, and who usually leads each
stage. A suggestion is only made from at least MIN_SAMPLES finished JDs, and always says how many it is based on.
A chat-style AI assistant can be added on top of this later; it needs an AI service account, so it waits for Sam.
"""

import datetime
from collections import Counter
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .factory import (
    ZERO,
    S,
    _f,
    _int,
    _require_user,
    _require_viewer,
    _round,
    average_cost,
    factory_people,
)
from .factory_planning import (
    DAYS,
    STAGES,
    _either,
    _job_row,
    _reload,
    _rollup,
    _working_days,
)
from .models import (
    FactoryTag,
    ProductionBoqLine,
    ProductionOrderMaterial,
    ProductionStage,
    RawMaterial,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction

K = ProductionStage.Kind
T = ProductionStage.Status
YEARS = 3
MIN_SAMPLES = 2


def _work_days(start, end):
    days, day = 0, start
    while day <= end:
        if day.weekday() != 4:
            days += 1
        day += datetime.timedelta(days=1)
    return max(days, 1)


def _finished_lines(business_unit):
    since = timezone.localdate() - datetime.timedelta(days=365 * YEARS)
    lines = []
    for line in (ProductionBoqLine.objects.filter(order__business_unit=business_unit)
                 .exclude(order__status=S.CANCELLED).prefetch_related("tags", "stages__lead", "labour")
                 .select_related("order")):
        stages = list(line.stages.all())
        if _rollup(stages) != T.DONE:
            continue
        finished = [stage.finished_on for stage in stages if stage.finished_on]
        if finished and max(finished) >= since:
            lines.append(line)
    return lines


def _profile(lines, active_people):
    if not lines:
        return None
    took, skipped, leads = {}, Counter(), {}
    materials, hours, units = {}, {}, ZERO
    for line in lines:
        units += line.quantity
        for stage in line.stages.all():
            if stage.status == T.SKIPPED:
                skipped[stage.kind] += 1
            elif stage.started_on and stage.finished_on:
                took.setdefault(stage.kind, []).append(_work_days(stage.started_on, stage.finished_on))
            if stage.lead_id and stage.status == T.DONE:
                leads.setdefault(stage.kind, Counter())[stage.lead_id] += 1
        for row in line.materials or []:
            materials[row["variant_id"]] = materials.get(row["variant_id"], ZERO) + Decimal(row["quantity"])
        for entry in line.labour.all():
            hours[entry.workshop] = hours.get(entry.workshop, ZERO) + entry.hours
    count = len(lines)
    variants = {raw.variant_id: raw for raw in RawMaterial.objects.filter(variant_id__in=materials)
                .select_related("variant__item")}
    stages = {}
    for kind in STAGES:
        samples = took.get(kind, [])
        stages[kind] = {"days": round(sum(samples) / len(samples)) if samples else None, "samples": len(samples),
                        "usually_skipped": skipped[kind] * 2 > count}
    usual = {}
    for kind, counter in leads.items():
        for person_id, times in counter.most_common():
            if person_id in active_people:
                usual[kind] = {"id": person_id, "name": active_people[person_id], "times": times}
                break
    per_unit = [{"variant_id": key, "sku": variants[key].variant.sku if key in variants else "",
                 "name": variants[key].variant.item.description if key in variants else "",
                 "accessory": key in variants and variants[key].category == RawMaterial.Category.HARDWARE,
                 "per_unit": _f(_round(total / units)) if units else "0.00"}
                for key, total in materials.items() if key in variants]
    return {
        "samples": count, "units": _f(units),
        "stages": stages,
        "materials": sorted(per_unit, key=lambda row: (row["accessory"], row["sku"])),
        "accessories_per_unit": _f(sum((Decimal(row["per_unit"]) for row in per_unit if row["accessory"]), ZERO)),
        "hours_per_unit": {key: _f(_round(value / units)) for key, value in hours.items()} if units else {},
        "leads": usual,
        "total_days": sum(row["days"] for kind, row in stages.items() if row["days"] and not row["usually_skipped"]),
    }


def _match(lines, tag_ids):
    """The finished JDs to learn from for a set of tags: the same tags first, then all that carry every one of them,
    then those that share the first tag."""
    wanted = set(tag_ids)
    if not wanted:
        return [], ""
    tagged = [(line, {tag.pk for tag in line.tags.all()}) for line in lines]
    for basis, test in (("same tags", lambda found: found == wanted), ("all these tags", lambda found: wanted <= found),
                        ("main tag", lambda found: tag_ids[0] in found)):
        chosen = [line for line, found in tagged if test(found)]
        if len(chosen) >= MIN_SAMPLES:
            return chosen, basis
    return [], ""


def _active(business_unit):
    return {person.pk: person.full_name for person in factory_people(business_unit)}


def learned_overview(*, actor, business_unit):
    """What has been learned for each tag: usual days per stage, materials and accessories per unit, the usual team."""
    _require_viewer(actor, business_unit)
    lines = _finished_lines(business_unit)
    people = _active(business_unit)
    rows = []
    for tag in FactoryTag.objects.filter(legal_entity=business_unit.legal_entity, is_active=True).order_by("name"):
        chosen, _ = _match(lines, [tag.pk])
        profile = _profile(chosen, people)
        rows.append({"id": tag.pk, "name": tag.name, "color": tag.color,
                     "finished": sum(1 for line in lines if tag in line.tags.all()), "profile": profile})
    return {"tags": rows, "min_samples": MIN_SAMPLES, "years": YEARS, "finished_items": len(lines),
            "stages": [{"id": kind, "label": label, "usual_days": DAYS[kind]} for kind, label in K.choices]}


def _line(business_unit, line_id, lock=False):
    rows = ProductionBoqLine.objects.filter(pk=_int(line_id), order__business_unit=business_unit).select_related("order")
    line = (rows.select_for_update(of=("self",)) if lock else rows).first()
    if line is None:
        raise InvalidTransaction("That BOQ item was not found.")
    return line


def suggest_for_line(*, actor, business_unit, line_id):
    """A suggestion for one JD from finished JDs with the same tags, sized to its quantity."""
    _require_viewer(actor, business_unit)
    line = _line(business_unit, line_id)
    tag_ids = [tag.pk for tag in line.tags.order_by("pk")]
    if not tag_ids:
        return {"line_id": line.pk, "suggestion": None,
                "reason": "Give this JD a tag (Door, Cabinet...) so it can be compared with finished work."}
    chosen, basis = _match([found for found in _finished_lines(business_unit) if found.pk != line.pk], tag_ids)
    profile = _profile(chosen, _active(business_unit))
    if profile is None:
        return {"line_id": line.pk, "suggestion": None,
                "reason": f"Fewer than {MIN_SAMPLES} finished JDs carry these tags yet, so there is nothing to learn from."}
    for row in profile["materials"]:
        row["quantity"] = _f(_round(Decimal(row["per_unit"]) * line.quantity))
    profile["hours"] = {key: _f(_round(Decimal(value) * line.quantity)) for key, value in profile["hours_per_unit"].items()}
    return {"line_id": line.pk, "basis": basis, "suggestion": profile, "reason": ""}


@transaction.atomic
def apply_suggestion(*, actor, business_unit, line_id, data):
    """Fill a JD from what was learned: stage days from a start date, the usual leads, the stages usually not needed,
    and (while the job is being prepared) its materials."""
    _require_user(actor, business_unit)
    if not _either(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'Run factory jobs' or 'Approve factory jobs' permission in this unit.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the start date.")
    found = suggest_for_line(actor=actor, business_unit=business_unit, line_id=line_id)
    profile = found["suggestion"]
    if profile is None:
        raise InvalidTransaction(found["reason"])
    line = _line(business_unit, line_id, lock=True)
    try:
        start = datetime.date.fromisoformat(str(data.get("start")))
    except ValueError:
        raise InvalidTransaction("Give the start date.") from None
    people = factory_people(business_unit)
    day, now = start, timezone.now()
    for stage in line.stages.select_for_update().order_by("position"):
        learned = profile["stages"][stage.kind]
        if stage.status == T.NOT_STARTED and learned["usually_skipped"]:
            stage.status = T.SKIPPED
        if stage.status in (T.SKIPPED, T.DONE):
            stage.save()
            continue
        length = min(max(learned["days"] or DAYS[stage.kind], 1), 60)
        stage.planned_start = _working_days(day, 1)
        stage.planned_end = _working_days(stage.planned_start, length)
        stage.planned_by_hand = False
        lead = profile["leads"].get(stage.kind)
        if lead and stage.lead_id is None:
            stage.lead = people.filter(pk=lead["id"]).first()
        stage.updated_by, stage.updated_at = actor, now
        stage.save()
        day = stage.planned_end + datetime.timedelta(days=1)
    order = line.order
    if order.status == S.DRAFT and not line.materials and profile["materials"]:
        line.materials = [{"variant_id": row["variant_id"], "sku": row["sku"], "quantity": row["quantity"]}
                          for row in profile["materials"] if Decimal(row["quantity"]) > 0]
        line.save(update_fields=["materials"])
        for row in line.materials:
            existing = ProductionOrderMaterial.objects.filter(order=order, material_id=row["variant_id"]).first()
            if existing:
                existing.quantity += Decimal(row["quantity"])
                existing.save(update_fields=["quantity"])
            else:
                from .models import Variant

                variant = Variant.objects.get(pk=row["variant_id"])
                ProductionOrderMaterial.objects.create(order=order, material=variant, quantity=Decimal(row["quantity"]),
                                                       unit_cost=average_cost(business_unit, variant))
    return _job_row(_reload(order), actor, timezone.localdate())



