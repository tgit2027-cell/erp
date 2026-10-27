"""Synthetic-only retail purchase-to-delivery workflow services.

These services coordinate the existing append-only stock/accounting services
with synthetic quote, approval, payment, hold, and storage records. They are
not approved Tivoli accounting, pricing, or commercial policy.
"""

import hashlib
import json
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from .access import active_grants
from .notification_outbox import enqueue_notification_intent
from .models import (
    ApprovedSellingPrice,
    BusinessUnit,
    BusinessUnitApprovalRole,
    BusinessUnitRoleAssignment,
    BASE_CURRENCY,
    BusinessUnitWorkCalendar,
    Currency,
    CustomerInvoice,
    CustomerInvoiceLine,
    CustomerInvoiceSequence,
    DemoUserMarker,
    IdempotencyRecord,
    JournalEntry,
    PurchaseCommitment,
    PurchaseCommitmentLine,
    PurchaseReceipt,
    PurchaseReceiptCharge,
    PurchaseReceiptLine,
    QuoteApproval,
    QuoteHold,
    QuotePayment,
    QuoteReservation,
    SalesQuote,
    SalesQuoteLine,
    SalesReportLine,
    SpecialOrderValueBand,
    StockBalance,
    StockMovement,
    StockReservation,
    StorageCharge,
    StorageChargeException,
    SupplierKyc,
    SyntheticCounterparty,
    UserActionGrant,
    WorkflowEvent,
    WorkflowPolicyConfig,
)
from .transaction_services import (
    InvalidTransaction,
    IdempotencyConflict,
    InsufficientStock,
    ReceiptLine,
    TransactionServiceError,
    _begin_idempotent_operation,
    _complete,
    _money,
    _post_journal,
    _quantity,
    _require_demo,
    _require_stock_location,
    _validate_bucket,
    deliver_reserved_stock,
    receive_stock,
    reserve_stock,
)


TEST_ONLY_LABEL = "TEST ONLY — not Tivoli-approved policy"
DEPOSIT_RESERVATION_PERCENT = Decimal("50.00")
SPECIAL_ORDER_MIN_DOWNPAYMENT = Decimal("40.00")
SPECIAL_ORDER_MAX_DOWNPAYMENT = Decimal("70.00")
POST_HANDOVER_MIN_PERCENT = Decimal("5.00")
POST_HANDOVER_MAX_PERCENT = Decimal("10.00")
STORAGE_MONTHLY_PERCENT = Decimal("1.00")
FREE_STORAGE_MONTHS = 1
# Owner decision N2: a quote hold lasts three working days unless shortened.
DEFAULT_HOLD_WORKING_DAYS = 3

DISCOUNT_CEILINGS = {
    BusinessUnitRoleAssignment.Role.SALESPERSON: Decimal("10.00"),
    BusinessUnitRoleAssignment.Role.MANAGER: Decimal("20.00"),
    BusinessUnitRoleAssignment.Role.COMMERCIAL_GM: Decimal("30.00"),
    BusinessUnitRoleAssignment.Role.CEO: Decimal("50.00"),
}
ROLE_ORDER = {
    BusinessUnitRoleAssignment.Role.SALESPERSON: 1,
    BusinessUnitRoleAssignment.Role.MANAGER: 2,
    BusinessUnitRoleAssignment.Role.COMMERCIAL_GM: 3,
    BusinessUnitRoleAssignment.Role.BU_GM: 4,
    BusinessUnitRoleAssignment.Role.CEO: 5,
}


class WorkflowPermissionDenied(TransactionServiceError):
    pass


class WorkflowConfigurationMissing(TransactionServiceError):
    pass


def _workflow_key(key, suffix):
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:48]
    return f"workflow:{suffix}:{digest}"


def _percent(value, field):
    if isinstance(value, float):
        raise InvalidTransaction(f"{field} must be supplied as a decimal-safe value, not float.")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise InvalidTransaction(f"{field} must be a finite decimal percentage.") from None
    if not result.is_finite():
        raise InvalidTransaction(f"{field} must be finite.")
    rounded = result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if result != rounded:
        raise InvalidTransaction(f"{field} supports at most two decimal places.")
    return rounded


def _require_demo_actor(user, business_unit, *, require_order_grant=True):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Purchasing and delivery workflows are restricted to APP_ENV=demo.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can perform these operations.")
    _require_demo(business_unit, business_unit.legal_entity)
    if require_order_grant and not active_grants(
        user,
        UserActionGrant.Action.CREATE_ORDER,
    ).filter(business_unit=business_unit).exists():
        raise WorkflowPermissionDenied("An active create_order grant is required for this business unit.")
    roles = list(
        BusinessUnitRoleAssignment.objects.filter(
            user=user,
            business_unit=business_unit,
            revoked_at__isnull=True,
            is_synthetic=True,
        ).values_list("role", flat=True)
    )
    if require_order_grant and not roles:
        raise WorkflowPermissionDenied("An active approval-role assignment is required for this business unit.")
    return roles


def require_supplier_approved(supplier):
    """Refuse a supplier whose KYC file exists but is not approved yet."""
    kyc = SupplierKyc.objects.filter(supplier=supplier).only("status").first()
    if kyc is not None and kyc.status != SupplierKyc.Status.APPROVED:
        raise InvalidTransaction(
            f"Supplier {supplier.code} is not approved yet (KYC: {SupplierKyc.Status(kyc.status).label.lower()})."
        )


def _require_staff_demo(user, business_unit=None, *synthetic_objects):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Workflow configuration is restricted to APP_ENV=demo.")
    if (
        not getattr(user, "is_authenticated", False)
        or not user.is_staff
        or not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists()
    ):
        raise WorkflowPermissionDenied("A synthetic Django staff account is required.")
    objects = ([business_unit] if business_unit is not None else []) + list(synthetic_objects)
    if objects:
        _require_demo(*objects)


def _primary_role(roles):
    if not roles:
        raise WorkflowPermissionDenied("An active approval-role assignment is required.")
    return max(roles, key=lambda role: ROLE_ORDER.get(role, 0))


def _price_approver_role(roles, discount):
    eligible = [
        role for role in roles
        if role in DISCOUNT_CEILINGS and DISCOUNT_CEILINGS[role] >= discount
    ]
    if eligible:
        return max(eligible, key=lambda role: DISCOUNT_CEILINGS[role])
    if discount == 0 and BusinessUnitRoleAssignment.Role.BU_GM in roles:
        return BusinessUnitRoleAssignment.Role.BU_GM
    raise WorkflowPermissionDenied("No assigned price-approval role can approve this discount.")


def _discount_cap(roles):
    return max((DISCOUNT_CEILINGS.get(role, Decimal("0.00")) for role in roles), default=Decimal("0.00"))


def _positive_quantity(value, field="quantity"):
    result = _quantity(value, field)
    if result <= 0:
        raise InvalidTransaction(f"{field} must be greater than zero.")
    return result


RATE_STEP = Decimal("0.00000001")


def _exchange_terms(currency, exchange_rate, rate_source, rate_date):
    """Validate a document's currency and its QAR rate (owner decision P1, provisional).

    The rate is entered per transaction as QAR per one unit of the currency,
    with where it came from and its date, so the conversion can be audited.
    """
    if currency in (None, ""):
        currency = BASE_CURRENCY
    code = currency.upper() if isinstance(currency, str) else None
    if code != BASE_CURRENCY and (code is None or not Currency.objects.filter(code=code, is_active=True).exists()):
        raise InvalidTransaction("Choose a supported currency.")
    if code == BASE_CURRENCY:
        if exchange_rate not in (None, "", 1, "1") and _rate(exchange_rate) != 1:
            raise InvalidTransaction("A QAR document always uses an exchange rate of 1.")
        return {"currency": code, "exchange_rate": Decimal("1"), "rate_source": "", "rate_date": None}
    rate = _rate(exchange_rate)
    source = rate_source.strip() if isinstance(rate_source, str) else ""
    if not source or len(source) > 120:
        raise InvalidTransaction("Say where the exchange rate comes from (at most 120 characters).")
    try:
        day = rate_date if isinstance(rate_date, date) else date.fromisoformat(rate_date)
    except (TypeError, ValueError):
        raise InvalidTransaction("Give the exchange-rate date as YYYY-MM-DD.") from None
    if day > timezone.localdate():
        raise InvalidTransaction("The exchange-rate date cannot be in the future.")
    return {"currency": code, "exchange_rate": rate, "rate_source": source, "rate_date": day}


def _charge_exchange_terms(charge, category, document_terms, index):
    """Currency of one landed charge (owner decision P1, provisional).

    Shipping and other charges may be billed in a foreign currency at a manually
    entered rate; customs clearance is always paid in QAR. A charge in the same
    currency as the supplier document reuses the document rate unless its own is given.
    """
    currency = charge.get("currency")
    if currency in (None, ""):
        return _exchange_terms(BASE_CURRENCY, None, None, None)
    code = currency.upper() if isinstance(currency, str) else None
    if code != BASE_CURRENCY and category == PurchaseReceiptCharge.Category.CLEARANCE:
        raise InvalidTransaction(f"Landed charge {index + 1}: customs clearance is always entered in QAR.")
    if code == document_terms["currency"] and code != BASE_CURRENCY and charge.get("exchange_rate") in (None, ""):
        return document_terms
    try:
        return _exchange_terms(code, charge.get("exchange_rate"), charge.get("rate_source"), charge.get("rate_date"))
    except InvalidTransaction as error:
        raise InvalidTransaction(f"Landed charge {index + 1}: {error}") from None


def _rate(value):
    if isinstance(value, float):
        raise InvalidTransaction("The exchange rate must be sent as a decimal string, not a float.")
    try:
        rate = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise InvalidTransaction("The exchange rate must be a number.") from None
    if not rate.is_finite() or rate <= 0:
        raise InvalidTransaction("The exchange rate must be greater than zero.")
    if rate != rate.quantize(RATE_STEP):
        raise InvalidTransaction("The exchange rate supports at most eight decimal places.")
    if rate >= Decimal("10000000000"):
        raise InvalidTransaction("The exchange rate is too large.")
    return rate


