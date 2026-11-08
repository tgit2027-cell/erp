"""Cost sharing between business units: list, prepare, approve or reject."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .cost_sharing import decide_share, prepare_share, share_detail, sharing_overview
from .purchasing_views import _payload, _run_service


class CostShareListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params
        return Response(_run_service(sharing_overview, actor=request.user, business_unit=resolve_unit(request),
                                     status=query.get("status", ""), month=query.get("month")))

    def post(self, request):
        return Response(_run_service(prepare_share, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)), status=201)


class CostShareView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, share_id):
        return Response(_run_service(share_detail, actor=request.user, business_unit=resolve_unit(request),
                                     share_id=share_id))

    def post(self, request, share_id):
        data = _payload(request)
        return Response(_run_service(decide_share, actor=request.user, business_unit=resolve_unit(request),
                                     share_id=share_id, action=data.get("action"), reason=data.get("reason", "")))
