"""Bank and cash endpoints, scoped to the selected business unit."""

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .bank_reconciliation import (
    add_statement_lines,
    approve_reconciliation,
    auto_match,
    match_lines,
    prepare_reconciliation,
    read_statement,
    reconciliation_view,
    record_from_statement,
    reject_reconciliation,
    remove_statement_line,
    unmatch_line,
)
from .cash_accounts import cash_overview, create_account, move_line, record_entry
from .purchasing_views import _idempotency_key, _payload, _run_service


def _int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return -1


class CashOverviewView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit = resolve_unit(request)
        return Response(_run_service(
            cash_overview, actor=request.user, business_unit=unit, account_id=_int(request.query_params.get("account")),
            start=request.query_params.get("start"), end=request.query_params.get("end"),
        ))


class CashAccountView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        unit = resolve_unit(request)
        data = _payload(request)
        result = _run_service(
            create_account, actor=request.user, business_unit=unit, name=data.get("name", ""), kind=data.get("kind"),
            bank_name=data.get("bank_name", ""), account_last4=data.get("account_last4", ""),
            opening_balance=data.get("opening_balance", "0.00"), opening_date=data.get("opening_date"),
            is_default=data.get("is_default") is True, account_type=data.get("account_type", "current"),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class CashEntryView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        unit = resolve_unit(request)
        data = _payload(request)
        result = _run_service(
            record_entry, actor=request.user, idempotency_key=_idempotency_key(request, data), business_unit=unit,
            kind=data.get("kind"), account_id=data.get("account_id"), to_account_id=data.get("to_account_id"),
            amount=data.get("amount"), entry_date=data.get("entry_date"), description=data.get("description", ""),
            reference=data.get("reference", ""),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class CashMoveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        unit = resolve_unit(request)
        data = _payload(request)
        return Response(_run_service(
            move_line, actor=request.user, business_unit=unit, line_id=data.get("line_id"), account_id=data.get("account_id"),
        ))


class BankReconciliationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit = resolve_unit(request)
        return Response(_run_service(
            reconciliation_view, actor=request.user, business_unit=unit,
            account_id=_int(request.query_params.get("account")), month=request.query_params.get("month"),
        ))

    def post(self, request):
        unit = resolve_unit(request)
        data = _payload(request)
        result = _run_service(
            prepare_reconciliation, actor=request.user, idempotency_key=_idempotency_key(request, data), business_unit=unit,
            account_id=data.get("account_id"), month=data.get("month"), statement_opening=data.get("statement_opening"),
            statement_closing=data.get("statement_closing"), note=data.get("note", ""),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class BankReconciliationDecisionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, reconciliation_id, action):
        unit = resolve_unit(request)
        data = _payload(request)
        if action == "approve":
            return Response(_run_service(approve_reconciliation, actor=request.user, business_unit=unit,
                                         reconciliation_id=reconciliation_id))
        return Response(_run_service(reject_reconciliation, actor=request.user, business_unit=unit,
                                     reconciliation_id=reconciliation_id, note=data.get("note", "")))


class StatementLineView(APIView):
    """Add statement lines, or act on one: match, unmatch, remove, auto-match, record as charge or interest."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        unit = resolve_unit(request)
        data = _payload(request)
        action = data.get("action")
        if action == "add":
            result = _run_service(add_statement_lines, actor=request.user, idempotency_key=_idempotency_key(request, data),
                                  business_unit=unit, account_id=data.get("account_id"), lines=data.get("lines"))
            return Response(result, status=status.HTTP_201_CREATED)
        if action == "match":
            return Response(_run_service(match_lines, actor=request.user, business_unit=unit,
                                         statement_line_id=data.get("statement_line_id"), journal_line_id=data.get("journal_line_id")))
        if action == "unmatch":
            return Response(_run_service(unmatch_line, actor=request.user, business_unit=unit,
                                         statement_line_id=data.get("statement_line_id")))
        if action == "remove":
            return Response(_run_service(remove_statement_line, actor=request.user, business_unit=unit,
                                         line_id=data.get("statement_line_id")))
        if action == "auto_match":
            return Response(_run_service(auto_match, actor=request.user, business_unit=unit,
                                         account_id=data.get("account_id"), month=data.get("month")))
        if action == "record":
            return Response(_run_service(record_from_statement, actor=request.user,
                                         idempotency_key=_idempotency_key(request, data), business_unit=unit,
                                         statement_line_id=data.get("statement_line_id"), kind=data.get("kind")),
                            status=status.HTTP_201_CREATED)
        return Response({"detail": "Choose add, match, unmatch, remove, auto_match or record."}, status=status.HTTP_400_BAD_REQUEST)


class StatementReadView(APIView):
    """Read an uploaded statement with AI and return draft lines to check. Nothing is saved here."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        unit = resolve_unit(request)
        if len(request.FILES.getlist("file")) != 1:
            raise ValidationError({"detail": "Attach one statement file."})
        return Response(_run_service(
            read_statement, actor=request.user, business_unit=unit, account_id=_int(request.data.get("account_id")),
            upload=request.FILES["file"], month=request.data.get("month") or None,
        ))
