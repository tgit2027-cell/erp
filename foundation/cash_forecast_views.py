"""Cash-flow forecast and its planned items."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .cash_forecast import add_planned_item, cash_forecast, remove_planned_item
from .models import CustomerType
from .purchasing_views import _payload, _run_service


class CashForecastView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params
        return Response(_run_service(cash_forecast, actor=request.user, business_unit=resolve_unit(request),
                                     weeks=query.get("weeks"), customer_days={kind: query.get(f"{kind}_days") for kind in CustomerType.values},
                                     supplier_days=query.get("supplier_days")))

    def post(self, request):
        return Response(_run_service(add_planned_item, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)), status=201)


class CashForecastItemView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, item_id):
        return Response(_run_service(remove_planned_item, actor=request.user, business_unit=resolve_unit(request),
                                     item_id=item_id))
