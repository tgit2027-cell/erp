"""Minimum existing choices for quotations, never a procurement directory.

Catalogs can hold tens of thousands of priced items, so the choices endpoint
returns only the first page of prices and customers with their totals; the
search endpoints below fetch the rest as the user types.
"""
from django.db.models import F, Q
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import active_grants
from .models import (
    ApprovedSellingPrice, BusinessUnitRoleAssignment, BusinessUnitWorkCalendar,
    Item, StockBalance, SyntheticCounterparty, UserActionGrant, Variant,
)
from .purchasing_services import DISCOUNT_CEILINGS
from .purchasing_views import _purchasing_unit_for_user, _query_id
from .quotation_presentation import item_presentation

CHOICE_PAGE = 50
MAX_LOOKUP_IDS = 40
# A broad search (say "sofa") is cut here; the user narrows it to see the rest.
MAX_SEARCH_MATCHES = 2000


def _search_term(params):
    term = (params.get("search") or "").strip()
    if len(term) > 80:
        raise ValidationError({"search": "Search text is too long."})
    return term


def _current_prices(unit):
    return ApprovedSellingPrice.objects.filter(
        business_unit=unit, is_current=True, is_synthetic=True, currency="QAR",
        variant__is_synthetic=True, variant__item__is_synthetic=True,
        variant__item__brand__is_synthetic=True,
    ).select_related("variant__item").order_by("variant__sku", "pk")


def _price_rows(request, unit, prices):
    """Present price rows with unreserved stock, read only for the variants shown."""
    prices = list(prices)
    balances = {}
    can_read_stock = active_grants(request.user, UserActionGrant.Action.READ_STOCK).filter(
        business_unit=unit,
    ).exists()
    if can_read_stock and prices:
        for bucket in StockBalance.objects.filter(
            business_unit=unit, legal_entity=unit.legal_entity, is_synthetic=True,
            location__legal_entity=unit.legal_entity, location__business_units=unit,
            location__is_synthetic=True, variant__is_synthetic=True,
            variant__item__is_synthetic=True, variant__item__brand__is_synthetic=True,
            variant_id__in=[price.variant_id for price in prices],
        ).annotate(unreserved=F("on_hand_quantity") - F("reserved_quantity")).order_by("location_id", "pk"):
            balances.setdefault(bucket.variant_id, []).append({
                "location_id": bucket.location_id, "quantity": format(bucket.unreserved, "f"),
            })
    return [
        {
            "id": price.pk, "variant_id": price.variant_id,
            "sku": price.variant.sku, "amount": format(price.amount, "f"),
            "currency": price.currency, "revision": price.revision, "approved_by": None,
            "item": item_presentation(price.variant.item),
            "stock": balances.get(price.variant_id, []),
        } for price in prices
    ]


def _customers():
    return SyntheticCounterparty.objects.filter(
        party_type=SyntheticCounterparty.Type.CUSTOMER, is_synthetic=True,
    ).order_by("code", "pk")


def _customer_rows(parties):
    return [
        {
            "counterparty_id": party.pk, "code": party.code,
            "display_name": party.name, "name_ar": party.name_ar,
            "party_type": "customer", "contact_en": party.contact_en, "contact_ar": party.contact_ar,
        } for party in parties
    ]


def _matching_variant_ids(term):
    """Variant ids whose SKU or item matches, each found through its own trigram index.

    Filtering prices by an OR across the joined item and variant tables makes
    PostgreSQL scan and join the whole catalog; separate single-table lookups
    use the indexes and stay in the low milliseconds at 80,000 items.
    """
    items = Item.objects.filter(
        Q(item_code__icontains=term) | Q(name_en__icontains=term)
        | Q(name_ar__icontains=term) | Q(description__icontains=term)
    ).values_list("pk", flat=True)[:MAX_SEARCH_MATCHES]
    ids = set(Variant.objects.filter(sku__icontains=term).values_list("pk", flat=True)[:MAX_SEARCH_MATCHES])
    ids.update(Variant.objects.filter(item_id__in=list(items)).values_list("pk", flat=True)[:MAX_SEARCH_MATCHES])
    return ids


