"""Fixed assets: register list, register a purchase, approve or reject, run depreciation, sell or write off."""

from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .fixed_assets import (
    approve_asset, asset_detail, assets_overview, dispose_asset, reject_asset, run_depreciation, submit_asset,
)
from .purchasing_views import _payload, _run_service


class FixedAssetListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(assets_overview, actor=request.user, business_unit=resolve_unit(request),
                                     status=request.query_params.get("status", "")))

    def post(self, request):
        return Response(_run_service(submit_asset, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)), status=201)


class FixedAssetView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, asset_id):
        return Response(_run_service(asset_detail, actor=request.user, business_unit=resolve_unit(request), asset_id=asset_id))

    def post(self, request, asset_id):
        unit, data = resolve_unit(request), _payload(request)
        decision = data.get("decision")
        if decision == "approve":
            return Response(_run_service(approve_asset, actor=request.user, business_unit=unit, asset_id=asset_id))
        if decision == "reject":
            return Response(_run_service(reject_asset, actor=request.user, business_unit=unit, asset_id=asset_id,
                                         reason=data.get("reason")))
        if decision == "dispose":
            return Response(_run_service(dispose_asset, actor=request.user, business_unit=unit, asset_id=asset_id,
                                         date=data.get("date"), proceeds=data.get("proceeds") or "0",
                                         account_id=data.get("account_id"), reason=data.get("reason")))
        raise ValidationError({"detail": "Approve, reject, or sell or write off."})


class DepreciationRunView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response(_run_service(run_depreciation, actor=request.user, business_unit=resolve_unit(request),
                                     month=_payload(request).get("month")))
