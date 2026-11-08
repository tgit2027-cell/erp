"""Manual journal entries: list, prepare, send for approval, approve or send back, reverse."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .manual_journals import decide_journal, journal_detail, journals_overview, reverse_journal, save_journal
from .purchasing_views import _payload, _run_service


class ManualJournalListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params
        return Response(_run_service(journals_overview, actor=request.user, business_unit=resolve_unit(request),
                                     status=query.get("status", ""), month=query.get("month"),
                                     search=query.get("search", "")))

    def post(self, request):
        return Response(_run_service(save_journal, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)), status=201)


class ManualJournalView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, journal_id):
        return Response(_run_service(journal_detail, actor=request.user, business_unit=resolve_unit(request),
                                     journal_id=journal_id))

    def post(self, request, journal_id):
        unit, data = resolve_unit(request), _payload(request)
        action = data.get("action")
        if action == "save":
            return Response(_run_service(save_journal, actor=request.user, business_unit=unit, data=data,
                                         journal_id=journal_id))
        if action == "reverse":
            return Response(_run_service(reverse_journal, actor=request.user, business_unit=unit,
                                         journal_id=journal_id, date=data.get("date")), status=201)
        return Response(_run_service(decide_journal, actor=request.user, business_unit=unit, journal_id=journal_id,
                                     action=action, reason=data.get("reason", "")))
