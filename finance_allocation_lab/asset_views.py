from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from foundation.finance_lab_views import guarded, _body
from .assets import save_asset, snapshot
from .models import FixedAssetDraft


def detail(row):
    return {**snapshot(row), "history": list(
        row.revisions.order_by("revision").values("revision", "created_at", "snapshot"))}


@require_http_methods(["GET", "POST"])
@guarded()
def assets(request):
    if request.method == "GET":
        rows = FixedAssetDraft.objects.filter(
            legal_entity=request.finance_entity, is_synthetic=True).order_by("-updated_at", "-pk")
        return JsonResponse({"assets": [
            {"id": row.pk, "asset_identifier": row.payload["asset_identifier"],
             "name": row.payload["name"], "revision": row.revision} for row in rows]})
    with transaction.atomic():
        row = save_asset(data=_body(request), entity=request.finance_entity)
        return JsonResponse(detail(row), status=201)


@require_http_methods(["GET", "PUT"])
@guarded()
def asset(request, draft_id):
    with transaction.atomic():
        try:
            row = FixedAssetDraft.objects.select_for_update().get(
                pk=draft_id, legal_entity=request.finance_entity, is_synthetic=True)
        except FixedAssetDraft.DoesNotExist:
            return JsonResponse({"detail": "Synthetic fixed-asset draft is unavailable."}, status=404)
        if request.method == "PUT":
            row = save_asset(data=_body(request), entity=request.finance_entity, draft_id=row.pk)
        return JsonResponse(detail(row))
