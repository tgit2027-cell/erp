"""Approval limits: discount ceilings per role and the most each approver may approve, per business unit."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .approval_limits import overview, set_discount, set_limit, set_person_discount
from .purchasing_views import _payload, _run_service


class ApprovalLimitView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(overview, actor=request.user, business_unit=resolve_unit(request)))

    def post(self, request):
        unit, data = resolve_unit(request), _payload(request)
        if data.get("role"):
            return Response(_run_service(set_discount, actor=request.user, business_unit=unit, role=data.get("role"),
                                         percent=data.get("percent")))
        if data.get("kind") == "person_discount":
            return Response(_run_service(set_person_discount, actor=request.user, business_unit=unit, user_id=data.get("user_id"),
                                         percent=data.get("percent")))
        return Response(_run_service(set_limit, actor=request.user, business_unit=unit, action=data.get("action"),
                                     user_id=data.get("user_id"), max_amount=data.get("max_amount")))
