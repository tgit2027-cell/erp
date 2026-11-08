"""Stock item list, details, photo upload and photo download."""

from django.http import FileResponse
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .purchasing_views import _payload, _run_service
from .stock_items import item_detail, items_overview, scan_item, photo_for_download, update_item, upload_photo


class StockItemListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit = resolve_unit(request)
        return Response(_run_service(
            items_overview, actor=request.user, business_unit=unit, search=request.query_params.get("search", ""),
            include_inactive=request.query_params.get("inactive") == "1",
        ))


class StockItemDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, item_id):
        unit = resolve_unit(request)
        return Response(_run_service(item_detail, actor=request.user, business_unit=unit, item_id=item_id))

    def patch(self, request, item_id):
        unit = resolve_unit(request)
        return Response(_run_service(update_item, actor=request.user, business_unit=unit, item_id=item_id,
                                     data=_payload(request)))


class StockItemPhotoUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request, item_id):
        unit = resolve_unit(request)
        if len(request.FILES.getlist("file")) != 1:
            raise ValidationError({"detail": "Attach one photo."})
        return Response(_run_service(upload_photo, actor=request.user, business_unit=unit, item_id=item_id,
                                     upload=request.FILES["file"]))


class ItemPhotoView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, item_id, name):
        try:
            item = _run_service(photo_for_download, actor=request.user, item_id=item_id, name=name)
        except ValidationError as error:
            raise NotFound("Photo not found.") from error
        response = FileResponse(item.photo.open("rb"), content_type=item.photo_content_type)
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, max-age=86400, immutable"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response


class StockScanView(APIView):
    """GET ?code= to find the item for a scanned label or maker's barcode."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(scan_item, actor=request.user, business_unit=resolve_unit(request),
                                     code=request.query_params.get("code", "")))
