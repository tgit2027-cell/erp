"""Low-stock levels and alerts, scoped to the selected business unit."""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .low_stock import alert_count, decide_request, low_stock_overview, remove_level, send_to_purchasing, set_level
from .purchasing_views import _payload, _run_service


class LowStockView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit = resolve_unit(request)
        if request.query_params.get("summary"):
            return Response({"alerts": alert_count(actor=request.user, business_unit=unit)})
        return Response(_run_service(low_stock_overview, actor=request.user, business_unit=unit))

    def post(self, request):
        unit = resolve_unit(request)
        data = _payload(request)
        result = _run_service(
            set_level, actor=request.user, business_unit=unit, variant_id=data.get("variant_id"),
            location_id=data.get("location_id"), minimum_quantity=data.get("minimum_quantity"),
            reorder_quantity=data.get("reorder_quantity"), note=data.get("note", ""),
        )
        return Response(result, status=status.HTTP_201_CREATED if result["created"] else status.HTTP_200_OK)


class LowStockLevelView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, level_id):
        unit = resolve_unit(request)
        return Response(_run_service(remove_level, actor=request.user, business_unit=unit, level_id=level_id))


class ReorderRequestListView(APIView):
    """POST {lines: [{level_id, quantity}], note} to send suggestions to purchasing."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = _payload(request)
        return Response(_run_service(send_to_purchasing, actor=request.user, business_unit=resolve_unit(request),
                                     lines=data.get("lines"), note=data.get("note", "")), status=status.HTTP_201_CREATED)


class ReorderRequestView(APIView):
    """POST {action: ordered | declined | cancel, reason}."""

    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        data = _payload(request)
        return Response(_run_service(decide_request, actor=request.user, business_unit=resolve_unit(request),
                                     request_id=request_id, action=data.get("action"), reason=data.get("reason", "")))
