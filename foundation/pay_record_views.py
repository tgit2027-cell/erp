"""Pay records: salaries and allowances, and each unit's working hours."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .pay_records import pay_overview, set_pay, set_working_hours
from .purchasing_views import _payload, _run_service


class PayListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(pay_overview, actor=request.user, business_unit=resolve_unit(request)))

    def post(self, request):
        return Response(_run_service(set_pay, actor=request.user, business_unit=resolve_unit(request), data=_payload(request)),
                        status=201)


class WorkingHoursView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response(_run_service(set_working_hours, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)))
