"""Synthetic-only stock transfer requests (owner decisions I1 and I2).

A showroom admin requests a move, a showroom manager approves or rejects it
with a reason, and the warehouse team schedules it, dispatches it and confirms
arrival. While goods are in transit they are reserved at the source, so they
cannot be sold there, and they reach the destination only on receipt.
"""

from datetime import date

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .access import active_grants
from .models import (
    DemoUserMarker,
    Location,
    StockBalance,
    StockReservation,
    StockTransfer,
    StockTransferRequest,
    StockTransferRequestEvent,
    StockTransferRequestLine,
    UserActionGrant,
    Variant,
)
from .purchasing_services import WorkflowPermissionDenied, _workflow_key
from .transaction_services import (
    InsufficientStock,
    InvalidTransaction,
    _begin_idempotent_operation,
    _complete,
    _quantity,
    _require_demo,
    _validate_bucket,
    held_quantity,
    reserve_stock,
    transfer_stock,
)


MAX_LINES = 50
Status = StockTransferRequest.Status
Action = UserActionGrant.Action


def _require_transfer_actor(user, business_unit, action):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Stock transfer requests are restricted to APP_ENV=demo.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can work on transfer requests.")
    _require_demo(business_unit, business_unit.legal_entity)
    if not active_grants(user, action).filter(business_unit=business_unit).exists():
        label = UserActionGrant.Action(action).label.lower()
        raise WorkflowPermissionDenied(f"You need the '{label}' permission for this business unit.")


def _reason(value, field):
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise InvalidTransaction(f"{field} is required.")
    if len(text) > 240:
        raise InvalidTransaction(f"{field} cannot exceed 240 characters.")
    return text


def _record(request, actor, from_status, note=""):
    StockTransferRequestEvent.objects.create(
        request=request,
        actor=actor,
        from_status=from_status,
        to_status=request.status,
        note=note,
    )


def _sellable(balance, now=None):
    return balance.available_quantity - held_quantity(balance, now)


def _locked_request(transfer_request, expected_statuses):
    locked = StockTransferRequest.objects.select_for_update().select_related(
        "business_unit__legal_entity", "source_location", "destination_location",
    ).get(pk=transfer_request.pk)
    if locked.status not in expected_statuses:
        raise InvalidTransaction(
            f"This request is {locked.get_status_display().lower()} and cannot take this step."
        )
    return locked


def _result(transfer_request):
    return {"request_id": transfer_request.pk, "status": transfer_request.status}


@transaction.atomic
def submit_transfer_request(
    *, actor, business_unit, idempotency_key, source_location, destination_location, lines, note=""
):
    _require_transfer_actor(actor, business_unit, Action.REQUEST_TRANSFER)
    if source_location.pk == destination_location.pk:
        raise InvalidTransaction("Choose a destination that differs from the current location.")
    if not isinstance(lines, list) or not 1 <= len(lines) <= MAX_LINES:
        raise InvalidTransaction(f"Pick between 1 and {MAX_LINES} items to move.")
    note = note.strip() if isinstance(note, str) else ""
    if len(note) > 240:
        raise InvalidTransaction("The note cannot exceed 240 characters.")
    normalized = []
    for line in lines:
        variant, quantity = line["variant"], _quantity(line["quantity"])
        _validate_bucket(business_unit, variant, source_location)
        _validate_bucket(business_unit, variant, destination_location)
        normalized.append((variant, quantity))
    if len({variant.pk for variant, _ in normalized}) != len(normalized):
        raise InvalidTransaction("Each item can appear only once in a request.")
    payload = {
        "business_unit": business_unit.pk,
        "source_location": source_location.pk,
        "destination_location": destination_location.pk,
        "lines": sorted([variant.pk, quantity] for variant, quantity in normalized),
        "note": note,
    }
    operation, created = _begin_idempotent_operation(idempotency_key, "transfer_request", payload)
    if not created:
        return operation.result
    for variant, quantity in normalized:
        balance = StockBalance.objects.filter(
            business_unit=business_unit, variant=variant, location=source_location,
        ).first()
        if balance is None or _sellable(balance) < quantity:
            raise InsufficientStock(f"Not enough free stock of {variant.sku} at {source_location.name}.")
    transfer_request = StockTransferRequest.objects.create(
        idempotency=operation,
        business_unit=business_unit,
        source_location=source_location,
        destination_location=destination_location,
        requested_by=actor,
        note=note,
    )
    for variant, quantity in normalized:
        StockTransferRequestLine.objects.create(request=transfer_request, variant=variant, quantity=quantity)
    _record(transfer_request, actor, "", note)
    return _complete(operation, _result(transfer_request))


