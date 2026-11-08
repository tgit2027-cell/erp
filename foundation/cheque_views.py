"""Cheques: the list and recording a cheque, and depositing, clearing, bouncing, returning or cancelling one."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .cheques import cheques_overview, record_cheque, update_cheque
from .purchasing_views import _payload, _run_service


class ChequeListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(cheques_overview, actor=request.user, business_unit=resolve_unit(request)))

    def post(self, request):
        return Response(_run_service(record_cheque, actor=request.user, business_unit=resolve_unit(request), data=_payload(request)),
                        status=201)


class ChequeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, cheque_id):
        data = _payload(request)
        return Response(_run_service(update_cheque, actor=request.user, business_unit=resolve_unit(request), cheque_id=cheque_id,
                                     action=data.get("action"), reason=data.get("reason", ""),
                                     account_id=data.get("account_id")))
