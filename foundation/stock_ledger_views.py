"""Quantity-only reads of the synthetic transactional stock projection."""

from decimal import Decimal

from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit, stock_page_queryset
from .models import Item, Location, QuoteHold, StockBalance, UserActionGrant, Variant
from .pagination import page_metadata, parse_page_parameters


class StockQuantitySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    item_code = serializers.CharField(source="variant.item.item_code", read_only=True)
    item_description = serializers.CharField(source="variant.item.description", read_only=True)
    brand_name = serializers.CharField(source="variant.item.brand.name", read_only=True)
    brand_code = serializers.CharField(source="variant.item.brand.code", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    variant_description = serializers.CharField(source="variant.description", read_only=True)
    location_code = serializers.CharField(source="location.code", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)
    unit_code = serializers.CharField(source="business_unit.code", read_only=True)
    unit_name = serializers.CharField(source="business_unit.name", read_only=True)
    quantity = serializers.DecimalField(
        source="available_quantity", max_digits=14, decimal_places=2, read_only=True,
    )
    held_quantity = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    sellable_quantity = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    is_synthetic = serializers.BooleanField(read_only=True)


def scoped_ledger_quantities(request):
    unit = resolve_unit(request, UserActionGrant.Action.READ_STOCK)
    records = StockBalance.objects.filter(
        business_unit=unit,
        legal_entity=unit.legal_entity,
        location__legal_entity=unit.legal_entity,
        location__business_units=unit,
        is_synthetic=True,
        location__is_synthetic=True,
        variant__is_synthetic=True,
        variant__item__is_synthetic=True,
        variant__item__brand__is_synthetic=True,
    ).select_related("variant__item__brand", "location", "business_unit").only(
        "id", "variant", "location", "business_unit",
        "on_hand_quantity", "reserved_quantity", "is_synthetic",
        "variant__sku", "variant__description", "variant__item",
        "variant__item__item_code", "variant__item__description", "variant__item__brand",
        "variant__item__brand__name", "variant__item__brand__code",
        "location__code", "location__name", "business_unit__code", "business_unit__name",
    ).annotate(
        held_quantity=_active_hold_total(),
    ).annotate(
        sellable_quantity=F("on_hand_quantity") - F("reserved_quantity") - F("held_quantity"),
    ).order_by("variant__item__item_code", "variant__sku", "pk")
    return unit, records


def _active_hold_total():
    holds = QuoteHold.objects.filter(
        balance=OuterRef("pk"),
        status=QuoteHold.Status.ACTIVE,
        expires_at__gt=timezone.now(),
    ).order_by().values("balance").annotate(total=Sum("quantity")).values("total")
    zero = Value(Decimal("0.00"), output_field=DecimalField(max_digits=14, decimal_places=2))
    return Coalesce(Subquery(holds, output_field=DecimalField(max_digits=14, decimal_places=2)), zero)


def _filter_location(request, unit, records):
    """Apply the list's existing explicit location contract to quantity reads."""
    location = request.query_params.get("location")
    if location not in (None, ""):
        try:
            location_id = int(location)
        except (TypeError, ValueError):
            raise ValidationError({"location": "Location must be a numeric ID."}) from None
        if not Location.objects.filter(
            pk=location_id, legal_entity=unit.legal_entity,
            business_units=unit, is_synthetic=True,
        ).exists():
            raise PermissionDenied("The requested location is outside the selected unit's authorized scope.")
        records = records.filter(location_id=location_id)
    return records


class StockQuantityListView(APIView):
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        unit, records = scoped_ledger_quantities(request)
        records = _filter_location(request, unit, records)
        term = request.query_params.get("search", "").strip()
        if term:
            items = Item.objects.filter(
                Q(item_code__icontains=term) | Q(description__icontains=term)
                | Q(brand__name__icontains=term)
            ).values("pk")
            variants = Variant.objects.filter(sku__icontains=term).values("pk")
            locations = Location.objects.filter(
                legal_entity=unit.legal_entity,
                business_units=unit,
                is_synthetic=True,
            ).filter(
                Q(name__icontains=term) | Q(code__icontains=term)
            ).values("pk")
            records = records.filter(
                Q(variant__item_id__in=Subquery(items))
                | Q(variant_id__in=Subquery(variants))
                | Q(location_id__in=Subquery(locations))
            )
        try:
            page, page_size = parse_page_parameters(request.query_params)
            total_count = records.count()
            pagination = page_metadata(total_count, page, page_size)
            records = list(stock_page_queryset(
                records, page=page, page_size=page_size, total_count=total_count,
            ))
        except ValueError as error:
            raise ValidationError({"pagination": str(error)}) from error
        return Response({
            "results": StockQuantitySerializer(records, many=True).data,
            "pagination": pagination,
        })


class StockCatalogMatchView(APIView):
    """Non-persistent exact-code checks against the caller's selected-unit ledger."""

    http_method_names = ["get", "head", "options"]
    max_codes = 80
    max_code_length = 80

    def get(self, request):
        _unit, records = scoped_ledger_quantities(request)
        submitted = request.query_params.getlist("code")
        codes = list(dict.fromkeys(value.strip() for value in submitted if value.strip()))
        if not codes:
            raise ValidationError({"code": "Provide at least one non-empty item code."})
        if len(codes) > self.max_codes:
            raise ValidationError({"code": f"Provide no more than {self.max_codes} item codes per request."})
        if any(len(code) > self.max_code_length for code in codes):
            raise ValidationError({"code": f"Item codes must be {self.max_code_length} characters or fewer."})

        lookup = Q()
        for code in codes:
            lookup |= Q(variant__item__item_code__iexact=code) | Q(variant__sku__iexact=code)
        available = set()
        for item_code, sku in records.filter(lookup).values_list(
            "variant__item__item_code", "variant__sku",
        ):
            available.add(item_code.casefold())
            available.add(sku.casefold())
        return Response({
            "matched_codes": [code for code in codes if code.casefold() in available],
        })


class StockQuantityDetailView(APIView):
    http_method_names = ["get", "head", "options"]

    def get(self, request, balance_id):
        unit, records = scoped_ledger_quantities(request)
        records = _filter_location(request, unit, records)
        balance = records.filter(pk=balance_id).first()
        if balance is None:
            raise NotFound("Stock balance not found.")
        return Response(StockQuantitySerializer(balance).data)


class StockReceiptCandidateView(APIView):
    """Exact variant choices from the existing authorized ledger, never global catalog."""

    http_method_names = ["get", "head", "options"]

    def get(self, request):
        unit, records = scoped_ledger_quantities(request)
        codes = list(dict.fromkeys(
            code.strip() for code in request.query_params.getlist("code") if code.strip()
        ))
        if not codes or len(codes) > 80 or any(len(code) > 80 for code in codes):
            raise ValidationError({"code": "Provide 1–80 non-empty codes, at most 80 characters each."})
        location = request.query_params.get("location")
        if location not in (None, ""):
            try:
                location_id = int(location)
            except (TypeError, ValueError):
                raise ValidationError({"location": "Location must be a numeric ID."}) from None
            if not Location.objects.filter(
                pk=location_id, legal_entity=unit.legal_entity,
                business_units=unit, is_synthetic=True,
            ).exists():
                raise PermissionDenied("The requested location is outside the selected unit's authorized scope.")
            records = records.filter(location_id=location_id)
        lookup = Q()
        for code in codes:
            lookup |= Q(variant__item__item_code__iexact=code) | Q(variant__sku__iexact=code)
        candidates = list(records.filter(lookup).order_by(
            "variant__sku", "variant_id",
        ).values(
            "variant_id", "variant__sku", "variant__description",
            "variant__item__item_code", "variant__item__description",
            "variant__item__brand__name",
        ).distinct()[:201])
        if len(candidates) > 200:
            raise ValidationError({"code": "Too many variant choices. Use exact variant SKUs."})
        return Response({"candidates": [
            {
                "variant_id": row["variant_id"],
                "variant_sku": row["variant__sku"],
                "variant_description": row["variant__description"],
                "item_code": row["variant__item__item_code"],
                "item_description": row["variant__item__description"],
                "brand_name": row["variant__item__brand__name"],
            }
            for row in candidates
        ]})
