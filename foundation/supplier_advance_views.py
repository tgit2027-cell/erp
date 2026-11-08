"""Supplier advances: list, prepare, approve or reject, and set against goods."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .purchasing_views import _idempotency_key, _payload, _run_service
from .supplier_advances import advances_overview, decide_advance, prepare_advance, use_advance


class SupplierAdvanceListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(advances_overview, actor=request.user, business_unit=resolve_unit(request),
                                     status=request.query_params.get("status", "")))

    def post(self, request):
        return Response(_run_service(prepare_advance, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)), status=201)


class SupplierAdvanceView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, advance_id):
        unit, data = resolve_unit(request), _payload(request)
        if data.get("action") == "use":
            return Response(_run_service(use_advance, actor=request.user, business_unit=unit, advance_id=advance_id,
                                         receipt_id=data.get("receipt_id"), amount=data.get("amount"),
                                         paid_on=data.get("paid_on"), idempotency_key=_idempotency_key(request, data)),
                            status=201)
        return Response(_run_service(decide_advance, actor=request.user, business_unit=unit, advance_id=advance_id,
                                     action=data.get("action"), reason=data.get("reason", "")))
