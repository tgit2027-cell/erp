"""End-of-service settlements: list people leaving, prepare, approve or cancel."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .end_of_service import decide_settlement, prepare_settlement, settlements_overview
from .purchasing_views import _payload, _run_service


class SettlementListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(settlements_overview, actor=request.user, business_unit=resolve_unit(request)))

    def post(self, request):
        return Response(_run_service(prepare_settlement, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)), status=201)


class SettlementView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, settlement_id):
        data = _payload(request)
        return Response(_run_service(decide_settlement, actor=request.user, business_unit=resolve_unit(request),
                                     settlement_id=settlement_id, action=data.get("action"), reason=data.get("reason", "")))
