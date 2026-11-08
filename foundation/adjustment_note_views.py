"""Credit and debit notes: list, prepare, approve or reject."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .adjustment_notes import decide_note, note_detail, notes_overview, prepare_note
from .purchasing_views import _payload, _run_service


class AdjustmentNoteListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params
        return Response(_run_service(notes_overview, actor=request.user, business_unit=resolve_unit(request),
                                     side=query.get("side", "customer"), status=query.get("status", ""),
                                     search=query.get("search", "")))

    def post(self, request):
        return Response(_run_service(prepare_note, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)), status=201)


class AdjustmentNoteView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, note_id):
        return Response(_run_service(note_detail, actor=request.user, business_unit=resolve_unit(request), note_id=note_id))

    def post(self, request, note_id):
        data = _payload(request)
        return Response(_run_service(decide_note, actor=request.user, business_unit=resolve_unit(request), note_id=note_id,
                                     action=data.get("action"), reason=data.get("reason", "")))
