"""Supplier catalogue and price-list reading: upload, review drafts, create items, draft photos."""

from django.http import FileResponse
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .catalogue_reader import create_items, discard_import, draft_photo, import_detail, overview, read_catalogue
from .purchasing_views import _payload, _run_service


class CatalogueImportListView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def get(self, request):
        return Response(_run_service(overview, actor=request.user, business_unit=resolve_unit(request)))

    def post(self, request):
        unit = resolve_unit(request)
        if len(request.FILES.getlist("file")) != 1:
            raise ValidationError({"detail": "Attach one catalogue or price list."})
        return Response(_run_service(
            read_catalogue, actor=request.user, business_unit=unit, upload=request.FILES["file"],
            supplier_id=request.data.get("supplier_id"), pages=request.data.get("pages", ""),
            prefix=request.data.get("prefix", "TVF"),
        ), status=201)


class CatalogueImportDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, import_id):
        return Response(_run_service(import_detail, actor=request.user, business_unit=resolve_unit(request), import_id=import_id))

    def delete(self, request, import_id):
        return Response(_run_service(discard_import, actor=request.user, business_unit=resolve_unit(request), import_id=import_id))


class CatalogueImportCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request, import_id):
        unit = resolve_unit(request)
        data = _payload(request)
        return Response(_run_service(create_items, actor=request.user, business_unit=unit, import_id=import_id,
                                     choices=data.get("items")))


class CatalogueDraftPhotoView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, import_id, index, name):
        unit = resolve_unit(request)
        try:
            handle = _run_service(draft_photo, actor=request.user, business_unit=unit, import_id=import_id,
                                  index=index, name=name)
        except ValidationError as error:
            raise NotFound("Photo not found.") from error
        response = FileResponse(handle, content_type="image/jpeg")
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, max-age=3600"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response