def _to_base(amount, rate):
    return (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _exchange_payload(terms):
    return {
        "currency": terms["currency"],
        "exchange_rate": format(terms["exchange_rate"], "f"),
        "rate_source": terms["rate_source"],
        "rate_date": terms["rate_date"].isoformat() if terms["rate_date"] else None,
    }


def _working_calendar(business_unit):
    try:
        calendar = BusinessUnitWorkCalendar.objects.get(business_unit=business_unit, is_configured=True)
    except BusinessUnitWorkCalendar.DoesNotExist:
        raise WorkflowConfigurationMissing(
            "The business-unit work calendar is not configured; temporary holds are disabled."
        ) from None
    closed_weekdays = calendar.closed_weekdays
    closed_dates = calendar.closed_dates
    if (
        not isinstance(closed_weekdays, list)
        or any(type(day) is not int or day < 0 or day > 6 for day in closed_weekdays)
        or not isinstance(closed_dates, list)
    ):
        raise WorkflowConfigurationMissing("The business-unit work calendar contains invalid configuration.")
    parsed_closed_dates = set()
    for value in closed_dates:
        try:
            parsed_closed_dates.add(date.fromisoformat(value))
        except (TypeError, ValueError):
            raise WorkflowConfigurationMissing(
                "The business-unit work calendar contains an invalid closed date."
            ) from None
    return calendar, set(closed_weekdays), parsed_closed_dates


def _hold_expiry(business_unit, now, working_days):
    if not 1 <= working_days <= 3:
        raise InvalidTransaction("A temporary hold may cover one to three working days.")
    _calendar, closed_weekdays, closed_dates = _working_calendar(business_unit)
    # Count from the business's local calendar day, not the UTC date.
    cursor = timezone.localtime(now).date()
    counted = 0
    while counted < working_days:
        cursor += timedelta(days=1)
        if cursor.weekday() not in closed_weekdays and cursor not in closed_dates:
            counted += 1
    local_now = timezone.localtime(now)
    return local_now.replace(year=cursor.year, month=cursor.month, day=cursor.day)


def _add_calendar_month(day):
    year = day.year + (day.month == 12)
    month = 1 if day.month == 12 else day.month + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def _quote_result(quote):
    return {
        "quote_id": quote.pk,
        "business_unit_id": quote.business_unit_id,
        "kind": quote.kind,
        "status": quote.status,
        "customer_id": quote.customer_id,
        "discount_percent": format(quote.discount_percent, "f"),
        "total_list_value": format(quote.total_list_value, "f"),
        "total_discount": format(quote.total_discount, "f"),
        "total_quote_value": format(quote.total_quote_value, "f"),
        "payment_plan": quote.payment_plan,
        "downpayment_percent": (
            format(quote.downpayment_percent, "f") if quote.downpayment_percent is not None else None
        ),
        "lines": [
            {
                "quote_line_id": line.pk,
                "variant_id": line.variant_id,
                "quantity": format(line.quantity, "f"),
                "selling_price_snapshot": format(line.selling_price_snapshot, "f"),
                "price_revision_snapshot": line.price_revision_snapshot,
                "discount_percent": format(line.discount_percent, "f"),
                "discount_amount": format(line.discount_amount, "f"),
                "net_unit_price": format(line.net_unit_price, "f"),
                "net_total": format(line.net_total, "f"),
                "specifications": line.specifications,
            }
            for line in quote.lines.order_by("pk")
        ],
        "special_order_value_bands_configured": quote.business_unit.special_order_value_bands.filter(
            is_active=True
        ).exists(),
    }


def _approve(quote, *, kind, approver, role, reason, details=None):
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason:
        raise InvalidTransaction("An approval reason is required.")
    return QuoteApproval.objects.create(
        quote=quote,
        kind=kind,
        approver=approver,
        role=role,
        reason=reason[:240],
        details=details or {},
    )


@transaction.atomic
def assign_approval_role(*, actor, user, business_unit, role):
    _require_staff_demo(actor, business_unit)
    if role not in BusinessUnitRoleAssignment.Role.values:
        raise InvalidTransaction("Choose a supported approval role.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Roles can only be assigned to synthetic demo users.")
    if not active_grants(user, UserActionGrant.Action.CREATE_ORDER).filter(
        business_unit=business_unit
    ).exists():
        raise WorkflowPermissionDenied(
            "The target user must already have an active create_order grant for this business unit."
        )
    assignment, created = BusinessUnitRoleAssignment.objects.get_or_create(
        user=user,
        business_unit=business_unit,
        role=role,
        revoked_at__isnull=True,
        defaults={"assigned_by": actor},
    )
    return {
        "assignment_id": assignment.pk,
        "user_id": user.pk,
        "business_unit_id": business_unit.pk,
        "role": assignment.role,
        "created": created,
    }


@transaction.atomic
def revoke_approval_role(*, actor, assignment):
    _require_staff_demo(actor, assignment.business_unit)
    if assignment.revoked_at is None:
        assignment.revoked_at = timezone.now()
        assignment.save(update_fields=["revoked_at"])
    return {"assignment_id": assignment.pk, "revoked": True}


@transaction.atomic
def configure_work_calendar(*, actor, business_unit, closed_weekdays, closed_dates):
    _require_staff_demo(actor, business_unit)
    if (
        not isinstance(closed_weekdays, list)
        or any(type(day) is not int or day < 0 or day > 6 for day in closed_weekdays)
        or len(set(closed_weekdays)) != len(closed_weekdays)
    ):
        raise InvalidTransaction("closed_weekdays must be unique weekday numbers from 0 to 6.")
    if not isinstance(closed_dates, list):
        raise InvalidTransaction("closed_dates must be a list of ISO dates.")
    normalized_dates = []
    for value in closed_dates:
        try:
            normalized_dates.append(date.fromisoformat(value).isoformat())
        except (TypeError, ValueError):
            raise InvalidTransaction("Every closed date must use YYYY-MM-DD format.") from None
    calendar, _ = BusinessUnitWorkCalendar.objects.update_or_create(
        business_unit=business_unit,
        defaults={
            "closed_weekdays": sorted(closed_weekdays),
            "closed_dates": sorted(set(normalized_dates)),
            "is_configured": True,
            "updated_at": timezone.now(),
        },
    )
    return {
        "business_unit_id": business_unit.pk,
        "closed_weekdays": calendar.closed_weekdays,
        "closed_dates": calendar.closed_dates,
        "is_configured": calendar.is_configured,
    }


@transaction.atomic
def configure_storage_exception_roles(*, actor, business_unit, roles):
    _require_staff_demo(actor, business_unit)
    if not isinstance(roles, list) or any(role not in BusinessUnitRoleAssignment.Role.values for role in roles):
        raise InvalidTransaction("Storage exception approver roles must be supported business-unit roles.")
    config, _ = WorkflowPolicyConfig.objects.update_or_create(
        business_unit=business_unit,
        defaults={"storage_exception_approver_roles": sorted(set(roles)), "updated_at": timezone.now()},
    )
    return {
        "business_unit_id": business_unit.pk,
        "storage_exception_approver_roles": config.storage_exception_approver_roles,
        "configured": bool(config.storage_exception_approver_roles),
    }


@transaction.atomic
def configure_selling_price(*, actor, business_unit, variant, amount):
    _require_staff_demo(actor, business_unit, variant, variant.item, variant.item.brand)
    if not variant.item.is_synthetic:
        raise WorkflowPermissionDenied("Only synthetic catalog variants can be priced in this prototype.")
    price = _money(amount, "approved selling price")
    if price <= 0:
        raise InvalidTransaction("Approved selling price must be greater than zero.")
    current = (
        ApprovedSellingPrice.objects.select_for_update()
        .filter(business_unit=business_unit, variant=variant, is_current=True)
        .order_by("-revision")
        .first()
    )
    revision = current.revision + 1 if current else 1
    if current:
        current.is_current = False
        current.save(update_fields=["is_current"])
    record = ApprovedSellingPrice.objects.create(
        business_unit=business_unit,
        variant=variant,
        amount=price,
        currency="QAR",
        revision=revision,
        is_current=True,
        approved_by=actor,
        approved_at=timezone.now(),
    )
    return {
        "price_id": record.pk,
        "business_unit_id": business_unit.pk,
        "variant_id": variant.pk,
        "approved_selling_price": format(record.amount, "f"),
        "currency": record.currency,
        "revision": record.revision,
        "approved_by": actor.pk,
        "approved_at": record.approved_at.isoformat(),
        "synthetic_only": True,
    }


@transaction.atomic
def create_synthetic_counterparty(*, actor, party_type, code, name):
    _require_staff_demo(actor)
    if party_type not in SyntheticCounterparty.Type.values:
        raise InvalidTransaction("Choose supplier or customer.")
    code = code.strip().upper() if isinstance(code, str) else ""
    name = name.strip() if isinstance(name, str) else ""
    expected_prefix = "TEST-SUP-" if party_type == SyntheticCounterparty.Type.SUPPLIER else "TEST-CUST-"
    if not code.startswith(expected_prefix) or len(code) > 40:
        raise InvalidTransaction(f"Synthetic codes must begin with {expected_prefix}.")
    if not name.startswith("TEST ONLY"):
        raise InvalidTransaction("Synthetic counterparty names must begin with TEST ONLY.")
    record = SyntheticCounterparty.objects.create(party_type=party_type, code=code, name=name)
    return {
        "counterparty_id": record.pk,
        "party_type": record.party_type,
        "code": record.code,
        "name": record.name,
        "synthetic_only": True,
    }


@transaction.atomic
def create_special_order_value_band(*, actor, business_unit, minimum_value, maximum_value=None):
    _require_staff_demo(actor, business_unit)
    minimum = _money(minimum_value, "minimum special-order value")
    maximum = _money(maximum_value, "maximum special-order value") if maximum_value is not None else None
    if maximum is not None and maximum <= minimum:
        raise InvalidTransaction("Maximum special-order value must exceed the minimum.")
    return SpecialOrderValueBand.objects.create(
        business_unit=business_unit,
        minimum_value=minimum,
        maximum_value=maximum,
        created_by=actor,
        is_active=False,
    )


@transaction.atomic
def approve_special_order_value_band(*, actor, band, reason):
    roles = _require_demo_actor(actor, band.business_unit)
    if BusinessUnitRoleAssignment.Role.CEO not in roles:
        raise WorkflowPermissionDenied("Only a synthetic user with an active CEO role can approve value bands.")
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason:
        raise InvalidTransaction("An approval reason is required.")
    if band.is_active:
        return {"band_id": band.pk, "is_active": True, "approved_by": band.approved_by_id}
    existing = SpecialOrderValueBand.objects.select_for_update().filter(
        business_unit=band.business_unit,
        is_active=True,
    )
    band_max = band.maximum_value
    for other in existing:
        other_max = other.maximum_value
        if (other_max is None or band.minimum_value < other_max) and (
            band_max is None or other.minimum_value < band_max
        ):
            raise InvalidTransaction("Approved special-order value bands cannot overlap.")
    band.approved_by = actor
    band.approved_at = timezone.now()
    band.approval_reason = reason[:240]
    band.is_active = True
    band.save(update_fields=["approved_by", "approved_at", "approval_reason", "is_active"])
    return {"band_id": band.pk, "is_active": True, "approved_by": actor.pk}


def _clean_specifications(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InvalidTransaction("Each quote line's specifications must be an object.")
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise InvalidTransaction("Quote specifications must contain JSON-safe values.") from None
    if len(encoded) > 4000:
        raise InvalidTransaction("Quote specifications cannot exceed 4,000 characters.")
    return value


@transaction.atomic
def create_sales_quote(
    *,
    actor,
    idempotency_key,
    business_unit,
    customer,
    kind,
    lines,
    discount_percent,
    discount_reason,
    payment_plan="full",
    downpayment_percent=None,
    payment_terms_reason="",
    presentation=None,
):
    roles = _require_demo_actor(actor, business_unit)
    if kind not in SalesQuote.Kind.values:
        raise InvalidTransaction("Choose stock or special_order for the quote kind.")
    _require_demo(customer)
    if customer.party_type != SyntheticCounterparty.Type.CUSTOMER:
        raise InvalidTransaction("A synthetic customer record is required.")
    if not isinstance(lines, (list, tuple)) or not lines or len(lines) > 40:
        raise InvalidTransaction("A quote must contain between one and 40 lines.")
    discount = _percent(discount_percent, "discount_percent")
    if discount < 0 or discount > Decimal("50.00"):
        raise InvalidTransaction("Discounts cannot exceed the exceptional 50% ceiling.")
    if not actor.is_staff and discount > _discount_cap(roles):
        raise WorkflowPermissionDenied("The requested discount exceeds this salesperson's existing authority.")
    from .quotation_presentation import clean_presentation, quotation_document
    display = clean_presentation(presentation)
    discount_reason = discount_reason.strip() if isinstance(discount_reason, str) else ""
    if not discount_reason:
        raise InvalidTransaction("A price-decision reason is required.")

    if kind == SalesQuote.Kind.SPECIAL_ORDER:
        if payment_plan not in ("full", "downpayment"):
            raise InvalidTransaction("Special orders must choose full payment or an approved downpayment schedule.")
        if payment_plan == "downpayment":
            downpayment = _percent(downpayment_percent, "downpayment_percent")
            if not SPECIAL_ORDER_MIN_DOWNPAYMENT <= downpayment <= SPECIAL_ORDER_MAX_DOWNPAYMENT:
                raise InvalidTransaction("Special-order downpayments must be from 40% through 70%.")
        else:
            downpayment = None
        terms_reason = payment_terms_reason.strip() if isinstance(payment_terms_reason, str) else ""
        if not terms_reason:
            raise InvalidTransaction("A payment-plan approver reason is required for every special order.")
    else:
        if payment_plan != "full" or downpayment_percent is not None:
            raise InvalidTransaction("In-stock quotes use the standard full-payment-before-delivery terms.")
        downpayment = None
        terms_reason = ""

    prepared = []
    for position, line in enumerate(lines):
        variant = line.get("variant")
        location = line.get("location")
        if variant is None:
            raise InvalidTransaction(f"Quote line {position + 1} requires a variant.")
        _require_demo(variant, variant.item, variant.item.brand)
        quantity = _positive_quantity(line.get("quantity"), "quote quantity")
        specifications = _clean_specifications(line.get("specifications"))
        if kind == SalesQuote.Kind.STOCK:
            if location is None:
                raise InvalidTransaction("In-stock quote lines require a stock location.")
            _validate_bucket(business_unit, variant, location)
            _require_stock_location(location, "sales")
        elif location is not None:
            raise InvalidTransaction("Special-order lines cannot claim an in-stock location before receipt.")
        prepared.append(
            {
                "variant": variant,
                "location": location,
                "quantity": quantity,
                "specifications": specifications,
            }
        )

    payload = {
        "business_unit": business_unit.pk,
        "customer": customer.pk,
        "kind": kind,
        "discount_percent": discount,
        "discount_reason": discount_reason,
        "payment_plan": payment_plan,
        "downpayment_percent": downpayment,
        "payment_terms_reason": terms_reason,
        "lines": [
            {
                "variant": row["variant"].pk,
                "location": row["location"].pk if row["location"] else None,
                "quantity": row["quantity"],
                "specifications": row["specifications"],
            }
            for row in prepared
        ],
    }
    if presentation is not None:
        payload["presentation"] = display
    operation, created = _begin_idempotent_operation(idempotency_key, "sales_quote", payload)
    if not created:
        return operation.result
    if display.get("valid_until") and date.fromisoformat(display["valid_until"]) < timezone.localdate():
        raise InvalidTransaction("Quote validity cannot precede the quotation date.")

    for row in prepared:
        try:
            approved_price = ApprovedSellingPrice.objects.select_for_update().get(
                business_unit=business_unit,
                variant=row["variant"],
                is_current=True,
                is_synthetic=True,
                currency="QAR",
            )
        except ApprovedSellingPrice.DoesNotExist:
            raise WorkflowConfigurationMissing(
                f"No approved synthetic selling-price snapshot is configured for {row['variant'].sku} in "
                f"{business_unit.code}; quoting is disabled until staff configures one."
            ) from None
        base_total = _money(approved_price.amount * row["quantity"], "quote line list value")
        net_unit_price = _money(
            approved_price.amount * (Decimal("100.00") - discount) / Decimal("100.00"),
            "discounted unit price",
        )
        net_total = _money(net_unit_price * row["quantity"], "quote line net value")
        row.update(
            {
                "approved_price": approved_price,
                "base_total": base_total,
                "discount_amount": base_total - net_total,
                "net_total": net_total,
                "net_unit_price": net_unit_price,
            }
        )

    total_list = sum((row["base_total"] for row in prepared), Decimal("0.00"))
    total_discount = sum((row["discount_amount"] for row in prepared), Decimal("0.00"))
    total_quote = total_list - total_discount
    status_value = (
        SalesQuote.Status.APPROVED
        if discount <= _discount_cap(roles)
        else SalesQuote.Status.PENDING_APPROVAL
    )
    quote = SalesQuote.objects.create(
        idempotency=operation,
        business_unit=business_unit,
        customer=customer,
        kind=kind,
        status=status_value,
        created_by=actor,
        discount_percent=discount,
        total_list_value=total_list,
        total_discount=total_discount,
        total_quote_value=total_quote,
        payment_plan=payment_plan,
        downpayment_percent=downpayment,
        presentation_document=quotation_document(
            actor=actor, unit=business_unit, customer=customer, prepared=prepared,
            discount=discount, total_list=total_list, total_discount=total_discount,
            total=total_quote, display=display,
        ),
    )
    for row in prepared:
        SalesQuoteLine.objects.create(
            quote=quote,
            variant=row["variant"],
            location=row["location"],
            approved_price=row["approved_price"],
            quantity=row["quantity"],
            specifications=row["specifications"],
            selling_price_snapshot=row["approved_price"].amount,
            price_revision_snapshot=row["approved_price"].revision,
            net_unit_price=row["net_unit_price"],
            discount_percent=discount,
            discount_amount=row["discount_amount"],
            net_total=row["net_total"],
        )

    if discount <= _discount_cap(roles):
        _approve(
            quote,
            kind=QuoteApproval.Kind.DISCOUNT,
            approver=actor,
            role=_price_approver_role(roles, discount),
            reason=discount_reason,
            details={"discount_percent": format(discount, "f"), "ceiling": format(_discount_cap(roles), "f")},
        )
    if kind == SalesQuote.Kind.SPECIAL_ORDER:
        _approve(
            quote,
            kind=QuoteApproval.Kind.PAYMENT_TERMS,
            approver=actor,
            role=_primary_role(roles),
            reason=terms_reason,
            details={
                "payment_plan": payment_plan,
                "downpayment_percent": format(downpayment, "f") if downpayment is not None else "100.00",
            },
        )

    result = _quote_result(quote)
    result["discount_approval_required"] = status_value == SalesQuote.Status.PENDING_APPROVAL
    return _complete(operation, result)


@transaction.atomic
def approve_quote_discount(*, actor, quote, idempotency_key, reason):
    roles = _require_demo_actor(actor, quote.business_unit)
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason:
        raise InvalidTransaction("An approval reason is required.")
    quote = SalesQuote.objects.select_for_update().get(pk=quote.pk)
    payload = {"quote_id": quote.pk, "discount_percent": quote.discount_percent, "reason": reason}
    operation, created = _begin_idempotent_operation(idempotency_key, "discount_approval", payload)
    if not created:
        return operation.result
    if quote.status != SalesQuote.Status.PENDING_APPROVAL:
        raise InvalidTransaction("Only quotes awaiting a price decision can be approved.")
    ceiling = _discount_cap(roles)
    if quote.discount_percent > ceiling:
        raise WorkflowPermissionDenied(
            f"This user's highest active role allows up to {format(ceiling, 'f')}% discount."
        )
    role = _price_approver_role(roles, quote.discount_percent)
    _approve(
        quote,
        kind=QuoteApproval.Kind.DISCOUNT,
        approver=actor,
        role=role,
        reason=reason,
        details={"discount_percent": format(quote.discount_percent, "f"), "ceiling": format(ceiling, "f")},
    )
    quote.status = SalesQuote.Status.APPROVED
    quote.updated_at = timezone.now()
    quote.save(update_fields=["status", "updated_at"])
    result = _quote_result(quote)
    return _complete(operation, result)


def _expire_holds(quote, now):
    quote.holds.filter(status=QuoteHold.Status.ACTIVE, expires_at__lte=now).update(
        status=QuoteHold.Status.EXPIRED
    )


@transaction.atomic
def create_temporary_quote_hold(
    *, actor, quote, idempotency_key, working_days=DEFAULT_HOLD_WORKING_DAYS, now=None
):
    _require_demo_actor(actor, quote.business_unit)
    now = now or timezone.now()
    if type(working_days) is not int or not 1 <= working_days <= 3:
        raise InvalidTransaction("A temporary hold may cover one to three working days.")
    quote = SalesQuote.objects.select_for_update().get(pk=quote.pk)
    payload = {
        "quote_id": quote.pk,
        "working_days": working_days,
    }
    operation, created = _begin_idempotent_operation(idempotency_key, "quote_hold", payload)
    if not created:
        return operation.result
    if quote.status not in (SalesQuote.Status.APPROVED, SalesQuote.Status.HELD):
        raise InvalidTransaction("Only an approved quote can be placed on a temporary hold.")
    lines = list(quote.lines.select_related("variant", "location").order_by("pk"))
    if not lines:
        raise InvalidTransaction("The quote has no lines to hold.")
    expiry = _hold_expiry(quote.business_unit, now, working_days)
    _expire_holds(quote, now)
    if quote.holds.filter(status=QuoteHold.Status.ACTIVE).exists():
        raise InvalidTransaction("This quote already has an active hold.")

    balances = {}
    if quote.kind == SalesQuote.Kind.STOCK:
        for line in lines:
            try:
                balance = StockBalance.objects.get(
                    business_unit=quote.business_unit,
                    variant=line.variant,
                    location=line.location,
                )
            except StockBalance.DoesNotExist:
                raise InsufficientStock("A quoted in-stock item has no received stock balance.") from None
            balances[line.pk] = balance.pk
        locked = {
            balance.pk: balance
            for balance in StockBalance.objects.select_for_update()
            .filter(pk__in=sorted(set(balances.values())))
            .order_by("pk")
        }
        active_holds = {}
        for balance_id in locked:
            active_holds[balance_id] = sum(
                QuoteHold.objects.filter(
                    balance_id=balance_id,
                    status=QuoteHold.Status.ACTIVE,
                    expires_at__gt=now,
                ).values_list("quantity", flat=True),
                Decimal("0.00"),
            )
        for line in lines:
            balance = locked[balances[line.pk]]
            if balance.available_quantity - active_holds[balance.pk] < line.quantity:
                raise InsufficientStock("The requested quantity exceeds stock not already held or reserved.")

    created_holds = []
    for line in lines:
        created_holds.append(
            QuoteHold.objects.create(
                idempotency=operation,
                quote=quote,
                quote_line=line,
                balance_id=balances.get(line.pk),
                quantity=line.quantity,
                working_days=working_days,
                expires_at=expiry,
                created_by=actor,
            )
        )
    quote.status = SalesQuote.Status.HELD
    quote.updated_at = now
    quote.save(update_fields=["status", "updated_at"])
    result = {
        "quote_id": quote.pk,
        "hold_ids": [hold.pk for hold in created_holds],
        "expires_at": expiry.isoformat(),
        "working_days": working_days,
        "calendar_closed_weekdays": sorted(_working_calendar(quote.business_unit)[1]),
    }
    return _complete(operation, result)


@transaction.atomic
def receive_imported_purchase(
    *,
    actor,
    idempotency_key,
    business_unit,
    supplier,
    lines,
    charges,
    purchase_commitment=None,
    currency=None,
    exchange_rate=None,
    rate_source="",
    rate_date=None,
):
    _require_demo_actor(actor, business_unit)
    if purchase_commitment is not None and currency is None:
        currency = purchase_commitment.currency
    terms = _exchange_terms(currency, exchange_rate, rate_source, rate_date)
    if purchase_commitment is not None and purchase_commitment.currency != terms["currency"]:
        raise InvalidTransaction("A committed receipt must use the purchase commitment's currency.")
    rate = terms["exchange_rate"]
    _require_demo(supplier)
    if supplier.party_type != SyntheticCounterparty.Type.SUPPLIER:
        raise InvalidTransaction("A synthetic supplier record is required.")
    require_supplier_approved(supplier)
    if not isinstance(lines, (list, tuple)) or not lines or len(lines) > 80:
        raise InvalidTransaction("A purchase receipt must contain between one and 80 lines.")
    if not isinstance(charges, (list, tuple)) or len(charges) > 40:
        raise InvalidTransaction("Landed charges must be supplied as a list.")

    prepared_lines = []
    for index, line in enumerate(lines):
        variant = line.get("variant")
        location = line.get("location")
        if variant is None or location is None:
            raise InvalidTransaction(f"Purchase line {index + 1} requires a variant and receiving location.")
        quantity = _positive_quantity(line.get("quantity"), "received quantity")
        commitment_line = line.get("commitment_line")
        if purchase_commitment is None and commitment_line is not None:
            raise InvalidTransaction("A commitment line requires a purchase commitment.")
        if purchase_commitment is not None and commitment_line is None:
            raise InvalidTransaction("Every line on a committed receipt must identify its commitment line.")
        if commitment_line is not None:
            if commitment_line.commitment_id != purchase_commitment.pk:
                raise InvalidTransaction("The receipt line does not belong to the selected purchase commitment.")
            if commitment_line.variant_id != variant.pk:
                raise InvalidTransaction("The received variant does not match the purchase commitment line.")
            supplied_unit_cost = line.get("unit_cost")
            unit_cost = commitment_line.unit_cost
            supplied_unit_cost = (
                _money(supplied_unit_cost, "supplier unit purchase value")
                if supplied_unit_cost is not None
                else None
            )
        else:
            supplied_unit_cost = None
            unit_cost = _money(line.get("unit_cost"), "supplier unit purchase value")
        quote_line = line.get("quote_line")
        # unit_cost is the supplier's price in the document currency; stock is valued in QAR.
        foreign_unit_cost = unit_cost
        foreign_purchase_value = _money(quantity * foreign_unit_cost, "supplier purchase value")
        unit_cost = _to_base(foreign_unit_cost, rate)
        purchase_value = _money(quantity * unit_cost, "supplier purchase value")
        prepared_lines.append(
            {
                "variant": variant,
                "location": location,
                "quantity": quantity,
                "unit_cost": unit_cost,
                "purchase_value": purchase_value,
                "foreign_unit_cost": foreign_unit_cost,
                "foreign_purchase_value": foreign_purchase_value,
                "quote_line": quote_line,
                "commitment_line": commitment_line,
                "supplied_unit_cost": supplied_unit_cost,
            }
        )

    prepared_charges = []
    for index, charge in enumerate(charges):
        category = charge.get("category")
        if category not in PurchaseReceiptCharge.Category.values:
            raise InvalidTransaction(f"Landed charge {index + 1} has an unsupported category.")
        amount = _money(charge.get("amount"), "landed charge amount")
        eligible = charge.get("eligible", True)
        if type(eligible) is not bool:
            raise InvalidTransaction("Landed charge eligibility must be a boolean.")
        if not eligible:
            raise WorkflowConfigurationMissing(
                "Non-capitalized landed-charge accounting is not configured; enter only eligible "
                "synthetic inventory charges."
            )
        charge_terms = _charge_exchange_terms(charge, category, terms, index)
        prepared = {"category": category, "amount": amount, "eligible": eligible}
        if charge_terms["currency"] != BASE_CURRENCY:
            # Kept out of QAR charges so earlier QAR idempotency keys still replay.
            prepared["foreign_amount"] = amount
            prepared["amount"] = _to_base(amount, charge_terms["exchange_rate"])
            prepared["exchange"] = _exchange_payload(charge_terms)
            prepared["terms"] = charge_terms
        prepared_charges.append(prepared)

    purchase_total = sum((row["purchase_value"] for row in prepared_lines), Decimal("0.00"))
    if purchase_total <= 0:
        raise InvalidTransaction("Imported stock must have a positive total supplier purchase value.")
    eligible_total = sum(
        (charge["amount"] for charge in prepared_charges if charge["eligible"]),
        Decimal("0.00"),
    )
    if purchase_commitment is not None:
        _require_demo(purchase_commitment, purchase_commitment.business_unit)
        if (
            purchase_commitment.business_unit_id != business_unit.pk
            or purchase_commitment.supplier_id != supplier.pk
        ):
            raise InvalidTransaction("The purchase commitment must match the receipt business unit and supplier.")

    payload = {
        "business_unit": business_unit.pk,
        "supplier": supplier.pk,
        "lines": [
            {
                "variant": row["variant"].pk,
                "location": row["location"].pk,
                "quantity": row["quantity"],
                "unit_cost": row["foreign_unit_cost"],
                "quote_line": row["quote_line"].pk if row["quote_line"] else None,
            }
            for row in prepared_lines
        ],
        "charges": [
            {key: value for key, value in charge.items() if key != "terms"}
            for charge in prepared_charges
        ],
    }
    if terms["currency"] != BASE_CURRENCY:
        # Kept out of QAR payloads so earlier QAR idempotency keys still replay.
        payload["exchange"] = _exchange_payload(terms)
    if purchase_commitment is not None:
        payload["purchase_commitment"] = purchase_commitment.pk
        for payload_line, prepared_line in zip(payload["lines"], prepared_lines):
            payload_line["commitment_line"] = prepared_line["commitment_line"].pk
            payload_line["unit_cost_assertion"] = (
                format(prepared_line["supplied_unit_cost"], "f")
                if prepared_line["supplied_unit_cost"] is not None
                else None
            )
    operation, created = _begin_idempotent_operation(idempotency_key, "purchase_receipt", payload)
    if not created:
        return operation.result

    if purchase_commitment is not None:
        if any(
            row["supplied_unit_cost"] is not None
            and row["supplied_unit_cost"] != row["foreign_unit_cost"]
            for row in prepared_lines
        ):
            raise InvalidTransaction("A committed receipt must use its purchase commitment unit value.")
        locked_commitment = PurchaseCommitment.objects.select_for_update().get(
            pk=purchase_commitment.pk,
            business_unit=business_unit,
            supplier=supplier,
            is_synthetic=True,
        )
        commitment_line_ids = sorted({row["commitment_line"].pk for row in prepared_lines})
        locked_commitment_lines = {
            line.pk: line
            for line in PurchaseCommitmentLine.objects.select_for_update()
            .filter(pk__in=commitment_line_ids, commitment=locked_commitment, is_synthetic=True)
            .order_by("pk")
        }
        if len(locked_commitment_lines) != len(commitment_line_ids):
            raise InvalidTransaction("A purchase commitment line is unavailable for this receipt.")
        already_received = {
            row["commitment_line_id"]: row["received_quantity"]
            for row in PurchaseReceiptLine.objects.filter(
                commitment_line_id__in=commitment_line_ids
            )
            .values("commitment_line_id")
            .annotate(received_quantity=Sum("quantity"))
        }
        incoming = {}
        for row in prepared_lines:
            locked_line = locked_commitment_lines[row["commitment_line"].pk]
            row["commitment_line"] = locked_line
            total = incoming.get(locked_line.pk, Decimal("0.00")) + row["quantity"]
            if already_received.get(locked_line.pk, Decimal("0.00")) + total > locked_line.ordered_quantity:
                raise InvalidTransaction(
                    f"Receipt quantity exceeds the remaining amount on commitment line {locked_line.pk}."
                )
            incoming[locked_line.pk] = total

    quote_ids = sorted(
        {
            row["quote_line"].quote_id
            for row in prepared_lines
            if row["quote_line"] is not None
        }
    )
    locked_quotes = {
        quote.pk: quote
        for quote in SalesQuote.objects.select_for_update()
        .filter(pk__in=quote_ids)
        .order_by("pk")
    }
    for row in prepared_lines:
        _validate_bucket(business_unit, row["variant"], row["location"])
        _require_stock_location(row["location"], "receipts")
        if row["quote_line"] is None:
            continue
        _require_demo(row["quote_line"].quote, row["quote_line"].quote.business_unit)
        if row["quote_line"].quote_id not in locked_quotes:
            raise InvalidTransaction("The linked special-order quote no longer exists.")
        quote_line = SalesQuoteLine.objects.select_for_update().select_related("quote").get(
            pk=row["quote_line"].pk
        )
        row["quote_line"] = quote_line
        if quote_line.quote.business_unit_id != business_unit.pk:
            raise WorkflowPermissionDenied("A receipt cannot allocate stock to another business unit's quote.")
        if quote_line.quote.kind != SalesQuote.Kind.SPECIAL_ORDER:
            raise InvalidTransaction("Only special-order quote lines can be linked to an import receipt.")
        if quote_line.variant_id != row["variant"].pk:
            raise InvalidTransaction("The received variant does not match the special-order quote line.")
        if quote_line.quote.status not in (
            SalesQuote.Status.APPROVED,
            SalesQuote.Status.HELD,
            SalesQuote.Status.CONFIRMED,
        ):
            raise InvalidTransaction("The linked special-order quote must be approved before receipt.")
        if quote_line.received_quantity + row["quantity"] > quote_line.quantity:
            raise InvalidTransaction("The receipt would exceed the special-order quote quantity.")

    stock_key = _workflow_key(idempotency_key, "stock-receipt")
    stock_result = receive_stock(
        idempotency_key=stock_key,
        business_unit=business_unit,
        lines=[
            ReceiptLine(
                variant=row["variant"],
                location=row["location"],
                quantity=row["quantity"],
                unit_cost=row["unit_cost"],
            )
            for row in prepared_lines
        ],
        eligible_landed_charges=eligible_total,
    )
    stock_operation = IdempotencyRecord.objects.get(key=stock_key)
    landed_allocations = [Decimal(value) for value in stock_result["landed_allocations"]]
    landed_rate = (eligible_total * Decimal("100.00") / purchase_total).quantize(
        Decimal("0.0001"),
        rounding=ROUND_HALF_UP,
    )
    foreign = terms["currency"] != BASE_CURRENCY
    foreign_total = sum((row["foreign_purchase_value"] for row in prepared_lines), Decimal("0.00"))
    receipt = PurchaseReceipt.objects.create(
        idempotency=operation,
        business_unit=business_unit,
        supplier=supplier,
        purchase_commitment=purchase_commitment,
        created_by=actor,
        purchase_value=purchase_total,
        eligible_landed_charges=eligible_total,
        landed_cost_rate_percent=landed_rate,
        inventory_value=purchase_total + eligible_total,
        stock_movement_ids=stock_result["movement_ids"],
        currency=terms["currency"],
        exchange_rate=terms["exchange_rate"],
        rate_source=terms["rate_source"],
        rate_date=terms["rate_date"],
        foreign_purchase_value=foreign_total if foreign else None,
    )
    for charge in prepared_charges:
        charge_terms = charge.get("terms")
        PurchaseReceiptCharge.objects.create(
            receipt=receipt,
            category=charge["category"],
            amount=charge["amount"],
            is_eligible=charge["eligible"],
            **(
                {
                    "currency": charge_terms["currency"],
                    "exchange_rate": charge_terms["exchange_rate"],
                    "rate_source": charge_terms["rate_source"],
                    "rate_date": charge_terms["rate_date"],
                    "foreign_amount": charge["foreign_amount"],
                }
                if charge_terms
                else {}
            ),
        )

    for row, allocation in zip(prepared_lines, landed_allocations):
        balance = StockBalance.objects.get(
            business_unit=business_unit,
            variant=row["variant"],
            location=row["location"],
        )
        movement_ids = list(
            StockMovement.objects.filter(
                idempotency=stock_operation,
                balance=balance,
            ).values_list("id", flat=True)
        )
        receipt_line = PurchaseReceiptLine.objects.create(
            receipt=receipt,
            commitment_line=row["commitment_line"],
            quote_line=row["quote_line"],
            variant=row["variant"],
            location=row["location"],
            quantity=row["quantity"],
            unit_cost=row["unit_cost"],
            purchase_value=row["purchase_value"],
            foreign_unit_cost=row["foreign_unit_cost"] if foreign else None,
            foreign_purchase_value=row["foreign_purchase_value"] if foreign else None,
            landed_cost_allocation=allocation,
            stock_movement_ids=movement_ids,
        )
        if row["quote_line"] is not None:
            quote_line = SalesQuoteLine.objects.select_for_update().get(pk=row["quote_line"].pk)
            quote_line.received_quantity += row["quantity"]
            quote_line.save(update_fields=["received_quantity"])
            _reserve_ready_quote_stock(
                quote_line.quote,
                idempotency_parent=_workflow_key(idempotency_key, f"special-reserve-{receipt_line.pk}"),
            )

    result = {
        "purchase_receipt_id": receipt.pk,
        "stock_journal_entry_id": stock_result["journal_entry_id"],
        "stock_movement_ids": stock_result["movement_ids"],
        "purchase_value": format(purchase_total, "f"),
        "eligible_landed_charges": format(eligible_total, "f"),
        "landed_cost_rate_percent": format(landed_rate, "f"),
        "landed_cost_allocations": [format(value, "f") for value in landed_allocations],
        "inventory_value": format(purchase_total + eligible_total, "f"),
        "currency": "QAR",
    }
    if foreign:
        result.update({
            **{f"transaction_{key}" if key == "currency" else key: value
               for key, value in _exchange_payload(terms).items()},
            "foreign_purchase_value": format(foreign_total, "f"),
        })
    foreign_charges = [
        {
            "category": charge["category"],
            "foreign_amount": format(charge["foreign_amount"], "f"),
            "amount": format(charge["amount"], "f"),
            **charge["exchange"],
        }
        for charge in prepared_charges
        if "exchange" in charge
    ]
    if foreign_charges:
        result["foreign_charges"] = foreign_charges
    if purchase_commitment is not None:
        result["purchase_commitment_id"] = purchase_commitment.pk
    return _complete(operation, result)


@transaction.atomic
def create_purchase_commitment(
    *,
    actor,
    idempotency_key,
    business_unit,
    supplier,
    lines,
    currency=None,
    exchange_rate=None,
    rate_source="",
    rate_date=None,
):
    """Create immutable synthetic commitment lines priced in one currency, without posting stock."""
    _require_demo_actor(actor, business_unit)
    terms = _exchange_terms(currency, exchange_rate, rate_source, rate_date)
    _require_demo(supplier)
    if supplier.party_type != SyntheticCounterparty.Type.SUPPLIER:
        raise InvalidTransaction("A synthetic supplier record is required.")
    require_supplier_approved(supplier)
    if not isinstance(lines, (list, tuple)) or not lines or len(lines) > 80:
        raise InvalidTransaction("A purchase commitment must contain between one and 80 lines.")

    prepared_lines = []
    for index, line in enumerate(lines):
        variant = line.get("variant")
        if variant is None:
            raise InvalidTransaction(f"Commitment line {index + 1} requires a catalog variant.")
        _require_demo(variant, variant.item, variant.item.brand)
        quantity = _positive_quantity(line.get("ordered_quantity"), "ordered quantity")
        unit_cost = _money(line.get("unit_cost"), "supplier unit purchase value")
        if unit_cost <= 0:
            raise InvalidTransaction("A commitment line must have a positive unit purchase value.")
        prepared_lines.append({
            "variant": variant,
            "ordered_quantity": quantity,
            "unit_cost": unit_cost,
        })

    payload = {
        "business_unit": business_unit.pk,
        "supplier": supplier.pk,
        "currency": terms["currency"],
        "lines": [
            {
                "variant": row["variant"].pk,
                "ordered_quantity": row["ordered_quantity"],
                "unit_cost": row["unit_cost"],
            }
            for row in prepared_lines
        ],
    }
    if terms["currency"] != BASE_CURRENCY:
        payload["exchange"] = _exchange_payload(terms)
    operation, created = _begin_idempotent_operation(
        idempotency_key, "purchase_commitment", payload
    )
    if not created:
        return operation.result

    commitment = PurchaseCommitment.objects.create(
        idempotency=operation,
        business_unit=business_unit,
        supplier=supplier,
        created_by=actor,
        currency=terms["currency"],
        exchange_rate=terms["exchange_rate"],
        rate_source=terms["rate_source"],
        rate_date=terms["rate_date"],
    )
    response_lines = []
    for row in prepared_lines:
        line = PurchaseCommitmentLine.objects.create(
            commitment=commitment,
            variant=row["variant"],
            ordered_quantity=row["ordered_quantity"],
            unit_cost=row["unit_cost"],
        )
        response_lines.append({
            "commitment_line_id": line.pk,
            "variant_id": line.variant_id,
            "ordered_quantity": format(line.ordered_quantity, "f"),
            "received_to_date": "0.00",
            "remaining_quantity": format(line.ordered_quantity, "f"),
        })
    return _complete(operation, {
        "purchase_commitment_id": commitment.pk,
        "business_unit_id": business_unit.pk,
        "supplier_id": supplier.pk,
        "lines": response_lines,
        **_exchange_payload(terms),
        "synthetic_only": True,
        "test_only_label": TEST_ONLY_LABEL,
    })


def _paid_total(quote, kinds=None):
    payments = quote.payments.all()
    if kinds is not None:
        payments = payments.filter(kind__in=kinds)
    return sum(payments.values_list("amount", flat=True), Decimal("0.00"))


def _non_returnable_paid(quote):
    return sum(
        quote.payments.values_list("non_returnable_amount", flat=True),
        Decimal("0.00"),
    )


def _balance_for_quote_line(quote, line, location=None):
    location = location or line.location
    if location is None:
        return None
    try:
        return StockBalance.objects.select_for_update().get(
            business_unit=quote.business_unit,
            variant=line.variant,
            location=location,
        )
    except StockBalance.DoesNotExist:
        raise InsufficientStock("No received stock balance exists for this quote line.") from None


def _check_unheld_availability(balance, quantity, quote, now):
    held_elsewhere = sum(
        QuoteHold.objects.filter(
            balance=balance,
            status=QuoteHold.Status.ACTIVE,
            expires_at__gt=now,
        )
        .exclude(quote=quote)
        .values_list("quantity", flat=True),
        Decimal("0.00"),
    )
    if balance.available_quantity - held_elsewhere < quantity:
        raise InsufficientStock("Available stock is held or reserved for another order.")


def _reserve_ready_quote_stock(quote, *, idempotency_parent):
    non_storage_paid = _paid_total(
        quote,
        kinds=[QuotePayment.Kind.DEPOSIT, QuotePayment.Kind.BALANCE],
    )
    if non_storage_paid < _money(
        quote.total_quote_value * DEPOSIT_RESERVATION_PERCENT / Decimal("100.00"),
        "reservation deposit",
    ):
        return []
    if quote.status in (SalesQuote.Status.PENDING_APPROVAL, SalesQuote.Status.CANCELLED):
        return []
    if quote.kind == SalesQuote.Kind.STOCK:
        lines = list(quote.lines.select_related("variant", "location").order_by("pk"))
        for line in lines:
            existing = QuoteReservation.objects.filter(quote_line=line).aggregate(
                total=Sum("quantity")
            )["total"] or Decimal("0.00")
            if existing >= line.quantity:
                continue
            if existing:
                raise InvalidTransaction("Partial stock reservations require review before continuing.")
            balance = _balance_for_quote_line(quote, line)
            _check_unheld_availability(balance, line.quantity, quote, timezone.now())
            result = reserve_stock(
                idempotency_key=_workflow_key(f"{idempotency_parent}:{line.pk}", "reservation"),
                business_unit=quote.business_unit,
                variant=line.variant,
                location=line.location,
                quantity=line.quantity,
            )
            stock_reservation = StockReservation.objects.get(pk=result["reservation_id"])
            QuoteReservation.objects.create(
                quote_line=line,
                stock_reservation=stock_reservation,
                quantity=line.quantity,
            )
            line.reserved_quantity = line.quantity
            line.save(update_fields=["reserved_quantity"])
            quote.holds.filter(
                quote_line=line,
                status=QuoteHold.Status.ACTIVE,
            ).update(status=QuoteHold.Status.CONVERTED)
    else:
        lines = list(quote.lines.select_related("variant").order_by("pk"))
        for line in lines:
            received_lines = PurchaseReceiptLine.objects.filter(quote_line=line).select_related(
                "location",
                "receipt",
            ).order_by("pk")
            for receipt_line in received_lines:
                already_reserved = QuoteReservation.objects.filter(
                    purchase_receipt_line=receipt_line
                ).aggregate(total=Sum("quantity"))["total"] or Decimal("0.00")
                amount = receipt_line.quantity - already_reserved
                if amount <= 0:
                    continue
                balance = _balance_for_quote_line(quote, line, location=receipt_line.location)
                _check_unheld_availability(balance, amount, quote, timezone.now())
                result = reserve_stock(
                    idempotency_key=_workflow_key(
                        f"{idempotency_parent}:{line.pk}:{receipt_line.pk}",
                        "special-reservation",
                    ),
                    business_unit=quote.business_unit,
                    variant=line.variant,
                    location=receipt_line.location,
                    quantity=amount,
                )
                stock_reservation = StockReservation.objects.get(pk=result["reservation_id"])
                QuoteReservation.objects.create(
                    quote_line=line,
                    stock_reservation=stock_reservation,
                    purchase_receipt_line=receipt_line,
                    quantity=amount,
                )
                line.reserved_quantity += amount
                line.save(update_fields=["reserved_quantity"])
                quote.holds.filter(
                    quote_line=line,
                    status=QuoteHold.Status.ACTIVE,
                ).update(status=QuoteHold.Status.CONVERTED)

    quote.refresh_from_db()
    all_reserved = all(
        line.reserved_quantity >= line.quantity
        for line in quote.lines.all()
    )
    if all_reserved and quote.status in (
        SalesQuote.Status.APPROVED,
        SalesQuote.Status.HELD,
        SalesQuote.Status.CONFIRMED,
    ):
        quote.status = SalesQuote.Status.CONFIRMED
        quote.updated_at = timezone.now()
        quote.save(update_fields=["status", "updated_at"])
    return [
        reservation.pk
        for line in quote.lines.all()
        for reservation in line.reservations.all()
    ]


@transaction.atomic
def record_simulated_payment(
    *,
    actor,
    quote,
    idempotency_key,
    amount,
    method,
    purpose="quote",
):
    _require_demo_actor(actor, quote.business_unit)
    quote = SalesQuote.objects.select_for_update().get(pk=quote.pk)
    payment_amount = _money(amount, "simulated payment amount")
    if payment_amount <= 0:
        raise InvalidTransaction("Payment amount must be greater than zero.")
    if method not in QuotePayment.Method.values:
        raise InvalidTransaction("Only a simulated payment method can be recorded.")
    if purpose not in ("quote", "storage"):
        raise InvalidTransaction("Payment purpose must be quote or storage.")
    payload = {
        "quote_id": quote.pk,
        "amount": payment_amount,
        "method": method,
        "purpose": purpose,
    }
    operation, created = _begin_idempotent_operation(idempotency_key, "quote_payment", payload)
    if not created:
        return operation.result
    if quote.status in (SalesQuote.Status.PENDING_APPROVAL, SalesQuote.Status.CANCELLED):
        raise InvalidTransaction("Payments are disabled until the quote is approved and active.")

    if purpose == "storage":
        outstanding = Decimal("0.00")
        for charge in quote.storage_charges.all():
            waiver = getattr(charge, "exception", None)
            waived = waiver.amount_waived if waiver else Decimal("0.00")
            outstanding += charge.amount - waived
        outstanding -= _paid_total(quote, kinds=[QuotePayment.Kind.STORAGE])
        if payment_amount > outstanding:
            raise InvalidTransaction("Storage payment exceeds the outstanding synthetic storage charges.")
        payment_kind = QuotePayment.Kind.STORAGE
        non_returnable = Decimal("0.00")
    else:
        already_paid = _paid_total(
            quote,
            kinds=[QuotePayment.Kind.DEPOSIT, QuotePayment.Kind.BALANCE],
        )
        if quote.status == SalesQuote.Status.DELIVERED:
            approval = (
                quote.approvals.filter(kind=QuoteApproval.Kind.POST_HANDOVER)
                .order_by("-approved_at")
                .first()
            )
            if approval is None:
                raise InvalidTransaction("No approved post-handover balance is outstanding.")
            permitted_percent = Decimal(approval.details["deferred_balance_percent"])
            permitted_balance = _money(
                quote.total_quote_value * permitted_percent / Decimal("100.00"),
                "approved deferred balance",
            )
            if quote.total_quote_value - already_paid <= 0:
                raise InvalidTransaction("This quote has no unpaid balance.")
            if payment_amount > quote.total_quote_value - already_paid:
                raise InvalidTransaction("Payment exceeds the remaining quote balance.")
            if quote.total_quote_value - already_paid > permitted_balance:
                raise InvalidTransaction("Outstanding balance exceeds the approved post-handover limit.")
        elif payment_amount > quote.total_quote_value - already_paid:
            raise InvalidTransaction("Payment exceeds the remaining quote value.")
        required_nonreturnable = _money(
            quote.total_quote_value * DEPOSIT_RESERVATION_PERCENT / Decimal("100.00"),
            "reservation deposit threshold",
        )
        non_returnable = min(
            payment_amount,
            max(required_nonreturnable - _non_returnable_paid(quote), Decimal("0.00")),
        )
        payment_kind = (
            QuotePayment.Kind.DEPOSIT if non_returnable > 0 else QuotePayment.Kind.BALANCE
        )

    payment = QuotePayment.objects.create(
        quote=quote,
        idempotency=operation,
        kind=payment_kind,
        amount=payment_amount,
        non_returnable_amount=non_returnable,
        method=method,
        simulated_reference=f"SIM-{hashlib.sha256(idempotency_key.encode()).hexdigest()[:24].upper()}",
        received_by=actor,
    )
    journal_entry_id = None
    if purpose == "quote":
        # Owner decision F4: money received before delivery is owed to the
        # customer until delivery; after delivery it settles the receivable.
        delivered = quote.status == SalesQuote.Status.DELIVERED
        credit = "TEST_RECEIVABLE" if delivered else "TEST_CUSTOMER_DEPOSITS"
        journal_entry_id = _post_journal(
            legal_entity=quote.business_unit.legal_entity,
            business_unit=quote.business_unit,
            idempotency=operation,
            kind=JournalEntry.Kind.PAYMENT,
            description=f"{TEST_ONLY_LABEL}: customer payment on quote {quote.pk}",
            lines=[
                ("TEST_CASH", payment_amount, Decimal("0.00"), "Simulated customer payment received"),
                (credit, Decimal("0.00"), payment_amount,
                 "Payment after delivery settles the receivable" if delivered
                 else "Customer deposit held until delivery"),
            ],
        ).pk
    reservation_ids = []
    if purpose == "quote":
        reservation_ids = _reserve_ready_quote_stock(
            quote,
            idempotency_parent=idempotency_key,
        )
    result = {
        "payment_id": payment.pk,
        "quote_id": quote.pk,
        "kind": payment.kind,
        "amount": format(payment.amount, "f"),
        "non_returnable_amount": format(payment.non_returnable_amount, "f"),
        "simulated_reference": payment.simulated_reference,
        "reservation_ids": reservation_ids,
        "total_paid": format(
            _paid_total(quote, kinds=[QuotePayment.Kind.DEPOSIT, QuotePayment.Kind.BALANCE]),
            "f",
        ),
        "payment_provider_connected": False,
        "journal_entry_id": journal_entry_id,
    }
    return _complete(operation, result)


@transaction.atomic
def approve_post_handover_balance(*, actor, quote, idempotency_key, deferred_balance_percent, reason):
    roles = _require_demo_actor(actor, quote.business_unit)
    role = BusinessUnitRoleAssignment.Role.CEO
    if BusinessUnitRoleAssignment.Role.CEO not in roles:
        if BusinessUnitRoleAssignment.Role.BU_GM not in roles:
            raise WorkflowPermissionDenied("Only the BU GM or CEO can approve a post-handover balance.")
        role = BusinessUnitRoleAssignment.Role.BU_GM
    percent = _percent(deferred_balance_percent, "deferred_balance_percent")
    if not POST_HANDOVER_MIN_PERCENT <= percent <= POST_HANDOVER_MAX_PERCENT:
        raise InvalidTransaction("Post-handover balance must be from 5% through 10%.")
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason:
        raise InvalidTransaction("An explicit post-handover approval reason is required.")
    quote = SalesQuote.objects.select_for_update().get(pk=quote.pk)
    payload = {
        "quote_id": quote.pk,
        "deferred_balance_percent": percent,
        "reason": reason,
    }
    operation, created = _begin_idempotent_operation(idempotency_key, "post_handover_approval", payload)
    if not created:
        return operation.result
    if quote.status not in (SalesQuote.Status.APPROVED, SalesQuote.Status.CONFIRMED, SalesQuote.Status.READY):
        raise InvalidTransaction("Post-handover terms can only be approved for an active quote.")
    if quote.approvals.filter(kind=QuoteApproval.Kind.POST_HANDOVER).exists():
        raise InvalidTransaction("A post-handover approval already exists for this quote.")
    _approve(
        quote,
        kind=QuoteApproval.Kind.POST_HANDOVER,
        approver=actor,
        role=role,
        reason=reason,
        details={
            "deferred_balance_percent": format(percent, "f"),
            "deferred_balance_amount": format(
                _money(quote.total_quote_value * percent / Decimal("100.00"), "deferred balance"),
                "f",
            ),
        },
    )
    return _complete(
        operation,
        {
            "quote_id": quote.pk,
            "approved": True,
            "approver_id": actor.pk,
            "role": role,
            "deferred_balance_percent": format(percent, "f"),
            "reason": reason,
        },
    )


@transaction.atomic
def notify_quote_ready(*, actor, quote, idempotency_key, message=""):
    _require_demo_actor(actor, quote.business_unit)
    quote = SalesQuote.objects.select_for_update().get(pk=quote.pk)
    message = message.strip() if isinstance(message, str) else ""
    payload = {"quote_id": quote.pk, "message": message}
    operation, created = _begin_idempotent_operation(idempotency_key, "quote_ready_notification", payload)
    if not created:
        return operation.result
    if quote.status != SalesQuote.Status.CONFIRMED:
        raise InvalidTransaction("A quote must have all ordered quantities reserved before readiness notification.")
    if any(line.reserved_quantity < line.quantity for line in quote.lines.all()):
        raise InvalidTransaction("Every quote line must be fully reserved before readiness notification.")
    now = timezone.now()
    quote.status = SalesQuote.Status.READY
    quote.ready_at = now
    quote.updated_at = now
    quote.save(update_fields=["status", "ready_at", "updated_at"])
    event = WorkflowEvent.objects.create(
        quote=quote,
        kind=WorkflowEvent.Kind.READINESS_NOTIFICATION,
        actor=actor,
        message=message or "TEST ONLY: synthetic readiness notification",
        details={"ready_at": now.isoformat(), "policy_label": TEST_ONLY_LABEL},
    )
    enqueue_notification_intent(event)
    result = {
        "quote_id": quote.pk,
        "status": quote.status,
        "ready_at": now.isoformat(),
        "event_id": event.pk,
        "free_storage_until": _add_calendar_month(timezone.localtime(now).date()).isoformat(),
        "policy_label": TEST_ONLY_LABEL,
    }
    return _complete(operation, result)


# Provisional invoice policy (decision pack F3, F4 and F8 defaults): revenue is
# recognised at delivery, payments received before delivery are shown as
# applied, and no tax is charged until Finance and legal confirm VAT rules.
INVOICE_TAX_RATE_PERCENT = Decimal("0.00")
INVOICE_POLICY_LABEL = (
    "TEST ONLY provisional invoice: issued at delivery, payments before delivery applied, "
    "no tax charged (decision pack F3, F4, F8)."
)


def _next_invoice_number(business_unit, issued_at):
    year = timezone.localtime(issued_at).year
    sequence, _ = CustomerInvoiceSequence.objects.get_or_create(business_unit=business_unit, year=year)
    sequence = CustomerInvoiceSequence.objects.select_for_update().get(pk=sequence.pk)
    sequence.last_number += 1
    sequence.save(update_fields=["last_number"])
    return f"{business_unit.code}-INV-{year}-{sequence.last_number:05d}"


def _issue_delivery_invoice(*, actor, quote, operation, paid, journal_ids_by_line):
    issued_at = quote.delivered_at
    net_total = quote.total_quote_value
    tax_total = _money(net_total * INVOICE_TAX_RATE_PERCENT / Decimal("100.00"), "invoice tax")
    invoice = CustomerInvoice.objects.create(
        business_unit=quote.business_unit,
        quote=quote,
        delivery_operation=operation,
        customer=quote.customer,
        number=_next_invoice_number(quote.business_unit, issued_at),
        issued_at=issued_at,
        issued_by=actor,
        currency=quote.currency,
        list_total=quote.total_list_value,
        discount_total=quote.total_list_value - net_total,
        net_total=net_total,
        tax_rate_percent=INVOICE_TAX_RATE_PERCENT,
        tax_total=tax_total,
        invoice_total=net_total + tax_total,
        paid_before_delivery=paid,
        balance_due=net_total + tax_total - paid,
        policy_label=INVOICE_POLICY_LABEL,
    )
    for line in quote.lines.select_related("variant__item__brand").order_by("pk"):
        item = line.variant.item
        invoice_line = CustomerInvoiceLine.objects.create(
            invoice=invoice,
            quote_line=line,
            item_code=item.item_code,
            description=item.description,
            brand_name=item.brand.name,
            quantity=line.quantity,
            list_unit_price=line.selling_price_snapshot,
            discount_percent=line.discount_percent,
            net_unit_price=line.net_unit_price,
            line_total=line.net_total,
        )
        invoice_line.journal_entries.set(
            JournalEntry.objects.filter(pk__in=journal_ids_by_line.get(line.pk, []))
        )
    return invoice


@transaction.atomic
def deliver_sales_quote(*, actor, quote, idempotency_key):
    _require_demo_actor(actor, quote.business_unit)
    quote = SalesQuote.objects.select_for_update().get(pk=quote.pk)
    payload = {"quote_id": quote.pk}
    operation, created = _begin_idempotent_operation(idempotency_key, "quote_delivery", payload)
    if not created:
        return operation.result
    if quote.status != SalesQuote.Status.READY:
        raise InvalidTransaction("Only a ready quote can be delivered.")
    paid = _paid_total(
        quote,
        kinds=[QuotePayment.Kind.DEPOSIT, QuotePayment.Kind.BALANCE],
    )
    post_approval = (
        quote.approvals.filter(kind=QuoteApproval.Kind.POST_HANDOVER)
        .order_by("-approved_at")
        .first()
    )
    if post_approval:
        deferred_percent = Decimal(post_approval.details["deferred_balance_percent"])
        minimum_paid = quote.total_quote_value - _money(
            quote.total_quote_value * deferred_percent / Decimal("100.00"),
            "approved post-handover amount",
        )
        if paid < minimum_paid:
            raise InvalidTransaction("Payment is below the explicitly approved pre-delivery threshold.")
        if paid < quote.total_quote_value:
            remaining_percent = (
                (quote.total_quote_value - paid) * Decimal("100.00") / quote.total_quote_value
            )
            if remaining_percent > POST_HANDOVER_MAX_PERCENT:
                raise InvalidTransaction("The remaining balance exceeds the 10% post-handover ceiling.")
    elif paid < quote.total_quote_value:
        raise InvalidTransaction("Full payment is required before delivery without a post-handover approval.")

    reservations = list(
        QuoteReservation.objects.select_related(
            "stock_reservation",
            "stock_reservation__balance__location",
            "quote_line",
            "quote_line__variant",
            "quote_line__variant__item",
            "quote_line__variant__item__brand",
            "quote_line__location",
        )
        .filter(quote_line__quote=quote, stock_reservation__status=StockReservation.Status.ACTIVE)
        .order_by("pk")
    )
    line_totals = {
        line.pk: line.quantity
        for line in quote.lines.all()
    }
    for line_id, quantity in line_totals.items():
        covered = sum(
            (reservation.quantity for reservation in reservations if reservation.quote_line_id == line_id),
            Decimal("0.00"),
        )
        if covered < quantity:
            raise InsufficientStock("Delivery is blocked until every quoted unit is reserved.")
    if not reservations:
        raise InsufficientStock("Delivery requires active stock reservations.")

    delivered = []
    journal_ids_by_line = {}
    for reservation in reservations:
        line = reservation.quote_line
        result = deliver_reserved_stock(
            idempotency_key=_workflow_key(
                f"{idempotency_key}:{reservation.pk}",
                "quote-delivery-line",
            ),
            reservation_id=reservation.stock_reservation_id,
            unit_selling_price=line.net_unit_price,
        )
        item = line.variant.item
        SalesReportLine.objects.create(
            delivery_operation=operation,
            quote_line=line,
            quote_reservation=reservation,
            business_unit=quote.business_unit,
            location=reservation.stock_reservation.balance.location,
            item_code=item.item_code,
            description=item.description,
            brand_name=item.brand.name,
            category_label=item.category_label,
            quantity=reservation.quantity,
            selling_price=line.net_unit_price,
            revenue=Decimal(result["revenue"]),
            cost_of_goods_sold=Decimal(result["cost_of_goods_sold"]),
            created_at=timezone.now(),
        )
        line.delivered_quantity += reservation.quantity
        line.save(update_fields=["delivered_quantity"])
        journal_ids_by_line.setdefault(line.pk, []).append(result["journal_entry_id"])
        delivered.append(result)

    quote.status = SalesQuote.Status.DELIVERED
    quote.delivered_at = timezone.now()
    quote.updated_at = quote.delivered_at
    quote.save(update_fields=["status", "delivered_at", "updated_at"])
    invoice = _issue_delivery_invoice(
        actor=actor, quote=quote, operation=operation, paid=paid,
        journal_ids_by_line=journal_ids_by_line,
    )
    if paid > 0:
        # The deposits held as a liability are applied against the receivable
        # raised by the delivery postings.
        application = _begin_idempotent_operation(
            _workflow_key(idempotency_key, "deposit-application"),
            "deposit_application",
            {"quote_id": quote.pk, "amount": paid},
        )[0]
        entry = _post_journal(
            legal_entity=quote.business_unit.legal_entity,
            business_unit=quote.business_unit,
            idempotency=application,
            kind=JournalEntry.Kind.PAYMENT,
            description=f"{TEST_ONLY_LABEL}: deposits applied to invoice {invoice.number}",
            lines=[
                ("TEST_CUSTOMER_DEPOSITS", paid, Decimal("0.00"), "Customer deposits applied at delivery"),
                ("TEST_RECEIVABLE", Decimal("0.00"), paid, "Receivable settled by deposits"),
            ],
        )
        _complete(application, {"journal_entry_id": entry.pk})
    result = {
        "quote_id": quote.pk,
        "status": quote.status,
        "delivered_at": quote.delivered_at.isoformat(),
        "invoice_id": invoice.pk,
        "invoice_number": invoice.number,
        "invoice_total": format(invoice.invoice_total, "f"),
        "delivery_results": delivered,
        "amount_paid": format(paid, "f"),
        "remaining_balance": format(max(quote.total_quote_value - paid, Decimal("0.00")), "f"),
        "currency": "QAR",
    }
    return _complete(operation, result)


@transaction.atomic
def accrue_storage_charges(*, actor, quote, idempotency_prefix, as_of=None):
    _require_demo_actor(actor, quote.business_unit)
    quote = SalesQuote.objects.select_for_update().get(pk=quote.pk)
    if quote.status not in (SalesQuote.Status.READY, SalesQuote.Status.DELIVERED):
        raise InvalidTransaction("Storage starts only after a readiness notification.")
    if quote.ready_at is None:
        raise InvalidTransaction("A readiness timestamp is required before storage can accrue.")
    as_of = as_of or timezone.now()
    ready_date = timezone.localtime(quote.ready_at).date()
    cursor = _add_calendar_month(ready_date)
    today = timezone.localtime(as_of).date()
    created_charges = []
    while cursor <= today:
        period_end = _add_calendar_month(cursor)
        payload = {
            "quote_id": quote.pk,
            "period_start": cursor.isoformat(),
            "period_end": period_end.isoformat(),
            "quote_value": quote.total_quote_value,
        }
        key = _workflow_key(f"{idempotency_prefix}:{quote.pk}:{cursor.isoformat()}", "storage-charge")
        operation, created = _begin_idempotent_operation(key, "storage_charge", payload)
        if not created:
            charge = StorageCharge.objects.get(idempotency=operation)
            created_charges.append(charge)
        else:
            charge_amount = _money(
                quote.total_quote_value * STORAGE_MONTHLY_PERCENT / Decimal("100.00"),
                "monthly storage charge",
            )
            charge = StorageCharge.objects.create(
                quote=quote,
                idempotency=operation,
                period_start=cursor,
                period_end=period_end,
                base_quote_value=quote.total_quote_value,
                amount=charge_amount,
            )
            event = WorkflowEvent.objects.create(
                quote=quote,
                kind=WorkflowEvent.Kind.STORAGE_CHARGE_NOTIFICATION,
                actor=actor,
                message="TEST ONLY: synthetic monthly storage charge accrued",
                details={
                    "storage_charge_id": charge.pk,
                    "period_start": cursor.isoformat(),
                    "period_end": period_end.isoformat(),
                    "amount": format(charge_amount, "f"),
                    "policy_label": TEST_ONLY_LABEL,
                },
            )
            enqueue_notification_intent(event)
            _complete(
                operation,
                {
                    "storage_charge_id": charge.pk,
                    "notification_event_id": event.pk,
                    "period_start": cursor.isoformat(),
                    "period_end": period_end.isoformat(),
                    "amount": format(charge_amount, "f"),
                },
            )
            created_charges.append(charge)
        cursor = period_end
    return {
        "quote_id": quote.pk,
        "charges": [
            {
                "charge_id": charge.pk,
                "period_start": charge.period_start.isoformat(),
                "period_end": charge.period_end.isoformat(),
                "amount": format(charge.amount, "f"),
            }
            for charge in created_charges
        ],
        "free_storage_until": _add_calendar_month(ready_date).isoformat(),
        "monthly_rate_percent": format(STORAGE_MONTHLY_PERCENT, "f"),
    }


@transaction.atomic
def approve_storage_charge_exception(*, actor, charge, idempotency_key, amount_waived, reason):
    roles = _require_demo_actor(actor, charge.quote.business_unit)
    try:
        config = WorkflowPolicyConfig.objects.get(business_unit=charge.quote.business_unit)
    except WorkflowPolicyConfig.DoesNotExist:
        raise WorkflowConfigurationMissing(
            "Storage exceptions are disabled until an authorized approver-role policy is configured."
        ) from None
    if not config.storage_exception_approver_roles:
        raise WorkflowConfigurationMissing(
            "Storage exceptions are disabled until an authorized approver-role policy is configured."
        )
    eligible_roles = [role for role in roles if role in config.storage_exception_approver_roles]
    if not eligible_roles:
        raise WorkflowPermissionDenied("No active assigned role is configured to approve storage exceptions.")
    role = _primary_role(eligible_roles)
    waiver = _money(amount_waived, "storage amount waived")
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason:
        raise InvalidTransaction("An explicit storage-exception reason is required.")
    if waiver <= 0 or waiver > charge.amount:
        raise InvalidTransaction("The waived amount must be positive and cannot exceed the charge.")
    payload = {"charge_id": charge.pk, "amount_waived": waiver, "reason": reason}
    operation, created = _begin_idempotent_operation(idempotency_key, "storage_exception", payload)
    if not created:
        return operation.result
    exception, created = StorageChargeException.objects.get_or_create(
        charge=charge,
        defaults={
            "amount_waived": waiver,
            "approver": actor,
            "approver_role": role,
            "reason": reason[:240],
        },
    )
    if not created:
        raise InvalidTransaction("This storage charge already has an approved exception.")
    event = WorkflowEvent.objects.create(
        quote=charge.quote,
        kind=WorkflowEvent.Kind.STORAGE_EXCEPTION,
        actor=actor,
        message="TEST ONLY: synthetic storage exception approved",
        details={
            "storage_charge_id": charge.pk,
            "exception_id": exception.pk,
            "amount_waived": format(waiver, "f"),
            "reason": reason,
            "role": role,
            "policy_label": TEST_ONLY_LABEL,
        },
    )
    return _complete(
        operation,
        {
            "exception_id": exception.pk,
            "charge_id": charge.pk,
            "amount_waived": format(waiver, "f"),
            "role": role,
            "event_id": event.pk,
        },
    )
