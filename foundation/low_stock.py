"""Low-stock levels and alerts per business unit (synthetic only, provisional design).

Someone with 'set low-stock levels' sets, for a variant, the least stock the
unit wants either at one location or across all its stock-holding locations,
and optionally how much to order when it runs low. The alert list compares
each level with what is free to sell right now:

    free = on hand - reserved for orders - held on open quotes

A level is 'out' at zero or below, 'low' at or below the minimum, otherwise
fine. Stock already ordered from suppliers but not yet received is shown next
to it, so a buyer can see whether an order already covers the gap. Alerts are
worked out live from stock, so they clear on their own once stock comes in.
Virtual locations (in transit, missing) do not count as stock on the shelf.

Sending to purchasing: the warehouse ticks the suggestions it wants and sends
them as reorder requests. Purchasing (people who can create orders) sees them
grouped by supplier, then marks each one ordered (linked to the purchase order
that includes the item, when there is one) or says why it was not ordered.
While a request is waiting, its quantity counts like stock on order, so the
same item is not suggested twice.
"""

from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .access import active_grants
from .models import (
    DemoUserMarker,
    Location,
    PurchaseCommitmentLine,
    PurchaseReceiptLine,
    QuoteHold,
    ReorderRequest,
    StockBalance,
    StockReorderLevel,
    UserActionGrant,
    Variant,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction, _as_decimal, _require_demo

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
MANAGE = UserActionGrant.Action.MANAGE_REORDER
READ = UserActionGrant.Action.READ_STOCK
BUY = UserActionGrant.Action.CREATE_ORDER
HISTORY = 30
STATUS_LABELS = {"out": "Out of stock", "low": "Low", "ok": "Enough"}
POLICY_LABEL = (
    "TEST ONLY provisional rules: free stock is on hand less reserved and quote holds; "
    "low means at or below the minimum; virtual locations are left out."
)


def _granted(user, business_unit, action):
    return active_grants(user, action).filter(business_unit=business_unit).exists()


def can_manage(user, business_unit):
    return _granted(user, business_unit, MANAGE)


def can_buy(user, business_unit):
    return _granted(user, business_unit, BUY)


def _require_user(user, business_unit):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Low-stock alerts run only in the synthetic demo environment.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can see low-stock alerts.")
    _require_demo(business_unit, business_unit.legal_entity)
    if not (_granted(user, business_unit, READ) or can_manage(user, business_unit) or can_buy(user, business_unit)):
        raise WorkflowPermissionDenied("You need permission to read stock or set low-stock levels for this unit.")


def _require_manager(user, business_unit):
    _require_user(user, business_unit)
    if not can_manage(user, business_unit):
        raise WorkflowPermissionDenied("You need the 'set low-stock levels' permission for this unit.")


def _locations(business_unit):
    return Location.objects.filter(business_units=business_unit, is_synthetic=True).exclude(kind=Location.Kind.VIRTUAL)


def _quantity(value, field, *, allow_zero):
    number = _as_decimal(value, field)
    if number != number.quantize(CENT):
        raise InvalidTransaction(f"The {field} can have at most two decimals.")
    if number < 0 or (number == 0 and not allow_zero):
        raise InvalidTransaction(f"The {field} must be {'zero or more' if allow_zero else 'more than zero'}.")
    return number.quantize(CENT)


def _free_stock(business_unit, variant_ids):
    """Free quantity per (variant, location) for the unit's stock-holding locations."""
    balances = list(StockBalance.objects.filter(
        business_unit=business_unit, variant_id__in=variant_ids, location__in=_locations(business_unit),
    ).values("pk", "variant_id", "location_id", "on_hand_quantity", "reserved_quantity"))
    holds = dict(QuoteHold.objects.filter(
        balance_id__in=[row["pk"] for row in balances], status=QuoteHold.Status.ACTIVE, expires_at__gt=timezone.now(),
    ).values("balance_id").annotate(total=Sum("quantity")).values_list("balance_id", "total"))
    free, on_hand = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    for row in balances:
        key = (row["variant_id"], row["location_id"])
        on_hand[key] += row["on_hand_quantity"]
        free[key] += row["on_hand_quantity"] - row["reserved_quantity"] - holds.get(row["pk"], ZERO)
    return free, on_hand


def _on_order(business_unit, variant_ids):
    """Ordered from suppliers and not yet received, per variant, for the whole unit."""
    lines = PurchaseCommitmentLine.objects.filter(commitment__business_unit=business_unit, variant_id__in=variant_ids)
    ordered = dict(lines.values("variant_id").annotate(total=Sum("ordered_quantity")).values_list("variant_id", "total"))
    received = dict(PurchaseReceiptLine.objects.filter(commitment_line__in=lines).values("variant_id")
                    .annotate(total=Sum("quantity")).values_list("variant_id", "total"))
    return {variant: max(total - received.get(variant, ZERO), ZERO) for variant, total in ordered.items()}


def _open_requests(business_unit, variant_ids):
    return {request.variant_id: request for request in ReorderRequest.objects.filter(
        business_unit=business_unit, variant_id__in=variant_ids, status=ReorderRequest.Status.SENT)}


def _rows(business_unit, levels):
    variant_ids = {level.variant_id for level in levels}
    free, on_hand = _free_stock(business_unit, variant_ids)
    on_order = _on_order(business_unit, variant_ids)
    requests = _open_requests(business_unit, variant_ids)
    rows = []
    for level in levels:
        if level.location_id:
            available = free[(level.variant_id, level.location_id)]
            held = on_hand[(level.variant_id, level.location_id)]
        else:
            available = sum((value for (variant, _), value in free.items() if variant == level.variant_id), ZERO)
            held = sum((value for (variant, _), value in on_hand.items() if variant == level.variant_id), ZERO)
        status = "out" if available <= 0 else "low" if available <= level.minimum_quantity else "ok"
        ordered = on_order.get(level.variant_id, ZERO)
        request = requests.get(level.variant_id)
        requested = request.quantity if request else ZERO
        short = max(level.minimum_quantity - available, ZERO)
        suggested = ZERO
        if status != "ok" and available + ordered + requested <= level.minimum_quantity:
            suggested = max(level.reorder_quantity or ZERO, level.minimum_quantity - available - ordered - requested + 1)
        rows.append({
            "id": level.pk, "variant_id": level.variant_id, "sku": level.variant.sku,
            "item_code": level.variant.item.item_code, "description": level.variant.item.description,
            "variant_description": level.variant.description,
            "location_id": level.location_id, "location": level.location.name if level.location_id else "All locations",
            "minimum": format(level.minimum_quantity, "f"),
            "reorder_quantity": format(level.reorder_quantity, "f") if level.reorder_quantity is not None else "",
            "on_hand": format(held, "f"), "free": format(available, "f"), "short_by": format(short, "f"),
            "on_order": format(ordered, "f"), "covered_by_order": status != "ok" and available + ordered > level.minimum_quantity,
            "suggested_order": format(suggested, "f"),
            "requested": format(requested, "f"), "request_id": request.pk if request else None,
            "requested_at": request.requested_at.isoformat() if request else "",
            "status": status, "status_label": STATUS_LABELS[status], "note": level.note,
            "updated_by": level.updated_by.username, "updated_at": level.updated_at.isoformat(),
        })
    order = {"out": 0, "low": 1, "ok": 2}
    rows.sort(key=lambda row: (order[row["status"]], row["sku"], row["location"]))
    return rows


def _levels(business_unit):
    return list(StockReorderLevel.objects.filter(business_unit=business_unit)
                .select_related("variant__item", "location", "updated_by"))


def alert_count(*, actor, business_unit):
    """How many levels are low or out, for a badge; zero when the user may not see stock."""
    try:
        _require_user(actor, business_unit)
    except WorkflowPermissionDenied:
        return 0
    return sum(1 for row in _rows(business_unit, _levels(business_unit)) if row["status"] != "ok")


def low_stock_overview(*, actor, business_unit):
    _require_user(actor, business_unit)
    rows = _rows(business_unit, _levels(business_unit))
    manage = can_manage(actor, business_unit)
    payload = {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "can_manage": manage, "rows": rows,
        "counts": {status: sum(1 for row in rows if row["status"] == status) for status in STATUS_LABELS},
        "covered": sum(1 for row in rows if row["covered_by_order"]),
        "policy_label": POLICY_LABEL,
        "can_buy": can_buy(actor, business_unit), "requests": _request_rows(business_unit),
    }
    if manage:
        payload["locations"] = [{"id": each.pk, "name": each.name} for each in _locations(business_unit).order_by("name")]
        stocked = list(StockBalance.objects.filter(business_unit=business_unit).values_list("variant_id", flat=True))
        stocked += [row["variant_id"] for row in rows]
        payload["variants"] = [
            {"id": each.pk, "sku": each.sku, "description": f"{each.item.description} · {each.description}"}
            for each in Variant.objects.filter(pk__in=stocked, is_synthetic=True).select_related("item").order_by("sku")[:2000]
        ]
    return payload


@transaction.atomic
def set_level(*, actor, business_unit, variant_id, location_id=None, minimum_quantity, reorder_quantity=None, note=""):
    """Create or change the level for a variant at a location (or across all locations)."""
    _require_manager(actor, business_unit)
    variant = Variant.objects.filter(pk=variant_id, is_synthetic=True).first() if variant_id else None
    if variant is None:
        raise InvalidTransaction("Choose an item.")
    location = None
    if location_id not in (None, ""):
        location = _locations(business_unit).filter(pk=location_id).first()
        if location is None:
            raise InvalidTransaction("Choose one of this unit's stock locations, or all locations.")
    minimum = _quantity(minimum_quantity, "minimum", allow_zero=True)
    reorder = None if reorder_quantity in (None, "") else _quantity(reorder_quantity, "order quantity", allow_zero=False)
    note = (note or "").strip()
    if len(note) > 160:
        raise InvalidTransaction("Keep the note under 160 characters.")
    level, created = StockReorderLevel.objects.select_for_update().get_or_create(
        business_unit=business_unit, variant=variant, location=location,
        defaults={"minimum_quantity": minimum, "reorder_quantity": reorder, "note": note, "updated_by": actor},
    )
    if not created:
        level.minimum_quantity, level.reorder_quantity, level.note = minimum, reorder, note
        level.updated_by, level.updated_at = actor, timezone.now()
        level.save()
    level = StockReorderLevel.objects.select_related("variant__item", "location", "updated_by").get(pk=level.pk)
    return _rows(business_unit, [level])[0] | {"created": created}


@transaction.atomic
def remove_level(*, actor, business_unit, level_id):
    _require_manager(actor, business_unit)
    deleted, _ = StockReorderLevel.objects.filter(business_unit=business_unit, pk=level_id).delete()
    if not deleted:
        raise InvalidTransaction("That low-stock level was not found.")
    return {"removed": level_id}


# Reorder requests to purchasing.

def _person(user):
    return (user.get_full_name() or user.get_username()) if user else ""


def _matching_order(request):
    """The newest purchase order for this unit that includes the item and was entered after the request."""
    line = (PurchaseCommitmentLine.objects.filter(commitment__business_unit=request.business_unit, variant=request.variant,
                                                  commitment__created_at__gte=request.requested_at)
            .select_related("commitment").order_by("-commitment__created_at", "-pk").first())
    return line


def _request_row(request):
    match = _matching_order(request) if request.status == ReorderRequest.Status.SENT else None
    item = request.variant.item
    return {
        "id": request.pk, "status": request.status, "status_label": request.get_status_display(),
        "sku": request.variant.sku, "item_code": item.item_code, "description": item.description,
        "variant_description": request.variant.description, "quantity": format(request.quantity, "f"),
        "location": request.location.name if request.location_id else "All locations",
        "supplier": request.supplier.name if request.supplier_id else "", "supplier_id": request.supplier_id,
        "supplier_item_code": item.supplier_item_code, "note": request.note,
        "requested_by": _person(request.requested_by), "requested_at": request.requested_at.isoformat(),
        "decided_by": _person(request.decided_by), "decided_at": request.decided_at.isoformat() if request.decided_at else "",
        "reason": request.reason, "order_id": request.commitment_id,
        "matching_order": {"id": match.commitment_id, "quantity": format(match.ordered_quantity, "f"),
                           "date": match.commitment.created_at.date().isoformat()} if match else None,
    }


def _request_rows(business_unit):
    base = ReorderRequest.objects.filter(business_unit=business_unit).select_related(
        "variant__item", "location", "supplier", "requested_by", "decided_by")
    open_ = list(base.filter(status=ReorderRequest.Status.SENT).order_by("supplier__name", "requested_at"))
    done = list(base.exclude(status=ReorderRequest.Status.SENT).order_by("-decided_at", "-pk")[:HISTORY])
    return [_request_row(request) for request in open_ + done]


def _require_buyer(user, business_unit):
    _require_user(user, business_unit)
    if not can_buy(user, business_unit):
        raise WorkflowPermissionDenied("Only purchasing (people who can create orders for this unit) can do this.")


def _text(value, field, limit, required=False):
    text = (value or "").strip() if isinstance(value, str) else ""
    if required and len(text) < 5:
        raise InvalidTransaction(f"Give the {field} (at least 5 characters).")
    if len(text) > limit:
        raise InvalidTransaction(f"Keep the {field} under {limit} characters.")
    return text


@transaction.atomic
def send_to_purchasing(*, actor, business_unit, lines, note=""):
    """lines: [{"level_id": 3, "quantity": "6"}]. One waiting request per item; the quantity can differ from the suggestion."""
    _require_manager(actor, business_unit)
    note = _text(note, "note", 200)
    if not isinstance(lines, list) or not lines or len(lines) > 100:
        raise InvalidTransaction("Choose between 1 and 100 items to send.")
    levels = {level.pk: level for level in StockReorderLevel.objects.select_for_update(of=("self",)).filter(
        business_unit=business_unit, pk__in=[line.get("level_id") for line in lines if isinstance(line, dict)])
        .select_related("variant__item__supplier", "location")}
    created, seen = [], set()
    for line in lines:
        level = levels.get(line.get("level_id")) if isinstance(line, dict) else None
        if level is None:
            raise InvalidTransaction("One of the items is no longer on the low-stock list. Refresh and try again.")
        if level.variant_id in seen:
            raise InvalidTransaction(f"{level.variant.sku} is listed twice. Send it once.")
        seen.add(level.variant_id)
        quantity = _quantity(line.get("quantity"), f"quantity for {level.variant.sku}", allow_zero=False)
        if ReorderRequest.objects.filter(business_unit=business_unit, variant=level.variant, status=ReorderRequest.Status.SENT).exists():
            raise InvalidTransaction(f"{level.variant.sku} is already waiting for purchasing.")
        created.append(ReorderRequest.objects.create(
            business_unit=business_unit, variant=level.variant, location=level.location, supplier=level.variant.item.supplier,
            quantity=quantity, note=note, requested_by=actor))
    return low_stock_overview(actor=actor, business_unit=business_unit) | {"sent": len(created)}


def _open_request(business_unit, request_id):
    request = ReorderRequest.objects.select_for_update().filter(business_unit=business_unit, pk=request_id).first()
    if request is None:
        raise InvalidTransaction("That request was not found.")
    if request.status != ReorderRequest.Status.SENT:
        raise InvalidTransaction(f"This request is already {request.get_status_display().lower()}.")
    return request


@transaction.atomic
def decide_request(*, actor, business_unit, request_id, action, reason=""):
    """Purchasing: action "ordered" or "declined" (with a reason). The warehouse: "cancel" its own waiting request."""
    if action == "cancel":
        _require_manager(actor, business_unit)
    else:
        _require_buyer(actor, business_unit)
    request = _open_request(business_unit, request_id)
    if action == "ordered":
        match = _matching_order(request)
        request.status, request.commitment = ReorderRequest.Status.ORDERED, match.commitment if match else None
        request.reason = _text(reason, "note", 200)
    elif action == "declined":
        request.status, request.reason = ReorderRequest.Status.DECLINED, _text(reason, "reason it is not being ordered", 200, required=True)
    elif action == "cancel":
        request.status, request.reason = ReorderRequest.Status.CANCELLED, _text(reason, "reason", 200)
    else:
        raise InvalidTransaction("Choose ordered, not ordered, or withdraw.")
    request.decided_by, request.decided_at = actor, timezone.now()
    request.save()
    return low_stock_overview(actor=actor, business_unit=business_unit)
