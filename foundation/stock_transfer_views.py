"""HTTP endpoints for synthetic stock transfer requests."""

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import active_grants, resolve_unit
from .models import Location, StockBalance, StockTransferRequest, UserActionGrant
from .purchasing_views import _idempotency_key, _payload, _run_service
from .stock_transfer_requests import (
    allowed_steps,
    decide_transfer_request,
    dispatch_transfer_request,
    lookup_location,
    lookup_variant,
    receive_transfer_request,
    schedule_transfer_request,
    submit_transfer_request,
)
from .transaction_services import TransactionServiceError, held_quantity


TRANSFER_ACTIONS = (
    UserActionGrant.Action.REQUEST_TRANSFER,
    UserActionGrant.Action.APPROVE_TRANSFER,
    UserActionGrant.Action.EXECUTE_TRANSFER,
)


def _transfer_grants(user, unit):
    return set(
        active_grants(user)
        .filter(business_unit=unit, action__in=TRANSFER_ACTIONS)
        .values_list("action", flat=True)
    )


def _transfer_unit(request):
    unit = resolve_unit(request)
    if not _transfer_grants(request.user, unit):
        raise PermissionDenied("You have no stock transfer permission for this business unit.")
    return unit


def _person(user):
    if user is None:
        return None
    return user.get_full_name() or user.username


def _serialize(transfer_request, user):
    return {
        "id": transfer_request.pk,
        "status": transfer_request.status,
        "status_label": transfer_request.get_status_display(),
        "source_location": {"id": transfer_request.source_location_id, "name": transfer_request.source_location.name},
        "destination_location": {
            "id": transfer_request.destination_location_id,
            "name": transfer_request.destination_location.name,
        },
        "note": transfer_request.note,
        "requested_by": _person(transfer_request.requested_by),
        "requested_at": transfer_request.created_at.isoformat(),
        "decided_by": _person(transfer_request.decided_by),
        "decided_at": transfer_request.decided_at.isoformat() if transfer_request.decided_at else None,
        "rejection_reason": transfer_request.rejection_reason,
        "transfer_date": transfer_request.transfer_date.isoformat() if transfer_request.transfer_date else None,
        "dispatched_at": transfer_request.dispatched_at.isoformat() if transfer_request.dispatched_at else None,
        "received_by": _person(transfer_request.received_by),
        "received_at": transfer_request.received_at.isoformat() if transfer_request.received_at else None,
        "is_mine": transfer_request.requested_by_id == user.pk,
        "lines": [
            {
                "variant_id": line.variant_id,
                "sku": line.variant.sku,
                "description": line.variant.description,
                "quantity": format(line.quantity, "f"),
            }
            for line in transfer_request.lines.all()
        ],
        "events": [
            {
                "to_status": event.to_status,
                "actor": _person(event.actor),
                "note": event.note,
                "occurred_at": event.occurred_at.isoformat(),
            }
            for event in transfer_request.events.all()
        ],
        "next_steps": allowed_steps(transfer_request, user),
    }


def _requests_for(unit):
    return (
        StockTransferRequest.objects.filter(business_unit=unit, is_synthetic=True)
        .select_related("source_location", "destination_location", "requested_by", "decided_by", "received_by")
        .prefetch_related("lines__variant", "events__actor")
    )


def _request_for_user(request, request_id):
    transfer_request = get_object_or_404(
        StockTransferRequest.objects.select_related("business_unit__legal_entity"),
        pk=request_id,
        is_synthetic=True,
    )
    if not _transfer_grants(request.user, transfer_request.business_unit):
        raise PermissionDenied("You have no stock transfer permission for this business unit.")
    return transfer_request


def _id(value, field):
    if type(value) is not int or value < 1:
        raise ValidationError({field: "Give a positive whole-number ID."})
    return value


def _service_lookup(function, *args):
    try:
        return function(*args)
    except TransactionServiceError as error:
        raise ValidationError({"detail": str(error)}) from error


class TransferRequestListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit = _transfer_unit(request)
        grants = _transfer_grants(request.user, unit)
        status_filter = request.query_params.get("status")
        records = _requests_for(unit)
        if status_filter:
            if status_filter not in StockTransferRequest.Status.values:
                raise ValidationError({"status": "Unknown status."})
            records = records.filter(status=status_filter)
        locations = Location.objects.filter(
            legal_entity=unit.legal_entity, business_units=unit, is_synthetic=True,
        ).order_by("name")
        return Response({
            "unit": {"id": unit.pk, "code": unit.code, "name": unit.name},
            "permissions": {
                "can_request": UserActionGrant.Action.REQUEST_TRANSFER in grants,
                "can_approve": UserActionGrant.Action.APPROVE_TRANSFER in grants,
                "can_execute": UserActionGrant.Action.EXECUTE_TRANSFER in grants,
            },
            "locations": [{"id": location.pk, "name": location.name} for location in locations],
            "results": [_serialize(item, request.user) for item in records[:100]],
        })

    def post(self, request):
        data = _payload(request)
        unit = _transfer_unit(request)
        lines = data.get("lines")
        if not isinstance(lines, list):
            raise ValidationError({"lines": "Pick at least one item."})
        parsed = []
        for line in lines:
            if not isinstance(line, dict):
                raise ValidationError({"lines": "Each line needs an item and a quantity."})
            parsed.append({
                "variant": _service_lookup(lookup_variant, _id(line.get("variant_id"), "variant_id")),
                "quantity": line.get("quantity"),
            })
        result = _run_service(
            submit_transfer_request,
            actor=request.user,
            business_unit=unit,
            idempotency_key=_idempotency_key(request, data),
            source_location=_service_lookup(
                lookup_location, unit, _id(data.get("source_location_id"), "source_location_id"),
            ),
            destination_location=_service_lookup(
                lookup_location, unit, _id(data.get("destination_location_id"), "destination_location_id"),
            ),
            lines=parsed,
            note=data.get("note", ""),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class TransferRequestStockView(APIView):
    """Items with free stock at one location, for the request form's item picker."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit = _transfer_unit(request)
        if UserActionGrant.Action.REQUEST_TRANSFER not in _transfer_grants(request.user, unit):
            raise PermissionDenied("You cannot request stock transfers for this business unit.")
        try:
            location_id = int(request.query_params.get("location", ""))
        except ValueError:
            raise ValidationError({"location": "Choose a location."}) from None
        location = _service_lookup(lookup_location, unit, location_id)
        balances = StockBalance.objects.filter(
            business_unit=unit, location=location, is_synthetic=True, on_hand_quantity__gt=0,
        ).select_related("variant__item").order_by("variant__sku")[:200]
        items = []
        for balance in balances:
            free = balance.available_quantity - held_quantity(balance)
            if free > 0:
                items.append({
                    "variant_id": balance.variant_id,
                    "sku": balance.variant.sku,
                    "description": balance.variant.description,
                    "item_code": balance.variant.item.item_code,
                    "free_quantity": format(free, "f"),
                })
        return Response({"location": {"id": location.pk, "name": location.name}, "items": items})


class TransferRequestDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, request_id):
        transfer_request = _request_for_user(request, request_id)
        record = _requests_for(transfer_request.business_unit).get(pk=transfer_request.pk)
        return Response(_serialize(record, request.user))


class TransferRequestStepView(APIView):
    permission_classes = [IsAuthenticated]
    step = None

    def post(self, request, request_id):
        data = _payload(request)
        transfer_request = _request_for_user(request, request_id)
        common = {
            "actor": request.user,
            "transfer_request": transfer_request,
            "idempotency_key": _idempotency_key(request, data),
        }
        if self.step == "approve":
            result = _run_service(decide_transfer_request, approve=True, reason=data.get("note", ""), **common)
        elif self.step == "reject":
            result = _run_service(decide_transfer_request, approve=False, reason=data.get("reason", ""), **common)
        elif self.step == "schedule":
            result = _run_service(schedule_transfer_request, transfer_date=data.get("transfer_date"), **common)
        elif self.step == "dispatch":
            result = _run_service(dispatch_transfer_request, **common)
        else:
            result = _run_service(receive_transfer_request, **common)
        return Response(result)
