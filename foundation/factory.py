"""Factory (Phase 2): job orders, raw materials, workshop hours, special purchases and job costing (synthetic only).

The section is built behind a switch. Until the Master Admin turns it on in Factory setup,
only the Master Admin can see or use it, so the teams testing Phase 1 are not disturbed.

How Tivoli's factory works (Sam, 5 Nov 2026):

- Mostly woodwork for fit-out projects (doors, cabinets, wood cladding), with a joinery and a
  paint section, CNC machines for wood and metal, and a small metal section. From time to time
  it makes loose furniture for the showrooms, and wooden items (curtain boxes, headboards,
  sofas) for clients of the textile and wallcovering studio.
- After the sales order is signed and the client has paid, a job order is opened listing all the
  works, as BOQ items coded JD-00, JD-01... The design team sends the approved material codes with
  the design package; the factory makes the production drawings and quantifies the materials.
- Raw materials have their own space in the warehouse. The factory takes most of them from there;
  the raw materials list shows code, description, size, unit price, supplier and stock.
- Workers' hours (and overtime) are recorded per job for the records and the job's cost.
- Finished work is stored in the warehouse, or sent straight to the client's site, where it sits
  in a virtual warehouse until the client receives it. A job costing more than 10% over its
  estimate needs Finance's approval.
- Special purchases for a job are requested, then bought by the factory purchaser by cash or card.

Provisional until confirmed: the Factory Manager approves each job, any materials beyond the job's
list and each special purchase, never their own; hours are costed at each workshop's hourly rate,
overtime at 125%; job cost = materials + special purchases + labour + 15% factory overhead; rates and
overhead are set by the Master Admin only; a client job needs an approved sales order with a payment.

Accounting:

    materials taken:    Dr factory jobs in production (WIP)   Cr stock, at the stock's average cost
    special purchase:   Dr WIP                                Cr bank and cash (the cash box or card)
    hours recorded:     Dr WIP                                Cr factory labour moved into jobs
    job finished:       Dr WIP                                Cr factory overhead moved into jobs
      client job:       Dr finished jobs not yet received     Cr WIP, at the job's full cost
      showroom piece:   Dr stock                              Cr WIP, at the job's full cost
    client receives:    Dr cost of goods sold                 Cr finished jobs not yet received
"""

