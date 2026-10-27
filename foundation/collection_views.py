"""Read-only operational quote collections; not accounting receivables."""
from decimal import Decimal

from django.db.models import (
    DecimalField, Exists, F, OuterRef, Subquery, Sum, Value,
)
from django.db.models.functions import Coalesce, Greatest
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Location, QuotePayment, SalesQuote, SalesQuoteLine
from .pagination import page_metadata, parse_page_parameters
from .purchasing_views import _purchasing_unit_for_user, _query_id


class QuoteCollectionListView(APIView):
    """Same order/role/demo authorization as the existing quote register.

    Correlated sums avoid multiplying payments by quote lines. Do not join
    movements, cost layers, illustrative facts or idempotency results.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit_id = _query_id(request.query_params, "business_unit_id", required=True)
        unit = _purchasing_unit_for_user(request, unit_id)
        quote_id = _query_id(request.query_params, "quote_id")
        location_id = _query_id(request.query_params, "location_id")
        remaining_only = request.query_params.get("remaining_only", "true")
        if remaining_only not in {"true", "false"}:
            raise ValidationError({"remaining_only": "Use true or false."})

        money = DecimalField(max_digits=18, decimal_places=2)
        zero = Value(Decimal("0.00"), output_field=money)
        merchandise = QuotePayment.objects.filter(
            quote_id=OuterRef("pk"),
            is_synthetic=True,
            kind__in=[QuotePayment.Kind.DEPOSIT, QuotePayment.Kind.BALANCE],
        ).order_by().values("quote_id").annotate(total=Sum("amount")).values("total")
        quotes = SalesQuote.objects.filter(
            business_unit=unit, is_synthetic=True,
        )
        if quote_id is not None:
            quotes = quotes.filter(pk=quote_id)
        if location_id is not None:
            # Existing explicit BU/location membership, not capacity authority
            # and not a new location-to-user permission.
            location = get_object_or_404(
                Location.objects.filter(
                    business_units=unit, legal_entity=unit.legal_entity,
                    is_synthetic=True,
                ),
                pk=location_id,
            )
            quotes = quotes.filter(Exists(SalesQuoteLine.objects.filter(
                quote_id=OuterRef("pk"), location=location,
            )))
        quotes = quotes.annotate(
            recorded_collections=Coalesce(Subquery(merchandise, output_field=money), zero),
        ).annotate(
            remaining_balance=Greatest(
                F("total_quote_value") - F("recorded_collections"), zero,
                output_field=money,
            ),
        )
        if remaining_only == "true":
            quotes = quotes.filter(remaining_balance__gt=0)
        quotes = quotes.order_by("-created_at", "-pk")
        try:
            page, page_size = parse_page_parameters(request.query_params)
            pagination = page_metadata(quotes.count(), page, page_size)
        except ValueError as error:
            raise ValidationError({"detail": str(error)}) from error
        start = (page - 1) * page_size
        rows = quotes.values(
            "pk", "kind", "status", "currency", "total_quote_value",
            "recorded_collections", "remaining_balance", "ready_at", "delivered_at",
        )[start:start + page_size]
        return Response({
            "business_unit": {"id": unit.pk, "code": unit.code, "name": unit.name},
            "rows": [{
                "quote_id": row["pk"], "kind": row["kind"],
                "status": row["status"], "currency": row["currency"],
                **{key: format(row[key], ".2f") for key in (
                    "total_quote_value", "recorded_collections", "remaining_balance",
                )},
                **{key: row[key].isoformat() if row[key] else None
                   for key in ("ready_at", "delivered_at")},
            } for row in rows],
            "pagination": pagination,
            "test_only_label": (
                "Recorded prototype collections only — not real payments, "
                "accounting receivables, overdue status or approved credit terms. "
                "Storage collections are separate. Amounts are whole-quote values."
            ),
        })
