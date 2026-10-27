from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from foundation.finance_lab_views import guarded, _body
from .invoices import save_invoice, snapshot, duplicate_warning
from .models import SupplierInvoiceDraft


def detail(row):
    return {**snapshot(row), "duplicate_warning": duplicate_warning(row), "history": list(
        row.revisions.order_by("revision").values("revision", "created_at", "snapshot"))}


@require_http_methods(["GET", "POST"])
@guarded()
def invoices(request):
    if request.method == "GET":
        rows = SupplierInvoiceDraft.objects.filter(
            legal_entity=request.finance_entity, is_synthetic=True).order_by("-updated_at", "-pk")
        return JsonResponse({"invoices": [{
            "id": row.pk, "supplier_reference": row.payload["supplier_reference"],
            "invoice_reference": row.payload["invoice_reference"], "revision": row.revision,
        } for row in rows]})
    with transaction.atomic():
        row = save_invoice(data=_body(request), entity=request.finance_entity)
        return JsonResponse(detail(row), status=201)


@require_http_methods(["GET", "PUT"])
@guarded()
def invoice(request, draft_id):
    with transaction.atomic():
        try:
            row = SupplierInvoiceDraft.objects.select_for_update().get(
                pk=draft_id, legal_entity=request.finance_entity, is_synthetic=True)
        except SupplierInvoiceDraft.DoesNotExist:
            return JsonResponse({"detail": "Synthetic supplier-invoice proposal is unavailable."}, status=404)
        if request.method == "PUT":
            row = save_invoice(data=_body(request), entity=request.finance_entity, draft_id=row.pk)
        return JsonResponse(detail(row))
