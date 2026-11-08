"""Overtime: month overview, record before the work, approve, reject or cancel."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .leave_views import _unit
from .overtime import decide_overtime, overtime_overview, prepare_overtime
from .purchasing_views import _payload, _run_service


class OvertimeListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(overtime_overview, actor=request.user, business_unit=_unit(request),
                                     month=request.query_params.get("month")))

    def post(self, request):
        return Response(_run_service(prepare_overtime, actor=request.user, business_unit=_unit(request), data=_payload(request)),
                        status=201)


class OvertimeRequestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        data = _payload(request)
        return Response(_run_service(decide_overtime, actor=request.user, request_id=request_id, action=data.get("action"),
                                     note=data.get("note", "")))
