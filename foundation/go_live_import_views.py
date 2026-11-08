"""Go-live import endpoints: overview, template download, checking a file, sending it and deciding."""

from django.http import HttpResponse
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .go_live_imports import MAX_BYTES, XLSX_TYPE, decide, imports_overview, preview, submit, template
from .purchasing_views import _payload, _run_service


def _upload(request):
    files = request.FILES.getlist("file")
    if len(files) != 1:
        raise ValidationError({"file": "Attach one file."})
    upload = files[0]
    if upload.size > MAX_BYTES:
        raise ValidationError({"file": "The file is larger than 5 MB. Split it into smaller files."})
    return {"kind": request.data.get("kind"), "file_name": upload.name, "content": upload.read(),
            "balances_on": request.data.get("balances_on") or None}


class ImportListView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        return Response(_run_service(imports_overview, actor=request.user))

    def post(self, request):
        return Response(_run_service(submit, actor=request.user, **_upload(request)), status=status.HTTP_201_CREATED)


class ImportPreviewView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        return Response(_run_service(preview, actor=request.user, **_upload(request)))


class ImportTemplateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, kind):
        name, content = _run_service(template, actor=request.user, kind=kind)
        response = HttpResponse(content, content_type=XLSX_TYPE)
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        response["Cache-Control"] = "no-store"
        return response


class ImportView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, batch_id):
        data = _payload(request)
        return Response(_run_service(decide, actor=request.user, batch_id=batch_id, action=data.get("action"),
                                     note=data.get("note", "")))
