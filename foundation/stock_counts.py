"""Stock counts by location (provisional design, following Bilal's review on #16).

Stock keeps moving while a location is counted: the warehouse does not stop
deliveries, and deliveries made during the count are recorded manually and
entered afterwards.

1. A warehouse user with count_stock starts a count of one location. Counters
   enter what they find (blind: they do not see the ERP quantity) and may add
   items the list did not expect, then submit.
2. The warehouse manager (approve_count) reviews the counted quantity against
   the live ERP quantity. Entering a manual delivery updates the difference at
   once, so they can check every manual transaction is in before confirming,
   or return the count for a recount. Nobody confirms a count they started,
   submitted or counted.
3. Confirming sends the differences to their own places: a shortage moves to
   the XX location ("physically missing but in the ERP") at its cost; a surplus
   is held on the count, not yet in stock or for sale.
4. The count stays open until every difference has an action:
   - a shortage is written off (Finance approves; inventory is credited and the
     stock count difference account debited), or found and moved back from XX;
   - a surplus is accepted into stock at the item's average cost (Finance
     approves; inventory debited, stock count difference credited).
   Finance (approve_write_off) cannot approve an action they requested.
Virtual locations (TFRW, VTIV, XX) are not counted here.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .approval_limits import require_within_limit
from .access import active_grants
from .models import (
    DemoUserMarker,
    JournalEntry,
    Location,
    StockBalance,
    StockCount,
    StockCountEvent,
    StockCountAction,
    StockCountActionStatus,
    StockCountLine,
    StockMovement,
    UserActionGrant,
    Variant,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import (
    TEST_MEMO,
    InvalidTransaction,
    _begin_idempotent_operation,
    _complete,
    _post_journal,
    _require_demo,
    _validate_bucket,
    held_quantity,
    transfer_stock,
)

COUNT = UserActionGrant.Action.COUNT_STOCK
APPROVE = UserActionGrant.Action.APPROVE_COUNT
WRITE_OFF = UserActionGrant.Action.APPROVE_WRITE_OFF
Action, ActionStatus = StockCountAction, StockCountActionStatus
Status = StockCount.Status
POSTED = (Status.DIFFERENCES, Status.CLOSED)
MISSING_CODE = "XX"
ZERO = Decimal("0.00")
CENT = Decimal("0.01")
MAX_LINES = 2000


def _granted(user, business_unit, action):
    return active_grants(user, action).filter(business_unit=business_unit).exists()


def _require(user, business_unit, *actions):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Stock counts run only in the synthetic demo environment.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can work on stock counts.")
    _require_demo(business_unit, business_unit.legal_entity)
    if not any(_granted(user, business_unit, action) for action in actions):
        labels = " or ".join(f"'{UserActionGrant.Action(action).label.lower()}'" for action in actions)
        raise WorkflowPermissionDenied(f"You need the {labels} permission for this business unit.")


def _note(value, field, *, required=False):
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if required and not text:
        raise InvalidTransaction(f"{field} is required.")
    if len(text) > 240:
        raise InvalidTransaction(f"{field} cannot exceed 240 characters.")
    return text


def _counted(value):
    try:
        number = Decimal(str(value).strip())
    except Exception:  # noqa: BLE001 - any unparsable entry is the same user error
        raise InvalidTransaction("Counted quantities must be numbers, for example 3 or 2.5.") from None
    if not number.is_finite() or number < 0:
        raise InvalidTransaction("Counted quantities cannot be negative.")
    if number.quantize(CENT) != number:
        raise InvalidTransaction("Counted quantities support at most two decimal places.")
    return number.quantize(CENT)


def _log(count, actor, event, note=""):
    StockCountEvent.objects.create(count=count, actor=actor, event=event, note=note[:240])


def _count_for(actor, business_unit, count_id, *, lock=False):
    query = StockCount.objects.select_related("location", "business_unit__legal_entity")
    if lock:
        query = query.select_for_update(of=("self",))
    count = query.filter(pk=count_id, business_unit=business_unit).first()
    if count is None:
        raise InvalidTransaction("This stock count is not in the selected business unit.")
    return count


def _expect(count, *statuses):
    if count.status not in statuses:
        raise InvalidTransaction(f"This count is {count.get_status_display().lower()} and cannot take this step.")


def _on_hand(business_unit, variant_id, location):
    balance = StockBalance.objects.filter(business_unit=business_unit, variant_id=variant_id, location=location).first()
    return balance.on_hand_quantity if balance else ZERO


def _person(user):
    return {"id": user.pk, "username": user.username} if user else None


def _summary(count):
    lines = list(count.lines.all())
    return {
        "id": count.pk,
        "location": {"id": count.location_id, "code": count.location.code, "name": count.location.name},
        "status": count.status,
        "status_label": count.get_status_display(),
        "note": count.note,
        "opened_by": _person(count.opened_by),
        "opened_at": count.opened_at.isoformat(),
        "submitted_at": count.submitted_at.isoformat() if count.submitted_at else None,
        "decided_by": _person(count.decided_by),
        "decided_at": count.decided_at.isoformat() if count.decided_at else None,
        "line_count": len(lines),
        "counted_lines": sum(1 for line in lines if line.counted_quantity is not None),
        "open_differences": sum(1 for line in lines if _has_difference(line) and line.action_status != ActionStatus.DONE),
        "waiting_for_finance": sum(1 for line in lines if line.action_status == ActionStatus.REQUESTED),
    }


def _has_difference(line):
    return line.moved_to_missing > 0 or line.surplus_quantity > 0


def counts_overview(*, actor, business_unit):
    _require(actor, business_unit, COUNT, APPROVE, WRITE_OFF)
    counts = StockCount.objects.filter(business_unit=business_unit).select_related(
        "location", "opened_by", "decided_by",
    ).prefetch_related("lines")[:50]
    locations = Location.objects.filter(business_units=business_unit, is_synthetic=True).exclude(
        kind=Location.Kind.VIRTUAL,
    ).order_by("code")
    open_ids = set(StockCount.objects.filter(
        business_unit=business_unit, status__in=[Status.COUNTING, Status.SUBMITTED],
    ).values_list("location_id", flat=True))
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "permissions": {
            "can_count": _granted(actor, business_unit, COUNT),
            "can_approve": _granted(actor, business_unit, APPROVE),
            "can_approve_write_off": _granted(actor, business_unit, WRITE_OFF),
        },
        "locations": [
            {"id": location.pk, "code": location.code, "name": location.name, "has_open_count": location.pk in open_ids}
            for location in locations
        ],
        "counts": [_summary(count) for count in counts],
    }


def count_detail(*, actor, business_unit, count_id):
    _require(actor, business_unit, COUNT, APPROVE, WRITE_OFF)
    count = _count_for(actor, business_unit, count_id)
    can_approve = _granted(actor, business_unit, APPROVE)
    can_finance = _granted(actor, business_unit, WRITE_OFF)
    # Counting is blind: the ERP quantity is shown to reviewers and Finance, and to everyone once confirmed.
    show_expected = can_approve or can_finance or count.status in POSTED
    rows = []
    for line in count.lines.select_related("variant__item__brand", "counted_by", "action_requested_by", "action_decided_by"):
        current = _on_hand(business_unit, line.variant_id, count.location) if show_expected else None
        row = {
            "id": line.pk,
            "sku": line.variant.sku,
            "item": line.variant.item.description,
            "brand": line.variant.item.brand.name,
            "counted_quantity": format(line.counted_quantity, "f") if line.counted_quantity is not None else None,
            "counted_by": line.counted_by.username if line.counted_by else None,
            "found_during_count": line.found_during_count,
        }
        if show_expected:
            row["expected_quantity"] = format(line.expected_quantity, "f")
            basis = line.posted_quantity if count.status in POSTED else current
            row["erp_quantity"] = format(basis, "f")
            row["difference"] = (
                format(line.counted_quantity - basis, "f") if line.counted_quantity is not None else None
            )
        if count.status in POSTED:
            open_difference = _has_difference(line) and not line.action_status
            row.update({
                "moved_to_missing": format(line.moved_to_missing, "f"),
                "surplus_quantity": format(line.surplus_quantity, "f"),
                "surplus_value": format(line.surplus_value, "f"),
                "written_off_value": format(line.written_off_value, "f"),
                "action": line.action,
                "action_label": line.get_action_display() if line.action else "",
                "action_status": line.action_status,
                "action_status_label": line.get_action_status_display() if line.action_status else (
                    "Needs an action" if _has_difference(line) else ""),
                "action_note": line.action_note,
                "action_requested_by": _person(line.action_requested_by),
                "action_decided_by": _person(line.action_decided_by),
                "action_decision_note": line.action_decision_note,
                "allowed_actions": (
                    ([Action.WRITE_OFF, Action.FOUND] if line.moved_to_missing > 0 else [Action.ACCEPT])
                    if open_difference and can_approve and count.status == Status.DIFFERENCES else []
                ),
                "can_decide_action": (
                    line.action_status == ActionStatus.REQUESTED and can_finance
                    and line.action_requested_by_id != actor.pk
                ),
            })
        rows.append(row)
    involved = _involved(count)
    return {
        **_summary(count),
        "decision_note": count.decision_note,
        "blind": not show_expected,
        "lines": rows,
        "can_count": count.status == Status.COUNTING and _granted(actor, business_unit, COUNT),
        "can_decide": count.status == Status.SUBMITTED and can_approve and actor.pk not in involved,
        "can_approve_write_off": can_finance,
        "is_involved": actor.pk in involved,
        "history": [
            {"event": event.event, "actor": event.actor.username, "note": event.note, "at": event.created_at.isoformat()}
            for event in count.events.select_related("actor")
        ],
    }


def _involved(count):
    people = {count.opened_by_id, count.submitted_by_id}
    people.update(count.lines.exclude(counted_by__isnull=True).values_list("counted_by_id", flat=True))
    return people - {None}


@transaction.atomic
def start_count(*, actor, business_unit, location_id, note=""):
    _require(actor, business_unit, COUNT)
    location = Location.objects.filter(pk=location_id, business_units=business_unit, is_synthetic=True).first()
    if location is None:
        raise InvalidTransaction("Choose a location of this business unit.")
    if location.kind == Location.Kind.VIRTUAL:
        raise InvalidTransaction("Virtual locations such as TFRW, VTIV and XX are not counted.")
    if StockCount.objects.filter(
        business_unit=business_unit, location=location, status__in=[Status.COUNTING, Status.SUBMITTED],
    ).exists():
        raise InvalidTransaction(f"{location.code} already has a count in progress.")
    count = StockCount.objects.create(
        business_unit=business_unit, location=location, opened_by=actor, note=_note(note, "The note"),
    )
    balances = StockBalance.objects.filter(business_unit=business_unit, location=location, on_hand_quantity__gt=0)
    StockCountLine.objects.bulk_create([
        StockCountLine(count=count, variant_id=balance.variant_id, expected_quantity=balance.on_hand_quantity)
        for balance in balances.order_by("variant__sku")
    ])
    _log(count, actor, "started", count.note)
    return count_detail(actor=actor, business_unit=business_unit, count_id=count.pk)


@transaction.atomic
def record_counts(*, actor, business_unit, count_id, entries):
    """Save counted quantities: [{"line_id": 1, "counted": "3"}] or [{"sku": "X", "counted": "1"}] for a found item."""
    _require(actor, business_unit, COUNT)
    count = _count_for(actor, business_unit, count_id, lock=True)
    _expect(count, Status.COUNTING)
    if not isinstance(entries, list) or not entries:
        raise InvalidTransaction("Send at least one counted quantity.")
    if len(entries) > MAX_LINES:
        raise InvalidTransaction(f"Send at most {MAX_LINES} quantities at a time.")
    now = timezone.now()
    for entry in entries:
        if not isinstance(entry, dict):
            raise InvalidTransaction("Each counted quantity needs a line or SKU and a number.")
        counted = _counted(entry.get("counted"))
        if entry.get("line_id") is not None:
            line = count.lines.filter(pk=entry.get("line_id")).first()
            if line is None:
                raise InvalidTransaction("A counted line does not belong to this count.")
        else:
            sku = entry.get("sku").strip() if isinstance(entry.get("sku"), str) else ""
            variant = Variant.objects.select_related("item__brand").filter(sku__iexact=sku).first()
            if variant is None:
                raise InvalidTransaction(f"No item with SKU {sku or '(blank)'} exists.")
            _validate_bucket(business_unit, variant, count.location)
            line = count.lines.filter(variant=variant).first() or StockCountLine.objects.create(
                count=count, variant=variant, found_during_count=True,
                expected_quantity=_on_hand(business_unit, variant.pk, count.location),
            )
        line.counted_quantity, line.counted_by, line.counted_at = counted, actor, now
        line.save(update_fields=["counted_quantity", "counted_by", "counted_at"])
    return count_detail(actor=actor, business_unit=business_unit, count_id=count.pk)


@transaction.atomic
def submit_count(*, actor, business_unit, count_id):
    _require(actor, business_unit, COUNT)
    count = _count_for(actor, business_unit, count_id, lock=True)
    _expect(count, Status.COUNTING)
    missing = count.lines.filter(counted_quantity__isnull=True).count()
    if missing:
        raise InvalidTransaction(f"{missing} line(s) have no count yet. Enter 0 for anything not found.")
    if not count.lines.exists():
        raise InvalidTransaction("Nothing has been counted. Add the items found, or cancel the count.")
    count.status, count.submitted_by, count.submitted_at = Status.SUBMITTED, actor, timezone.now()
    count.save(update_fields=["status", "submitted_by", "submitted_at"])
    _log(count, actor, "submitted")
    return count_detail(actor=actor, business_unit=business_unit, count_id=count.pk)


@transaction.atomic
def return_count(*, actor, business_unit, count_id, note):
    _require(actor, business_unit, APPROVE)
    count = _count_for(actor, business_unit, count_id, lock=True)
    _expect(count, Status.SUBMITTED)
    note = _note(note, "A reason for recounting", required=True)
    count.status, count.submitted_by, count.submitted_at = Status.COUNTING, None, None
    count.save(update_fields=["status", "submitted_by", "submitted_at"])
    _log(count, actor, "returned", note)
    return count_detail(actor=actor, business_unit=business_unit, count_id=count.pk)


@transaction.atomic
def cancel_count(*, actor, business_unit, count_id, note):
    _require(actor, business_unit, COUNT, APPROVE)
    count = _count_for(actor, business_unit, count_id, lock=True)
    _expect(count, Status.COUNTING, Status.SUBMITTED)
    note = _note(note, "A reason for cancelling", required=True)
    count.status, count.decided_by, count.decided_at, count.decision_note = Status.CANCELLED, actor, timezone.now(), note
    count.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    _log(count, actor, "cancelled", note)
    return count_detail(actor=actor, business_unit=business_unit, count_id=count.pk)


def _average_cost(business_unit, variant_id):
    totals = StockBalance.objects.filter(
        business_unit=business_unit, variant_id=variant_id, on_hand_quantity__gt=0,
    ).aggregate(quantity=Sum("on_hand_quantity"), value=Sum("on_hand_value"))
    if not totals["quantity"]:
        return ZERO
    return totals["value"] / totals["quantity"]


def _free(balance):
    return balance.on_hand_quantity - balance.reserved_quantity - held_quantity(balance)


@transaction.atomic
def approve_count(*, actor, business_unit, count_id, idempotency_key, note=""):
    """Confirm the reviewed differences against the live ERP quantity and send them to XX or hold them."""
    _require(actor, business_unit, APPROVE)
    count = _count_for(actor, business_unit, count_id, lock=True)
    payload = {"count": count.pk}
    operation, created = _begin_idempotent_operation(idempotency_key, "stock_count_approval", payload)
    if not created:
        return operation.result
    _expect(count, Status.SUBMITTED)
    if actor.pk in _involved(count):
        raise WorkflowPermissionDenied("You started, counted or submitted this count, so someone else must confirm it.")
    missing = _missing_location(business_unit)
    location, totals = count.location, {"missing": ZERO, "held": ZERO}
    for line in count.lines.select_related("variant__item__brand").select_for_update(of=("self",)):
        balance = StockBalance.objects.select_for_update().filter(
            business_unit=business_unit, variant=line.variant, location=location,
        ).first()
        on_hand = balance.on_hand_quantity if balance else ZERO
        difference = line.counted_quantity - on_hand
        line.posted_quantity = on_hand
        if difference < 0:
            shortage = -difference
            if _free(balance) < shortage:
                raise InvalidTransaction(
                    f"{line.variant.sku}: {shortage} short, but only {_free(balance)} is free; the rest is reserved "
                    "or held for customers. Enter any manual deliveries first, release those holds, or recount."
                )
            transfer_stock(
                idempotency_key=f"{idempotency_key}:{line.pk}:to-missing", business_unit=business_unit,
                variant=line.variant, source_location=location, destination_location=missing, quantity=shortage,
            )
            line.moved_to_missing = shortage
            totals["missing"] += shortage
        elif difference > 0:
            # Held on the count until Finance accepts it into stock. An item that
            # reappears after an earlier count is handled on that count instead
            # ("found, move back"), so each XX quantity is settled exactly once.
            line.surplus_quantity = difference
            totals["held"] += difference
        line.save(update_fields=["posted_quantity", "moved_to_missing", "surplus_quantity"])
    count.status = Status.DIFFERENCES if totals["missing"] or totals["held"] else Status.CLOSED
    count.decided_by, count.decided_at = actor, timezone.now()
    count.decision_note, count.posting = _note(note, "The confirmation note"), operation
    count.save(update_fields=["status", "decided_by", "decided_at", "decision_note", "posting"])
    _log(count, actor, "confirmed", count.decision_note)
    if count.status == Status.CLOSED:
        _log(count, actor, "closed", "No differences to act on.")
    return _complete(operation, {
        "count_id": count.pk,
        "status": count.status,
        "moved_to_missing": format(totals["missing"], "f"),
        "held_surplus": format(totals["held"], "f"),
    })


def _missing_location(business_unit):
    missing = Location.objects.filter(code=MISSING_CODE, business_units=business_unit).first()
    if missing is None:
        raise InvalidTransaction("The XX location for missing items is not set up for this business unit.")
    return missing


def _line_for(count, line_id):
    line = count.lines.select_related("variant__item__brand").select_for_update(of=("self",)).filter(pk=line_id).first()
    if line is None:
        raise InvalidTransaction("This line does not belong to the count.")
    return line


def _close_when_done(count, actor):
    if not any(_has_difference(line) and line.action_status != ActionStatus.DONE for line in count.lines.all()):
        count.status = Status.CLOSED
        count.save(update_fields=["status"])
        _log(count, actor, "closed", "Every difference has an action.")


@transaction.atomic
def request_line_action(*, actor, business_unit, count_id, line_id, action, idempotency_key, note=""):
    """The warehouse manager chooses what happens to one difference.

    "found" moves a shortage back from XX straight away (no value changes);
    "write_off" and "accept" change inventory value, so they wait for Finance.
    """
    _require(actor, business_unit, APPROVE)
    count = _count_for(actor, business_unit, count_id, lock=True)
    operation = None
    if action == Action.FOUND:
        operation, created = _begin_idempotent_operation(
            idempotency_key, "stock_count_found", {"count": count.pk, "line": line_id},
        )
        if not created:
            return operation.result
    _expect(count, Status.DIFFERENCES)
    line = _line_for(count, line_id)
    if not _has_difference(line) or line.action_status:
        raise InvalidTransaction(f"{line.variant.sku} has no difference waiting for an action.")
    allowed = [Action.WRITE_OFF, Action.FOUND] if line.moved_to_missing > 0 else [Action.ACCEPT]
    if action not in allowed:
        raise InvalidTransaction(
            f"{line.variant.sku}: choose {' or '.join(Action(choice).label.lower() for choice in allowed)}."
        )
    note = _note(note, "A reason", required=action != Action.FOUND)
    now = timezone.now()
    line.action, line.action_note, line.action_requested_by, line.action_requested_at = action, note, actor, now
    if action == Action.FOUND:
        transfer_stock(
            idempotency_key=f"{idempotency_key}:found", business_unit=business_unit, variant=line.variant,
            source_location=_missing_location(business_unit), destination_location=count.location,
            quantity=line.moved_to_missing,
        )
        line.action_status, line.action_decided_by, line.action_decided_at = ActionStatus.DONE, actor, now
        line.action_posting = operation
        line.save()
        _log(count, actor, "found", f"{line.variant.sku}: {line.moved_to_missing} moved back from XX. {note}".strip())
        _close_when_done(count, actor)
        return _complete(operation, count_detail(actor=actor, business_unit=business_unit, count_id=count.pk))
    line.action_status = ActionStatus.REQUESTED
    line.save()
    _log(count, actor, "action_requested", f"{line.variant.sku}: {line.get_action_display().lower()}. {note}")
    return count_detail(actor=actor, business_unit=business_unit, count_id=count.pk)


@transaction.atomic
def decide_line_action(*, actor, business_unit, count_id, line_id, approve, idempotency_key, note=""):
    """Finance approves or rejects a write-off or a surplus acceptance."""
    _require(actor, business_unit, WRITE_OFF)
    count = _count_for(actor, business_unit, count_id, lock=True)
    if type(approve) is not bool:
        raise InvalidTransaction("Say whether you approve the action.")
    operation = None
    if approve:
        operation, created = _begin_idempotent_operation(
            idempotency_key, "stock_count_line_decision", {"count": count.pk, "line": line_id},
        )
        if not created:
            return operation.result
    _expect(count, Status.DIFFERENCES)
    line = _line_for(count, line_id)
    if line.action_status != ActionStatus.REQUESTED:
        raise InvalidTransaction(f"{line.variant.sku} has no action waiting for Finance.")
    if line.action_requested_by_id == actor.pk:
        raise WorkflowPermissionDenied("You requested this action, so someone else in Finance must decide it.")
    if not approve:
        reason = _note(note, "A reason for rejecting", required=True)
        _log(count, actor, "action_rejected", f"{line.variant.sku}: {line.get_action_display().lower()} rejected. {reason}")
        line.action, line.action_status, line.action_note = "", "", ""
        line.action_requested_by, line.action_requested_at = None, None
        line.action_decision_note = reason
        line.save()
        return count_detail(actor=actor, business_unit=business_unit, count_id=count.pk)
    if line.action == Action.WRITE_OFF:
        balance = StockBalance.objects.select_for_update().filter(
            business_unit=business_unit, variant=line.variant, location=_missing_location(business_unit),
        ).first()
        quantity = line.moved_to_missing
        if balance is None or _free(balance) < quantity:
            raise InvalidTransaction(f"{line.variant.sku}: XX no longer holds {quantity} to write off.")
        value = (balance.on_hand_value * quantity / balance.on_hand_quantity).quantize(CENT, rounding=ROUND_HALF_UP)
        require_within_limit(actor, business_unit, WRITE_OFF, value, "this write-off")
        StockMovement.objects.create(
            balance=balance, idempotency=operation, kind=StockMovement.Kind.COUNT_LOSS,
            quantity_delta=-quantity, value_delta=-value, memo=f"{TEST_MEMO}: stock count {count.pk} write-off",
        )
        balance.on_hand_quantity -= quantity
        balance.on_hand_value -= value
        balance.updated_at = timezone.now()
        balance.save(update_fields=["on_hand_quantity", "on_hand_value", "updated_at"])
        line.written_off_value = value
        journal = [
            ("TEST_STOCK_COUNT", value, ZERO, f"Stock count {count.pk} write-off of {line.variant.sku}"),
            ("TEST_INVENTORY", ZERO, value, f"Stock count {count.pk} write-off of {line.variant.sku}"),
        ]
        event = f"{line.variant.sku}: {quantity} written off from XX at {value}."
    else:
        quantity = line.surplus_quantity
        value = (_average_cost(business_unit, line.variant_id) * quantity).quantize(CENT, rounding=ROUND_HALF_UP)
        require_within_limit(actor, business_unit, WRITE_OFF, value, "this adjustment")
        balance, _ = StockBalance.objects.get_or_create(
            legal_entity_id=business_unit.legal_entity_id, business_unit=business_unit,
            variant=line.variant, location=count.location,
        )
        balance = StockBalance.objects.select_for_update().get(pk=balance.pk)
        StockMovement.objects.create(
            balance=balance, idempotency=operation, kind=StockMovement.Kind.COUNT_GAIN,
            quantity_delta=quantity, value_delta=value, memo=f"{TEST_MEMO}: stock count {count.pk} surplus",
        )
        balance.on_hand_quantity += quantity
        balance.on_hand_value += value
        balance.updated_at = timezone.now()
        balance.save(update_fields=["on_hand_quantity", "on_hand_value", "updated_at"])
        line.surplus_value = value
        journal = [
            ("TEST_INVENTORY", value, ZERO, f"Stock count {count.pk} surplus of {line.variant.sku} at average cost"),
            ("TEST_STOCK_COUNT", ZERO, value, f"Stock count {count.pk} surplus of {line.variant.sku}"),
        ]
        event = f"{line.variant.sku}: {quantity} accepted into {count.location.code} at {value}."
    journal_id = None
    if value > 0:
        journal_id = _post_journal(
            legal_entity=business_unit.legal_entity, business_unit=business_unit, idempotency=operation,
            kind=JournalEntry.Kind.STOCK_COUNT,
            description=f"TEST ONLY stock count {count.pk} at {count.location.code}: {line.get_action_display().lower()}",
            lines=journal,
        ).pk
    now = timezone.now()
    line.action_status, line.action_decided_by, line.action_decided_at = ActionStatus.DONE, actor, now
    line.action_decision_note, line.action_posting = _note(note, "The approval note"), operation
    line.save()
    _log(count, actor, "written_off" if line.action == Action.WRITE_OFF else "accepted", event)
    _close_when_done(count, actor)
    return _complete(operation, {"count_id": count.pk, "line_id": line.pk, "value": format(value, "f"), "journal_entry_id": journal_id})
