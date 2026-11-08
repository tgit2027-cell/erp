"""Finance review of inspected customer returns in the selected business unit."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .purchasing_views import _idempotency_key, _payload, _run_service
from .return_refunds import approve_refund, refund_queue, reject_refund


class RefundQueueView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(
            refund_queue, actor=request.user, business_unit=resolve_unit(request),
            status=request.query_params.get("status") or "pending",
        ))


class RefundDecisionView(APIView):
    """POST .../approve/ with {credit_total, refund_method, note} or .../reject/ with {reason}."""

    permission_classes = [IsAuthenticated]
    action = None

    def post(self, request, intake_id):
        data = _payload(request)
        common = {
            "actor": request.user, "business_unit": resolve_unit(request), "intake_id": intake_id,
            "idempotency_key": _idempotency_key(request, data),
        }
        if self.action == "approve":
            return Response(_run_service(
                approve_refund, **common, credit_total=data.get("credit_total"),
                refund_method=data.get("refund_method"), note=data.get("note", ""),
            ))
        return Response(_run_service(reject_refund, **common, reason=data.get("reason")))
