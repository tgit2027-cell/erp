from django.http import JsonResponse
from django.db import transaction
from django.views.decorators.http import require_http_methods
from foundation.finance_lab_views import guarded, _body
from .models import ExpenseProposal
from .expenses import save_proposal, snapshot


def detail(row):
    return {**snapshot(row), "history": list(
        row.revisions.order_by("revision").values("revision", "created_at", "snapshot"))}


@require_http_methods(["GET", "POST"])
@guarded()
def proposals(request):
    if request.method == "GET":
        rows = ExpenseProposal.objects.filter(legal_entity=request.finance_entity, is_synthetic=True).order_by("-updated_at", "-pk")
        return JsonResponse({"proposals": [
            {"id": row.pk, "name": row.payload["name"], "proposal_type": row.payload["proposal_type"],
             "revision": row.revision} for row in rows]})
    with transaction.atomic():
        row = save_proposal(data=_body(request), entity=request.finance_entity)
        return JsonResponse(detail(row), status=201)


@require_http_methods(["GET", "PUT"])
@guarded()
def proposal(request, proposal_id):
    with transaction.atomic():
        try:
            row = ExpenseProposal.objects.select_for_update().get(
                pk=proposal_id, legal_entity=request.finance_entity, is_synthetic=True)
        except ExpenseProposal.DoesNotExist:
            return JsonResponse({"detail": "Synthetic expense proposal is unavailable."}, status=404)
        if request.method == "PUT":
            row = save_proposal(data=_body(request), entity=request.finance_entity, proposal_id=row.pk)
        return JsonResponse(detail(row))
