from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import active_grants
from .models import (
    ApprovedSellingPrice,
    BusinessUnit,
    BusinessUnitRoleAssignment,
    BusinessUnitWorkCalendar,
    Currency,
    Location,
    PurchaseCommitment,
    PurchaseCommitmentLine,
    QuoteApproval,
    QuoteHold,
    QuotePayment,
    ReportDataCapability,
    SalesQuote,
    SalesQuoteLine,
    SpecialOrderValueBand,
    StorageCharge,
    SyntheticCounterparty,
    UserActionGrant,
    Variant,
    WorkflowEvent,
    WorkflowPolicyConfig,
)
from .purchasing_services import (
    DEFAULT_HOLD_WORKING_DAYS,
    WorkflowConfigurationMissing,
    WorkflowPermissionDenied,
    accrue_storage_charges,
    approve_post_handover_balance,
    approve_quote_discount,
    approve_special_order_value_band,
    approve_storage_charge_exception,
    assign_approval_role,
    configure_selling_price,
    configure_storage_exception_roles,
    configure_work_calendar,
    create_purchase_commitment,
    create_sales_quote,
    create_special_order_value_band,
    create_synthetic_counterparty,
    create_temporary_quote_hold,
    deliver_sales_quote,
    notify_quote_ready,
    receive_imported_purchase,
    record_simulated_payment,
    revoke_approval_role,
    _quote_result,
    _require_demo_actor,
    _require_staff_demo,
)
from .transaction_services import (
    IdempotencyConflict,
    TransactionServiceError,
)
from .pagination import page_metadata, parse_page_parameters


class WorkflowConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The requested operation conflicts with the current workflow state."
    default_code = "workflow_conflict"


def _run_service(function, **kwargs):
    try:
        return function(**kwargs)
    except WorkflowPermissionDenied as error:
        raise PermissionDenied(str(error)) from error
    except (WorkflowConfigurationMissing, IdempotencyConflict) as error:
        raise WorkflowConflict(str(error)) from error
    except TransactionServiceError as error:
        raise ValidationError({"detail": str(error)}) from error


def _payload(request):
    if not isinstance(request.data, dict):
        raise ValidationError({"detail": "Send a JSON object."})
    return request.data


def _idempotency_key(request, data):
    key = request.headers.get("Idempotency-Key") or data.get("idempotency_key")
    if not isinstance(key, str) or not key.strip():
        raise ValidationError({"idempotency_key": "Provide an Idempotency-Key header."})
    if len(key) > 128:
        raise ValidationError({"idempotency_key": "Idempotency keys cannot exceed 128 characters."})
    return key.strip()


def _exchange_fields(data):
    return {
        "currency": data.get("currency"),
        "exchange_rate": data.get("exchange_rate"),
        "rate_source": data.get("rate_source", ""),
        "rate_date": data.get("rate_date"),
    }


class CurrencyListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"currencies": [
            {"code": currency.code, "name": currency.name, "is_base": currency.is_base}
            for currency in Currency.objects.filter(is_active=True)
        ]})


def _unit(unit_id):
    return get_object_or_404(BusinessUnit.objects.select_related("legal_entity"), pk=unit_id)


def _query_id(query_params, name, *, required=False):
    raw = query_params.get(name)
    if raw in (None, ""):
        if required:
            raise ValidationError({name: "This filter is required."})
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({name: "This value must be a positive whole number."}) from None
    if value < 1:
        raise ValidationError({name: "This value must be a positive whole number."})
    return value


def _purchasing_unit_for_user(request, unit_id):
    unit = get_object_or_404(
        BusinessUnit.objects.select_related("legal_entity").filter(
            is_synthetic=True,
            is_active=True,
        ),
        pk=unit_id,
    )
    if not active_grants(
        request.user,
        UserActionGrant.Action.CREATE_ORDER,
    ).filter(business_unit=unit).exists():
        raise Http404
    _run_service(_require_demo_actor, user=request.user, business_unit=unit)
    return unit


