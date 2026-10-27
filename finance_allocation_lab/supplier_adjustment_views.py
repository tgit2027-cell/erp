from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from foundation.finance_lab_views import guarded, _body
from .supplier_adjustments import save_adjustment, snapshot
from .models import SupplierAdjustmentDraft


def detail(row):
    return {**snapshot(row), "history": list(
        row.revisions.order_by("revision").values("revision", "created_at", "snapshot"))}


@require_http_methods(["GET", "POST"])
@guarded()
def adjustments(request):
    if request.method == "GET":
        rows = SupplierAdjustmentDraft.objects.filter(
            legal_entity=request.finance_entity, is_synthetic=True).order_by("-updated_at", "-pk")
        return JsonResponse({"adjustments": [{
            "id": row.pk, "proposal_type": row.payload["proposal_type"],
            "supplier_reference": row.payload["supplier_reference"],
            "document_reference": row.payload["document_reference"], "revision": row.revision,
        } for row in rows]})
    with transaction.atomic():
        return JsonResponse(detail(save_adjustment(data=_body(request), entity=request.finance_entity)), status=201)


@require_http_methods(["GET", "PUT"])
@guarded()
def adjustment(request, draft_id):
    with transaction.atomic():
        try:
            row = SupplierAdjustmentDraft.objects.select_for_update().get(
                pk=draft_id, legal_entity=request.finance_entity, is_synthetic=True)
        except SupplierAdjustmentDraft.DoesNotExist:
            return JsonResponse({"detail": "Synthetic supplier credit/debit proposal is unavailable."}, status=404)
        if request.method == "PUT":
            row = save_adjustment(data=_body(request), entity=request.finance_entity, draft_id=row.pk)
        return JsonResponse(detail(row))
