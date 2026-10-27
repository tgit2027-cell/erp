"""Authenticated, BU-scoped non-posting synthetic return intake."""

from decimal import Decimal
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError

from .models import Location, SalesQuote, SalesReturnIntake
from .pagination import page_metadata, parse_page_parameters
from .purchasing_views import (
    _idempotency_key, _payload, _purchasing_unit_for_user, _query_id,
    _quote_for_user, _run_service,
)
from .return_services import (
    _authorize, _source_lines, create_return, inspect_return, return_policy, serialize_return,
    delivery_return_window,
)


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError({"detail": "Only the documented intake fields are supported."})
        return super().to_internal_value(data)


class ReturnLineInput(StrictSerializer):
    quote_line_id = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))


class ReturnInput(StrictSerializer):
    business_unit_id = serializers.IntegerField(min_value=1)
    quote_id = serializers.IntegerField(min_value=1)
    request_date = serializers.DateField()
    receiving_mode = serializers.ChoiceField(choices=["showroom", "warehouse_collection"])
    location_id = serializers.IntegerField(min_value=1)
    receipt_collection_details = serializers.CharField(max_length=1000)
    proposed_refund_amount = serializers.DecimalField(max_digits=16, decimal_places=2, min_value=0)
    original_payment_method = serializers.ChoiceField(choices=["cash", "card"])
    requested_refund_method = serializers.ChoiceField(choices=["cash", "card"])
    refund_preference = serializers.ChoiceField(choices=["usually_original", "cash", "card"])
    lines = ReturnLineInput(many=True, allow_empty=False, max_length=100)


class InspectionLineInput(StrictSerializer):
    quote_line_id = serializers.IntegerField(min_value=1)
    condition = serializers.ChoiceField(choices=["good", "not_good"])
    inspection_notes = serializers.CharField(max_length=1000)


class InspectionInput(StrictSerializer):
    business_unit_id = serializers.IntegerField(min_value=1)
    lines = InspectionLineInput(many=True, allow_empty=False, max_length=100)


class ReturnSalesSourceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, quote_id):
        # The selected context is required, not an optional cross-unit fallback.
        _query_id(request.query_params, "business_unit_id", required=True)
        quote = _quote_for_user(request, quote_id)
        _run_service(_authorize, actor=request.user, quote=quote)
        if quote.status != SalesQuote.Status.DELIVERED or quote.delivered_at is None:
            raise ValidationError("Select an already delivered synthetic sale.")
        sources = _run_service(_source_lines, quote=quote)
        request_date = serializers.DateField().run_validation(
            request.query_params.get("request_date", timezone.localdate().isoformat()),
        )
        window = _run_service(delivery_return_window, quote=quote, request_date=request_date)
        locations = Location.objects.filter(
            business_units=quote.business_unit,
            legal_entity=quote.business_unit.legal_entity,
            is_synthetic=True,
        ).order_by("code", "pk")
        return Response({
            "quote_id": quote.pk,
            "delivered_at": quote.delivered_at.isoformat(),
            "delivery_window": window,
            "customer": {"id": quote.customer_id, "name": quote.customer.name},
            "business_unit": {
                "id": quote.business_unit_id,
                "code": quote.business_unit.code,
                "name": quote.business_unit.name,
            },
            "lines": [
                {"quote_line_id": line.pk, "variant_id": line.variant_id,
                 "item_id": line.variant.item.item_code, "description": line.variant.item.description,
                 "brand": line.variant.item.brand.name, "quantity": format(line.quantity, ".2f")}
                for line in sources.values()
            ],
            "locations": [{"id": loc.pk, "code": loc.code, "name": loc.name} for loc in locations],
        })


class ReturnIntakeListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit = _purchasing_unit_for_user(
            request, _query_id(request.query_params, "business_unit_id", required=True)
        )
        queryset = SalesReturnIntake.objects.filter(
            quote__business_unit=unit, quote__is_synthetic=True, is_synthetic=True,
            location__business_units=unit, location__legal_entity=unit.legal_entity,
            location__is_synthetic=True,
        ).select_related("quote__business_unit", "quote__customer", "location").prefetch_related("events")
        try:
            page, page_size = parse_page_parameters(request.query_params)
            pagination = page_metadata(queryset.count(), page, page_size)
        except ValueError as error:
            raise ValidationError(str(error)) from error
        start = (page - 1) * page_size
        return Response({
            "rows": [serialize_return(row) for row in queryset[start:start + page_size]],
            "pagination": pagination, "policy": return_policy(unit),
        })

    def post(self, request):
        data = _payload(request)
        serializer = ReturnInput(data=data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        unit = _purchasing_unit_for_user(request, values["business_unit_id"])
        quote = get_object_or_404(
            SalesQuote.objects.select_related("business_unit__legal_entity", "customer"),
            pk=values["quote_id"], business_unit=unit, is_synthetic=True,
        )
        values["request_date"] = values["request_date"].isoformat()
        return Response(_run_service(
            create_return, actor=request.user, quote=quote,
            idempotency_key=_idempotency_key(request, data), data=values,
        ))


class ReturnInspectionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, intake_id):
        data = _payload(request)
        serializer = InspectionInput(data=data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        unit = _purchasing_unit_for_user(request, values["business_unit_id"])
        intake = get_object_or_404(
            SalesReturnIntake.objects.select_related("quote__business_unit__legal_entity", "quote__customer"),
            pk=intake_id, quote__business_unit=unit, quote__is_synthetic=True, is_synthetic=True,
            location__business_units=unit, location__legal_entity=unit.legal_entity,
            location__is_synthetic=True,
        )
        return Response(_run_service(
            inspect_return, actor=request.user, intake=intake,
            idempotency_key=_idempotency_key(request, data), lines=values["lines"],
        ))