def _quote_for_user(request, quote_id):
    allowed_unit_ids = active_grants(
        request.user,
        UserActionGrant.Action.CREATE_ORDER,
    ).values("business_unit_id")
    quote_filters = {
        "pk": quote_id,
        "business_unit_id__in": allowed_unit_ids,
        "is_synthetic": True,
    }
    selected_unit_id = _query_id(request.query_params, "business_unit_id")
    if selected_unit_id is not None:
        quote_filters["business_unit_id"] = selected_unit_id
    quote = get_object_or_404(
        SalesQuote.objects.select_related("business_unit", "customer"),
        **quote_filters,
    )
    _run_service(
        _require_demo_actor,
        user=request.user,
        business_unit=quote.business_unit,
    )
    return quote


def _receipt_acknowledgement(actor, unit, result):
    """Project private posting results using the existing receipt cost scopes."""
    from .reporting_services import _optional_capability_units

    filters = {"unit_id": unit.pk, "company_id": None, "location_id": None}
    supplier_cost = bool(_optional_capability_units(
        actor, ReportDataCapability.SUPPLIER_COST, filters, {unit.pk},
    ))
    landed_cost = bool(_optional_capability_units(
        actor, ReportDataCapability.LANDED_COST, filters, {unit.pk},
    ))
    fields = {
        "purchase_receipt_id", "stock_journal_entry_id", "stock_movement_ids",
        "purchase_commitment_id", "currency",
        "transaction_currency", "exchange_rate", "rate_source", "rate_date",
    }
    if supplier_cost:
        fields.update({"purchase_value", "foreign_purchase_value"})
    if landed_cost:
        fields.update({"eligible_landed_charges", "landed_cost_allocations", "foreign_charges"})
    if supplier_cost and landed_cost:
        fields.update({"inventory_value", "landed_cost_rate_percent"})
    # Never edit the immutable private idempotency result. Apply current grants
    # again even when receive_imported_purchase returns an earlier operation.
    return {key: value for key, value in result.items() if key in fields}


def _quote_summary(quote):
    data = _quote_result(quote)
    data["presentation_document"] = quote.presentation_document
    # Read existing quantities only: this is not a partial-delivery operation,
    # active-reservation calculation, or authority to post another handover.
    data["delivery_progress"] = {
        "ready_at": quote.ready_at.isoformat() if quote.ready_at else None,
        "delivered_at": quote.delivered_at.isoformat() if quote.delivered_at else None,
        "lines": [
            {
                "quote_line_id": line.pk,
                "variant_id": line.variant_id,
                "ordered_quantity": format(line.quantity, "f"),
                "reserved_to_date_quantity": format(line.reserved_quantity, "f"),
                "delivered_quantity": format(line.delivered_quantity, "f"),
                "remaining_quantity": format(line.quantity - line.delivered_quantity, "f"),
            }
            for line in quote.lines.order_by("pk")
        ],
    }
    data["payments"] = [
        {
            "payment_id": payment.pk,
            "kind": payment.kind,
            "amount": format(payment.amount, "f"),
            "non_returnable_amount": format(payment.non_returnable_amount, "f"),
            "simulated_reference": payment.simulated_reference,
            "created_at": payment.created_at.isoformat(),
        }
        for payment in quote.payments.order_by("created_at", "pk")
    ]
    data["approvals"] = [
        {
            "approval_id": approval.pk,
            "kind": approval.kind,
            "approver_id": approval.approver_id,
            "role": approval.role,
            "reason": approval.reason,
            "details": approval.details,
            "approved_at": approval.approved_at.isoformat(),
        }
        for approval in quote.approvals.order_by("approved_at", "pk")
    ]
    data["holds"] = [
        {
            "hold_id": hold.pk,
            "status": hold.status,
            "quantity": format(hold.quantity, "f"),
            "working_days": hold.working_days,
            "expires_at": hold.expires_at.isoformat(),
        }
        for hold in quote.holds.order_by("pk")
    ]
    data["events"] = [
        {
            "event_id": event.pk,
            "kind": event.kind,
            "message": event.message,
            "details": event.details,
            "occurred_at": event.occurred_at.isoformat(),
        }
        for event in quote.workflow_events.order_by("occurred_at", "pk")
    ]
    return data


