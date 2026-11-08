"""Expenses and petty cash: list, enter (with receipt), approve, reject, repay staff, receipt download."""

from django.http import FileResponse
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .expenses import (
    approve_expense, expenses_overview, receipt_for_download, reimburse_expense, reject_expense, submit_expense,
)
from .purchasing_views import _payload, _run_service

RECEIPT_EXTENSIONS = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}


class ExpenseListView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, JSONParser]

    def get(self, request):
        query = request.query_params
        return Response(_run_service(
            expenses_overview, actor=request.user, business_unit=resolve_unit(request), status=query.get("status", ""),
            month=query.get("month"), search=query.get("search", ""),
        ))

    def post(self, request):
        unit = resolve_unit(request)
        files = request.FILES.getlist("receipt")
        if len(files) > 1:
            raise ValidationError({"detail": "Attach one receipt."})
        data = {key: request.data.get(key) for key in
                ("date", "category", "amount", "description", "payee", "paid_by", "account_id", "claimant_id")}
        return Response(_run_service(submit_expense, actor=request.user, business_unit=unit, data=data,
                                     receipt=files[0] if files else None), status=201)


class ExpenseDecisionView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request, expense_id):
        unit = resolve_unit(request)
        data = _payload(request)
        decision = data.get("decision")
        if decision == "approve":
            return Response(_run_service(approve_expense, actor=request.user, business_unit=unit, expense_id=expense_id))
        if decision == "reject":
            return Response(_run_service(reject_expense, actor=request.user, business_unit=unit, expense_id=expense_id,
                                         reason=data.get("reason")))
        if decision == "repay":
            return Response(_run_service(reimburse_expense, actor=request.user, business_unit=unit, expense_id=expense_id,
                                         account_id=data.get("account_id"), date=data.get("date")))
        raise ValidationError({"detail": "Approve, reject or repay."})


class ExpenseReceiptView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, expense_id):
        unit = resolve_unit(request)
        try:
            expense = _run_service(receipt_for_download, actor=request.user, business_unit=unit, expense_id=expense_id)
        except ValidationError as error:
            raise NotFound("Receipt not found.") from error
        response = FileResponse(expense.receipt.open("rb"), content_type=expense.receipt_content_type,
                                filename=f"{expense.number}{RECEIPT_EXTENSIONS.get(expense.receipt_content_type, '')}")
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response