import datetime
import io
import re
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .access import active_grants
from .access_levels import is_master_admin, require_master_admin
from .cash_accounts import _account as _cash_account
from .cash_accounts import _reconciled
from .models import (
    CashAccount,
    CashLineAssignment,
    DemoUserMarker,
    Employee,
    FactoryMaterialRequest,
    FactoryPurchase,
    FactorySettings,
    JournalEntry,
    Location,
    ProductionBoqLine,
    ProductionIssue,
    ProductionLabour,
    ProductionOrder,
    ProductionOrderMaterial,
    RawMaterial,
    SalesQuote,
    StockBalance,
    StockMovement,
    StockTransferRequest,
    StockTransferRequestEvent,
    StockTransferRequestLine,
    UserActionGrant,
    Variant,
    Workshop,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import (
    InvalidTransaction,
    _begin_idempotent_operation,
    _complete,
    _get_or_create_balances,
    _post_journal,
    _quantity,
    _require_demo,
    _validate_bucket,
    held_quantity,
)

A = UserActionGrant.Action
S = ProductionOrder.Status
P = ProductionOrder.Purpose
D = ProductionOrder.Destination
B = FactoryPurchase.Status
MR = FactoryMaterialRequest.Status
RUN, APPROVE, APPROVE_COST, BUY = A.RUN_FACTORY, A.APPROVE_FACTORY, A.APPROVE_FACTORY_COST, A.BUY_FACTORY
FACTORY_ACTIONS = (RUN, APPROVE, APPROVE_COST, BUY)
ZERO = Decimal("0.00")
CENT = Decimal("0.01")
HUNDRED = Decimal("100")
WORKSHOPS = [value for value, _ in Workshop.choices]
SALES_ORDER_STATUSES = (SalesQuote.Status.APPROVED, SalesQuote.Status.HELD, SalesQuote.Status.CONFIRMED,
                        SalesQuote.Status.READY)
BOQ_CODE = re.compile(r"^JD-\d{2,4}(\.\d{1,3}|[A-Z])?$")
MAX_BOQ_LINES = 2000
SITE_CODE = "VSITE"  # one virtual site warehouse per project: VSITE-<PROJECT>, named "Vsite=<project name>"
OPEN = (S.DRAFT, S.WAITING, S.IN_PROGRESS)
SHOWN_DAYS = 180
POLICY_LABEL = (
    "Provisional rules: the Factory Manager approves each job, materials beyond its list and special purchases; hours "
    "are costed at each workshop's rate, overtime at the overtime rate; job cost is materials + special purchases + "
    "labour + factory overhead; Finance approves a job that costs more than the estimate allows. Rates and overhead "
    "are set by the Master Admin."
)


def _round(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _f(value):
    return format(Decimal(str(value)).quantize(CENT), "f")


def _int(value):
    return value if type(value) is int else None


def _text(value, field, limit, required=False):
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if len(text) > limit:
        raise InvalidTransaction(f"The {field} can be at most {limit} characters.")
    if required and not text:
        raise InvalidTransaction(f"Give the {field}.")
    return text


def _number(value, field, *, allow_zero=True, limit=Decimal("10000")):
    if value in (None, ""):
        if allow_zero:
            return ZERO
        raise InvalidTransaction(f"Enter the {field}.")
    try:
        number = Decimal(str(value).replace(",", ""))
    except Exception:
        raise InvalidTransaction(f"Enter the {field} as a number.") from None
    if not number.is_finite() or number < 0 or (number == 0 and not allow_zero) or number > limit \
            or number != number.quantize(CENT):
        low = "more than 0 and at most" if not allow_zero else "between 0 and"
        raise InvalidTransaction(f"The {field} must be {low} {limit:,f}, with at most two decimals.")
    return number


def _day(value, field):
    try:
        return datetime.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise InvalidTransaction(f"Give the {field} as a date (YYYY-MM-DD).") from None


def _past_day(value, field):
    day = _day(value, field)
    if day > timezone.localdate():
        raise InvalidTransaction(f"The {field} cannot be in the future.")
    return day


def _name(user):
    if user is None:
        return ""
    return user.get_full_name() or user.get_username()


# Who may do what.

def _granted(user, business_unit, action):
    return active_grants(user, action).filter(business_unit=business_unit).exists()


def can_run(user, business_unit):
    return _granted(user, business_unit, RUN)


def can_approve(user, business_unit):
    return _granted(user, business_unit, APPROVE)


def can_approve_cost(user, business_unit):
    return _granted(user, business_unit, APPROVE_COST)


def can_buy(user, business_unit):
    return _granted(user, business_unit, BUY)


def factory_settings(legal_entity):
    found, created = FactorySettings.objects.get_or_create(legal_entity=legal_entity)
    if created:
        found.refresh_from_db()
    return found


def is_live(legal_entity):
    return FactorySettings.objects.filter(legal_entity=legal_entity, is_live=True).exists()


def visibility(user):
    """What the sign-in tells the screen: whether this person sees the Factory section, and whether it is live."""
    if getattr(settings, "APP_ENV", None) != "demo" or not getattr(user, "is_authenticated", False):
        return {"can_see_factory": False, "factory_live": False}
    live = FactorySettings.objects.filter(is_live=True, is_synthetic=True).exists()
    if is_master_admin(user):
        return {"can_see_factory": True, "factory_live": live}
    holds = active_grants(user).filter(action__in=FACTORY_ACTIONS,
                                       business_unit__legal_entity__factory_settings__is_live=True)
    return {"can_see_factory": live and holds.exists(), "factory_live": live}


def factory_people(business_unit):
    """The company's active employees who work in the factory: in the Factory department, or given a factory section."""
    from django.db.models import Q

    return (Employee.objects.filter(business_unit__legal_entity=business_unit.legal_entity, status=Employee.Status.ACTIVE)
            .filter(Q(department__iexact="factory") | Q(factory_worker__isnull=False))
            .select_related("factory_worker").order_by("full_name"))


def person_row(person):
    worker = getattr(person, "factory_worker", None)
    return {"id": person.pk, "name": person.full_name, "job_title": person.job_title, "department": person.department,
            "section": worker.section if worker else "", "section_label": worker.get_section_display() if worker else ""}


def _require_user(actor, business_unit):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("The factory runs only in the synthetic demo environment.")
    if not getattr(actor, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=actor, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can use the factory.")
    if business_unit is None:
        raise WorkflowPermissionDenied("Choose a business unit.")
    _require_demo(business_unit, business_unit.legal_entity)
    if not is_master_admin(actor) and not is_live(business_unit.legal_entity):
        raise WorkflowPermissionDenied("The factory section is not live yet.")


def _require_viewer(actor, business_unit):
    _require_user(actor, business_unit)
    if is_master_admin(actor):
        return
    if not any(_granted(actor, business_unit, action) for action in FACTORY_ACTIONS):
        raise WorkflowPermissionDenied("The factory is shown only to people with a factory permission in this unit.")


def _require(actor, business_unit, allowed, action):
    _require_user(actor, business_unit)
    if not allowed(actor, business_unit):
        raise WorkflowPermissionDenied(f"You need the '{A(action).label}' permission in this unit.")


# Stock and cost helpers.

def _locations(business_unit):
    """Physical locations of this unit: the warehouse (raw materials store included), factory and showrooms."""
    return (Location.objects.filter(is_active=True, business_units=business_unit, legal_entity=business_unit.legal_entity)
            .exclude(kind=Location.Kind.VIRTUAL).order_by("code"))


def _stores(business_unit):
    """Warehouse locations the factory asks the warehouse for materials from (the raw materials store first)."""
    return _locations(business_unit).filter(kind=Location.Kind.WAREHOUSE)


def _factory_places(business_unit):
    """Factory locations the warehouse delivers to: W41-FAC, metal & CNC, upholstery."""
    return _locations(business_unit).filter(kind=Location.Kind.FACTORY)


def _project_label(order):
    if order.project_name:
        return order.project_name
    return order.quote.customer.name if order.quote_id else order.number


def _site_location(order):
    """The project's own virtual site warehouse: finished work waits there until the client receives it (Sam, 5 Nov)."""
    business_unit, label = order.business_unit, _project_label(order)
    slug = re.sub(r"[^A-Z0-9]+", "-", label.replace("TEST ONLY", "").upper()).strip("-")[:60] or order.number
    code = f"{SITE_CODE}-{slug}"
    location = Location.objects.filter(code=code, legal_entity=business_unit.legal_entity).first()
    if location is None:
        location = Location.objects.create(code=code, name=f"Vsite={label}"[:160], kind=Location.Kind.VIRTUAL,
                                           legal_entity=business_unit.legal_entity, area="Virtual",
                                           street="Client site", sub_location=label[:80])
    location.business_units.add(business_unit)
    return location


def _free(balance):
    return balance.on_hand_quantity - balance.reserved_quantity - held_quantity(balance)


def average_cost(business_unit, variant):
    """The material's average cost per unit across this unit's stock."""
    totals = StockBalance.objects.filter(business_unit=business_unit, variant=variant, on_hand_quantity__gt=0) \
        .aggregate(quantity=Sum("on_hand_quantity"), value=Sum("on_hand_value"))
    if not totals["quantity"]:
        return ZERO
    return _round(totals["value"] / totals["quantity"])


def _rate(found, workshop):
    return getattr(found, f"rate_{workshop}")


def _planned_hours(order):
    return {workshop: getattr(order, f"planned_hours_{workshop}") for workshop in WORKSHOPS}


def _estimate(order, found):
    materials = sum((_round(line.quantity * line.unit_cost) for line in order.materials.all()), ZERO)
    labour = sum((_round(hours * _rate(found, workshop)) for workshop, hours in _planned_hours(order).items()), ZERO)
    overhead = _round((materials + labour) * found.overhead_percent / HUNDRED)
    return materials, labour, overhead


def _actual(order):
    """(materials taken, special purchases bought, labour) booked to the job so far."""
    materials = order.issues.filter(status=ProductionIssue.Status.ISSUED).aggregate(total=Sum("value"))["total"] or ZERO
    bought = order.purchases.filter(status=B.BOUGHT).aggregate(total=Sum("actual_cost"))["total"] or ZERO
    labour = order.labour.aggregate(total=Sum("cost"))["total"] or ZERO
    return materials, bought, labour


def _limit(order):
    estimate = order.estimate_materials + order.estimate_labour + order.estimate_overhead
    return estimate, _round(estimate * (HUNDRED + order.overrun_percent) / HUNDRED)


# Reading.

def _purchase_row(purchase, actor):
    unit = purchase.order.business_unit
    return {
        "id": purchase.pk, "job": purchase.order.number, "job_id": purchase.order_id, "description": purchase.description,
        "quantity": _f(purchase.quantity), "estimated_cost": _f(purchase.estimated_cost),
        "supplier_name": purchase.supplier_name, "reason": purchase.reason, "status": purchase.status,
        "status_label": purchase.get_status_display(), "requested_by": _name(purchase.requested_by),
        "requested_at": purchase.requested_at.isoformat(), "decided_by": _name(purchase.decided_by),
        "decision_note": purchase.decision_note, "bought_by": _name(purchase.bought_by),
        "bought_on": purchase.bought_on.isoformat() if purchase.bought_on else "",
        "actual_cost": _f(purchase.actual_cost) if purchase.actual_cost is not None else "",
        "paid_from": purchase.paid_from.name if purchase.paid_from_id else "", "receipt_reference": purchase.receipt_reference,
        "can_decide": (purchase.status == B.REQUESTED and can_approve(actor, unit) and purchase.requested_by_id != actor.pk),
        "can_buy": (purchase.status == B.APPROVED and purchase.order.status == S.IN_PROGRESS and can_buy(actor, unit)),
        "can_cancel": purchase.status in (B.REQUESTED, B.APPROVED) and (can_run(actor, unit) or can_approve(actor, unit)),
    }


def _issue_label(issue):
    if issue.status != ProductionIssue.Status.WITH_WAREHOUSE:
        return issue.get_status_display()
    request = issue.transfer_request
    if request.status == StockTransferRequest.Status.SUBMITTED:
        return "Waiting for the warehouse admin to approve"
    if request.status == StockTransferRequest.Status.SCHEDULED:
        return f"Warehouse delivers on {request.transfer_date.isoformat()}"
    if request.status == StockTransferRequest.Status.IN_TRANSIT:
        return "On its way from the warehouse: confirm when it arrives"
    return "Waiting for the warehouse to prepare it"


def _order_row(order, actor):
    unit = order.business_unit
    materials, bought, labour = _actual(order)
    finished = order.status not in OPEN and order.status != S.CANCELLED
    overhead = order.actual_overhead if finished else _round((materials + bought + labour) * order.overhead_percent / HUNDRED)
    estimate, limit = _limit(order)
    taken = {}
    for issue in order.issues.all():
        if issue.status != ProductionIssue.Status.REJECTED:
            taken[issue.material_id] = taken.get(issue.material_id, ZERO) + issue.quantity
    running = order.status == S.IN_PROGRESS
    runs, approves = can_run(actor, unit), can_approve(actor, unit)
    nothing_booked = (not order.issues.filter(status__in=(ProductionIssue.Status.ISSUED,
                                                          ProductionIssue.Status.WITH_WAREHOUSE)).exists() and not order.labour.exists()
                      and not order.purchases.filter(status=B.BOUGHT).exists())
    return {
        "id": order.pk, "number": order.number, "status": order.status, "status_label": order.get_status_display(),
        "purpose": order.purpose, "purpose_label": order.get_purpose_display(),
        "quote": ({"id": order.quote_id, "customer": order.quote.customer.name} if order.quote_id else None),
        "project_name": order.project_name, "design_reference": order.design_reference,
        "project": ({"id": order.project_id, "number": order.project.number, "name": order.project.name}
                    if order.project_id else None),
        "product": ({"variant_id": order.variant_id, "sku": order.variant.sku, "name": order.variant.item.description,
                     "quantity": _f(order.quantity)} if order.variant_id else None),
        "due_date": order.due_date.isoformat(), "late": order.status in OPEN and order.due_date < timezone.localdate(),
        "destination": order.destination if finished else "",
        "destination_label": order.get_destination_display() if finished else "",
        "finish_location": ({"id": order.finish_location_id, "code": order.finish_location.code,
                             "name": order.finish_location.name} if order.finish_location_id else None),
        "note": order.note, "decision_note": order.decision_note,
        "boq": [{"id": line.pk, "code": line.code, "description": line.description, "quantity": _f(line.quantity), "unit": line.unit,
                 "details": line.details, "area": line.area, "floor": line.floor, "zone": line.zone,
                 "materials": line.materials,
                 "tags": [tag.pk for tag in line.tags.all()]} for line in order.boq_lines.all()],
        "planned_hours": {workshop: _f(hours) for workshop, hours in _planned_hours(order).items()},
        "materials": [{"variant_id": line.material_id, "sku": line.material.sku, "name": line.material.item.description,
                       "quantity": _f(line.quantity), "unit_cost": _f(line.unit_cost),
                       "taken": _f(taken.get(line.material_id, ZERO))} for line in order.materials.all()],
        "estimate": {"materials": _f(order.estimate_materials), "labour": _f(order.estimate_labour),
                     "overhead": _f(order.estimate_overhead), "total": _f(estimate), "limit": _f(limit),
                     "overhead_percent": _f(order.overhead_percent), "overrun_percent": _f(order.overrun_percent)},
        "actual": {"materials": _f(materials), "purchases": _f(bought), "labour": _f(labour), "overhead": _f(overhead),
                   "total": _f(materials + bought + labour + overhead)},
        "issues": [{"id": issue.pk, "sku": issue.material.sku, "name": issue.material.item.description,
                    "location": issue.location.code, "quantity": _f(issue.quantity), "value": _f(issue.value),
                    "deliver_to": issue.deliver_to.code if issue.deliver_to_id else "",
                    "request_id": issue.transfer_request_id, "slip_id": issue.request_id,
                    "beyond_plan": issue.beyond_plan, "status": issue.status, "status_label": _issue_label(issue),
                    "can_receive": (issue.status == ProductionIssue.Status.WITH_WAREHOUSE and runs
                                    and issue.transfer_request.status == StockTransferRequest.Status.IN_TRANSIT),
                    "note": issue.note, "requested_by": _name(issue.requested_by),
                    "requested_at": issue.requested_at.isoformat(), "decided_by": _name(issue.decided_by),
                    "can_decide": (issue.status == ProductionIssue.Status.WAITING and approves and not issue.request_id
                                   and issue.requested_by_id != actor.pk)}
                   for issue in order.issues.all()],
        "material_requests": [_slip_row(request, actor) for request in order.material_requests.all()],
        "labour": [{"id": row.pk, "line": row.line.code if row.line_id else "", "workshop": row.workshop, "workshop_label": row.get_workshop_display(),
                    "employee": row.employee.full_name, "work_date": row.work_date.isoformat(), "hours": _f(row.hours),
                    "overtime": row.overtime, "rate": _f(row.rate), "cost": _f(row.cost),
                    "recorded_by": _name(row.recorded_by)} for row in order.labour.all()],
        "purchases": [_purchase_row(purchase, actor) for purchase in order.purchases.all()],
        "created_by": _name(order.created_by), "created_at": order.created_at.isoformat(),
        "approved_by": _name(order.approved_by), "approved_at": order.approved_at.isoformat() if order.approved_at else "",
        "finished_by": _name(order.finished_by), "finished_at": order.finished_at.isoformat() if order.finished_at else "",
        "cost_approved_by": _name(order.cost_approved_by), "received_by": _name(order.received_by),
        "received_on": order.received_on.isoformat() if order.received_on else "",
        "can_edit": order.status == S.DRAFT and runs,
        "can_submit": order.status == S.DRAFT and runs,
        "can_approve": order.status == S.WAITING and approves and order.created_by_id != actor.pk,
        "can_work": running and runs,
        "can_finish": running and runs,
        "can_approve_cost": (order.status == S.COST_CHECK and can_approve_cost(actor, unit)
                             and order.finished_by_id != actor.pk),
        "can_send": order.status == S.STORED and runs,
        "can_receive": order.status == S.ON_THE_WAY and runs,
        "can_cancel": order.status in OPEN and (runs or approves) and nothing_booked,
    }


def _orders(business_unit):
    return (ProductionOrder.objects.filter(business_unit=business_unit)
            .select_related("business_unit", "variant__item", "quote__customer", "project", "finish_location", "created_by",
                            "approved_by", "finished_by", "cost_approved_by", "received_by")
            .prefetch_related("boq_lines__tags", "materials__material__item", "issues__material__item", "issues__location",
                              "issues__deliver_to", "issues__transfer_request",
                              "issues__requested_by", "issues__decided_by",
                              "material_requests__store", "material_requests__deliver_to",
                              "material_requests__requested_by", "material_requests__manager_by",
                              "material_requests__transfer_request__decided_by",
                              "material_requests__issues__material__item", "labour__employee", "labour__recorded_by", "labour__line",
                              "purchases__requested_by", "purchases__decided_by", "purchases__bought_by",
                              "purchases__paid_from", "purchases__order__business_unit"))


def _raw_material_rows(business_unit):
    stock = {}
    for balance in StockBalance.objects.filter(business_unit=business_unit, on_hand_quantity__gt=0,
                                               variant__raw_material__isnull=False).select_related("location"):
        found = stock.setdefault(balance.variant_id, {"on_hand": ZERO, "free": ZERO, "value": ZERO, "places": []})
        free = _free(balance)
        found["on_hand"] += balance.on_hand_quantity
        found["free"] += free
        found["value"] += balance.on_hand_value
        if balance.location.kind != Location.Kind.VIRTUAL:
            found["places"].append({"location_id": balance.location_id, "location": balance.location.code,
                                    "on_hand": _f(balance.on_hand_quantity), "free": _f(free)})
    rows = []
    for material in (RawMaterial.objects.filter(is_active=True)
                     .select_related("variant__item__supplier").order_by("category", "variant__sku")):
        item = material.variant.item
        found = stock.get(material.variant_id, {"on_hand": ZERO, "free": ZERO, "value": ZERO, "places": []})
        unit_price = _round(found["value"] / found["on_hand"]) if found["on_hand"] else None
        rows.append({
            "variant_id": material.variant_id, "code": material.variant.sku, "item_code": item.item_code,
            "description": item.description, "size": material.size, "category": material.category,
            "category_label": material.get_category_display(), "unit": item.get_unit_of_measure_display(),
            "unit_price": _f(unit_price) if unit_price is not None else "",
            "list_price": _f(item.supplier_price) if item.supplier_price is not None else "",
            "list_currency": item.supplier_price_currency,
            "supplier": item.supplier.name if item.supplier_id else "",
            "on_hand": _f(found["on_hand"]), "free": _f(found["free"]), "places": found["places"],
        })
    return rows


# Checking the BOQ's materials against raw materials stock.

def _free_stock(business_unit, variant_ids):
    """Free quantity of each material in the stores and at the factory (virtual places do not count)."""
    free = {}
    for balance in StockBalance.objects.filter(business_unit=business_unit, variant_id__in=variant_ids,
                                               location__kind__in=(Location.Kind.WAREHOUSE, Location.Kind.FACTORY)):
        free[balance.variant_id] = free.get(balance.variant_id, ZERO) + max(_free(balance), ZERO)
    return free


def _still_needed(business_unit, exclude=None):
    """What each open job still needs of each material: its list less what it has already asked for.

    Jobs are taken in the order they are due, so the earliest job is served first.
    """
    taken = {}
    for row in (ProductionIssue.objects.filter(order__business_unit=business_unit, order__status__in=OPEN)
                .exclude(status=ProductionIssue.Status.REJECTED).values("order_id", "material_id")
                .annotate(total=Sum("quantity"))):
        taken[(row["order_id"], row["material_id"])] = row["total"]
    rows = []
    for line in (ProductionOrderMaterial.objects.filter(order__business_unit=business_unit, order__status__in=OPEN)
                 .exclude(order_id=exclude).select_related("order", "material__item")
                 .order_by("order__due_date", "order_id", "material__sku")):
        left = line.quantity - taken.get((line.order_id, line.material_id), ZERO)
        if left > 0:
            rows.append((line.order, line.material, left))
    return rows


def stock_check(business_unit, needs, exclude=None):
    """For materials a new BOQ needs: what is free, what other open jobs already need, and what to order."""
    if not needs:
        return []
    others = {}
    for _, material, left in _still_needed(business_unit, exclude=exclude):
        if material.pk in needs:
            others[material.pk] = others.get(material.pk, ZERO) + left
    free = _free_stock(business_unit, list(needs))
    rows = []
    for variant in Variant.objects.filter(pk__in=needs).select_related("item").order_by("sku"):
        available = max(free.get(variant.pk, ZERO) - others.get(variant.pk, ZERO), ZERO)
        short = max(needs[variant.pk] - available, ZERO)
        rows.append({"variant_id": variant.pk, "sku": variant.sku, "name": variant.item.description,
                     "unit": variant.item.get_unit_of_measure_display(), "needed": _f(needs[variant.pk]),
                     "free": _f(free.get(variant.pk, ZERO)), "other_jobs": _f(others.get(variant.pk, ZERO)),
                     "to_order": _f(short), "short": short > 0})
    return rows


def material_shortages(business_unit):
    """Alerts for materials to order: every open job's remaining needs against free stock, earliest job first."""
    needs = _still_needed(business_unit)
    free = _free_stock(business_unit, {material.pk for _, material, _ in needs})
    left = dict(free)
    by_material, by_order = {}, {}
    for order, material, quantity in needs:
        covered = min(left.get(material.pk, ZERO), quantity)
        left[material.pk] = left.get(material.pk, ZERO) - covered
        short = quantity - covered
        row = by_material.setdefault(material.pk, {
            "variant_id": material.pk, "sku": material.sku, "name": material.item.description,
            "unit": material.item.get_unit_of_measure_display(), "needed": ZERO, "free": free.get(material.pk, ZERO),
            "to_order": ZERO, "jobs": []})
        row["needed"] += quantity
        row["to_order"] += short
        if short > 0:
            row["jobs"].append({"job": order.number, "job_id": order.pk, "due_date": order.due_date.isoformat(),
                                "short": _f(short)})
            by_order.setdefault(order.pk, []).append({"sku": material.sku, "name": material.item.description,
                                                      "short": _f(short)})
    rows = [row | {"needed": _f(row["needed"]), "free": _f(row["free"]), "to_order": _f(row["to_order"])}
            for row in by_material.values() if row["to_order"] > 0]
    return {"materials": sorted(rows, key=lambda row: row["sku"]), "by_order": by_order}


def _settings_row(found):
    return {"is_live": found.is_live, "overhead_percent": _f(found.overhead_percent),
            "overrun_percent": _f(found.overrun_percent), "overtime_percent": _f(found.overtime_percent),
            "rates": {workshop: _f(_rate(found, workshop)) for workshop in WORKSHOPS},
            "asset_threshold": _f(found.asset_threshold), "machine_life_months": found.machine_life_months,
            "order_lead_days": found.order_lead_days, "stages_per_person": found.stages_per_person,
            "updated_by": _name(found.updated_by), "updated_at": found.updated_at.isoformat() if found.updated_at else ""}


def _sales_orders(business_unit):
    return (SalesQuote.objects.filter(business_unit=business_unit, status__in=SALES_ORDER_STATUSES, payments__isnull=False)
            .distinct())


def factory_overview(*, actor, business_unit):
    _require_viewer(actor, business_unit)
    found = factory_settings(business_unit.legal_entity)
    since = timezone.now() - datetime.timedelta(days=SHOWN_DAYS)
    orders = [order for order in _orders(business_unit)
              if order.status not in (S.DELIVERED, S.IN_STOCK, S.CANCELLED)
              or (order.finished_at or order.created_at) >= since]
    rank = {S.COST_CHECK: 0, S.WAITING: 1, S.IN_PROGRESS: 2, S.DRAFT: 3, S.STORED: 4, S.ON_THE_WAY: 5,
            S.DELIVERED: 6, S.IN_STOCK: 7, S.CANCELLED: 8}
    orders.sort(key=lambda order: (rank[order.status], order.due_date, order.pk))
    rows = [_order_row(order, actor) for order in orders]
    shortages = material_shortages(business_unit)
    for row in rows:
        row["short_materials"] = shortages["by_order"].get(row["id"], [])
    in_production = [row for row in rows if row["status"] == S.IN_PROGRESS]
    waiting = (sum(1 for row in rows if row["status"] in (S.WAITING, S.COST_CHECK))
               + sum(1 for row in rows for issue in row["issues"]
                     if issue["status"] == ProductionIssue.Status.WAITING or issue["can_receive"])
               + sum(1 for row in rows for purchase in row["purchases"] if purchase["status"] == B.REQUESTED))
    items = [{"variant_id": variant.pk, "sku": variant.sku, "name": variant.item.description}
             for variant in Variant.objects.filter(is_synthetic=True, item__is_active=True, raw_material__isnull=True)
             .select_related("item").order_by("sku")[:500]]
    sales_orders = [{"id": quote.pk, "customer": quote.customer.name, "status_label": quote.get_status_display(),
                     "total": _f(quote.total_quote_value)}
                    for quote in _sales_orders(business_unit).select_related("customer").order_by("-created_at")[:100]]
    people = [person_row(person) for person in factory_people(business_unit)]
    accounts = [{"id": account.pk, "name": account.name, "kind_label": account.get_kind_display()}
                for account in CashAccount.objects.filter(business_unit=business_unit, is_synthetic=True)
                .order_by("kind", "name")] if can_buy(actor, business_unit) else []
    return {
        "unit": {"id": business_unit.pk, "name": business_unit.name}, "today": timezone.localdate().isoformat(),
        "settings": _settings_row(found),
        "workshops": [{"id": value, "label": label} for value, label in Workshop.choices],
        "purposes": [{"id": value, "label": label} for value, label in P.choices],
        "categories": [{"id": value, "label": label} for value, label in RawMaterial.Category.choices],
        "tiles": {
            "in_production": len(in_production), "waiting": waiting,
            "late": sum(1 for row in rows if row["late"]),
            "to_site": sum(1 for row in rows if row["status"] in (S.STORED, S.ON_THE_WAY)),
            "to_order": len(shortages["materials"]),
            "work_in_progress": _f(sum((Decimal(row["actual"]["materials"]) + Decimal(row["actual"]["purchases"])
                                       + Decimal(row["actual"]["labour"]) for row in in_production), ZERO)),
        },
        "orders": rows, "raw_materials": _raw_material_rows(business_unit), "items": items,
        "to_order": shortages["materials"],
        "sales_orders": sales_orders, "people": people, "accounts": accounts,
        "locations": [{"id": location.pk, "code": location.code, "name": location.name, "kind": location.kind}
                      for location in _locations(business_unit)],
        "can_run": can_run(actor, business_unit), "can_approve": can_approve(actor, business_unit),
        "can_approve_cost": can_approve_cost(actor, business_unit), "can_buy": can_buy(actor, business_unit),
        "can_keep_list": can_approve(actor, business_unit) or is_master_admin(actor),
        "is_master_admin": is_master_admin(actor), "policy": POLICY_LABEL,
    }


# Preparing a job.

def _variant(variant_id, field):
    variant = Variant.objects.filter(pk=_int(variant_id), is_synthetic=True).select_related("item").first()
    if variant is None:
        raise InvalidTransaction(f"Choose the {field}.")
    return variant


def _material_lines(value):
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise InvalidTransaction("Send the materials as a list.")
    lines = {}
    for number, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise InvalidTransaction(f"Material line {number} is not readable.")
        material = _variant(raw.get("variant_id"), f"material on line {number}")
        if material.pk in lines:
            raise InvalidTransaction(f"{material.sku} is listed twice. Put it on one line.")
        lines[material.pk] = (material, _quantity(raw.get("quantity"), f"quantity of {material.sku}"))
    if len(lines) > 100:
        raise InvalidTransaction("A job can list at most 100 materials.")
    return list(lines.values())


def _boq_lines(value, business_unit=None):
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise InvalidTransaction("Send the BOQ items as a list.")
    lines = {}
    for number, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise InvalidTransaction(f"BOQ line {number} is not readable.")
        code = str(raw.get("code") or "").strip().upper()
        if not BOQ_CODE.match(code):
            raise InvalidTransaction(f"BOQ line {number}: the code must look like JD-00, JD-01 or JD-120.")
        if code in lines:
            raise InvalidTransaction(f"{code} is listed twice.")
        from .factory_planning import tags_from

        lines[code] = {"code": code, "description": _text(raw.get("description"), f"description of {code}", 500, required=True),
                       "quantity": _quantity(raw.get("quantity"), f"quantity of {code}"),
                       "unit": _text(raw.get("unit"), f"unit of {code}", 20) or "piece",
                       "details": _details(raw.get("details"), code),
                       "area": _text(raw.get("area"), f"area of {code}", 80),
                       "floor": _text(raw.get("floor"), f"floor of {code}", 80),
                       "zone": _text(raw.get("zone"), f"zone of {code}", 80),
                       "materials": _line_materials(raw.get("materials"), code),
                       "tags": tags_from(business_unit, raw.get("tags")) if business_unit else []}
    if len(lines) > MAX_BOQ_LINES:
        raise InvalidTransaction(f"A job can list at most {MAX_BOQ_LINES} BOQ items.")
    return list(lines.values())


def _line_materials(value, code):
    """The materials one JD needs (from the BOQ's Materials sheet), kept to learn from and to check stock."""
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > 50:
        raise InvalidTransaction(f"The materials of {code} are not readable.")
    kept = {}
    for raw in value:
        if not isinstance(raw, dict):
            raise InvalidTransaction(f"The materials of {code} are not readable.")
        material = _variant(raw.get("variant_id"), f"material of {code}")
        quantity = _quantity(raw.get("quantity"), f"quantity of {material.sku} for {code}")
        found = kept.setdefault(material.pk, {"variant_id": material.pk, "sku": material.sku, "quantity": ZERO})
        found["quantity"] += quantity
    return [row | {"quantity": _f(row["quantity"])} for row in kept.values()]


def _details(value, code):
    """Extra BOQ columns (size, finish, area, remarks...) kept as short heading -> text pairs."""
    if value in (None, ""):
        return {}
    if not isinstance(value, dict) or len(value) > 20:
        raise InvalidTransaction(f"The extra details of {code} are not readable.")
    kept = {}
    for heading, text in value.items():
        heading, text = str(heading).strip()[:60], str(text if text is not None else "").strip()[:300]
        if heading and text:
            kept[heading] = text
    return kept


def _job_fields(business_unit, data, order=None):
    purpose = data.get("purpose")
    if purpose not in P.values:
        raise InvalidTransaction("Choose who the job is for: a fit-out project, a studio client or the showroom.")
    project = order.project if order is not None and order.project_id else None
    if purpose == P.FITOUT and project is None and data.get("project_id") not in (None, ""):
        from .projects import project_for_factory

        project = project_for_factory(business_unit, _int(data.get("project_id")))
    fields = {"purpose": purpose, "quote": None, "variant": None, "quantity": None, "project": None,
              "project_name": _text(data.get("project_name"), "project name", 120),
              "design_reference": _text(data.get("design_reference"), "design package reference", 120),
              "due_date": _day(data.get("due_date"), "date it is needed"),
              "note": _text(data.get("note"), "note", 200)}
    if purpose == P.SHOWROOM:
        fields["variant"] = _variant(data.get("variant_id"), "showroom piece to make")
        fields["quantity"] = _quantity(data.get("quantity"), "quantity")
    elif purpose == P.FITOUT and project is not None:
        # A job sent from Projects & Fit-Out: the project is the client's signed contract.
        fields["project"], fields["project_name"] = project, project.name
    else:
        quote = _sales_orders(business_unit).filter(pk=_int(data.get("quote_id"))).select_related("customer").first()
        if quote is None:
            raise InvalidTransaction("Choose the client's signed sales order in this unit. A job starts only after the "
                                     "sales order is approved and the client has paid.")
        fields["quote"] = quote
    return fields


def _apply_plan(order, data):
    from .factory_planning import ensure_stages, snapshot

    boq = _boq_lines(data.get("boq"), order.business_unit)
    if order.purpose == P.SHOWROOM and not boq:
        # A showroom piece is planned as one item, so it shows on the schedule too.
        boq = [{"code": "JD-00", "description": f"{order.variant.sku} {order.variant.item.description}"[:500],
                "quantity": order.quantity, "unit": "piece", "details": {}, "area": "Showroom", "floor": "", "zone": "",
                "materials": [], "tags": []}]
    if order.purpose != P.SHOWROOM and not boq:
        raise InvalidTransaction("List the BOQ items (JD-00, JD-01...) the job makes.")
    lines = _material_lines(data.get("materials"))
    given = data.get("planned_hours") if isinstance(data.get("planned_hours"), dict) else {}
    for workshop in WORKSHOPS:
        setattr(order, f"planned_hours_{workshop}", _number(given.get(workshop), f"{Workshop(workshop).label} hours"))
    order.save()
    kept = snapshot(order)
    order.boq_lines.all().delete()
    made = ProductionBoqLine.objects.bulk_create([
        ProductionBoqLine(order=order, **{key: value for key, value in line.items() if key != "tags"}) for line in boq])
    for row, line in zip(made, boq):
        if line["tags"]:
            row.tags.set(line["tags"])
    ensure_stages(order, kept)
    order.materials.all().delete()
    for material, quantity in lines:
        ProductionOrderMaterial.objects.create(order=order, material=material, quantity=quantity,
                                               unit_cost=average_cost(order.business_unit, material))


@transaction.atomic
def create_order(*, actor, business_unit, data):
    _require(actor, business_unit, can_run, RUN)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the job's details.")
    fields = _job_fields(business_unit, data)
    if fields["due_date"] < timezone.localdate():
        raise InvalidTransaction("The date it is needed cannot be in the past.")
    found = FactorySettings.objects.select_for_update().get(pk=factory_settings(business_unit.legal_entity).pk)
    number = f"JOB-{found.next_number:04d}"
    found.next_number += 1
    found.save(update_fields=["next_number"])
    order = ProductionOrder.objects.create(business_unit=business_unit, number=number, created_by=actor, **fields)
    _apply_plan(order, data)
    return _order_row(_orders(business_unit).get(pk=order.pk), actor)


def _load(business_unit, order_id):
    order = (ProductionOrder.objects.select_for_update(of=("self",)).filter(pk=_int(order_id), business_unit=business_unit)
             .select_related("business_unit__legal_entity", "variant", "finish_location").first())
    if order is None:
        raise InvalidTransaction("That job was not found.")
    return order


@transaction.atomic
def update_order(*, actor, business_unit, order_id, data):
    _require(actor, business_unit, can_run, RUN)
    order = _load(business_unit, order_id)
    if order.status != S.DRAFT:
        raise InvalidTransaction(f"{order.number} can be changed only while it is being prepared.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the job's details.")
    for key, value in _job_fields(business_unit, data, order).items():
        setattr(order, key, value)
    _apply_plan(order, data)
    return _order_row(_orders(business_unit).get(pk=order.pk), actor)


# Posting.

def _post(order, key, payload, description, lines, day):
    """Post a balanced factory journal; the caller completes the returned operation once its own records are saved."""
    operation, _ = _begin_idempotent_operation(key, "factory", payload)
    entry = _post_journal(legal_entity=order.business_unit.legal_entity, business_unit=order.business_unit,
                          idempotency=operation, kind=JournalEntry.Kind.FACTORY,
                          description=f"TEST ONLY: {order.number} {description}"[:240], lines=lines, document_date=day)
    return operation, entry


def _take_from_stock(issue, order, location):
    balance = (StockBalance.objects.select_for_update()
               .filter(business_unit=order.business_unit, variant=issue.material, location=location).first())
    free = _free(balance) if balance else ZERO
    if balance is None or free < issue.quantity:
        raise InvalidTransaction(f"Only {_f(free)} of {issue.material.sku} is free at {location.code}; "
                                 "the rest is reserved or held.")
    value = balance.on_hand_value if issue.quantity == balance.on_hand_quantity else \
        _round(balance.on_hand_value * issue.quantity / balance.on_hand_quantity)
    memo = f"{issue.quantity} x {issue.material.sku} from {location.code}"
    operation, _ = _begin_idempotent_operation(f"factory-issue-{issue.pk}", "factory", {"issue": issue.pk})
    if value > 0:
        issue.journal_entry = _post_journal(
            legal_entity=order.business_unit.legal_entity, business_unit=order.business_unit, idempotency=operation,
            kind=JournalEntry.Kind.FACTORY, description=f"TEST ONLY: {order.number} materials taken"[:240],
            lines=[("TEST_WIP", value, ZERO, memo), ("TEST_INVENTORY", ZERO, value, memo)])
    StockMovement.objects.create(balance=balance, idempotency=operation, kind=StockMovement.Kind.PRODUCTION_USE,
                                 quantity_delta=-issue.quantity, value_delta=-value,
                                 memo=f"TEST ONLY: taken for factory job {order.number}"[:240])
    balance.on_hand_quantity -= issue.quantity
    balance.on_hand_value -= value
    balance.updated_at = timezone.now()
    balance.save(update_fields=["on_hand_quantity", "on_hand_value", "updated_at"])
    _complete(operation, {"issue": issue.pk})
    issue.value = value
    issue.status = ProductionIssue.Status.ISSUED


def _finish(order):
    """Move the job's full cost out of production: into showroom stock, or onto finished jobs until the client receives them."""
    business_unit = order.business_unit
    total, overhead = order.actual_total, order.actual_overhead
    lines = []
    if overhead > 0:
        lines += [("TEST_WIP", overhead, ZERO, "factory overhead"), ("TEST_FACTORY_OVERHEAD", ZERO, overhead, "factory overhead")]
    if order.purpose == P.SHOWROOM:
        _validate_bucket(business_unit, order.variant, order.finish_location)
        memo = f"{order.quantity} x {order.variant.sku} into {order.finish_location.code}"
        lines += [("TEST_INVENTORY", total, ZERO, memo), ("TEST_WIP", ZERO, total, memo)]
    else:
        where = order.finish_location.code if order.destination == D.WAREHOUSE else f"the client's site ({order.finish_location.code})"
        memo = f"finished, to {where}"
        lines += [("TEST_FINISHED_JOBS", total, ZERO, memo), ("TEST_WIP", ZERO, total, memo)]
    operation, entry = _post(order, f"factory-finish-{order.pk}", {"order": order.pk}, "finished", lines,
                             timezone.localdate())
    if order.purpose == P.SHOWROOM:
        buckets = _get_or_create_balances(business_unit, [(order.variant, order.finish_location)])
        balance = buckets[(business_unit.pk, order.variant_id, order.finish_location_id)]
        StockMovement.objects.create(balance=balance, idempotency=operation, kind=StockMovement.Kind.PRODUCTION_IN,
                                     quantity_delta=order.quantity, value_delta=total,
                                     memo=f"TEST ONLY: made in factory job {order.number}"[:240])
        balance.on_hand_quantity += order.quantity
        balance.on_hand_value += total
        balance.updated_at = timezone.now()
        balance.save(update_fields=["on_hand_quantity", "on_hand_value", "updated_at"])
        order.status = S.IN_STOCK
    else:
        order.status = S.STORED if order.destination == D.WAREHOUSE else S.ON_THE_WAY
    _complete(operation, {"order": order.pk, "journal_entry_id": entry.pk})
    order.finish_entry = entry


def _choose_destination(order, business_unit, destination, location_id):
    if order.purpose == P.SHOWROOM:
        destination = D.WAREHOUSE
    elif destination not in D.values:
        raise InvalidTransaction("Choose whether the finished work is stored in the warehouse or sent to the client's site.")
    if destination == D.WAREHOUSE:
        location = _locations(business_unit).exclude(kind=Location.Kind.FACTORY).filter(pk=_int(location_id)).first()
        if location is None:
            raise InvalidTransaction("Choose the warehouse (or showroom) where the finished work is stored.")
    else:
        location = _site_location(order)
    order.destination, order.finish_location = destination, location


@transaction.atomic
def order_action(*, actor, business_unit, order_id, action, note="", destination=None, location_id=None, date=None):
    _require_user(actor, business_unit)
    order = _load(business_unit, order_id)
    now = timezone.now()
    if action == "submit":
        _require(actor, business_unit, can_run, RUN)
        if order.status != S.DRAFT:
            raise InvalidTransaction(f"{order.number} is already {order.get_status_display().lower()}.")
        if not order.materials.exists() and not any(_planned_hours(order).values()):
            raise InvalidTransaction("List the materials or the workshop hours the job needs before sending it.")
        found = factory_settings(business_unit.legal_entity)
        for line in order.materials.select_related("material"):
            line.unit_cost = average_cost(business_unit, line.material)
            line.save(update_fields=["unit_cost"])
        order.estimate_materials, order.estimate_labour, order.estimate_overhead = _estimate(order, found)
        order.overhead_percent, order.overrun_percent = found.overhead_percent, found.overrun_percent
        order.status, order.submitted_at, order.decision_note = S.WAITING, now, ""
    elif action in ("approve", "reject"):
        _require(actor, business_unit, can_approve, APPROVE)
        if order.status != S.WAITING:
            raise InvalidTransaction(f"{order.number} is not waiting for approval.")
        if order.created_by_id == actor.pk:
            raise WorkflowPermissionDenied("You opened this job, so another Factory Manager must decide it.")
        if action == "approve":
            order.status, order.approved_by, order.approved_at = S.IN_PROGRESS, actor, now
            order.decision_note = _text(note, "note", 200)
        else:
            order.status, order.decision_note = S.DRAFT, _text(note, "reason", 200, required=True)
    elif action == "finish":
        _require(actor, business_unit, can_run, RUN)
        if order.status != S.IN_PROGRESS:
            raise InvalidTransaction(f"Only a job in production can be finished; {order.number} is "
                                     f"{order.get_status_display().lower()}.")
        if order.issues.filter(status=ProductionIssue.Status.WAITING).exists():
            raise InvalidTransaction("A material request is still waiting for the Factory Manager. Decide it first.")
        if order.issues.filter(status=ProductionIssue.Status.WITH_WAREHOUSE).exists():
            raise InvalidTransaction("Some materials are still with the warehouse. Receive them first.")
        if order.purchases.filter(status__in=(B.REQUESTED, B.APPROVED)).exists():
            raise InvalidTransaction("A special purchase is still open on this job. Buy it or cancel it first.")
        materials, bought, labour = _actual(order)
        if materials + bought + labour <= 0:
            raise InvalidTransaction("No materials, purchases or hours are recorded on this job yet.")
        _choose_destination(order, business_unit, destination, location_id)
        order.actual_overhead = _round((materials + bought + labour) * order.overhead_percent / HUNDRED)
        order.actual_total = materials + bought + labour + order.actual_overhead
        order.finished_by, order.finished_at = actor, now
        if order.actual_total > _limit(order)[1]:
            order.status = S.COST_CHECK
        else:
            _finish(order)
    elif action in ("approve_cost", "reject_cost"):
        _require(actor, business_unit, can_approve_cost, APPROVE_COST)
        if order.status != S.COST_CHECK:
            raise InvalidTransaction(f"{order.number} is not waiting for a cost decision.")
        if order.finished_by_id == actor.pk:
            raise WorkflowPermissionDenied("You finished this job, so someone else in Finance must approve its cost.")
        if action == "approve_cost":
            order.cost_approved_by, order.cost_approved_at = actor, now
            order.decision_note = _text(note, "note", 200)
            _finish(order)
        else:
            order.decision_note = _text(note, "reason", 200, required=True)
            order.status, order.finished_by, order.finished_at = S.IN_PROGRESS, None, None
            order.actual_overhead = order.actual_total = ZERO
    elif action == "send":
        _require(actor, business_unit, can_run, RUN)
        if order.status != S.STORED:
            raise InvalidTransaction(f"{order.number} is not stored in the warehouse.")
        order.status, order.finish_location = S.ON_THE_WAY, _site_location(order)
    elif action == "received":
        _require(actor, business_unit, can_run, RUN)
        if order.status != S.ON_THE_WAY:
            raise InvalidTransaction(f"{order.number} is not on its way to the client's site.")
        day = _past_day(date or timezone.localdate().isoformat(), "date the client received it")
        if day < timezone.localtime(order.finished_at).date():
            raise InvalidTransaction("The client cannot receive it before it was finished.")
        memo = "received at the client's site"
        operation, order.received_entry = _post(order, f"factory-received-{order.pk}", {"order": order.pk}, memo,
                                                [("TEST_COGS", order.actual_total, ZERO, memo),
                                                 ("TEST_FINISHED_JOBS", ZERO, order.actual_total, memo)], day)
        _complete(operation, {"order": order.pk})
        order.status, order.received_by, order.received_on = S.DELIVERED, actor, day
    elif action == "cancel":
        _require(actor, business_unit, lambda user, unit: can_run(user, unit) or can_approve(user, unit), RUN)
        if order.status not in OPEN:
            raise InvalidTransaction(f"{order.number} is {order.get_status_display().lower()} and cannot be cancelled.")
        if (order.issues.filter(status__in=(ProductionIssue.Status.ISSUED, ProductionIssue.Status.WITH_WAREHOUSE))
                .exists() or order.labour.exists()
                or order.purchases.filter(status=B.BOUGHT).exists()):
            raise InvalidTransaction("Materials, purchases or hours are already booked to this job (or the warehouse "
                                     "is preparing materials for it), so it cannot be cancelled. Finish it instead.")
        reason = _text(note, "reason", 200, required=True)
        order.issues.filter(status=ProductionIssue.Status.WAITING).update(
            status=ProductionIssue.Status.REJECTED, decided_by=actor, decided_at=now, note="Job cancelled")
        order.material_requests.filter(status=MR.MANAGER).update(status=MR.REJECTED, rejected_by=actor,
                                                                 rejection_reason="Job cancelled")
        order.purchases.filter(status__in=(B.REQUESTED, B.APPROVED)).update(status=B.CANCELLED, decision_note="Job cancelled")
        order.status, order.decision_note = S.CANCELLED, reason
    else:
        raise InvalidTransaction("Choose submit, approve, reject, finish, approve_cost, reject_cost, send, received or cancel.")
    order.save()
    return _order_row(_orders(business_unit).get(pk=order.pk), actor)


def _ask_warehouse(request, actor):
    """The Factory Manager's approval passes the slip to the warehouse as a normal transfer request.

    The warehouse admin approves it on the warehouse's own transfer screen (a different person from the one who asked),
    then schedules, dispatches and receives it exactly like a furniture move; on receipt the materials are used on the
    job (see use_received_materials). The destination is always a factory location.
    """
    order = request.order
    business_unit = order.business_unit
    issues = list(request.issues.select_for_update().select_related("material").order_by("pk"))
    for issue in issues:
        _validate_bucket(business_unit, issue.material, request.store)
        _validate_bucket(business_unit, issue.material, request.deliver_to)
    operation, _ = _begin_idempotent_operation(f"factory-material-request-{request.pk}", "transfer_request",
                                               {"material_request": request.pk})
    transfer = StockTransferRequest.objects.create(
        idempotency=operation, business_unit=business_unit, source_location=request.store,
        destination_location=request.deliver_to, status=StockTransferRequest.Status.SUBMITTED,
        requested_by=request.requested_by,
        note=f"Factory material request {request.number} for {order.number}, approved by {_name(actor)}"[:240])
    for issue in issues:
        StockTransferRequestLine.objects.create(request=transfer, variant=issue.material, quantity=issue.quantity)
        issue.transfer_request, issue.status = transfer, ProductionIssue.Status.WITH_WAREHOUSE
        issue.decided_by, issue.decided_at = actor, request.manager_at
        issue.save(update_fields=["transfer_request", "status", "decided_by", "decided_at"])
    StockTransferRequestEvent.objects.create(request=transfer, actor=actor, from_status="",
                                             to_status=transfer.status, note=transfer.note)
    _complete(operation, {"request_id": transfer.pk, "status": transfer.status})
    request.transfer_request, request.status = transfer, MR.WAREHOUSE
    request.save(update_fields=["transfer_request", "status"])
    return transfer


def _check_free(business_unit, material, location, quantity):
    balance = StockBalance.objects.filter(business_unit=business_unit, variant=material, location=location).first()
    free = _free(balance) if balance else ZERO
    if free < quantity:
        raise InvalidTransaction(f"Only {_f(free)} of {material.sku} is free at {location.code}.")


@transaction.atomic
def take_materials(*, actor, business_unit, order_id, data):
    """Ask for materials for a job.

    From the raw materials store it becomes a request slip: the Factory Manager approves it, then the warehouse admin,
    then it is printed with the approvals and the warehouse delivers to the chosen factory location. From a factory
    location (leftovers already there) the materials are used straight away. More than the job's list needs a reason.
    """
    _require(actor, business_unit, can_run, RUN)
    order = _load(business_unit, order_id)
    if order.status != S.IN_PROGRESS:
        raise InvalidTransaction("Materials can be asked for only for a job in production.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the materials, where they come from and the quantities.")
    location = _locations(business_unit).filter(pk=_int(data.get("location_id"))).first()
    if location is None or location.kind not in (Location.Kind.WAREHOUSE, Location.Kind.FACTORY):
        raise InvalidTransaction("Choose the warehouse store (or factory location) the materials come from.")
    from_factory = location.kind == Location.Kind.FACTORY
    deliver_to = None
    if not from_factory:
        deliver_to = _factory_places(business_unit).filter(pk=_int(data.get("deliver_to_id"))).first()
        if deliver_to is None:
            raise InvalidTransaction("Choose the factory location the warehouse delivers to.")
    raw = data.get("lines")
    if raw is None:
        raw = [{"variant_id": data.get("variant_id"), "quantity": data.get("quantity")}]
    if not isinstance(raw, list) or not 1 <= len(raw) <= 50:
        raise InvalidTransaction("Ask for between 1 and 50 materials at a time.")
    note = _text(data.get("note"), "note", 200)
    lines, seen = [], set()
    for number, line in enumerate(raw, start=1):
        if not isinstance(line, dict):
            raise InvalidTransaction(f"Material line {number} is not readable.")
        material = _variant(line.get("variant_id"), f"material on line {number}")
        if material.pk in seen:
            raise InvalidTransaction(f"{material.sku} is listed twice. Put it on one line.")
        seen.add(material.pk)
        quantity = _quantity(line.get("quantity"), f"quantity of {material.sku}")
        _check_free(business_unit, material, location, quantity)
        planned = order.materials.filter(material=material).aggregate(total=Sum("quantity"))["total"] or ZERO
        taken = (order.issues.filter(material=material).exclude(status=ProductionIssue.Status.REJECTED)
                 .aggregate(total=Sum("quantity"))["total"] or ZERO)
        beyond = taken + quantity > planned
        if beyond and not note:
            raise InvalidTransaction(f"This is more {material.sku} than the job listed ({_f(planned)}, {_f(taken)} "
                                     "already asked for). Say why, for the Factory Manager.")
        lines.append((material, quantity, beyond))
    request = None
    if not from_factory:
        count = FactoryMaterialRequest.objects.filter(order=order).count()
        request = FactoryMaterialRequest.objects.create(
            order=order, number=f"{order.number}-M{count + 1:02d}", store=location, deliver_to=deliver_to,
            note=note, requested_by=actor)
    for material, quantity, beyond in lines:
        issue = ProductionIssue.objects.create(order=order, material=material, location=location, deliver_to=deliver_to,
                                               quantity=quantity, beyond_plan=beyond, note=note, requested_by=actor,
                                               status=ProductionIssue.Status.WAITING, request=request)
        if from_factory and not beyond:
            _take_from_stock(issue, order, location)
            issue.save(update_fields=["value", "status", "journal_entry"])
    return _order_row(_orders(business_unit).get(pk=order.pk), actor)


@transaction.atomic
def decide_material_request(*, actor, business_unit, request_id, action, note=""):
    """The Factory Manager checks a request slip and approves it (on to the warehouse admin) or rejects it."""
    _require(actor, business_unit, can_approve, APPROVE)
    request = (FactoryMaterialRequest.objects.select_for_update(of=("self",))
               .filter(pk=_int(request_id), order__business_unit=business_unit).select_related("order").first())
    if request is None:
        raise InvalidTransaction("That material request was not found.")
    if request.status != MR.MANAGER:
        raise InvalidTransaction(f"{request.number} is already decided.")
    if request.requested_by_id == actor.pk:
        raise WorkflowPermissionDenied("You asked for these materials, so another Factory Manager must decide.")
    order = _load(business_unit, request.order_id)
    if order.status != S.IN_PROGRESS:
        raise InvalidTransaction(f"{order.number} is no longer in production.")
    now = timezone.now()
    if action == "approve":
        for issue in request.issues.select_related("material"):
            _check_free(business_unit, issue.material, request.store, issue.quantity)
        request.manager_by, request.manager_at = actor, now
        request.save(update_fields=["manager_by", "manager_at"])
        _ask_warehouse(request, actor)
    elif action == "reject":
        reason = _text(note, "reason for rejecting it", 120, required=True)
        _reject_request(request, actor, f"Factory Manager: {reason}", now)
    else:
        raise InvalidTransaction("Choose approve or reject.")
    return _order_row(_orders(business_unit).get(pk=order.pk), actor)


def _reject_request(request, actor, reason, now):
    request.status, request.rejected_by, request.rejection_reason = MR.REJECTED, actor, reason[:200]
    request.save(update_fields=["status", "rejected_by", "rejection_reason"])
    for issue in request.issues.exclude(status=ProductionIssue.Status.REJECTED):
        issue.status, issue.decided_by, issue.decided_at = ProductionIssue.Status.REJECTED, actor, now
        issue.note = f"{issue.note} · Rejected: {reason}"[:200] if issue.note else f"Rejected: {reason}"[:200]
        issue.save(update_fields=["status", "decided_by", "decided_at", "note"])


def warehouse_decided(transfer_request, actor, approve, reason):
    """Called when the warehouse admin approves or rejects a factory request on the transfer screen."""
    request = FactoryMaterialRequest.objects.select_for_update().filter(transfer_request=transfer_request).first()
    if request is None or request.status != MR.WAREHOUSE:
        return
    if approve:
        request.status = MR.APPROVED
        request.save(update_fields=["status"])
    else:
        _reject_request(request, actor, f"Warehouse: {reason}", timezone.now())


@transaction.atomic
def decide_materials(*, actor, business_unit, issue_id, action, note=""):
    """Decide a beyond-plan line taken from a factory location (leftovers): used at once when approved."""
    _require(actor, business_unit, can_approve, APPROVE)
    issue = (ProductionIssue.objects.select_for_update(of=("self",))
             .filter(pk=_int(issue_id), order__business_unit=business_unit)
             .select_related("material", "location", "deliver_to").first())
    if issue is None:
        raise InvalidTransaction("That request was not found.")
    if issue.request_id:
        return decide_material_request(actor=actor, business_unit=business_unit, request_id=issue.request_id,
                                       action=action, note=note)
    if issue.status != ProductionIssue.Status.WAITING:
        raise InvalidTransaction("That request is already decided.")
    if issue.requested_by_id == actor.pk:
        raise WorkflowPermissionDenied("You asked for these materials, so another Factory Manager must decide.")
    order = _load(business_unit, issue.order_id)
    if order.status != S.IN_PROGRESS:
        raise InvalidTransaction(f"{order.number} is no longer in production.")
    issue.decided_by, issue.decided_at = actor, timezone.now()
    if action == "approve":
        _check_free(business_unit, issue.material, issue.location, issue.quantity)
        _take_from_stock(issue, order, issue.location)
    elif action == "reject":
        reason = _text(note, "reason for rejecting it", 120, required=True)
        issue.status = ProductionIssue.Status.REJECTED
        issue.note = f"{issue.note} · Rejected: {reason}"[:200] if issue.note else f"Rejected: {reason}"
    else:
        raise InvalidTransaction("Choose approve or reject.")
    issue.save()
    return _order_row(_orders(business_unit).get(pk=order.pk), actor)


def use_received_materials(transfer_request):
    """Called when the warehouse's delivery reaches the factory: the materials are used on their job at once."""
    issues = list(ProductionIssue.objects.select_for_update()
                  .filter(transfer_request=transfer_request, status=ProductionIssue.Status.WITH_WAREHOUSE)
                  .select_related("material", "order").order_by("pk"))
    for issue in issues:
        order = _load(issue.order.business_unit, issue.order_id)
        _take_from_stock(issue, order, transfer_request.destination_location)
        issue.save(update_fields=["value", "status", "journal_entry"])
    FactoryMaterialRequest.objects.filter(transfer_request=transfer_request).update(status=MR.DELIVERED)


def _slip_row(request, actor):
    """A material request slip, as shown on screen and printed for the warehouse team and the file."""
    transfer = request.transfer_request
    unit = request.order.business_unit
    warehouse_ok = transfer is not None and transfer.status not in (StockTransferRequest.Status.SUBMITTED,
                                                                   StockTransferRequest.Status.REJECTED)
    return {
        "id": request.pk, "number": request.number, "status": request.status, "status_label": request.get_status_display(),
        "job": request.order.number, "project": request.order.project_name
        or (request.order.quote.customer.name if request.order.quote_id else ""),
        "store": f"{request.store.code} {request.store.name}", "deliver_to": f"{request.deliver_to.code} {request.deliver_to.name}",
        "note": request.note, "rejection_reason": request.rejection_reason,
        "lines": [{"sku": issue.material.sku, "name": issue.material.item.description,
                   "unit": issue.material.item.get_unit_of_measure_display(), "quantity": _f(issue.quantity),
                   "beyond_plan": issue.beyond_plan} for issue in request.issues.all()],
        "approvals": [
            {"step": "Requested by (factory admin)", "name": _name(request.requested_by),
             "at": request.requested_at.isoformat()},
            {"step": "Approved by the Factory Manager", "name": _name(request.manager_by),
             "at": request.manager_at.isoformat() if request.manager_at else ""},
            {"step": "Approved by the warehouse admin", "name": _name(transfer.decided_by) if warehouse_ok else "",
             "at": transfer.decided_at.isoformat() if warehouse_ok and transfer.decided_at else ""},
        ],
        "transfer_id": transfer.pk if transfer else None,
        "transfer_status": transfer.get_status_display() if transfer else "",
        "can_decide": request.status == MR.MANAGER and can_approve(actor, unit) and request.requested_by_id != actor.pk,
        "can_print": request.status in (MR.APPROVED, MR.DELIVERED), "printed_count": request.printed_count,
    }


@transaction.atomic
def material_request_slip(*, actor, business_unit, request_id, printing=False):
    """The slip to print once both approvals are in; each print is counted so the filed copy can be traced."""
    _require_viewer(actor, business_unit)
    request = (FactoryMaterialRequest.objects.select_for_update(of=("self",)) if printing
               else FactoryMaterialRequest.objects).filter(
        pk=_int(request_id), order__business_unit=business_unit).first()
    if request is None:
        raise InvalidTransaction("That material request was not found.")
    if printing:
        if request.status not in (MR.APPROVED, MR.DELIVERED):
            raise InvalidTransaction(f"{request.number} can be printed once the Factory Manager and the warehouse "
                                     "admin have both approved it.")
        request.printed_count += 1
        request.save(update_fields=["printed_count"])
    return _slip_row(_slips(business_unit).get(pk=request.pk), actor) | {
        "company": business_unit.legal_entity.name, "unit": business_unit.name,
        "printed_at": timezone.now().isoformat(), "printed_by": _name(actor)}


def _slips(business_unit):
    return (FactoryMaterialRequest.objects.filter(order__business_unit=business_unit)
            .select_related("order__quote__customer", "order__business_unit", "store", "deliver_to", "requested_by",
                            "manager_by", "transfer_request__decided_by")
            .prefetch_related("issues__material__item"))


@transaction.atomic
def receive_materials(*, actor, business_unit, request_id):
    """The factory confirms the warehouse's delivery arrived (the warehouse can also confirm it on its own screen)."""
    from .stock_transfer_requests import receive_locked_request

    _require(actor, business_unit, can_run, RUN)
    request = (StockTransferRequest.objects.select_for_update()
               .filter(pk=_int(request_id), business_unit=business_unit,
                       pk__in=ProductionIssue.objects.values("transfer_request")).first())
    if request is None:
        raise InvalidTransaction("That delivery was not found.")
    if request.status != StockTransferRequest.Status.IN_TRANSIT:
        raise InvalidTransaction("The warehouse has not sent these materials yet.")
    receive_locked_request(request, actor, f"factory-receive-{request.pk}")
    order = request.factory_issues.first().order
    return _order_row(_orders(business_unit).get(pk=order.pk), actor)


@transaction.atomic
def record_hours(*, actor, business_unit, order_id, data):
    _require(actor, business_unit, can_run, RUN)
    order = _load(business_unit, order_id)
    if order.status != S.IN_PROGRESS:
        raise InvalidTransaction("Hours can be recorded only for a job in production.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the workshop, the person, the date and the hours.")
    workshop = data.get("workshop")
    if workshop not in WORKSHOPS:
        raise InvalidTransaction("Choose the workshop.")
    employee = factory_people(business_unit).filter(pk=_int(data.get("employee_id"))).first()
    if employee is None:
        raise InvalidTransaction("Choose who did the work, from the factory's staff.")
    line = None
    if data.get("line_id") not in (None, ""):
        line = order.boq_lines.filter(pk=_int(data.get("line_id"))).first()
        if line is None:
            raise InvalidTransaction(f"Choose a BOQ item (JD) of {order.number}.")
    day = _past_day(data.get("work_date"), "date worked")
    started = timezone.localtime(order.approved_at).date()
    if day < started:
        raise InvalidTransaction(f"{order.number} was approved on {started}; hours before that cannot be booked to it.")
    hours = _number(data.get("hours"), "hours", allow_zero=False, limit=Decimal("16"))
    overtime = data.get("overtime", False)
    if type(overtime) is not bool:
        raise InvalidTransaction("Say whether the hours are overtime.")
    already = ProductionLabour.objects.filter(employee=employee, work_date=day).aggregate(total=Sum("hours"))["total"] or ZERO
    if already + hours > 16:
        raise InvalidTransaction(f"{employee.full_name} already has {_f(already)} hours on {day}; at most 16 a day.")
    found = factory_settings(business_unit.legal_entity)
    rate = _rate(found, workshop)
    if overtime:
        rate = _round(rate * found.overtime_percent / HUNDRED)
    cost = _round(hours * rate)
    row = ProductionLabour.objects.create(order=order, workshop=workshop, employee=employee, work_date=day, hours=hours,
                                          overtime=overtime, rate=rate, cost=cost, recorded_by=actor, line=line)
    if cost > 0:
        memo = f"{hours} h {Workshop(workshop).label}{' overtime' if overtime else ''}, {employee.full_name}"
        operation, row.journal_entry = _post(order, f"factory-hours-{row.pk}", {"labour": row.pk}, "workshop hours",
                                             [("TEST_WIP", cost, ZERO, memo), ("TEST_FACTORY_LABOUR", ZERO, cost, memo)], day)
        _complete(operation, {"labour": row.pk})
        row.save(update_fields=["journal_entry"])
    return _order_row(_orders(business_unit).get(pk=order.pk), actor)


# Special purchases.

@transaction.atomic
def request_purchase(*, actor, business_unit, order_id, data):
    """Ask for something the warehouse does not hold, bought for this job only."""
    _require(actor, business_unit, can_run, RUN)
    order = _load(business_unit, order_id)
    if order.status != S.IN_PROGRESS:
        raise InvalidTransaction("Special purchases can be requested only for a job in production.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send what is needed, how many, the estimated cost and why.")
    FactoryPurchase.objects.create(
        order=order, description=_text(data.get("description"), "description of what is needed", 200, required=True),
        quantity=_quantity(data.get("quantity"), "quantity"),
        estimated_cost=_number(data.get("estimated_cost"), "estimated cost (QAR)", allow_zero=False,
                               limit=Decimal("1000000")),
        supplier_name=_text(data.get("supplier_name"), "supplier", 120),
        reason=_text(data.get("reason"), "reason it is not taken from the warehouse", 200, required=True),
        requested_by=actor)
    return _order_row(_orders(business_unit).get(pk=order.pk), actor)


@transaction.atomic
def purchase_action(*, actor, business_unit, purchase_id, data):
    _require_user(actor, business_unit)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send what to do.")
    purchase = (FactoryPurchase.objects.select_for_update()
                .filter(pk=_int(purchase_id), order__business_unit=business_unit).first())
    if purchase is None:
        raise InvalidTransaction("That special purchase was not found.")
    order = _load(business_unit, purchase.order_id)
    action, now = data.get("action"), timezone.now()
    if action in ("approve", "reject"):
        _require(actor, business_unit, can_approve, APPROVE)
        if purchase.status != B.REQUESTED:
            raise InvalidTransaction("That purchase is not waiting for approval.")
        if purchase.requested_by_id == actor.pk:
            raise WorkflowPermissionDenied("You asked for this purchase, so another Factory Manager must decide it.")
        purchase.status = B.APPROVED if action == "approve" else B.REJECTED
        purchase.decision_note = _text(data.get("note"), "reason", 200, required=action == "reject")
        purchase.decided_by, purchase.decided_at = actor, now
    elif action == "buy":
        _require(actor, business_unit, can_buy, BUY)
        if purchase.status != B.APPROVED:
            raise InvalidTransaction("Only an approved purchase can be bought.")
        if order.status != S.IN_PROGRESS:
            raise InvalidTransaction(f"{order.number} is no longer in production.")
        account = _cash_account(business_unit, _int(data.get("account_id")), "cash box or card it was paid from")
        day = _past_day(data.get("date"), "date bought")
        if day < timezone.localtime(purchase.decided_at).date():
            raise InvalidTransaction("It cannot be bought before it was approved.")
        if day < account.opening_date:
            raise InvalidTransaction(f"{account.name} opened on {account.opening_date.isoformat()}, after this date.")
        if _reconciled(account, day):
            raise InvalidTransaction(f"{day:%B %Y} is reconciled and approved for {account.name}. Use a later date.")
        cost = _number(data.get("actual_cost"), "amount paid (QAR)", allow_zero=False, limit=Decimal("1000000"))
        purchase.receipt_reference = _text(data.get("receipt_reference"), "receipt number", 60, required=True)
        memo = purchase.description[:120]
        operation, purchase.journal_entry = _post(order, f"factory-purchase-{purchase.pk}", {"purchase": purchase.pk},
                                                  "special purchase", [("TEST_WIP", cost, ZERO, memo),
                                                                       ("TEST_CASH", ZERO, cost, memo)], day)
        CashLineAssignment.objects.create(journal_line=purchase.journal_entry.lines.get(credit__gt=0), account=account,
                                          assigned_by=actor)
        _complete(operation, {"purchase": purchase.pk})
        purchase.status, purchase.actual_cost, purchase.paid_from = B.BOUGHT, cost, account
        purchase.bought_by, purchase.bought_on = actor, day
    elif action == "cancel":
        _require(actor, business_unit, lambda user, unit: can_run(user, unit) or can_approve(user, unit), RUN)
        if purchase.status not in (B.REQUESTED, B.APPROVED):
            raise InvalidTransaction("Only a purchase not yet bought can be cancelled.")
        purchase.status, purchase.decision_note = B.CANCELLED, _text(data.get("note"), "reason", 200, required=True)
    else:
        raise InvalidTransaction("Choose approve, reject, buy or cancel.")
    purchase.save()
    return _order_row(_orders(business_unit).get(pk=order.pk), actor)


# Raw materials list and setup.

@transaction.atomic
def save_raw_material(*, actor, business_unit, data):
    """The Factory Manager keeps the raw materials list: which stock items the factory usually uses."""
    _require_user(actor, business_unit)
    if not (can_approve(actor, business_unit) or is_master_admin(actor)):
        raise WorkflowPermissionDenied(f"You need the '{A(APPROVE).label}' permission in this unit.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the item, its category and its size.")
    variant = _variant(data.get("variant_id"), "stock item")
    category = data.get("category")
    if category not in RawMaterial.Category.values:
        raise InvalidTransaction("Choose the category.")
    active = data.get("is_active", True)
    if type(active) is not bool:
        raise InvalidTransaction("Say whether it stays on the list.")
    RawMaterial.objects.update_or_create(variant=variant, defaults={
        "category": category, "size": _text(data.get("size"), "size", 80), "is_active": active, "added_by": actor})
    return {"raw_materials": _raw_material_rows(business_unit)}


@transaction.atomic
def update_settings(*, actor, business_unit, data):
    """Rates, overtime, overhead, the cost check and the go-live switch: the Master Admin only (the setup lock)."""
    require_master_admin(actor, "change the factory's rates, overhead or switch it on")
    if business_unit is None:
        raise WorkflowPermissionDenied("Choose a business unit.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the factory settings.")
    found = FactorySettings.objects.select_for_update().get(pk=factory_settings(business_unit.legal_entity).pk)
    for field, label, top in (("overhead_percent", "factory overhead", 200), ("overrun_percent", "allowed cost overrun", 100),
                              ("overtime_percent", "overtime rate", 300)):
        if field in data:
            setattr(found, field, _number(data[field], f"{label} (%)", limit=Decimal(top)))
    if found.overtime_percent < 100:
        raise InvalidTransaction("The overtime rate cannot be less than 100% of the normal rate.")
    if "asset_threshold" in data:
        found.asset_threshold = _number(data["asset_threshold"], "machine asset threshold (QAR)", limit=Decimal("1000000"))
    if "machine_life_months" in data:
        life = _int(data["machine_life_months"])
        if life is None or not 12 <= life <= 360:
            raise InvalidTransaction("A machine's useful life must be between 12 and 360 months.")
        found.machine_life_months = life
    for field, label, low, top in (("order_lead_days", "days from ordering to delivery", 1, 365),
                                   ("stages_per_person", "stages one person works on at once", 1, 10)):
        if field in data:
            value = _int(data[field])
            if value is None or not low <= value <= top:
                raise InvalidTransaction(f"The {label} must be between {low} and {top}.")
            setattr(found, field, value)
    rates = data.get("rates")
    if rates is not None:
        if not isinstance(rates, dict):
            raise InvalidTransaction("Send the hourly rates per workshop.")
        for workshop, value in rates.items():
            if workshop not in WORKSHOPS:
                raise InvalidTransaction("Unknown workshop.")
            setattr(found, f"rate_{workshop}", _number(value, f"{Workshop(workshop).label} hourly rate"))
    if "is_live" in data:
        if type(data["is_live"]) is not bool:
            raise InvalidTransaction("Say whether the factory section is live.")
        found.is_live = data["is_live"]
    found.updated_by, found.updated_at = actor, timezone.now()
    found.save()
    found.refresh_from_db()
    return _settings_row(found)


# Reading a BOQ from Excel.

BOQ_MAX_BYTES = 5 * 1024 * 1024
_RAW_CODE = re.compile(r"^JD\s*[-_.]?\s*(\d{1,4})\s*(\.\d{1,3}|[A-Z])?$")
_HEADINGS = {
    "description": re.compile(r"desc|particular|item name|works?$", re.I),
    "quantity": re.compile(r"^(qty|quantity|quant)", re.I),
    "unit": re.compile(r"^(unit|uom|u\.o\.m)(?!.*(price|rate|cost|amount))", re.I),
    "area": re.compile(r"^(area|location|room|space)\b", re.I),
    "floor": re.compile(r"^(floor|level)\b", re.I),
    "zone": re.compile(r"^zone\b", re.I),
    "tags": re.compile(r"^(tags?|type|category)\b", re.I),
}


def _boq_code(value):
    found = _RAW_CODE.match(" ".join(str(value or "").split()).upper())
    if not found:
        return None
    code = f"JD-{int(found.group(1)):02d}{found.group(2) or ''}"
    return code if BOQ_CODE.match(code) else None


def _sheet_lines(rows):
    """BOQ items from one sheet: rows whose code cell reads JD-xx, with the headings above them."""
    counts = {}
    for cells in rows:
        for index, cell in enumerate(cells):
            if _boq_code(cell):
                counts[index] = counts.get(index, 0) + 1
    if not counts:
        return [], []
    code_at = max(counts, key=lambda index: (counts[index], -index))
    first = next(number for number, cells in enumerate(rows) if code_at < len(cells) and _boq_code(cells[code_at]))
    header = []
    for cells in reversed(rows[max(0, first - 15):first]):
        if sum(1 for cell in cells if cell and not _boq_code(cell)) >= 2:
            header = cells
            break
    columns = {}
    for index, heading in enumerate(header):
        if index == code_at or not heading:
            continue
        for key, pattern in _HEADINGS.items():
            if key not in columns and pattern.search(heading.strip()):
                columns[key] = index
                break
    if "description" not in columns:
        columns["description"] = code_at + 1
    used = {code_at, *columns.values()}
    extra = [(index, heading.strip()[:60]) for index, heading in enumerate(header) if heading and index not in used][:20]
    lines, problems = [], []
    for number, cells in enumerate(rows):
        code = _boq_code(cells[code_at]) if code_at < len(cells) else None
        if not code:
            continue
        cell = lambda key: cells[columns[key]] if key in columns and columns[key] < len(cells) else ""  # noqa: E731
        description, quantity = " ".join(cell("description").split()), cell("quantity").replace(",", "")
        if not description:
            problems.append(f"{code}: no description")
            continue
        try:
            amount = Decimal(quantity) if quantity else Decimal("1")
        except ArithmeticError:
            problems.append(f"{code}: the quantity '{quantity}' is not a number")
            continue
        if amount <= 0:
            problems.append(f"{code}: the quantity must be more than 0")
            continue
        details = {heading: cells[index] for index, heading in extra if index < len(cells) and cells[index]}
        if not quantity:
            details["Quantity note"] = "No quantity in the sheet; 1 assumed"
        lines.append({"code": code, "description": description[:500], "quantity": _f(amount),
                      "unit": (cell("unit") or "piece")[:20], "details": details, "area": cell("area")[:80],
                      "floor": cell("floor")[:80], "zone": cell("zone")[:80],
                      "tags": [name.strip() for name in re.split(r"[,;/]", cell("tags")) if name.strip()][:10]})
    return lines, problems


_MATERIAL_HEADINGS = {
    "jd": re.compile(r"^(jd|boq|ref)", re.I),
    "material": re.compile(r"material|item|description|sku|code", re.I),
    "quantity": re.compile(r"^(qty|quantity|quant)", re.I),
    "unit": re.compile(r"^(unit|uom)", re.I),
}


def _materials_sheet(rows, known):
    """A sheet named "Materials": JD code, material (code or name from the raw materials list), quantity, unit.

    Returns the job's material totals, each JD's own materials, and the problems found.
    """
    header_at, columns = None, {}
    for number, cells in enumerate(rows[:15]):
        found = {}
        for index, heading in enumerate(cells):
            for key, pattern in _MATERIAL_HEADINGS.items():
                if heading and key not in found and pattern.search(heading.strip()):
                    found[key] = index
                    break
        if "material" in found and "quantity" in found:
            header_at, columns = number, found
            break
    if header_at is None:
        return {}, {}, ["Materials sheet: put the headings JD, Material, Qty and Unit in one row at the top"]
    totals, per_line, problems, missing = {}, {}, [], set()
    for number, cells in enumerate(rows[header_at + 1:], start=header_at + 2):
        cell = lambda key: cells[columns[key]] if key in columns and columns[key] < len(cells) else ""  # noqa: E731
        name = " ".join(cell("material").split())
        if not name:
            continue
        material = known.get(name.lower())
        if material is None:
            missing.add(name)
            continue
        raw = cell("quantity").replace(",", "")
        try:
            quantity = Decimal(raw)
        except ArithmeticError:
            problems.append(f"Materials row {number}: the quantity '{raw}' is not a number")
            continue
        if quantity <= 0:
            problems.append(f"Materials row {number}: the quantity must be more than 0")
            continue
        totals[material.pk] = totals.get(material.pk, ZERO) + quantity
        code = _boq_code(cell("jd")) if "jd" in columns else None
        if code:
            line = per_line.setdefault(code, {})
            line[material.pk] = line.get(material.pk, ZERO) + quantity
    if missing:
        problems.append("Not on the raw materials list, so left off (add them to the list first): "
                        + ", ".join(sorted(missing)[:20]) + (" and more" if len(missing) > 20 else ""))
    return totals, per_line, problems


def _known_materials():
    known = {}
    for raw in RawMaterial.objects.filter(is_active=True).select_related("variant__item"):
        variant = raw.variant
        for key in (variant.sku, variant.item.item_code, variant.item.description):
            if key:
                known.setdefault(key.strip().lower(), variant)
    return known


def read_boq_sheet(*, actor, business_unit, file_name, content):
    """Turn a joinery BOQ workbook into job lines (preview only; nothing is saved until the job is)."""
    _require(actor, business_unit, can_run, RUN)
    return read_boq_workbook(business_unit, file_name, content)


def read_boq_workbook(business_unit, file_name, content):
    """The BOQ reader itself, shared with Projects & Fit-Out; the caller checks who may read it."""
    from .go_live_imports import _cell

    if len(content) > BOQ_MAX_BYTES:
        raise InvalidTransaction("The file is larger than 5 MB.")
    if not (file_name or "").lower().endswith((".xlsx", ".xlsm")):
        raise InvalidTransaction("Upload the BOQ as an Excel .xlsx file.")
    from openpyxl import load_workbook

    try:
        book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        raise InvalidTransaction("This file could not be read as an Excel workbook.") from None
    lines, problems, sheets, seen = [], [], [], {}
    totals, per_line = {}, {}
    try:
        for sheet in book.worksheets:
            rows = [[_cell(value) for value in row] for row in sheet.iter_rows(values_only=True)]
            if re.search(r"material", sheet.title, re.I):
                found_totals, found_lines, issues = _materials_sheet(rows, _known_materials())
                for key, quantity in found_totals.items():
                    totals[key] = totals.get(key, ZERO) + quantity
                for code, row in found_lines.items():
                    for key, quantity in row.items():
                        per_line.setdefault(code, {})[key] = per_line.get(code, {}).get(key, ZERO) + quantity
                problems += [f"{sheet.title}: {text}" if not text.startswith("Materials") else text for text in issues]
                continue
            found, issues = _sheet_lines(rows)
            if not found and not issues:
                continue
            kept = 0
            for line in found:
                if line["code"] in seen:
                    problems.append(f"{line['code']} appears twice (sheets {seen[line['code']]} and {sheet.title}); "
                                    "the first one is kept")
                    continue
                seen[line["code"]] = sheet.title
                lines.append(line)
                kept += 1
            problems += [f"{sheet.title}: {text}" for text in issues]
            sheets.append({"name": sheet.title, "items": kept})
    finally:
        book.close()
    from .models import FactoryTag

    known = {tag.name.lower(): tag.pk for tag in FactoryTag.objects.filter(legal_entity=business_unit.legal_entity,
                                                                         is_active=True)}
    unknown = set()
    for line in lines:
        names, line["tags"] = line["tags"], []
        for name in names:
            if name.lower() in known:
                line["tags"].append(known[name.lower()])
            else:
                unknown.add(name)
    if unknown:
        problems.append(f"Tags not on the Factory Manager's list were left off: {', '.join(sorted(unknown))}")
    variants = {variant.pk: variant for variant in Variant.objects.filter(pk__in=totals).select_related("item")}
    for line in lines:
        line["materials"] = [{"variant_id": key, "sku": variants[key].sku, "quantity": _f(quantity)}
                             for key, quantity in per_line.pop(line["code"], {}).items()]
    if per_line:
        problems.append("Materials listed for JD codes not in the BOQ: " + ", ".join(sorted(per_line)[:20]))
    materials = [{"variant_id": key, "sku": variants[key].sku, "name": variants[key].item.description,
                  "quantity": _f(quantity)} for key, quantity in totals.items()]
    if len(materials) > 100:
        problems.append("A job can list at most 100 materials; only the first 100 are kept")
        materials = materials[:100]
    if not lines:
        raise InvalidTransaction("No BOQ items were found. Each item needs a code like JD-00 or JD-01 in its own column.")
    if len(lines) > MAX_BOQ_LINES:
        raise InvalidTransaction(f"The file has {len(lines)} BOQ items; a job can hold at most {MAX_BOQ_LINES}.")
    return {"file_name": file_name, "lines": lines, "sheets": sheets, "materials": materials,
            "stock_check": stock_check(business_unit, {row["variant_id"]: Decimal(row["quantity"]) for row in materials}),
            "problems": problems[:50],
            "more_problems": max(0, len(problems) - 50)}
