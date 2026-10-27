"""Synthetic non-posting intake with an owner-confirmed delivery-date window."""

from datetime import date, timedelta
from django.db import transaction
from django.utils import timezone

from .models import Location, SalesQuote, SalesReturnIntake, SalesReturnPolicy, SalesReturnEvent
from .purchasing_services import _require_demo_actor
from .transaction_services import (
    InvalidTransaction, _begin_idempotent_operation, _complete, _money, _quantity,
)

POLICY_SOURCE = (
    "Owner-confirmed company rule: calendar days counted from actual delivery. "
    "Legal validation pending; not a verified statement of Qatar law."
)


def return_policy(unit):
    policy = SalesReturnPolicy.objects.filter(business_unit=unit, is_synthetic=True).first()
    return {
        "provisional_calendar_days": policy.provisional_calendar_days if policy else 14,
        "date_basis": "delivery",
        "source": POLICY_SOURCE,
        "status": "owner_confirmed_pending_legal_validation",
    }


def delivery_return_window(quote, request_date, policy=None):
    """Date-window eligibility only; never financial or condition approval."""
    if quote.delivered_at is None:
        raise InvalidTransaction("An actual delivery timestamp is required to assess the return window.")
    policy = policy or return_policy(quote.business_unit)
    days = policy["provisional_calendar_days"]
    delivered = timezone.localdate(quote.delivered_at)
    deadline = delivered + timedelta(days=days)
    eligible = delivered <= request_date <= deadline
    if request_date < delivered:
        message = f"Return request date cannot precede the actual delivery date {delivered.isoformat()}."
    elif request_date > deadline:
        message = (
            f"Return request is outside the {days}-calendar-day window from actual delivery "
            f"{delivered.isoformat()}; the inclusive deadline is {deadline.isoformat()}."
        )
    else:
        message = (
            f"Within the delivery-date window through {deadline.isoformat()} (inclusive). "
            "Good-condition inspection and Finance review are still required."
        )
    return {
        "date_basis": "delivery", "calendar_days": days,
        "delivery_date": delivered.isoformat(), "deadline": deadline.isoformat(),
        "request_date": request_date.isoformat(), "eligible": eligible, "message": message,
    }


def _authorize(actor, quote):
    _require_demo_actor(actor, quote.business_unit)
    if not quote.is_synthetic or not quote.customer.is_synthetic:
        raise InvalidTransaction("Only synthetic sales/customer records are supported.")


def _location(unit, location_id):
    location = Location.objects.filter(
        pk=location_id, legal_entity=unit.legal_entity,
        business_units=unit, is_synthetic=True,
    ).first()
    if location is None:
        raise InvalidTransaction("Choose an existing location associated with this business unit.")
    return location


def _source_lines(quote):
    lines = list(quote.lines.select_related("variant__item__brand").order_by("pk"))
    if any(not line.is_synthetic or not line.variant.is_synthetic or not line.variant.item.is_synthetic
           or not line.variant.item.brand.is_synthetic for line in lines):
        raise InvalidTransaction("Only synthetic item records are supported.")
    return {line.pk: line for line in lines}


def serialize_return(intake):
    quote = intake.quote
    sources = _source_lines(quote)
    return {
        "id": intake.pk,
        "quote_id": quote.pk,
        "customer": {"id": quote.customer_id, "name": quote.customer.name},
        "business_unit": {"id": quote.business_unit_id, "code": quote.business_unit.code, "name": quote.business_unit.name},
        "request_date": intake.request_date.isoformat(),
        "status": intake.status,
        "receiving_mode": intake.receiving_mode,
        "location": {"id": intake.location_id, "code": intake.location.code, "name": intake.location.name},
        "receipt_collection_details": intake.receipt_collection_details,
        "proposed_refund_amount": format(intake.proposed_refund_amount, ".2f"),
        "currency": "QAR",
        "original_payment_method": intake.original_payment_method,
        "original_method_source": "user_recorded_not_verified",
        "requested_refund_method": intake.requested_refund_method,
        "refund_preference": intake.refund_preference,
        "lines": [
            {**line, "item_id": sources[line["quote_line_id"]].variant.item.item_code,
             "description": sources[line["quote_line_id"]].variant.item.description,
             "brand": sources[line["quote_line_id"]].variant.item.brand.name}
            for line in intake.lines
        ],
        "policy": intake.policy_snapshot,
        "delivery_window": intake.delivery_window,
        "events": [
            {"kind": event.kind, "occurred_at": event.occurred_at.isoformat()}
            for event in intake.events.all()
        ],
    }


