"""Read-only customer invoices issued at delivery (provisional policy)."""
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CustomerInvoice
from .pagination import page_metadata, parse_page_parameters
from .purchasing_views import _purchasing_unit_for_user, _query_id

MONEY_FIELDS = (
    "list_total", "discount_total", "net_total", "tax_rate_percent", "tax_total",
    "invoice_total", "paid_before_delivery", "balance_due",
)


def _summary(invoice):
    return {
        "invoice_id": invoice.pk,
        "number": invoice.number,
        "quote_id": invoice.quote_id,
        "customer": {"code": invoice.customer.code, "name": invoice.customer.name},
        "issued_at": invoice.issued_at.isoformat(),
        "currency": invoice.currency,
        **{field: format(getattr(invoice, field), ".2f") for field in MONEY_FIELDS},
    }


def _detail(invoice):
    return {
        **_summary(invoice),
        "business_unit": {
            "id": invoice.business_unit_id,
            "code": invoice.business_unit.code,
            "name": invoice.business_unit.name,
        },
        "issued_by": invoice.issued_by.get_username(),
        "policy_label": invoice.policy_label,
        "lines": [{
            "item_code": line.item_code,
            "description": line.description,
            "brand_name": line.brand_name,
            "quantity": format(line.quantity, ".2f"),
            "list_unit_price": format(line.list_unit_price, ".2f"),
            "discount_percent": format(line.discount_percent, ".2f"),
            "net_unit_price": format(line.net_unit_price, ".2f"),
            "line_total": format(line.line_total, ".2f"),
            "journal_entry_ids": sorted(entry.pk for entry in line.journal_entries.all()),
        } for line in invoice.lines.all()],
    }


class CustomerInvoiceListView(APIView):
    """Same order, role and demo checks as the quote register and collections."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit = _purchasing_unit_for_user(
            request, _query_id(request.query_params, "business_unit_id", required=True)
        )
        invoices = CustomerInvoice.objects.filter(business_unit=unit, is_synthetic=True).select_related("customer")
        quote_id = _query_id(request.query_params, "quote_id")
        if quote_id is not None:
            invoices = invoices.filter(quote_id=quote_id)
        number = request.query_params.get("number", "").strip()
        if number:
            invoices = invoices.filter(number__iexact=number)
        invoices = invoices.order_by("-issued_at", "-pk")
        try:
            page, page_size = parse_page_parameters(request.query_params)
            pagination = page_metadata(invoices.count(), page, page_size)
        except ValueError as error:
            raise ValidationError({"detail": str(error)}) from error
        start = (page - 1) * page_size
        return Response({
            "business_unit": {"id": unit.pk, "code": unit.code, "name": unit.name},
            "rows": [_summary(invoice) for invoice in invoices[start:start + page_size]],
            "pagination": pagination,
            "test_only_label": "TEST ONLY provisional invoices, issued at delivery. No tax is charged yet.",
        })


class CustomerInvoiceDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, invoice_id):
        unit = _purchasing_unit_for_user(
            request, _query_id(request.query_params, "business_unit_id", required=True)
        )
        invoice = get_object_or_404(
            CustomerInvoice.objects.select_related("business_unit", "customer", "issued_by")
            .prefetch_related("lines__journal_entries"),
            pk=invoice_id, business_unit=unit, is_synthetic=True,
        )
        return Response(_detail(invoice))
