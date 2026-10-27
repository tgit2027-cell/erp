from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from foundation.finance_drafts import FinanceDraftValidationError
from foundation.finance_lab_views import guarded, _body
from .models import ReversalProposal
from .reversals import save_reversal, snapshot, source_record


def detail(row):
    return {**snapshot(row), "history": list(
        row.revisions.order_by("revision").values("revision", "created_at", "snapshot"))}


@require_http_methods(["GET"])
@guarded()
def source(request, journal_id, revision):
    try:
        row = source_record(request.finance_entity, journal_id, revision)
    except FinanceDraftValidationError:
        return JsonResponse({"detail": "Synthetic source revision unavailable."}, status=404)
    return JsonResponse({"source": row.snapshot})


@require_http_methods(["GET", "POST"])
@guarded()
def reversals(request):
    if request.method == "GET":
        rows = ReversalProposal.objects.filter(
            legal_entity=request.finance_entity, is_synthetic=True).order_by("-updated_at", "-pk")
        return JsonResponse({"reversals": [{
            "id": row.pk, "revision": row.revision,
            **{key: row.payload[key] for key in ("source_journal_id", "source_revision", "proposed_date", "explanation")},
        } for row in rows]})
    with transaction.atomic():
        return JsonResponse(detail(save_reversal(data=_body(request), entity=request.finance_entity)), status=201)


@require_http_methods(["GET", "PUT"])
@guarded()
def reversal(request, draft_id):
    with transaction.atomic():
        try:
            row = ReversalProposal.objects.select_for_update().get(
                pk=draft_id, legal_entity=request.finance_entity, is_synthetic=True)
        except ReversalProposal.DoesNotExist:
            return JsonResponse({"detail": "Synthetic reversal proposal unavailable."}, status=404)
        if request.method == "PUT":
            row = save_reversal(data=_body(request), entity=request.finance_entity, draft_id=row.pk)
        return JsonResponse(detail(row))
