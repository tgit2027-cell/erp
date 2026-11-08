"""Budgets: view budget against actual, save, send for approval, approve, send back, reopen."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .budgets import budget_view, decide_budget, save_budget
from .purchasing_views import _payload, _run_service


class BudgetView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params
        return Response(_run_service(budget_view, actor=request.user, business_unit=resolve_unit(request),
                                     year=query.get("year"), month=query.get("month")))

    def post(self, request):
        data = _payload(request)
        return Response(_run_service(save_budget, actor=request.user, business_unit=resolve_unit(request),
                                     year=data.get("year"), amounts=data.get("amounts"), note=data.get("note", "")))


class BudgetDecisionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, year, action):
        data = _payload(request)
        return Response(_run_service(decide_budget, actor=request.user, business_unit=resolve_unit(request),
                                     year=year, action=action, reason=data.get("reason", "")))
