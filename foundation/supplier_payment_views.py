"""Supplier payment and supplier bill endpoints, scoped to the selected business unit (prepare, then a second person approves)."""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .purchasing_views import _idempotency_key, _payload, _run_service
from .supplier_bills import accept_bill, bills_overview, record_bill, reject_bill, void_bill
from .supplier_payments import approve_payment, payments_overview, reject_payment, request_payment


class SupplierPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit = resolve_unit(request)
        return Response(_run_service(payments_overview, actor=request.user, business_unit=unit))

    def post(self, request):
        data = _payload(request)
        result = _run_service(
            request_payment,
            actor=request.user,
            idempotency_key=_idempotency_key(request, data),
            receipt_id=data.get("receipt_id"),
            charge_id=data.get("charge_id"),
            payee_name=data.get("payee_name", ""),
            amount=data.get("amount"),
            exchange_rate=data.get("exchange_rate"),
            rate_source=data.get("rate_source", ""),
            rate_date=data.get("rate_date"),
            paid_on=data.get("paid_on"),
            bank_reference=data.get("bank_reference", ""),
            advance_id=data.get("advance_id"),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class SupplierPaymentApproveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, payment_id):
        data = _payload(request)
        return Response(_run_service(
            approve_payment, actor=request.user, idempotency_key=_idempotency_key(request, data), payment_id=payment_id,
        ))


class SupplierPaymentRejectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, payment_id):
        data = _payload(request)
        return Response(_run_service(reject_payment, actor=request.user, payment_id=payment_id, note=data.get("note", "")))


class SupplierBillView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit = resolve_unit(request)
        return Response(_run_service(bills_overview, actor=request.user, business_unit=unit))

    def post(self, request):
        data = _payload(request)
        result = _run_service(
            record_bill,
            actor=request.user,
            idempotency_key=_idempotency_key(request, data),
            receipt_id=data.get("receipt_id"),
            bill_number=data.get("bill_number", ""),
            bill_date=data.get("bill_date"),
            amount=data.get("amount"),
            note=data.get("note", ""),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class SupplierBillAcceptView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, bill_id):
        data = _payload(request)
        return Response(_run_service(
            accept_bill, actor=request.user, idempotency_key=_idempotency_key(request, data), bill_id=bill_id,
            note=data.get("note", ""),
        ))


class SupplierBillRejectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, bill_id):
        data = _payload(request)
        return Response(_run_service(reject_bill, actor=request.user, bill_id=bill_id, note=data.get("note", "")))


class SupplierBillVoidView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, bill_id):
        data = _payload(request)
        return Response(_run_service(void_bill, actor=request.user, bill_id=bill_id, note=data.get("note", "")))
