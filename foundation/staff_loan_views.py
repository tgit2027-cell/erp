"""Staff loans and salary advances: list, request, approve or reject, pay out, cancel."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .purchasing_views import _payload, _run_service
from .staff_loans import decide_loan, loans_overview, request_loan


class StaffLoanListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(loans_overview, actor=request.user, business_unit=resolve_unit(request)))

    def post(self, request):
        return Response(_run_service(request_loan, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)), status=201)


class StaffLoanView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, loan_id):
        data = _payload(request)
        return Response(_run_service(decide_loan, actor=request.user, business_unit=resolve_unit(request), loan_id=loan_id,
                                     action=data.get("action"), note=data.get("note", ""), account_id=data.get("account_id"),
                                     date=data.get("date")))