@transaction.atomic
def decide_transfer_request(*, actor, transfer_request, idempotency_key, approve, reason=""):
    _require_transfer_actor(actor, transfer_request.business_unit, Action.APPROVE_TRANSFER)
    if type(approve) is not bool:
        raise InvalidTransaction("A decision must approve or reject the request.")
    if approve:
        reason = reason.strip() if isinstance(reason, str) else ""
        if len(reason) > 240:
            raise InvalidTransaction("The note cannot exceed 240 characters.")
    else:
        reason = _reason(reason, "A rejection reason")
    payload = {"request": transfer_request.pk, "approve": approve, "reason": reason}
    operation, created = _begin_idempotent_operation(idempotency_key, "transfer_request_decision", payload)
    if not created:
        return operation.result
    locked = _locked_request(transfer_request, [Status.SUBMITTED])
    if locked.requested_by_id == actor.pk:
        raise WorkflowPermissionDenied("A different person must approve or reject your own request.")
    now = timezone.now()
    locked.status = Status.APPROVED if approve else Status.REJECTED
    locked.decided_by = actor
    locked.decided_at = now
    locked.rejection_reason = "" if approve else reason
    locked.updated_at = now
    locked.save(update_fields=["status", "decided_by", "decided_at", "rejection_reason", "updated_at"])
    _record(locked, actor, Status.SUBMITTED, reason)
    return _complete(operation, _result(locked))


@transaction.atomic
def schedule_transfer_request(*, actor, transfer_request, idempotency_key, transfer_date):
    _require_transfer_actor(actor, transfer_request.business_unit, Action.EXECUTE_TRANSFER)
    try:
        planned = transfer_date if isinstance(transfer_date, date) else date.fromisoformat(transfer_date)
    except (TypeError, ValueError):
        raise InvalidTransaction("Give the transfer date as YYYY-MM-DD.") from None
    if planned < timezone.localdate():
        raise InvalidTransaction("The transfer date cannot be in the past.")
    payload = {"request": transfer_request.pk, "transfer_date": planned.isoformat()}
    operation, created = _begin_idempotent_operation(idempotency_key, "transfer_request_schedule", payload)
    if not created:
        return operation.result
    locked = _locked_request(transfer_request, [Status.APPROVED, Status.SCHEDULED])
    previous = locked.status
    now = timezone.now()
    locked.status = Status.SCHEDULED
    locked.transfer_date = planned
    locked.scheduled_by = actor
    locked.scheduled_at = now
    locked.updated_at = now
    locked.save(update_fields=["status", "transfer_date", "scheduled_by", "scheduled_at", "updated_at"])
    _record(locked, actor, previous, f"Transfer date {planned.isoformat()}")
    return _complete(operation, {**_result(locked), "transfer_date": planned.isoformat()})


@transaction.atomic
def dispatch_transfer_request(*, actor, transfer_request, idempotency_key):
    _require_transfer_actor(actor, transfer_request.business_unit, Action.EXECUTE_TRANSFER)
    payload = {"request": transfer_request.pk}
    operation, created = _begin_idempotent_operation(idempotency_key, "transfer_request_dispatch", payload)
    if not created:
        return operation.result
    locked = _locked_request(transfer_request, [Status.SCHEDULED])
    lines = list(locked.lines.select_related("variant").order_by("pk"))
    balances = {
        balance.variant_id: balance
        for balance in StockBalance.objects.select_for_update().filter(
            business_unit=locked.business_unit,
            location=locked.source_location,
            variant_id__in=[line.variant_id for line in lines],
        ).order_by("pk")
    }
    for line in lines:
        balance = balances.get(line.variant_id)
        if balance is None or _sellable(balance) < line.quantity:
            raise InsufficientStock(
                f"Not enough free stock of {line.variant.sku} at {locked.source_location.name} to dispatch."
            )
    for line in lines:
        reserved = reserve_stock(
            idempotency_key=_workflow_key(f"{idempotency_key}:{line.pk}", "transfer-dispatch"),
            business_unit=locked.business_unit,
            variant=line.variant,
            location=locked.source_location,
            quantity=line.quantity,
        )
        line.reservation_id = reserved["reservation_id"]
        line.save(update_fields=["reservation"])
    now = timezone.now()
    locked.status = Status.IN_TRANSIT
    locked.dispatched_by = actor
    locked.dispatched_at = now
    locked.updated_at = now
    locked.save(update_fields=["status", "dispatched_by", "dispatched_at", "updated_at"])
    _record(locked, actor, Status.SCHEDULED)
    return _complete(operation, _result(locked))


