from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from foundation.finance_lab_views import guarded, _body
from .customer_credits import save_credit, snapshot
from .models import CustomerCreditDraft


def detail(row):
    return {**snapshot(row), "history": list(
        row.revisions.order_by("revision").values("revision", "created_at", "snapshot"))}


@require_http_methods(["GET", "POST"])
@guarded()
def credits(request):
    if request.method == "GET":
        rows = CustomerCreditDraft.objects.filter(
            legal_entity=request.finance_entity, is_synthetic=True).order_by("-updated_at", "-pk")
        return JsonResponse({"customer_credits": [{
            "id": row.pk, "customer_reference": row.payload["customer_reference"],
            "document_reference": row.payload["document_reference"], "revision": row.revision,
        } for row in rows]})
    with transaction.atomic():
        return JsonResponse(detail(save_credit(data=_body(request), entity=request.finance_entity)), status=201)


@require_http_methods(["GET", "PUT"])
@guarded()
def credit(request, draft_id):
    with transaction.atomic():
        try:
            row = CustomerCreditDraft.objects.select_for_update().get(
                pk=draft_id, legal_entity=request.finance_entity, is_synthetic=True)
        except CustomerCreditDraft.DoesNotExist:
            return JsonResponse({"detail": "Synthetic customer credit proposal is unavailable."}, status=404)
        if request.method == "PUT":
            row = save_credit(data=_body(request), entity=request.finance_entity, draft_id=row.pk)
        return JsonResponse(detail(row))