class GovernancePurchasingConfigView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, unit_id):
        unit = _unit(unit_id)
        _run_service(
            _require_staff_demo,
            user=request.user,
            business_unit=unit,
        )
        try:
            calendar = BusinessUnitWorkCalendar.objects.get(business_unit=unit)
        except BusinessUnitWorkCalendar.DoesNotExist:
            calendar = None
        try:
            storage_policy = WorkflowPolicyConfig.objects.get(business_unit=unit)
        except WorkflowPolicyConfig.DoesNotExist:
            storage_policy = None
        current_prices = ApprovedSellingPrice.objects.filter(
            business_unit=unit,
            is_current=True,
            is_synthetic=True,
        ).select_related("variant").order_by("variant__sku")
        return Response(
            {
                "test_only_label": "Synthetic prototype configuration; not Tivoli-approved policy.",
                "business_unit": {"id": unit.pk, "code": unit.code, "name": unit.name},
                "role_assignments": [
                    {
                        "id": assignment.pk,
                        "user_id": assignment.user_id,
                        "username": assignment.user.username,
                        "role": assignment.role,
                    }
                    for assignment in BusinessUnitRoleAssignment.objects.filter(
                        business_unit=unit,
                        revoked_at__isnull=True,
                        is_synthetic=True,
                    ).select_related("user").order_by("user__username", "role")
                ],
                "current_prices": [
                    {
                        "id": price.pk,
                        "variant_id": price.variant_id,
                        "sku": price.variant.sku,
                        "amount": format(price.amount, "f"),
                        "currency": price.currency,
                        "revision": price.revision,
                        "approved_by": price.approved_by_id,
                    }
                    # A review sample only: large catalogs are browsed through the price registers.
                    for price in current_prices[:50]
                ],
                "current_price_count": current_prices.count(),
                "work_calendar": (
                    {
                        "is_configured": calendar.is_configured,
                        "closed_weekdays": calendar.closed_weekdays,
                        "closed_dates": calendar.closed_dates,
                    }
                    if calendar
                    else {"is_configured": False, "closed_weekdays": [], "closed_dates": []}
                ),
                "storage_exception_approver_roles": (
                    storage_policy.storage_exception_approver_roles if storage_policy else []
                ),
                "special_order_value_bands": [
                    {
                        "id": band.pk,
                        "minimum_value": format(band.minimum_value, "f"),
                        "maximum_value": format(band.maximum_value, "f") if band.maximum_value else None,
                        "is_active": band.is_active,
                        "approval_reason": band.approval_reason,
                    }
                    for band in SpecialOrderValueBand.objects.filter(
                        business_unit=unit,
                        is_synthetic=True,
                    ).order_by("minimum_value", "pk")
                ],
                "special_order_bands_used_for_approval": False,
                "special_order_bands_note": (
                    "Value bands remain unconfigured until explicitly approved and have no implemented "
                    "business effect until their purpose is approved."
                ),
            }
        )


class ApprovalRoleAssignmentView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        data = _payload(request)
        unit = _unit(data.get("business_unit_id"))
        target = get_object_or_404(get_user_model(), pk=data.get("user_id"))
        result = _run_service(
            assign_approval_role,
            actor=request.user,
            user=target,
            business_unit=unit,
            role=data.get("role"),
        )
        return Response(result, status=status.HTTP_201_CREATED if result["created"] else status.HTTP_200_OK)


class ApprovalRoleAssignmentDetailView(APIView):
    permission_classes = [IsAdminUser]

    def delete(self, request, assignment_id):
        assignment = get_object_or_404(
            BusinessUnitRoleAssignment.objects.select_related("business_unit"),
            pk=assignment_id,
            is_synthetic=True,
        )
        return Response(_run_service(revoke_approval_role, actor=request.user, assignment=assignment))


