"""Lab-only create identity. No change to financial validation or PUT revisions."""
import hashlib
import json
from uuid import UUID

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from foundation.finance_lab_views import guarded
from .models import DraftCreateReceipt

RESOURCES = {
    "reversals": ("Source-linked GL reversal reviews", "/lab-reversals-ui/", "finance_allocation_lab.ReversalProposal"),
    "journals": ("Journal proposals", "/lab-ui/", "foundation.FinanceDraftJournal"),
    "allocations": ("Allocation proposals", "/lab-allocation-ui/", "finance_allocation_lab.AllocationProposal"),
    "budgets": ("Budget scenarios", "/lab-budget-ui/", "finance_allocation_lab.BudgetScenario"),
    "reconciliations": ("Reconciliation proposals", "/lab-reconciliation-ui/", "finance_allocation_lab.ReconciliationProposal"),
    "expenses": ("Expense / petty-cash proposals", "/lab-expense-ui/", "finance_allocation_lab.ExpenseProposal"),
    "assets": ("Fixed-asset drafts", "/lab-assets-ui/", "finance_allocation_lab.FixedAssetDraft"),
    "forecasts": ("Cash-forecast scenarios", "/lab-forecasts-ui/", "finance_allocation_lab.CashForecastDraft"),
    "invoices": ("Supplier-invoice proposals", "/lab-invoices-ui/", "finance_allocation_lab.SupplierInvoiceDraft"),
    "adjustments": ("Supplier credit/debit proposals", "/lab-adjustments-ui/", "finance_allocation_lab.SupplierAdjustmentDraft"),
    "customer-invoices": ("Customer-invoice proposals", "/lab-customer-invoices-ui/", "finance_allocation_lab.CustomerInvoiceDraft"),
    "customer-credits": ("Customer-credit proposals", "/lab-customer-credits-ui/", "finance_allocation_lab.CustomerCreditDraft"),
    "supplier-advances": ("Supplier-advance proposals", "/lab-supplier-advances-ui/", "finance_allocation_lab.SupplierAdvanceDraft"),
}


def identity(value):
    parsed = UUID(value)
    if str(parsed) != value:
        raise ValueError("Canonical request identity required.")
    return parsed


def create_with_identity(request, view, args, kwargs):
    """Unique-key insertion serializes independent transactions before creation.

    Receipt and full original response commit with draft/history; rollback of
    any of them removes all. A concurrent duplicate waits, then returns the
    original result. A 404 status read is deliberately not proof of absence.
    """
    resource = request.path.removeprefix("/lab/").removesuffix("/")
    key = identity(request.headers["X-Finance-Request-ID"])
    body = json.loads(request.body)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    fingerprint = hashlib.sha256((resource + "\n" + canonical).encode()).hexdigest()
    with transaction.atomic():
        receipt, created = DraftCreateReceipt.objects.get_or_create(
            actor_id=request.user.pk, legal_entity=request.finance_entity, request_id=key,
            defaults={"resource": resource, "fingerprint": fingerprint})
        receipt = DraftCreateReceipt.objects.select_for_update().get(pk=receipt.pk)
        if receipt.resource != resource or receipt.fingerprint != fingerprint:
            return JsonResponse({"detail": "Request identity was already used with different input or draft type."}, status=409)
        if not created:
            if receipt.status != 201:
                # Not reachable through this transaction protocol. Never create
                # a replacement if a receipt has been corrupted manually.
                return JsonResponse({"detail": "Recorded outcome unavailable; retain request identity."}, status=409)
            result = JsonResponse(receipt.response, status=receipt.status)
            result["X-Finance-Replayed"] = "true"
            return result
        result = view(request, *args, **kwargs)
        if result.status_code != 201:
            transaction.set_rollback(True)
            return result
        receipt.response = json.loads(result.content)
        receipt.status = result.status_code
        receipt.save(update_fields=("response", "status"))
        return result


@require_GET
@guarded()
def request_status(request, request_id):
    key = identity(request_id)
    row = DraftCreateReceipt.objects.filter(
        request_id=key, actor_id=request.user.pk, legal_entity=request.finance_entity,
        is_synthetic=True, status=201).first()
    if row is None:
        return JsonResponse({
            "detail": "No committed outcome is visible for this request. It may still be running; retry only the same identity and original input.",
        }, status=404)
    # Original acknowledgement is evidence, not the current edited revision.
    return JsonResponse({"request_id": str(row.request_id), "state": "saved",
                         "resource": row.resource, "editor_path": RESOURCES[row.resource][1],
                         "draft_id": row.response["id"], "original_revision": row.response.get("revision", 1)})


@require_GET
@guarded()
def review(request):
    from django.apps import apps
    from django.db import connection
    groups = []
    # One read-only snapshot avoids a mixed listing during concurrent edits.
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for resource, (label, editor_path, model_name) in RESOURCES.items():
            model = apps.get_model(model_name)
            rows = model.objects.filter(legal_entity=request.finance_entity, is_synthetic=True).order_by("pk")
            items = []
            for row in rows:
                payload = getattr(row, "payload", {})
                name = getattr(row, "title_en", "") or next((
                    payload[field] for field in ("name", "document_reference", "invoice_reference", "asset_name", "asset_identifier", "explanation")
                    if payload.get(field)), f"{label} {row.pk}")
                items.append({"id": row.pk, "revision": row.revision, "label": name,
                              "kind": getattr(row, "kind", resource), "updated_at": row.updated_at.isoformat()})
            groups.append({"resource": resource, "label": label, "editor_path": editor_path, "items": items})
    return JsonResponse({"groups": groups})
