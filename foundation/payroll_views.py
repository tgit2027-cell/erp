"""Payroll: month overview, prepare, approve and post, discard, WPS details and the WPS salary file."""

from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .payroll import approve_payroll, discard_payroll, payroll_overview, prepare_payroll, set_wps_settings, wps_file
from .purchasing_views import _payload, _run_service


class PayrollView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(payroll_overview, actor=request.user, business_unit=resolve_unit(request),
                                     month=request.query_params.get("month")))

    def post(self, request):
        return Response(_run_service(prepare_payroll, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)), status=201)


class PayrollRunView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, run_id):
        action = _payload(request).get("action")
        function = {"approve": approve_payroll, "discard": discard_payroll}.get(action)
        if function is None:
            return Response({"detail": "Choose approve or discard."}, status=400)
        return Response(_run_service(function, actor=request.user, business_unit=resolve_unit(request), run_id=run_id))


class PayrollWpsFileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, run_id):
        name, text = _run_service(wps_file, actor=request.user, business_unit=resolve_unit(request), run_id=run_id)
        response = HttpResponse(text, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        response["Cache-Control"] = "no-store"
        return response


class WpsSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response(_run_service(set_wps_settings, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)))
