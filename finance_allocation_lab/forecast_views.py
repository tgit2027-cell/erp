from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from foundation.finance_lab_views import guarded, _body
from .forecasts import save_forecast, snapshot
from .models import CashForecastDraft


def detail(row):
    return {**snapshot(row), "history": list(
        row.revisions.order_by("revision").values("revision", "created_at", "snapshot"))}


@require_http_methods(["GET", "POST"])
@guarded()
def forecasts(request):
    if request.method == "GET":
        rows = CashForecastDraft.objects.filter(
            legal_entity=request.finance_entity, is_synthetic=True).order_by("-updated_at", "-pk")
        return JsonResponse({"forecasts": [
            {"id": row.pk, "name": row.payload["name"], "revision": row.revision} for row in rows]})
    with transaction.atomic():
        row = save_forecast(data=_body(request), entity=request.finance_entity)
        return JsonResponse(detail(row), status=201)


@require_http_methods(["GET", "PUT"])
@guarded()
def forecast(request, draft_id):
    with transaction.atomic():
        try:
            row = CashForecastDraft.objects.select_for_update().get(
                pk=draft_id, legal_entity=request.finance_entity, is_synthetic=True)
        except CashForecastDraft.DoesNotExist:
            return JsonResponse({"detail": "Synthetic forecast draft is unavailable."}, status=404)
        if request.method == "PUT":
            row = save_forecast(data=_body(request), entity=request.finance_entity, draft_id=row.pk)
        return JsonResponse(detail(row))
