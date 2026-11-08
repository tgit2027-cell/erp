"""Factory machines: how many, their condition, maintenance alerts, spare parts and consumables, and Finance's view.

Sam, 7 Nov 2026: a section for the machines (count, status: new, good condition, needs replacement...), alerts for
coming maintenance (especially the CNC, sector saw and paint machines), the spare parts and consumables on hand and
when to order more; machine purchases must be identified correctly for Finance as consumables or depreciated items.

Confirmed by Sam on 7 Nov (the QAR 2,000 threshold until Finance replies): each machine is serviced every so many days, counted from its last
service, and the alert starts 7 days before; a machine costing the factory settings' threshold (QAR 2,000) or more is
a fixed asset (Machinery and tools, depreciated over 60 months) and must be registered in Finance's fixed assets;
below that, and every spare part and consumable, is an expense. The factory admin team and the Factory Manager keep
the list.
"""

import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .access_levels import is_master_admin
from .factory import (
    _f,
    _int,
    _require_user,
    _require_viewer,
    _text,
    can_approve,
    can_run,
    factory_settings,
)
from .models import (
    FactoryMachine,
    FactoryMachineService,
    FactoryPart,
    FactorySection,
    FixedAsset,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction

M = FactoryMachine
DUE_SOON_DAYS = 7
KEY_KINDS = (M.Kind.CNC, M.Kind.SAW, M.Kind.SPRAY)
NEEDS_ACTION = (M.Condition.REPAIR, M.Condition.REPLACE, M.Condition.OUT)


def can_keep(user, business_unit):
    return can_run(user, business_unit) or can_approve(user, business_unit) or is_master_admin(user)


def _require_keeper(actor, business_unit):
    _require_user(actor, business_unit)
    if not can_keep(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'Run factory jobs' or 'Approve factory jobs' permission in this unit.")


def _money(value, field, limit=Decimal("100000000")):
    try:
        amount = Decimal(str(value if value not in (None, "") else "0").replace(",", ""))
    except (InvalidOperation, ValueError):
        raise InvalidTransaction(f"The {field} must be a number.") from None
    if not amount.is_finite() or amount < 0 or amount > limit:
        raise InvalidTransaction(f"The {field} must be between 0 and {limit:,.0f}.")
    return amount.quantize(Decimal("0.01"))


def _day_or_none(value, field):
    if value in (None, ""):
        return None
    try:
        day = datetime.date.fromisoformat(str(value))
    except ValueError:
        raise InvalidTransaction(f"The {field} is not a date.") from None
    if day > timezone.localdate():
        raise InvalidTransaction(f"The {field} cannot be in the future.")
    return day


def next_service(machine):
    if not machine.service_every_days:
        return None
    since = machine.last_service_on or machine.bought_on or machine.created_at.date()
    return since + datetime.timedelta(days=machine.service_every_days)


def _treatment(machine, threshold):
    if machine.cost >= threshold and machine.cost > 0:
        return "asset", "Fixed asset (depreciated)"
    return "expense", "Expense (consumable)"


def _machine_row(machine, today, threshold, editable):
    due = next_service(machine)
    days = (due - today).days if due else None
    if days is None:
        service, service_label = "none", "No maintenance plan"
    elif days < 0:
        service, service_label = "overdue", f"Maintenance overdue by {-days} day{'s' if days != -1 else ''}"
    elif days <= DUE_SOON_DAYS:
        service, service_label = "soon", "Maintenance due today" if days == 0 else f"Maintenance due in {days} day{'s' if days != 1 else ''}"
    else:
        service, service_label = "ok", f"Next maintenance {due.isoformat()}"
    treatment, treatment_label = _treatment(machine, threshold)
    asset = machine.fixed_asset
    return {
        "id": machine.pk, "code": machine.code, "name": machine.name, "kind": machine.kind,
        "kind_label": machine.get_kind_display(), "section": machine.section, "section_label": machine.get_section_display(),
        "make_model": machine.make_model, "serial_number": machine.serial_number, "quantity": machine.quantity,
        "condition": machine.condition, "condition_label": machine.get_condition_display(),
        "bought_on": machine.bought_on.isoformat() if machine.bought_on else "", "cost": _f(machine.cost),
        "service_every_days": machine.service_every_days or "",
        "last_service_on": machine.last_service_on.isoformat() if machine.last_service_on else "",
        "next_service_on": due.isoformat() if due else "", "service": service, "service_label": service_label,
        "key_machine": machine.kind in KEY_KINDS, "treatment": treatment, "treatment_label": treatment_label,
        "fixed_asset": ({"number": asset.number, "status_label": asset.get_status_display()} if asset else None),
        "finance_missing": treatment == "asset" and asset is None,
        "note": machine.note, "is_active": machine.is_active,
        "services": [{"id": row.pk, "kind_label": row.get_kind_display(), "done_on": row.done_on.isoformat(),
                      "done_by": row.done_by, "cost": _f(row.cost), "note": row.note}
                     for row in list(machine.services.all())[:10]],
        "can_update": editable,
    }


def _part_row(part):
    order = part.is_active and part.reorder_at > 0 and part.on_hand <= part.reorder_at
    return {"id": part.pk, "name": part.name, "kind": part.kind, "kind_label": part.get_kind_display(),
            "machine_id": part.machine_id, "machine": f"{part.machine.code} {part.machine.name}" if part.machine_id else "",
            "unit": part.unit, "on_hand": _f(part.on_hand), "reorder_at": _f(part.reorder_at),
            "unit_cost": _f(part.unit_cost), "value": _f(part.on_hand * part.unit_cost), "order_needed": order,
            "is_active": part.is_active}


def machines_overview(*, actor, business_unit):
    _require_viewer(actor, business_unit)
    today = timezone.localdate()
    entity = business_unit.legal_entity
    found = factory_settings(entity)
    editable = can_keep(actor, business_unit)
    machines = [_machine_row(machine, today, found.asset_threshold, editable) for machine in
                FactoryMachine.objects.filter(legal_entity=entity).select_related("fixed_asset")
                .prefetch_related("services")]
    parts = [_part_row(part) for part in FactoryPart.objects.filter(legal_entity=entity).select_related("machine")]
    active = [row for row in machines if row["is_active"]]
    alerts = []
    for row in sorted(active, key=lambda row: (not row["key_machine"], row["next_service_on"] or "9999")):
        if row["service"] in ("overdue", "soon"):
            alerts.append({"level": "red" if row["service"] == "overdue" else "amber", "kind": "maintenance",
                           "text": f"{row['code']} {row['name']}: {row['service_label'].lower()}"})
    for row in active:
        if row["condition"] in NEEDS_ACTION:
            alerts.append({"level": "red", "kind": "condition", "text": f"{row['code']} {row['name']}: {row['condition_label'].lower()}"})
        if row["finance_missing"]:
            alerts.append({"level": "amber", "kind": "finance",
                           "text": f"{row['code']} {row['name']} cost QAR {Decimal(row['cost']):,.2f}: register it in Finance's fixed assets"})
    for row in parts:
        if row["order_needed"]:
            alerts.append({"level": "amber", "kind": "parts",
                           "text": f"Order {row['name']}: {row['on_hand']} {row['unit']} left (order at {row['reorder_at']})"})
    counts = {}
    for row in active:
        found_kind = counts.setdefault(row["kind"], {"kind": row["kind"], "label": row["kind_label"], "count": 0})
        found_kind["count"] += row["quantity"]
    return {
        "today": today.isoformat(), "machines": machines, "parts": parts, "alerts": alerts,
        "summary": {"machines": sum(row["quantity"] for row in active),
                    "need_action": sum(1 for row in active if row["condition"] in NEEDS_ACTION),
                    "maintenance_due": sum(1 for row in active if row["service"] in ("overdue", "soon")),
                    "parts_to_order": sum(1 for row in parts if row["order_needed"]),
                    "by_kind": sorted(counts.values(), key=lambda row: -row["count"]),
                    "by_condition": [{"condition": value, "label": label,
                                      "count": sum(1 for row in active if row["condition"] == value)}
                                     for value, label in M.Condition.choices]},
        "kinds": [{"id": value, "label": label} for value, label in M.Kind.choices],
        "conditions": [{"id": value, "label": label} for value, label in M.Condition.choices],
        "sections": [{"id": value, "label": label} for value, label in FactorySection.choices],
        "service_kinds": [{"id": value, "label": label} for value, label in FactoryMachineService.Kind.choices],
        "part_kinds": [{"id": value, "label": label} for value, label in FactoryPart.Kind.choices],
        "policy": {"asset_threshold": _f(found.asset_threshold), "life_months": found.machine_life_months,
                   "due_soon_days": DUE_SOON_DAYS},
        "can_update": editable,
    }


@transaction.atomic
def save_machine(*, actor, business_unit, data):
    _require_keeper(actor, business_unit)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the machine's details.")
    entity = business_unit.legal_entity
    machine = None
    if data.get("id") not in (None, ""):
        machine = FactoryMachine.objects.select_for_update().filter(pk=_int(data.get("id")), legal_entity=entity).first()
        if machine is None:
            raise InvalidTransaction("That machine was not found.")
    code = _text(data.get("code"), "machine code", 20, required=True).upper()
    if FactoryMachine.objects.filter(legal_entity=entity, code=code).exclude(pk=machine.pk if machine else None).exists():
        raise InvalidTransaction(f"There is already a machine {code}.")
    kind, section, condition = data.get("kind"), data.get("section"), data.get("condition") or M.Condition.GOOD
    if kind not in M.Kind.values:
        raise InvalidTransaction("Choose what kind of machine it is.")
    if section not in FactorySection.values:
        raise InvalidTransaction("Choose the section it works in.")
    if condition not in M.Condition.values:
        raise InvalidTransaction("Choose its condition.")
    quantity = _int(data.get("quantity") or 1)
    if quantity is None or not 1 <= quantity <= 500:
        raise InvalidTransaction("The count must be between 1 and 500.")
    every = data.get("service_every_days")
    every = None if every in (None, "") else _int(every)
    if every is not None and not 1 <= every <= 730:
        raise InvalidTransaction("Maintenance must be every 1 to 730 days.")
    active = data.get("is_active", True)
    if type(active) is not bool:
        raise InvalidTransaction("Say whether the machine is still in use.")
    fields = {
        "code": code, "name": _text(data.get("name"), "machine name", 120, required=True), "kind": kind,
        "section": section, "condition": condition, "quantity": quantity,
        "make_model": _text(data.get("make_model"), "make and model", 120),
        "serial_number": _text(data.get("serial_number"), "serial number", 60),
        "bought_on": _day_or_none(data.get("bought_on"), "date bought"),
        "cost": _money(data.get("cost"), "cost"), "service_every_days": every,
        "last_service_on": _day_or_none(data.get("last_service_on"), "last service date"),
        "note": _text(data.get("note"), "note", 200), "is_active": active,
    }
    if machine is None:
        FactoryMachine.objects.create(legal_entity=entity, created_by=actor, **fields)
    else:
        for key, value in fields.items():
            setattr(machine, key, value)
        machine.save()
    return machines_overview(actor=actor, business_unit=business_unit)


@transaction.atomic
def record_service(*, actor, business_unit, machine_id, data):
    """Maintenance or a repair was done: it restarts the maintenance count and can update the condition."""
    _require_keeper(actor, business_unit)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send what was done.")
    machine = FactoryMachine.objects.select_for_update().filter(pk=_int(machine_id),
                                                                legal_entity=business_unit.legal_entity).first()
    if machine is None:
        raise InvalidTransaction("That machine was not found.")
    kind = data.get("kind")
    if kind not in FactoryMachineService.Kind.values:
        raise InvalidTransaction("Choose maintenance, repair or inspection.")
    done_on = _day_or_none(data.get("done_on"), "date") or timezone.localdate()
    FactoryMachineService.objects.create(
        machine=machine, kind=kind, done_on=done_on, done_by=_text(data.get("done_by"), "who did it", 120),
        cost=_money(data.get("cost"), "cost", Decimal("10000000")), note=_text(data.get("note"), "note", 200),
        recorded_by=actor)
    if machine.last_service_on is None or done_on > machine.last_service_on:
        machine.last_service_on = done_on
    condition = data.get("condition")
    if condition not in (None, ""):
        if condition not in M.Condition.values:
            raise InvalidTransaction("Choose its condition.")
        machine.condition = condition
    machine.save()
    return machines_overview(actor=actor, business_unit=business_unit)


@transaction.atomic
def link_asset(*, actor, business_unit, machine_id, data):
    """Tie a machine to its fixed asset in Finance, so the two records agree."""
    _require_keeper(actor, business_unit)
    machine = FactoryMachine.objects.select_for_update().filter(pk=_int(machine_id),
                                                                legal_entity=business_unit.legal_entity).first()
    if machine is None:
        raise InvalidTransaction("That machine was not found.")
    number = _text((data or {}).get("asset_number"), "fixed asset number", 24)
    if not number:
        machine.fixed_asset = None
    else:
        asset = FixedAsset.objects.filter(number=number, business_unit__legal_entity=business_unit.legal_entity).first()
        if asset is None:
            raise InvalidTransaction(f"Finance has no fixed asset {number}.")
        if FactoryMachine.objects.filter(fixed_asset=asset).exclude(pk=machine.pk).exists():
            raise InvalidTransaction(f"{number} is already tied to another machine.")
        machine.fixed_asset = asset
    machine.save(update_fields=["fixed_asset"])
    return machines_overview(actor=actor, business_unit=business_unit)


@transaction.atomic
def save_part(*, actor, business_unit, data):
    """Add or change a spare part or consumable; 'use' or 'add' changes the quantity on hand."""
    _require_keeper(actor, business_unit)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the part's details.")
    entity = business_unit.legal_entity
    part = None
    if data.get("id") not in (None, ""):
        part = FactoryPart.objects.select_for_update().filter(pk=_int(data.get("id")), legal_entity=entity).first()
        if part is None:
            raise InvalidTransaction("That part was not found.")
    change = data.get("change")
    if part is not None and change in ("use", "add"):
        amount = _money(data.get("quantity"), "quantity", Decimal("1000000"))
        if amount <= 0:
            raise InvalidTransaction("Give a quantity more than 0.")
        if change == "use" and amount > part.on_hand:
            raise InvalidTransaction(f"Only {_f(part.on_hand)} {part.unit} of {part.name} is on hand.")
        part.on_hand += amount if change == "add" else -amount
        part.updated_by, part.updated_at = actor, timezone.now()
        part.save(update_fields=["on_hand", "updated_by", "updated_at"])
        return machines_overview(actor=actor, business_unit=business_unit)
    kind = data.get("kind")
    if kind not in FactoryPart.Kind.values:
        raise InvalidTransaction("Choose spare part or consumable.")
    machine = None
    if data.get("machine_id") not in (None, ""):
        machine = FactoryMachine.objects.filter(pk=_int(data.get("machine_id")), legal_entity=entity).first()
        if machine is None:
            raise InvalidTransaction("That machine was not found.")
    active = data.get("is_active", True)
    if type(active) is not bool:
        raise InvalidTransaction("Say whether the part is still used.")
    fields = {"name": _text(data.get("name"), "part name", 120, required=True), "kind": kind, "machine": machine,
              "unit": _text(data.get("unit"), "unit", 20) or "pcs",
              "on_hand": _money(data.get("on_hand"), "quantity on hand", Decimal("1000000")),
              "reorder_at": _money(data.get("reorder_at"), "order-at level", Decimal("1000000")),
              "unit_cost": _money(data.get("unit_cost"), "unit cost", Decimal("10000000")), "is_active": active,
              "updated_by": actor, "updated_at": timezone.now()}
    if part is None:
        FactoryPart.objects.create(legal_entity=entity, **fields)
    else:
        for key, value in fields.items():
            setattr(part, key, value)
        part.save()
    return machines_overview(actor=actor, business_unit=business_unit)


def machine_alerts(business_unit):
    """The short list of machine alerts for the factory dashboard."""
    today = timezone.localdate()
    found = factory_settings(business_unit.legal_entity)
    rows = [_machine_row(machine, today, found.asset_threshold, False) for machine in
            FactoryMachine.objects.filter(legal_entity=business_unit.legal_entity, is_active=True)
            .select_related("fixed_asset").prefetch_related("services")]
    parts = [_part_row(part) for part in FactoryPart.objects.filter(legal_entity=business_unit.legal_entity,
                                                                    is_active=True).select_related("machine")]
    return {"maintenance": [{"code": row["code"], "name": row["name"], "label": row["service_label"],
                             "overdue": row["service"] == "overdue"} for row in rows if row["service"] in ("overdue", "soon")],
            "condition": [{"code": row["code"], "name": row["name"], "label": row["condition_label"]}
                          for row in rows if row["condition"] in NEEDS_ACTION],
            "parts": [{"name": row["name"], "on_hand": row["on_hand"], "unit": row["unit"]}
                      for row in parts if row["order_needed"]],
            "machines": sum(row["quantity"] for row in rows)}
