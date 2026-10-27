from django.http import JsonResponse
from django.db import transaction
from django.views.decorators.http import require_http_methods
from foundation.finance_lab_views import guarded, _body
from .models import BudgetScenario
from .budget import save_scenario, snapshot


def detail(row):
    return {**snapshot(row), "history": list(
        row.revisions.order_by("revision").values("revision", "created_at", "snapshot"))}


@require_http_methods(["GET", "POST"])
@guarded()
def scenarios(request):
    if request.method == "GET":
        rows = BudgetScenario.objects.filter(legal_entity=request.finance_entity, is_synthetic=True).order_by("-updated_at", "-pk")
        return JsonResponse({"scenarios": [
            {"id": row.pk, "name": row.payload["name"], "revision": row.revision} for row in rows]})
    with transaction.atomic():
        row = save_scenario(data=_body(request), entity=request.finance_entity)
        return JsonResponse(detail(row), status=201)


@require_http_methods(["GET", "PUT"])
@guarded()
def scenario(request, scenario_id):
    with transaction.atomic():
        try:
            row = BudgetScenario.objects.select_for_update().get(
                pk=scenario_id, legal_entity=request.finance_entity, is_synthetic=True)
        except BudgetScenario.DoesNotExist:
            return JsonResponse({"detail": "Synthetic budget scenario is unavailable."}, status=404)
        if request.method == "PUT":
            row = save_scenario(data=_body(request), entity=request.finance_entity, scenario_id=row.pk)
        return JsonResponse(detail(row))
