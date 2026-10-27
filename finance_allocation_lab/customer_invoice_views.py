from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from foundation.finance_lab_views import guarded, _body
from .customer_invoices import save_invoice, snapshot, duplicate_warning
from .models import CustomerInvoiceDraft


def detail(row):
    return {**snapshot(row), "duplicate_warning": duplicate_warning(row), "history": list(
        row.revisions.order_by("revision").values("revision", "created_at", "snapshot"))}


@require_http_methods(["GET", "POST"])
@guarded()
def invoices(request):
    if request.method == "GET":
        rows = CustomerInvoiceDraft.objects.filter(
            legal_entity=request.finance_entity, is_synthetic=True).order_by("-updated_at", "-pk")
        return JsonResponse({"customer_invoices": [{
            "id": row.pk, "customer_reference": row.payload["customer_reference"],
            "invoice_reference": row.payload["invoice_reference"], "revision": row.revision,
        } for row in rows]})
    with transaction.atomic():
        return JsonResponse(detail(save_invoice(data=_body(request), entity=request.finance_entity)), status=201)


@require_http_methods(["GET", "PUT"])
@guarded()
def invoice(request, draft_id):
    with transaction.atomic():
        try:
            row = CustomerInvoiceDraft.objects.select_for_update().get(
                pk=draft_id, legal_entity=request.finance_entity, is_synthetic=True)
        except CustomerInvoiceDraft.DoesNotExist:
            return JsonResponse({"detail": "Internal synthetic customer-invoice proposal is unavailable."}, status=404)
        if request.method == "PUT":
            row = save_invoice(data=_body(request), entity=request.finance_entity, draft_id=row.pk)
        return JsonResponse(detail(row))
