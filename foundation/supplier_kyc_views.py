"""Supplier onboarding (KYC) endpoints."""

from django.http import FileResponse
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .purchasing_views import _payload, _run_service
from .supplier_kyc import (
    add_document,
    decide_supplier,
    document_for_download,
    kyc_detail,
    kyc_overview,
    open_supplier,
    submit_supplier,
    update_supplier,
)


class SupplierKycListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(kyc_overview, actor=request.user, status=request.query_params.get("status") or None))

    def post(self, request):
        return Response(
            _run_service(open_supplier, actor=request.user, data=_payload(request)),
            status=status.HTTP_201_CREATED,
        )


class SupplierKycDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, kyc_id):
        return Response(_run_service(kyc_detail, actor=request.user, kyc_id=kyc_id))

    def patch(self, request, kyc_id):
        return Response(_run_service(update_supplier, actor=request.user, kyc_id=kyc_id, data=_payload(request)))


class SupplierKycSubmitView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, kyc_id):
        return Response(_run_service(submit_supplier, actor=request.user, kyc_id=kyc_id))


class SupplierKycDecisionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, kyc_id):
        data = _payload(request)
        return Response(_run_service(
            decide_supplier, actor=request.user, kyc_id=kyc_id, approve=data.get("approve"), note=data.get("note", ""),
        ))


class SupplierKycDocumentListView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, kyc_id):
        if len(request.FILES.getlist("file")) != 1:
            raise ValidationError({"file": "Attach one file at a time."})
        return Response(
            _run_service(
                add_document, actor=request.user, kyc_id=kyc_id,
                kind=request.data.get("kind"), upload=request.FILES["file"],
            ),
            status=status.HTTP_201_CREATED,
        )


class SupplierKycDocumentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, kyc_id, document_id):
        try:
            document = _run_service(document_for_download, actor=request.user, kyc_id=kyc_id, document_id=document_id)
        except ValidationError as error:
            raise NotFound("Document not found.") from error
        response = FileResponse(
            document.file.open("rb"), as_attachment=True, filename=document.original_name,
            content_type=document.content_type,
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response