def _lookup_ids(params):
    raw = params.get("ids") or ""
    if not raw:
        return None
    try:
        ids = sorted({int(value) for value in raw.split(",") if value.strip()})
    except ValueError:
        raise ValidationError({"ids": "Send numeric ids separated by commas."}) from None
    if len(ids) > MAX_LOOKUP_IDS:
        raise ValidationError({"ids": f"Look up at most {MAX_LOOKUP_IDS} ids at once."})
    return ids


class QuotationChoicesView(APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        unit = _purchasing_unit_for_user(
            request, _query_id(request.query_params, "business_unit_id", required=True),
        )
        assignments = BusinessUnitRoleAssignment.objects.filter(
            user=request.user, business_unit=unit, revoked_at__isnull=True, is_synthetic=True,
        ).order_by("role", "pk")
        roles = list(assignments)
        calendar = BusinessUnitWorkCalendar.objects.filter(business_unit=unit, is_synthetic=True).first()
        prices = _current_prices(unit)
        customers = _customers()
        return Response({
            "business_unit": {"id": unit.pk, "code": unit.code, "name": unit.name},
            # Only the first page; the item and customer search endpoints return the rest.
            "current_prices": _price_rows(request, unit, prices[:CHOICE_PAGE]),
            "current_price_count": prices.count(),
            # Existing counterparties have no BU membership field. Only the
            # synthetic CUSTOMER choices already accepted by quote creation are
            # exposed; do not invent a customer ownership rule or show suppliers.
            "customers": _customer_rows(customers[:CHOICE_PAGE]),
            "customer_count": customers.count(),
            "role_assignments": [
                {"id": row.pk, "user_id": request.user.pk, "username": request.user.get_username(), "role": row.role}
                for row in roles
            ],
            "discount_ceiling": format(max(
                (DISCOUNT_CEILINGS.get(row.role, 0) for row in roles), default=0,
            ), ".2f"),
            "work_calendar": {
                "is_configured": bool(calendar and calendar.is_configured),
                "closed_weekdays": [], "closed_dates": [],
            },
            "test_only_label": "Synthetic quotation choices only. Quantities require independent read_stock access.",
        })


class QuotationItemSearchView(APIView):
    """Approved, current QAR prices for one business unit, found by SKU, item code or name."""

    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        unit = _purchasing_unit_for_user(
            request, _query_id(request.query_params, "business_unit_id", required=True),
        )
        prices = _current_prices(unit)
        ids = _lookup_ids(request.query_params)
        if ids is not None:
            prices = prices.filter(variant_id__in=ids)
        else:
            term = _search_term(request.query_params)
            if term:
                prices = prices.filter(variant_id__in=_matching_variant_ids(term))
        rows = list(prices[:CHOICE_PAGE + 1])
        return Response({
            "rows": _price_rows(request, unit, rows[:CHOICE_PAGE]),
            "more": len(rows) > CHOICE_PAGE,
        })


class QuotationCustomerSearchView(APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        _purchasing_unit_for_user(request, _query_id(request.query_params, "business_unit_id", required=True))
        customers = _customers()
        ids = _lookup_ids(request.query_params)
        if ids is not None:
            customers = customers.filter(pk__in=ids)
        else:
            term = _search_term(request.query_params)
            if term:
                customers = customers.filter(
                    Q(code__icontains=term) | Q(name__icontains=term) | Q(name_ar__icontains=term)
                    | Q(contact_en__icontains=term)  # phone numbers and email live in the contact text
                )
        rows = list(customers[:CHOICE_PAGE + 1])
        return Response({"rows": _customer_rows(rows[:CHOICE_PAGE]), "more": len(rows) > CHOICE_PAGE})
