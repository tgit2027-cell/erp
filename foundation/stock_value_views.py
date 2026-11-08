"""Stock value report and its Excel download, scoped to the selected business unit."""

from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .purchasing_views import _run_service
from .stock_value import XLSX_TYPE, export_stock_value, stock_value_report


class StockValueView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit = resolve_unit(request)
        as_of = request.query_params.get("as_of")
        if request.query_params.get("export") == "xlsx":
            filename, content = _run_service(export_stock_value, actor=request.user, business_unit=unit, as_of=as_of)
            response = HttpResponse(content, content_type=XLSX_TYPE)
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            response["Cache-Control"] = "no-store"
            return response
        return Response(_run_service(stock_value_report, actor=request.user, business_unit=unit, as_of=as_of))
