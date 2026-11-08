"""Read-only general ledger endpoints for the selected business unit or its whole company (scope=company)."""

from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .general_ledger import XLSX_TYPE, account_ledger, export_ledger, journal_entries, journal_entry_detail, trial_balance
from .financial_statements import balance_sheet, close_month, income_statement, month_close_overview, reopen_month
from .fx_revaluation import fx_revaluation_overview, post_fx_revaluation
from .purchasing_views import _payload, _run_service


def _common(request):
    params = request.query_params
    return {
        "actor": request.user, "business_unit": resolve_unit(request),
        "scope": params.get("scope") or "unit",
    }


class TrialBalanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = request.query_params
        return Response(_run_service(trial_balance, **_common(request), start=params.get("start"), end=params.get("end")))


class AccountLedgerView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, account_id):
        params = request.query_params
        return Response(_run_service(
            account_ledger, **_common(request), account_id=account_id,
            start=params.get("start"), end=params.get("end"), page=params.get("page"),
        ))


class JournalEntryListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = request.query_params
        return Response(_run_service(
            journal_entries, **_common(request),
            start=params.get("start"), end=params.get("end"), kind=params.get("kind") or None,
            search=params.get("q"), page=params.get("page"),
        ))


class JournalEntryDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, entry_id):
        return Response(_run_service(journal_entry_detail, **_common(request), entry_id=entry_id))


class LedgerExportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, view):
        params = request.query_params
        filename, content = _run_service(
            export_ledger, **_common(request), view=view,
            start=params.get("start"), end=params.get("end"), account_id=params.get("account"),
            kind=params.get("kind") or None, search=params.get("q"),
        )
        response = HttpResponse(content, content_type=XLSX_TYPE)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "no-store"
        return response


class IncomeStatementView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = request.query_params
        return Response(_run_service(income_statement, **_common(request), start=params.get("start"), end=params.get("end")))


class BalanceSheetView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(balance_sheet, **_common(request), as_of=request.query_params.get("as_of")))


class MonthCloseView(APIView):
    """GET the unit's months; POST {month, reason?} to .../close/ or {month, reason} to .../reopen/."""

    permission_classes = [IsAuthenticated]
    action = None

    def get(self, request):
        return Response(_run_service(month_close_overview, actor=request.user, business_unit=resolve_unit(request)))

    def post(self, request):
        data = _payload(request)
        common = {"actor": request.user, "business_unit": resolve_unit(request), "month": data.get("month")}
        if self.action == "close":
            return Response(_run_service(close_month, **common, reason=data.get("reason", "")))
        return Response(_run_service(reopen_month, **common, reason=data.get("reason")))


class FxRevaluationView(APIView):
    """GET ?month=YYYY-MM&rate_EUR=4.05 to preview; POST {month, rates: {EUR: "4.05"}, source} to post."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = request.query_params
        rates = {key[5:]: value for key, value in params.items() if key.startswith("rate_")}
        return Response(_run_service(fx_revaluation_overview, actor=request.user, business_unit=resolve_unit(request),
                                     month=params.get("month") or None, rates=rates))

    def post(self, request):
        data = _payload(request)
        return Response(_run_service(post_fx_revaluation, actor=request.user, business_unit=resolve_unit(request),
                                     month=data.get("month"), rates=data.get("rates"), source=data.get("source")))