class WorkCalendarConfigurationView(APIView):
    permission_classes = [IsAdminUser]

    def put(self, request, unit_id):
        data = _payload(request)
        return Response(
            _run_service(
                configure_work_calendar,
                actor=request.user,
                business_unit=_unit(unit_id),
                closed_weekdays=data.get("closed_weekdays"),
                closed_dates=data.get("closed_dates", []),
            )
        )


class SellingPriceConfigurationView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, unit_id):
        data = _payload(request)
        variant = get_object_or_404(Variant, pk=data.get("variant_id"), is_synthetic=True)
        return Response(
            _run_service(
                configure_selling_price,
                actor=request.user,
                business_unit=_unit(unit_id),
                variant=variant,
                amount=data.get("amount"),
            ),
            status=status.HTTP_201_CREATED,
        )


class StoragePolicyConfigurationView(APIView):
    permission_classes = [IsAdminUser]

    def put(self, request, unit_id):
        data = _payload(request)
        return Response(
            _run_service(
                configure_storage_exception_roles,
                actor=request.user,
                business_unit=_unit(unit_id),
                roles=data.get("approver_roles"),
            )
        )


class SyntheticCounterpartyView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        _run_service(_require_staff_demo, user=request.user)
        party_type = request.query_params.get("party_type")
        if party_type and party_type not in SyntheticCounterparty.Type.values:
            raise ValidationError({"party_type": "Choose a supported synthetic party type."})
        queryset = SyntheticCounterparty.objects.filter(is_synthetic=True)
        if party_type:
            queryset = queryset.filter(party_type=party_type)
        queryset = queryset.order_by("party_type", "code", "pk")
        try:
            page, page_size = parse_page_parameters(request.query_params)
            total_count = queryset.count()
            pagination = page_metadata(total_count, page, page_size)
        except ValueError as error:
            raise ValidationError({"detail": str(error)}) from error
        start = (page - 1) * page_size
        rows = list(queryset[start:start + page_size])
        return Response(
            {
                "rows": [
                    {
                        "counterparty_id": row.pk,
                        "party_type": row.party_type,
                        "code": row.code,
                        "name": row.name,
                    }
                    for row in rows
                ],
                "pagination": pagination,
                "synthetic_only": True,
            }
        )

    def post(self, request):
        data = _payload(request)
        return Response(
            _run_service(
                create_synthetic_counterparty,
                actor=request.user,
                party_type=data.get("party_type"),
                code=data.get("code"),
                name=data.get("name"),
            ),
            status=status.HTTP_201_CREATED,
        )


class SyntheticCounterpartyDetailView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, counterparty_id):
        _run_service(_require_staff_demo, user=request.user)
        row = get_object_or_404(
            SyntheticCounterparty.objects.filter(is_synthetic=True),
            pk=counterparty_id,
        )
        return Response(
            {
                "counterparty_id": row.pk,
                "party_type": row.party_type,
                "code": row.code,
                "name": row.name,
                "synthetic_only": True,
            }
        )


class SpecialOrderValueBandView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, unit_id):
        data = _payload(request)
        band = _run_service(
            create_special_order_value_band,
            actor=request.user,
            business_unit=_unit(unit_id),
            minimum_value=data.get("minimum_value"),
            maximum_value=data.get("maximum_value"),
        )
        return Response(
            {
                "band_id": band.pk,
                "business_unit_id": band.business_unit_id,
                "minimum_value": format(band.minimum_value, "f"),
                "maximum_value": format(band.maximum_value, "f") if band.maximum_value else None,
                "is_active": band.is_active,
                "test_only_label": "Draft only; no value thresholds are preconfigured.",
            },
            status=status.HTTP_201_CREATED,
        )


class SpecialOrderValueBandApprovalView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, band_id):
        data = _payload(request)
        band = get_object_or_404(
            SpecialOrderValueBand.objects.select_related("business_unit"),
            pk=band_id,
            is_synthetic=True,
        )
        result = _run_service(
            approve_special_order_value_band,
            actor=request.user,
            band=band,
            reason=data.get("reason", ""),
        )
        return Response(result)


class PurchaseReceiptCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = _payload(request)
        unit = _unit(data.get("business_unit_id"))
        supplier = get_object_or_404(
            SyntheticCounterparty,
            pk=data.get("supplier_id"),
            party_type=SyntheticCounterparty.Type.SUPPLIER,
            is_synthetic=True,
        )
        raw_lines = data.get("lines")
        if not isinstance(raw_lines, list):
            raise ValidationError({"lines": "Send a list of synthetic receipt lines."})
        commitment = None
        if data.get("purchase_commitment_id") is not None:
            commitment = get_object_or_404(
                PurchaseCommitment,
                pk=data.get("purchase_commitment_id"),
                business_unit=unit,
                supplier=supplier,
                is_synthetic=True,
            )
        lines = []
        for index, line in enumerate(raw_lines):
            if not isinstance(line, dict):
                raise ValidationError({"lines": f"Line {index + 1} must be an object."})
            commitment_line = None
            if line.get("commitment_line_id") is not None:
                if commitment is None:
                    raise ValidationError(
                        {"lines": f"Line {index + 1} cannot reference a commitment without purchase_commitment_id."}
                    )
                commitment_line = get_object_or_404(
                    PurchaseCommitmentLine,
                    pk=line.get("commitment_line_id"),
                    commitment=commitment,
                    is_synthetic=True,
                )
            lines.append(
                {
                    "variant": get_object_or_404(
                        Variant,
                        pk=line.get("variant_id"),
                        is_synthetic=True,
                    ),
                    "location": get_object_or_404(
                        Location,
                        pk=line.get("location_id"),
                        is_synthetic=True,
                    ),
                    "quantity": line.get("quantity"),
                    "unit_cost": line.get("unit_cost"),
                    "commitment_line": commitment_line,
                    "quote_line": (
                        get_object_or_404(
                            SalesQuoteLine,
                            pk=line.get("quote_line_id"),
                            is_synthetic=True,
                        )
                        if line.get("quote_line_id") is not None
                        else None
                    ),
                }
            )
        result = _run_service(
            receive_imported_purchase,
            actor=request.user,
            idempotency_key=_idempotency_key(request, data),
            business_unit=unit,
            supplier=supplier,
            lines=lines,
            charges=data.get("charges", []),
            purchase_commitment=commitment,
            **_exchange_fields(data),
        )
        return Response(_receipt_acknowledgement(request.user, unit, result),
                        status=status.HTTP_201_CREATED)


class PurchaseCommitmentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit_id = _query_id(request.query_params, "business_unit_id", required=True)
        unit = _purchasing_unit_for_user(request, unit_id)
        queryset = (
            PurchaseCommitmentLine.objects.filter(
                commitment__business_unit=unit,
                commitment__is_synthetic=True,
                is_synthetic=True,
            )
            .select_related("commitment", "variant__item")
            .annotate(received_to_date=Sum("receipt_lines__quantity"))
            .order_by("commitment__created_at", "commitment_id", "pk")
        )
        try:
            page, page_size = parse_page_parameters(request.query_params)
            total_count = queryset.count()
            pagination = page_metadata(total_count, page, page_size)
        except ValueError as error:
            raise ValidationError({"detail": str(error)}) from error
        start = (page - 1) * page_size
        rows = []
        for line in queryset[start : start + page_size]:
            received = line.received_to_date or Decimal("0.00")
            remaining = line.ordered_quantity - received
            rows.append(
                {
                    "commitment_id": line.commitment_id,
                    "commitment_line_id": line.pk,
                    "supplier_id": line.commitment.supplier_id,
                    "variant_id": line.variant_id,
                    "variant_sku": line.variant.sku,
                    "item_description": line.variant.item.description,
                    "ordered_quantity": format(line.ordered_quantity, "f"),
                    "received_to_date": format(received, "f"),
                    "remaining_quantity": format(remaining, "f"),
                    "currency": line.commitment.currency,
                }
            )
        return Response(
            {
                "rows": rows,
                "pagination": pagination,
                "synthetic_only": True,
                "test_only_label": "Synthetic QAR purchase commitments; not approved company procurement policy.",
            }
        )

    def post(self, request):
        data = _payload(request)
        unit = _unit(data.get("business_unit_id"))
        supplier = get_object_or_404(
            SyntheticCounterparty,
            pk=data.get("supplier_id"),
            party_type=SyntheticCounterparty.Type.SUPPLIER,
            is_synthetic=True,
        )
        raw_lines = data.get("lines")
        if not isinstance(raw_lines, list):
            raise ValidationError({"lines": "Send a list of synthetic commitment lines."})
        lines = []
        for index, line in enumerate(raw_lines):
            if not isinstance(line, dict):
                raise ValidationError({"lines": f"Line {index + 1} must be an object."})
            lines.append(
                {
                    "variant": get_object_or_404(
                        Variant,
                        pk=line.get("variant_id"),
                        is_synthetic=True,
                    ),
                    "ordered_quantity": line.get("ordered_quantity"),
                    "unit_cost": line.get("unit_cost"),
                }
            )
        result = _run_service(
            create_purchase_commitment,
            actor=request.user,
            idempotency_key=_idempotency_key(request, data),
            business_unit=unit,
            supplier=supplier,
            lines=lines,
            **_exchange_fields(data),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class SalesQuoteCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit_id = _query_id(request.query_params, "business_unit_id", required=True)
        unit = _purchasing_unit_for_user(request, unit_id)
        queryset = SalesQuote.objects.filter(
            business_unit=unit,
            is_synthetic=True,
        ).order_by("-created_at", "-pk")
        try:
            page, page_size = parse_page_parameters(request.query_params)
            total_count = queryset.count()
            pagination = page_metadata(total_count, page, page_size)
        except ValueError as error:
            raise ValidationError({"detail": str(error)}) from error
        start = (page - 1) * page_size
        rows = list(queryset[start:start + page_size])
        return Response(
            {
                "business_unit": {"id": unit.pk, "code": unit.code, "name": unit.name},
                "rows": [
                    {
                        "quote_id": quote.pk,
                        "kind": quote.kind,
                        "status": quote.status,
                        "customer_id": quote.customer_id,
                        "total_quote_value": format(quote.total_quote_value, "f"),
                        "currency": quote.currency,
                        "created_at": quote.created_at.isoformat(),
                    }
                    for quote in rows
                ],
                "pagination": pagination,
                "test_only_label": "Synthetic quote records only.",
            }
        )

    def post(self, request):
        data = _payload(request)
        allowed = {
            "business_unit_id", "customer_id", "kind", "lines", "discount_percent",
            "discount_reason", "payment_plan", "downpayment_percent", "payment_terms_reason",
            "idempotency_key", "presentation",
        }
        if set(data) - allowed:
            raise ValidationError({"detail": "Price overrides and undocumented quotation fields are not accepted."})
        unit = _unit(data.get("business_unit_id"))
        customer = get_object_or_404(
            SyntheticCounterparty,
            pk=data.get("customer_id"),
            party_type=SyntheticCounterparty.Type.CUSTOMER,
            is_synthetic=True,
        )
        raw_lines = data.get("lines")
        if not isinstance(raw_lines, list):
            raise ValidationError({"lines": "Send a list of synthetic quote lines."})
        lines = []
        for index, line in enumerate(raw_lines):
            if not isinstance(line, dict):
                raise ValidationError({"lines": f"Line {index + 1} must be an object."})
            if set(line) - {"variant_id", "location_id", "quantity", "specifications"}:
                raise ValidationError({"lines": f"Line {index + 1}: approved prices and item data cannot be overridden."})
            lines.append(
                {
                    "variant": get_object_or_404(Variant, pk=line.get("variant_id"), is_synthetic=True),
                    "location": (
                        get_object_or_404(Location, pk=line.get("location_id"), is_synthetic=True)
                        if line.get("location_id") is not None
                        else None
                    ),
                    "quantity": line.get("quantity"),
                    "specifications": line.get("specifications", {}),
                }
            )
        result = _run_service(
            create_sales_quote,
            actor=request.user,
            idempotency_key=_idempotency_key(request, data),
            business_unit=unit,
            customer=customer,
            kind=data.get("kind"),
            lines=lines,
            discount_percent=data.get("discount_percent", "0.00"),
            discount_reason=data.get("discount_reason", ""),
            payment_plan=data.get("payment_plan", "full"),
            downpayment_percent=data.get("downpayment_percent"),
            payment_terms_reason=data.get("payment_terms_reason", ""),
            presentation=data.get("presentation"),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class QuotePreparationView(APIView):
    """Non-posting review with independently authorized ledger identities."""

    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "head", "options"]

    def get(self, request, quote_id):
        from rest_framework.exceptions import PermissionDenied
        from .stock_ledger_views import scoped_ledger_quantities

        quote = _quote_for_user(request, quote_id)
        # Do not let resolve_unit fall back to another session/default unit.
        if _query_id(request.query_params, "unit", required=True) != quote.business_unit_id:
            raise PermissionDenied("The stock context must match the quote business unit.")
        data = _quote_summary(quote)
        identities = {}
        try:
            _unit, records = scoped_ledger_quantities(request)
        except PermissionDenied:
            records = None
        if records is not None:
            for line in quote.lines.order_by("pk"):
                bucket = records.filter(
                    variant_id=line.variant_id, location_id=line.location_id,
                ).first() if line.location_id else None
                if bucket:
                    identities[line.pk] = {
                        "item_code": bucket.variant.item.item_code,
                        "description": bucket.variant.item.description,
                        "sku": bucket.variant.sku,
                        "brand": bucket.variant.item.brand.name,
                        "location_code": bucket.location.code,
                        "location_name": bucket.location.name,
                    }
        for line in data["lines"]:
            line["preparation_identity"] = identities.get(line["quote_line_id"])
        return Response(data)


class SalesQuoteDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, quote_id):
        return Response(_quote_summary(_quote_for_user(request, quote_id)))


