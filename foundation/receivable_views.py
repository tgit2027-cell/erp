"""Read-only receivables and payables aging, and customer statements, for the selected business unit."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .payables import payables_aging
from .purchasing_views import _run_service
from .receivables import customer_statement, receivables_aging


class ReceivablesAgingView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(
            receivables_aging, actor=request.user, business_unit=resolve_unit(request),
            as_of=request.query_params.get("as_of"), by=request.query_params.get("by"),
        ))


class CustomerStatementView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, customer_id):
        params = request.query_params
        return Response(_run_service(
            customer_statement, actor=request.user, business_unit=resolve_unit(request), customer_id=customer_id,
            start=params.get("start"), end=params.get("end"), show=params.get("show"), by=params.get("by"),
        ))


class PayablesAgingView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(
            payables_aging, actor=request.user, business_unit=resolve_unit(request),
            as_of=request.query_params.get("as_of"), by=request.query_params.get("by"),
        ))
