"""Stock count endpoints, scoped to the selected business unit."""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .purchasing_views import _idempotency_key, _payload, _run_service
from .stock_counts import (
    approve_count,
    cancel_count,
    count_detail,
    counts_overview,
    decide_line_action,
    record_counts,
    request_line_action,
    return_count,
    start_count,
    submit_count,
)


class StockCountListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(counts_overview, actor=request.user, business_unit=resolve_unit(request)))

    def post(self, request):
        data = _payload(request)
        return Response(_run_service(
            start_count, actor=request.user, business_unit=resolve_unit(request),
            location_id=data.get("location_id"), note=data.get("note", ""),
        ), status=status.HTTP_201_CREATED)


class StockCountDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, count_id):
        return Response(_run_service(count_detail, actor=request.user, business_unit=resolve_unit(request), count_id=count_id))


class StockCountActionView(APIView):
    permission_classes = [IsAuthenticated]
    action = None

    def post(self, request, count_id):
        data = _payload(request)
        unit = resolve_unit(request)
        common = {"actor": request.user, "business_unit": unit, "count_id": count_id}
        if self.action == "counts":
            return Response(_run_service(record_counts, entries=data.get("entries"), **common))
        if self.action == "submit":
            return Response(_run_service(submit_count, **common))
        if self.action == "return":
            return Response(_run_service(return_count, note=data.get("note", ""), **common))
        if self.action == "cancel":
            return Response(_run_service(cancel_count, note=data.get("note", ""), **common))
        return Response(_run_service(
            approve_count, idempotency_key=_idempotency_key(request, data), note=data.get("note", ""), **common,
        ))


class StockCountLineActionView(APIView):
    """The warehouse manager's action on one difference, and Finance's decision on it."""

    permission_classes = [IsAuthenticated]
    action = None

    def post(self, request, count_id, line_id):
        data = _payload(request)
        common = {
            "actor": request.user, "business_unit": resolve_unit(request), "count_id": count_id, "line_id": line_id,
            "idempotency_key": _idempotency_key(request, data), "note": data.get("note", ""),
        }
        if self.action == "request":
            return Response(_run_service(request_line_action, action=data.get("action"), **common))
        return Response(_run_service(decide_line_action, approve=data.get("approve"), **common))
