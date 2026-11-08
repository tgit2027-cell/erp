"""Leave: overview, request, approve or reject, cancel, adjust balances."""

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .leave import adjust_leave, decide_leave, leave_overview, request_leave
from .purchasing_views import _payload, _run_service


def _unit(request):
    """The selected unit, or none for someone who only uses their own leave."""
    try:
        return resolve_unit(request)
    except PermissionDenied:
        return None


class LeaveListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(leave_overview, actor=request.user, business_unit=_unit(request)))

    def post(self, request):
        return Response(_run_service(request_leave, actor=request.user, data=_payload(request)), status=201)


class LeaveRequestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        data = _payload(request)
        return Response(_run_service(decide_leave, actor=request.user, request_id=request_id, action=data.get("action"),
                                     note=data.get("note", "")))


class LeaveAdjustmentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response(_run_service(adjust_leave, actor=request.user, data=_payload(request)), status=201)
