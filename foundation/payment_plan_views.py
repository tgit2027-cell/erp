"""Payment plan by week or month, and each bank's daily foreign-currency transfer limit."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .payment_plan import payment_plan, set_bank_limit
from .purchasing_views import _payload, _run_service


class PaymentPlanView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params
        return Response(_run_service(payment_plan, actor=request.user, business_unit=resolve_unit(request),
                                     view=query.get("view"), periods=query.get("periods"),
                                     supplier_days=query.get("supplier_days")))


class BankLimitView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response(_run_service(set_bank_limit, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)))