@transaction.atomic
def receive_transfer_request(*, actor, transfer_request, idempotency_key):
    _require_transfer_actor(actor, transfer_request.business_unit, Action.EXECUTE_TRANSFER)
    payload = {"request": transfer_request.pk}
    operation, created = _begin_idempotent_operation(idempotency_key, "transfer_request_receive", payload)
    if not created:
        return operation.result
    locked = _locked_request(transfer_request, [Status.IN_TRANSIT])
    lines = list(locked.lines.select_related("variant").order_by("pk"))
    reservations = {
        reservation.pk: reservation
        for reservation in StockReservation.objects.select_for_update().filter(
            pk__in=[line.reservation_id for line in lines],
        ).order_by("pk")
    }
    balances = {
        balance.pk: balance
        for balance in StockBalance.objects.select_for_update().filter(
            pk__in=sorted({reservation.balance_id for reservation in reservations.values()}),
        ).order_by("pk")
    }
    now = timezone.now()
    for line in lines:
        reservation = reservations.get(line.reservation_id)
        if reservation is None or reservation.status != StockReservation.Status.ACTIVE:
            raise InvalidTransaction("The in-transit reservation for this request is no longer active.")
        balance = balances[reservation.balance_id]
        balance.reserved_quantity -= reservation.quantity
        balance.updated_at = now
        balance.save(update_fields=["reserved_quantity", "updated_at"])
        reservation.status = StockReservation.Status.RELEASED
        reservation.updated_at = now
        reservation.save(update_fields=["status", "updated_at"])
        moved = transfer_stock(
            idempotency_key=_workflow_key(f"{idempotency_key}:{line.pk}", "transfer-receive"),
            business_unit=locked.business_unit,
            variant=line.variant,
            source_location=locked.source_location,
            destination_location=locked.destination_location,
            quantity=line.quantity,
        )
        line.transfer = StockTransfer.objects.get(pk=moved["transfer_id"])
        line.save(update_fields=["transfer"])
    locked.status = Status.RECEIVED
    locked.received_by = actor
    locked.received_at = now
    locked.updated_at = now
    locked.save(update_fields=["status", "received_by", "received_at", "updated_at"])
    _record(locked, actor, Status.IN_TRANSIT)
    return _complete(operation, _result(locked))


def allowed_steps(transfer_request, user):
    """The next steps this user may take; the services re-check everything."""
    granted = set(
        active_grants(user)
        .filter(business_unit=transfer_request.business_unit)
        .values_list("action", flat=True)
    )
    steps = []
    if transfer_request.status == Status.SUBMITTED and Action.APPROVE_TRANSFER in granted:
        if transfer_request.requested_by_id != user.pk:
            steps += ["approve", "reject"]
    if transfer_request.status in (Status.APPROVED, Status.SCHEDULED) and Action.EXECUTE_TRANSFER in granted:
        steps.append("schedule")
    if transfer_request.status == Status.SCHEDULED and Action.EXECUTE_TRANSFER in granted:
        steps.append("dispatch")
    if transfer_request.status == Status.IN_TRANSIT and Action.EXECUTE_TRANSFER in granted:
        steps.append("receive")
    return steps


def lookup_location(business_unit, location_id):
    location = Location.objects.filter(
        pk=location_id, legal_entity=business_unit.legal_entity,
        business_units=business_unit, is_synthetic=True,
    ).first()
    if location is None:
        raise InvalidTransaction("Choose a location that belongs to this business unit.")
    return location


def lookup_variant(variant_id):
    variant = Variant.objects.select_related("item__brand").filter(pk=variant_id, is_synthetic=True).first()
    if variant is None:
        raise InvalidTransaction("Choose an item from the catalog.")
    return variant
