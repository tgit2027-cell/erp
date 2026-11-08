"""Staff letters: overview and asking for a letter, issuing or rejecting it, the PDF, and the unit's letter set-up."""

from django.http import HttpResponse
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .letters import decide_letter, letter_pdf, letters_overview, request_letter, set_letter_settings
from .purchasing_views import _payload, _run_service


def _unit(request):
    """The selected unit, or none for someone who only asks for their own letters."""
    try:
        return resolve_unit(request)
    except PermissionDenied:
        return None


class LetterListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(letters_overview, actor=request.user, business_unit=_unit(request)))

    def post(self, request):
        return Response(_run_service(request_letter, actor=request.user, business_unit=_unit(request), data=_payload(request)),
                        status=201)


class LetterView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, letter_id):
        data = _payload(request)
        return Response(_run_service(decide_letter, actor=request.user, letter_id=letter_id, action=data.get("action"),
                                     reason=data.get("reason", "")))


class LetterPdfView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, letter_id):
        name, pdf = _run_service(letter_pdf, actor=request.user, letter_id=letter_id)
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        response["Cache-Control"] = "no-store"
        return response


class LetterSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response(_run_service(set_letter_settings, actor=request.user, business_unit=_unit(request),
                                     data=_payload(request)))
