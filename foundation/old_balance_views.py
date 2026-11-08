"""Old system balances: what is still owed on them, and receipts or payments a second person approves."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .old_balances import decide, overview, prepare
from .purchasing_views import _payload, _run_service


class OldBalanceListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(overview, actor=request.user, business_unit=resolve_unit(request),
                                     side=request.query_params.get("side", "customer")))

    def post(self, request):
        return Response(_run_service(prepare, actor=request.user, business_unit=resolve_unit(request), data=_payload(request)),
                        status=201)


class OldBalanceSettlementView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, settlement_id):
        data = _payload(request)
        return Response(_run_service(decide, actor=request.user, business_unit=resolve_unit(request), settlement_id=settlement_id,
                                     action=data.get("action"), note=data.get("note", "")))
