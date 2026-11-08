"""Commission and rewards for the selected business unit's company and year."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import incentives
from .access import resolve_unit
from .purchasing_views import _payload, _run_service

ACTIONS = {
    "open_year": incentives.open_year,
    "policy": incentives.save_policy,
    "approve_policy": incentives.approve_policy,
    "company": incentives.save_company,
    "reward_month": incentives.set_reward_month,
    "unit": incentives.save_unit,
    "validate_unit": incentives.validate_unit,
    "team": incentives.save_team,
    "plan": incentives.save_entitlement,
    "decide_plan": incentives.decide_entitlement,
    "prepare": incentives.prepare_run,
    "send_back": incentives.discard_run,
    "check": incentives.check_run,
    "approve": incentives.approve_run,
    "release": incentives.release_run,
}


class IncentiveView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(incentives.overview, actor=request.user, business_unit=resolve_unit(request),
                                     year=request.query_params.get("year")))

    def post(self, request):
        data = _payload(request)
        service = ACTIONS.get(data.get("action"))
        if service is None:
            return Response({"detail": "Choose what to do."}, status=400)
        return Response(_run_service(service, actor=request.user, business_unit=resolve_unit(request), data=data))