def _storage_charge_payload(charge):
    exception = getattr(charge, "exception", None)
    return {
        "charge_id": charge.pk,
        "quote_id": charge.quote_id,
        "business_unit_id": charge.quote.business_unit_id,
        "period_start": charge.period_start.isoformat(),
        "period_end": charge.period_end.isoformat(),
        "base_quote_value": format(charge.base_quote_value, "f"),
        "amount": format(charge.amount, "f"),
        "currency": charge.quote.currency,
        "created_at": charge.created_at.isoformat(),
        "exception": (
            {
                "approver_id": exception.approver_id,
                "approver_role": exception.approver_role,
                "amount_waived": format(exception.amount_waived, "f"),
                "reason": exception.reason,
                "approved_at": exception.approved_at.isoformat(),
            }
            if exception is not None
            else None
        ),
    }


class StorageChargeListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit_id = _query_id(request.query_params, "business_unit_id", required=True)
        unit = _purchasing_unit_for_user(request, unit_id)
        quote_id = _query_id(request.query_params, "quote_id")
        queryset = StorageCharge.objects.filter(
            is_synthetic=True,
            quote__is_synthetic=True,
            quote__business_unit=unit,
        ).select_related("quote", "quote__business_unit", "exception")
        if quote_id is not None:
            queryset = queryset.filter(quote_id=quote_id)
        queryset = queryset.order_by("period_start", "pk")
        try:
            page, page_size = parse_page_parameters(request.query_params)
            total_count = queryset.count()
            pagination = page_metadata(total_count, page, page_size)
        except ValueError as error:
            raise ValidationError({"detail": str(error)}) from error
        start = (page - 1) * page_size
        return Response(
            {
                "business_unit": {"id": unit.pk, "code": unit.code, "name": unit.name},
                "rows": [_storage_charge_payload(charge) for charge in queryset[start:start + page_size]],
                "pagination": pagination,
                "test_only_label": "Synthetic storage-charge records only.",
            }
        )


class StorageChargeDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, charge_id):
        unit_id = _query_id(request.query_params, "business_unit_id", required=True)
        unit = _purchasing_unit_for_user(request, unit_id)
        charge = get_object_or_404(
            StorageCharge.objects.select_related(
                "quote",
                "quote__business_unit",
                "exception",
            ).filter(
                is_synthetic=True,
                quote__is_synthetic=True,
                quote__business_unit=unit,
            ),
            pk=charge_id,
        )
        return Response(
            {
                "business_unit": {"id": unit.pk, "code": unit.code, "name": unit.name},
                "charge": _storage_charge_payload(charge),
                "test_only_label": "Synthetic storage-charge record only.",
            }
        )


class QuoteDiscountApprovalView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, quote_id):
        data = _payload(request)
        result = _run_service(
            approve_quote_discount,
            actor=request.user,
            quote=_quote_for_user(request, quote_id),
            idempotency_key=_idempotency_key(request, data),
            reason=data.get("reason", ""),
        )
        return Response(result)


class QuoteHoldView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, quote_id):
        data = _payload(request)
        result = _run_service(
            create_temporary_quote_hold,
            actor=request.user,
            quote=_quote_for_user(request, quote_id),
            idempotency_key=_idempotency_key(request, data),
            working_days=data.get("working_days", DEFAULT_HOLD_WORKING_DAYS),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class QuotePaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, quote_id):
        data = _payload(request)
        result = _run_service(
            record_simulated_payment,
            actor=request.user,
            quote=_quote_for_user(request, quote_id),
            idempotency_key=_idempotency_key(request, data),
            amount=data.get("amount"),
            method=data.get("method"),
            purpose=data.get("purpose", "quote"),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class QuotePostHandoverApprovalView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, quote_id):
        data = _payload(request)
        result = _run_service(
            approve_post_handover_balance,
            actor=request.user,
            quote=_quote_for_user(request, quote_id),
            idempotency_key=_idempotency_key(request, data),
            deferred_balance_percent=data.get("deferred_balance_percent"),
            reason=data.get("reason", ""),
        )
        return Response(result)


class QuoteReadyNotificationView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, quote_id):
        data = _payload(request)
        result = _run_service(
            notify_quote_ready,
            actor=request.user,
            quote=_quote_for_user(request, quote_id),
            idempotency_key=_idempotency_key(request, data),
            message=data.get("message", ""),
        )
        return Response(result)


class QuoteDeliveryView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, quote_id):
        data = _payload(request)
        result = _run_service(
            deliver_sales_quote,
            actor=request.user,
            quote=_quote_for_user(request, quote_id),
            idempotency_key=_idempotency_key(request, data),
        )
        # This purchasing action is not a sensitive-financial reporting grant.
        # Keep private cost/valuation facts in the immutable service result;
        # project the same non-cost acknowledgement on first call and replay.
        public_line_fields = {
            "journal_entry_id", "movement_id", "reservation_id",
            "quantity_delivered", "revenue",
        }
        return Response({
            **result,
            "delivery_results": [
                {key: value for key, value in line.items() if key in public_line_fields}
                for line in result["delivery_results"]
            ],
        })


class QuoteStorageAccrualView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, quote_id):
        data = _payload(request)
        return Response(
            _run_service(
                accrue_storage_charges,
                actor=request.user,
                quote=_quote_for_user(request, quote_id),
                idempotency_prefix=_idempotency_key(request, data),
            )
        )


class StorageChargeExceptionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, charge_id):
        data = _payload(request)
        charge = get_object_or_404(
            StorageCharge.objects.select_related("quote", "quote__business_unit"),
            pk=charge_id,
            is_synthetic=True,
            quote__business_unit_id__in=active_grants(
                request.user,
                UserActionGrant.Action.CREATE_ORDER,
            ).values("business_unit_id"),
        )
        return Response(
            _run_service(
                approve_storage_charge_exception,
                actor=request.user,
                charge=charge,
                idempotency_key=_idempotency_key(request, data),
                amount_waived=data.get("amount_waived"),
                reason=data.get("reason", ""),
            )
        )