@transaction.atomic
def create_return(*, actor, quote, idempotency_key, data):
    _authorize(actor, quote)
    if quote.status != SalesQuote.Status.DELIVERED or quote.delivered_at is None:
        raise InvalidTransaction("Select an already delivered synthetic sale.")
    # Capture only; no return-window decision while date basis is unconfigured.
    try:
        request_date = date.fromisoformat(data["request_date"])
    except (TypeError, ValueError):
        raise InvalidTransaction("Provide an ISO calendar request date.") from None
    location = _location(quote.business_unit, data["location_id"])
    sources = _source_lines(quote)
    captured = []
    seen = set()
    for line in data["lines"]:
        line_id = line["quote_line_id"]
        if line_id not in sources or line_id in seen:
            raise InvalidTransaction("Choose distinct lines from the originating sale.")
        seen.add(line_id)
        quantity = _quantity(line["quantity"])
        if quantity > sources[line_id].quantity:
            raise InvalidTransaction("Requested item quantity exceeds the originating sale line.")
        captured.append({
            "quote_line_id": line_id, "quantity": format(quantity, ".2f"),
            "condition": "uninspected", "inspection_notes": "",
        })
    amount = _money(data["proposed_refund_amount"])
    payload = {"actor": actor.pk, "quote": quote.pk, "data": data}
    record, created = _begin_idempotent_operation(idempotency_key, "sales_return_intake", payload)
    if not created:
        return record.result
    policy = return_policy(quote.business_unit)
    window = delivery_return_window(quote, request_date, policy)
    if not window["eligible"]:
        raise InvalidTransaction(window["message"])
    intake = SalesReturnIntake.objects.create(
        quote=quote, idempotency=record, created_by=actor,
        request_date=request_date, receiving_mode=data["receiving_mode"],
        location=location, receipt_collection_details=data["receipt_collection_details"],
        proposed_refund_amount=amount, original_payment_method=data["original_payment_method"],
        requested_refund_method=data["requested_refund_method"],
        refund_preference=data["refund_preference"], lines=captured,
        policy_snapshot=policy, delivery_window=window,
    )
    SalesReturnEvent.objects.create(
        intake=intake, actor=actor, kind="intake_recorded",
        details={"status": intake.status, "lines": captured},
    )
    return _complete(record, serialize_return(intake))


@transaction.atomic
def inspect_return(*, actor, intake, idempotency_key, lines):
    _authorize(actor, intake.quote)
    _location(intake.quote.business_unit, intake.location_id)
    record, created = _begin_idempotent_operation(
        idempotency_key, "sales_return_inspection",
        {"actor": actor.pk, "intake": intake.pk, "lines": lines},
    )
    if not created:
        return record.result
    intake = SalesReturnIntake.objects.select_for_update().get(pk=intake.pk)
    if intake.delivery_window.get("eligible") is not True:
        raise InvalidTransaction(intake.delivery_window.get(
            "message", "Delivery-date eligibility is unavailable; Finance review remains pending.",
        ))
    if intake.status != "awaiting_inspection":
        raise InvalidTransaction("Inspection is already recorded; Finance review remains pending.")
    inspection = {line["quote_line_id"]: line for line in lines}
    if len(inspection) != len(lines) or set(inspection) != {line["quote_line_id"] for line in intake.lines}:
        raise InvalidTransaction("Inspect every returned line exactly once.")
    intake.lines = [
        {**line, "condition": inspection[line["quote_line_id"]]["condition"],
         "inspection_notes": inspection[line["quote_line_id"]]["inspection_notes"]}
        for line in intake.lines
    ]
    # A not-good finding is recorded without inventing exclusion/disposition.
    intake.status = "finance_pending"
    intake.updated_at = timezone.now()
    intake.save(update_fields=["lines", "status", "updated_at"])
    SalesReturnEvent.objects.create(
        intake=intake, actor=actor, kind="inspection_recorded",
        details={"status": intake.status, "lines": intake.lines},
    )
    return _complete(record, serialize_return(intake))
