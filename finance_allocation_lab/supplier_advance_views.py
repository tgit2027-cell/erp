from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from foundation.finance_lab_views import guarded, _body
from .supplier_advances import save_advance, snapshot
from .models import SupplierAdvanceDraft


def detail(row):
    return {**snapshot(row), "history": list(
        row.revisions.order_by("revision").values("revision", "created_at", "snapshot"))}


@require_http_methods(["GET", "POST"])
@guarded()
def advances(request):
    if request.method == "GET":
        rows = SupplierAdvanceDraft.objects.filter(
            legal_entity=request.finance_entity, is_synthetic=True).order_by("-updated_at", "-pk")
        return JsonResponse({"advances": [{
            "id": row.pk, "revision": row.revision,
            **{key: row.payload[key] for key in ("supplier_reference", "document_reference", "proposed_date", "amount")},
        } for row in rows]})
    with transaction.atomic():
        return JsonResponse(detail(save_advance(data=_body(request), entity=request.finance_entity)), status=201)


@require_http_methods(["GET", "PUT"])
@guarded()
def advance(request, draft_id):
    with transaction.atomic():
        try:
            row = SupplierAdvanceDraft.objects.select_for_update().get(
                pk=draft_id, legal_entity=request.finance_entity, is_synthetic=True)
        except SupplierAdvanceDraft.DoesNotExist:
            return JsonResponse({"detail": "Synthetic supplier advance proposal is unavailable."}, status=404)
        if request.method == "PUT":
            row = save_advance(data=_body(request), entity=request.finance_entity, draft_id=row.pk)
        return JsonResponse(detail(row))
