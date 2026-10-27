"""Demo-only stock and accounting services.

All journal mappings and valuation behavior here are synthetic test assumptions,
not Tivoli-approved accounting or inventory policy. Services intentionally have
no HTTP endpoints and refuse to run outside APP_ENV=demo or on non-synthetic data.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
import hashlib
import json
from typing import Sequence

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    IdempotencyRecord,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Location,
    QuoteHold,
    StockBalance,
    StockMovement,
    StockReservation,
    StockTransfer,
    Variant,
)


CENT = Decimal("0.01")
QUANTITY_STEP = Decimal("0.01")
TEST_POLICY_LABEL = "TEST_ONLY_NOT_TIVOLI_APPROVED"
TEST_MEMO = "TEST ONLY — not Tivoli-approved policy"


class TransactionServiceError(Exception):
    """Base class for rejected demo-only transaction commands."""


class UnsafeTransactionEnvironment(TransactionServiceError):
    pass


class InvalidTransaction(TransactionServiceError):
    pass


class InsufficientStock(TransactionServiceError):
    pass


class IdempotencyConflict(TransactionServiceError):
    pass


class UnbalancedJournal(TransactionServiceError):
    pass


@dataclass(frozen=True)
class ReceiptLine:
    variant: Variant
    location: Location
    quantity: Decimal
    unit_cost: Decimal


_TEST_ACCOUNTS = {
    "TEST_INVENTORY": ("TEST ONLY — Inventory asset", LedgerAccount.AccountClass.ASSET),
    "TEST_GRNI": ("TEST ONLY — Receipt clearing", LedgerAccount.AccountClass.LIABILITY),
    "TEST_LANDED_CLEARING": (
        "TEST ONLY — Landed-charge clearing",
        LedgerAccount.AccountClass.LIABILITY,
    ),
    "TEST_RECEIVABLE": ("TEST ONLY — Receivable clearing", LedgerAccount.AccountClass.ASSET),
    "TEST_REVENUE": ("TEST ONLY — Delivery revenue", LedgerAccount.AccountClass.REVENUE),
    "TEST_COGS": ("TEST ONLY — Cost of goods sold", LedgerAccount.AccountClass.EXPENSE),
    "TEST_CASH": ("TEST ONLY — Simulated cash and bank", LedgerAccount.AccountClass.ASSET),
    "TEST_CUSTOMER_DEPOSITS": ("TEST ONLY — Customer deposits", LedgerAccount.AccountClass.LIABILITY),
}


def _require_demo(*objects):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise UnsafeTransactionEnvironment("Transaction services are restricted to APP_ENV=demo.")
    for obj in objects:
        if obj is None or not getattr(obj, "is_synthetic", False):
            raise UnsafeTransactionEnvironment("Transaction services accept synthetic records only.")


def _as_decimal(value, field):
    if isinstance(value, float):
        raise InvalidTransaction(f"{field} must be supplied as a decimal-safe value, not float.")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise InvalidTransaction(f"{field} must be a finite decimal value.") from None
    if not number.is_finite():
        raise InvalidTransaction(f"{field} must be finite.")
    return number


def _money(value, field="amount"):
    number = _as_decimal(value, field)
    if number < 0:
        raise InvalidTransaction(f"{field} cannot be negative.")
    return number.quantize(CENT, rounding=ROUND_HALF_UP)


def _quantity(value, field="quantity"):
    number = _as_decimal(value, field)
    if number <= 0:
        raise InvalidTransaction(f"{field} must be greater than zero.")
    quantized = number.quantize(QUANTITY_STEP, rounding=ROUND_HALF_UP)
    if quantized != number:
        raise InvalidTransaction(f"{field} supports at most two decimal places.")
    return quantized


def allocate_by_purchase_value(total, purchase_values: Sequence[Decimal]) -> list[Decimal]:
    """Allocate a QAR amount proportionally; remainder cents follow stable input order."""
    amount = _money(total, "landed charges")
    values = [_money(value, "purchase value") for value in purchase_values]
    if not values:
        if amount:
            raise InvalidTransaction("Cannot allocate charges without receipt lines.")
        return []
    total_purchase_value = sum(values, Decimal("0.00"))
    if total_purchase_value <= 0:
        if amount:
            raise InvalidTransaction("Positive landed charges require positive purchase value.")
        return [Decimal("0.00") for _ in values]
    exact = [amount * value / total_purchase_value for value in values]
    allocated = [value.quantize(CENT, rounding=ROUND_DOWN) for value in exact]
    remaining_cents = int((amount - sum(allocated, Decimal("0.00"))) / CENT)
    order = sorted(range(len(values)), key=lambda index: (-(exact[index] - allocated[index]), index))
    for index in order[:remaining_cents]:
        allocated[index] += CENT
    if sum(allocated, Decimal("0.00")) != amount:
        raise UnbalancedJournal("Landed-cost allocation did not reconcile to its source amount.")
    return allocated


def _canonical(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _begin_idempotent_operation(key, operation, payload):
    if not isinstance(key, str) or not key.strip() or len(key) > 128:
        raise InvalidTransaction("An idempotency key of 1 to 128 characters is required.")
    canonical = json.dumps(_canonical(payload), sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    record, created = IdempotencyRecord.objects.get_or_create(
        key=key,
        defaults={
            "operation": operation,
            "payload_hash": payload_hash,
            "status": IdempotencyRecord.Status.PROCESSING,
        },
    )
    record = IdempotencyRecord.objects.select_for_update().get(pk=record.pk)
    if not created:
        if record.operation != operation or record.payload_hash != payload_hash:
            raise IdempotencyConflict("This idempotency key was already used with a different request.")
        if record.status != IdempotencyRecord.Status.COMPLETE:
            raise TransactionServiceError("The idempotent request is not in a completed state.")
        return record, False
    return record, True


def _complete(record, result):
    record.result = result
    record.status = IdempotencyRecord.Status.COMPLETE
    record.completed_at = timezone.now()
    record.save(update_fields=["result", "status", "completed_at"])
    return result


def _validate_bucket(business_unit, variant, location):
    _require_demo(
        business_unit,
        business_unit.legal_entity,
        variant,
        variant.item,
        variant.item.brand,
        location,
    )
    if business_unit.legal_entity_id != location.legal_entity_id:
        raise InvalidTransaction("Business unit and stock location must belong to the same legal entity.")
    if not location.business_units.filter(pk=business_unit.pk).exists():
        raise InvalidTransaction("The business unit is not associated with this stock location.")


def _require_stock_location(location, purpose):
    """Receipts and sales use physical, active locations only.

    Virtual locations (TFRW in transit, VTIV, XX missing) are reached through
    their own workflows: transfers and stock counts. A retired location only
    lets stock leave by transfer.
    """
    if location.kind == location.Kind.VIRTUAL:
        raise InvalidTransaction(f"{location.code} is a virtual location and cannot be used for {purpose}.")
    if not location.is_active:
        raise InvalidTransaction(f"{location.code} is retired and cannot be used for {purpose}.")


def held_quantity(balance, now=None):
    """Quantity on active, unexpired quote holds; owner decision I3 blocks it from other uses."""
    now = now or timezone.now()
    return QuoteHold.objects.filter(
        balance=balance,
        status=QuoteHold.Status.ACTIVE,
        expires_at__gt=now,
    ).aggregate(total=Sum("quantity"))["total"] or Decimal("0.00")


def _balance_key(business_unit, variant, location):
    return (business_unit.pk, variant.pk, location.pk)


def _get_or_create_balances(business_unit, buckets):
    """Create missing projection rows in stable order, then lock all rows by PK."""
    keys = sorted({_balance_key(business_unit, variant, location) for variant, location in buckets})
    ids = {}
    for _, variant_id, location_id in keys:
        balance, _ = StockBalance.objects.get_or_create(
            legal_entity_id=business_unit.legal_entity_id,
            business_unit_id=business_unit.pk,
            variant_id=variant_id,
            location_id=location_id,
        )
        ids[(business_unit.pk, variant_id, location_id)] = balance.pk
    locked = StockBalance.objects.select_for_update().filter(pk__in=ids.values()).order_by("pk")
    by_id = {balance.pk: balance for balance in locked}
    return {key: by_id[balance_id] for key, balance_id in ids.items()}


def _account(legal_entity, code):
    name, account_class = _TEST_ACCOUNTS[code]
    account, _ = LedgerAccount.objects.get_or_create(
        legal_entity=legal_entity,
        code=code,
        defaults={
            "name": name,
            "account_class": account_class,
            "currency": "QAR",
            "is_synthetic": True,
        },
    )
    if not account.is_synthetic or account.currency != "QAR":
        raise UnsafeTransactionEnvironment("A transaction account is not a synthetic QAR account.")
    return account


def _post_journal(*, legal_entity, business_unit, idempotency, kind, description, lines):
    if business_unit.legal_entity_id != legal_entity.pk:
        raise InvalidTransaction("A journal business unit must belong to its legal entity.")
    normalized = []
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    for account_code, debit, credit, memo in lines:
        debit = _money(debit, "journal debit")
        credit = _money(credit, "journal credit")
        if (debit > 0) == (credit > 0):
            raise UnbalancedJournal("Each journal line must have exactly one positive side.")
        normalized.append((account_code, debit, credit, memo))
        total_debit += debit
        total_credit += credit
    if len(normalized) < 2 or total_debit != total_credit:
        raise UnbalancedJournal("A posted journal must have at least two lines and balance in QAR.")

    entry = JournalEntry.objects.create(
        idempotency=idempotency,
        legal_entity=legal_entity,
        business_unit=business_unit,
        kind=kind,
        status=JournalEntry.Status.DRAFT,
        currency="QAR",
        policy_label=TEST_POLICY_LABEL,
        description=description,
    )
    for account_code, debit, credit, memo in normalized:
        JournalLine.objects.create(
            entry=entry,
            account=_account(legal_entity, account_code),
            debit=debit,
            credit=credit,
            memo=f"{TEST_MEMO}: {memo}",
        )
    entry.status = JournalEntry.Status.POSTED
    entry.posted_at = timezone.now()
    entry.save(update_fields=["status", "posted_at"])
    return entry


def _value_for_quantity(balance, quantity):
    if quantity == balance.on_hand_quantity:
        return balance.on_hand_value
    if balance.on_hand_quantity <= 0:
        raise InsufficientStock("The stock balance has no on-hand quantity.")
    return _money(balance.on_hand_value * quantity / balance.on_hand_quantity, "inventory cost")


@transaction.atomic
def receive_stock(*, idempotency_key, business_unit, lines: Sequence[ReceiptLine], eligible_landed_charges):
    """Receive synthetic demo stock and allocate test-only eligible charges by purchase value."""
    _require_demo(business_unit, business_unit.legal_entity)
    if not lines:
        raise InvalidTransaction("At least one receipt line is required.")
    prepared = []
    for line in lines:
        _validate_bucket(business_unit, line.variant, line.location)
        _require_stock_location(line.location, "receipts")
        quantity = _quantity(line.quantity)
        unit_cost = _money(line.unit_cost, "unit cost")
        purchase_value = _money(quantity * unit_cost, "purchase value")
        prepared.append(
            {
                "variant": line.variant,
                "location": line.location,
                "quantity": quantity,
                "unit_cost": unit_cost,
                "purchase_value": purchase_value,
            }
        )
    charges = _money(eligible_landed_charges, "eligible landed charges")
    allocations = allocate_by_purchase_value(
        charges, [line["purchase_value"] for line in prepared]
    )
    purchase_total = sum((line["purchase_value"] for line in prepared), Decimal("0.00"))
    if purchase_total + charges <= 0:
        raise InvalidTransaction("A receipt must have positive inventory value.")
    payload = {
        "business_unit": business_unit.pk,
        "lines": [
            {
                "variant": line["variant"].pk,
                "location": line["location"].pk,
                "quantity": line["quantity"],
                "unit_cost": line["unit_cost"],
            }
            for line in prepared
        ],
        "eligible_landed_charges": charges,
    }
    _require_demo(business_unit, business_unit.legal_entity)
    operation, created = _begin_idempotent_operation(idempotency_key, "receipt", payload)
    if not created:
        return operation.result

    balances = _get_or_create_balances(
        business_unit,
        [(line["variant"], line["location"]) for line in prepared],
    )
    movement_ids = []
    deltas = {}
    for line, allocation in zip(prepared, allocations):
        key = _balance_key(business_unit, line["variant"], line["location"])
        balance = balances[key]
        movement = StockMovement.objects.create(
            balance=balance,
            idempotency=operation,
            kind=StockMovement.Kind.RECEIPT,
            quantity_delta=line["quantity"],
            value_delta=line["purchase_value"],
            memo=f"{TEST_MEMO}: synthetic receipt",
        )
        movement_ids.append(movement.pk)
        bucket = deltas.setdefault(key, [Decimal("0.00"), Decimal("0.00")])
        bucket[0] += line["quantity"]
        bucket[1] += line["purchase_value"] + allocation
        if allocation:
            movement = StockMovement.objects.create(
                balance=balance,
                idempotency=operation,
                kind=StockMovement.Kind.LANDED_COST,
                quantity_delta=Decimal("0.00"),
                value_delta=allocation,
                memo=f"{TEST_MEMO}: eligible synthetic landed charges",
            )
            movement_ids.append(movement.pk)

    for key, (quantity_delta, value_delta) in deltas.items():
        balance = balances[key]
        balance.on_hand_quantity += quantity_delta
        balance.on_hand_value += value_delta
        balance.updated_at = timezone.now()
        balance.save(update_fields=["on_hand_quantity", "on_hand_value", "updated_at"])

    journal_lines = [
        ("TEST_INVENTORY", purchase_total + charges, Decimal("0.00"), "Receipt and allocated landed cost"),
    ]
    if purchase_total:
        journal_lines.append(("TEST_GRNI", Decimal("0.00"), purchase_total, "Synthetic receipt clearing"))
    if charges:
        journal_lines.append(
            ("TEST_LANDED_CLEARING", Decimal("0.00"), charges, "Synthetic eligible landed-charge clearing")
        )
    entry = _post_journal(
        legal_entity=business_unit.legal_entity,
        business_unit=business_unit,
        idempotency=operation,
        kind=JournalEntry.Kind.RECEIPT,
        description=f"{TEST_MEMO}: receipt posting",
        lines=journal_lines,
    )
    result = {
        "journal_entry_id": entry.pk,
        "movement_ids": movement_ids,
        "balance_ids": [balances[key].pk for key in sorted(balances)],
        "quantity_received": format(sum((line["quantity"] for line in prepared), Decimal("0.00")), "f"),
        "purchase_value": format(purchase_total, "f"),
        "landed_charges": format(charges, "f"),
        "landed_allocations": [format(value, "f") for value in allocations],
        "inventory_value": format(purchase_total + charges, "f"),
    }
    return _complete(operation, result)


@transaction.atomic
def reserve_stock(*, idempotency_key, business_unit, variant, location, quantity):
    _validate_bucket(business_unit, variant, location)
    amount = _quantity(quantity)
    payload = {
        "business_unit": business_unit.pk,
        "variant": variant.pk,
        "location": location.pk,
        "quantity": amount,
    }
    operation, created = _begin_idempotent_operation(idempotency_key, "reservation", payload)
    if not created:
        return operation.result
    try:
        balance = StockBalance.objects.select_for_update().get(
            legal_entity_id=business_unit.legal_entity_id,
            business_unit=business_unit,
            variant=variant,
            location=location,
        )
    except StockBalance.DoesNotExist:
        raise InsufficientStock("No stock balance exists for this bucket.") from None
    if balance.available_quantity < amount:
        raise InsufficientStock("Requested quantity exceeds unreserved stock.")
    balance.reserved_quantity += amount
    balance.updated_at = timezone.now()
    balance.save(update_fields=["reserved_quantity", "updated_at"])
    reservation = StockReservation.objects.create(
        idempotency=operation,
        balance=balance,
        quantity=amount,
        status=StockReservation.Status.ACTIVE,
    )
    return _complete(
        operation,
        {
            "reservation_id": reservation.pk,
            "balance_id": balance.pk,
            "quantity": format(amount, "f"),
            "available_quantity": format(balance.available_quantity, "f"),
        },
    )


@transaction.atomic
def deliver_reserved_stock(*, idempotency_key, reservation_id, unit_selling_price):
    price = _money(unit_selling_price, "unit selling price")
    if price <= 0:
        raise InvalidTransaction("Unit selling price must be greater than zero.")
    payload = {"reservation_id": reservation_id, "unit_selling_price": price}
    operation, created = _begin_idempotent_operation(idempotency_key, "delivery", payload)
    if not created:
        return operation.result

    try:
        reservation = (
            StockReservation.objects.select_for_update()
            .select_related(
                "balance",
                "balance__legal_entity",
                "balance__business_unit",
                "balance__variant",
                "balance__variant__item",
                "balance__variant__item__brand",
                "balance__location",
            )
            .get(pk=reservation_id)
        )
    except StockReservation.DoesNotExist:
        raise InvalidTransaction("The requested synthetic reservation does not exist.") from None
    balance = StockBalance.objects.select_for_update().get(pk=reservation.balance_id)
    _validate_bucket(balance.business_unit, balance.variant, balance.location)
    if reservation.status != StockReservation.Status.ACTIVE:
        raise InvalidTransaction("Only active reservations can be delivered.")
    quantity = reservation.quantity
    if balance.reserved_quantity < quantity or balance.on_hand_quantity < quantity:
        raise InsufficientStock("The reserved quantity is no longer available.")

    inventory_cost = _value_for_quantity(balance, quantity)
    revenue = _money(quantity * price, "delivery revenue")
    if revenue <= 0:
        raise InvalidTransaction("Delivery revenue must be greater than zero.")

    balance.on_hand_quantity -= quantity
    balance.reserved_quantity -= quantity
    balance.on_hand_value -= inventory_cost
    balance.updated_at = timezone.now()
    balance.save(update_fields=["on_hand_quantity", "reserved_quantity", "on_hand_value", "updated_at"])

    movement = StockMovement.objects.create(
        balance=balance,
        idempotency=operation,
        kind=StockMovement.Kind.DELIVERY,
        quantity_delta=-quantity,
        value_delta=-inventory_cost,
        memo=f"{TEST_MEMO}: delivery and COGS valuation",
    )
    reservation.status = StockReservation.Status.CONSUMED
    reservation.updated_at = timezone.now()
    reservation.save(update_fields=["status", "updated_at"])

    lines = [
        ("TEST_RECEIVABLE", revenue, Decimal("0.00"), "Synthetic delivery receivable"),
        ("TEST_REVENUE", Decimal("0.00"), revenue, "Revenue recognized at synthetic delivery"),
    ]
    if inventory_cost:
        lines.extend(
            [
                ("TEST_COGS", inventory_cost, Decimal("0.00"), "Synthetic cost of goods sold"),
                ("TEST_INVENTORY", Decimal("0.00"), inventory_cost, "Synthetic inventory relief"),
            ]
        )
    entry = _post_journal(
        legal_entity=balance.legal_entity,
        business_unit=balance.business_unit,
        idempotency=operation,
        kind=JournalEntry.Kind.DELIVERY,
        description=f"{TEST_MEMO}: delivery posting",
        lines=lines,
    )
    return _complete(
        operation,
        {
            "journal_entry_id": entry.pk,
            "movement_id": movement.pk,
            "reservation_id": reservation.pk,
            "quantity_delivered": format(quantity, "f"),
            "revenue": format(revenue, "f"),
            "cost_of_goods_sold": format(inventory_cost, "f"),
            "remaining_inventory_value": format(balance.on_hand_value, "f"),
        },
    )


@transaction.atomic
def transfer_stock(
    *,
    idempotency_key,
    business_unit,
    variant,
    source_location,
    destination_location,
    quantity,
):
    _validate_bucket(business_unit, variant, source_location)
    _validate_bucket(business_unit, variant, destination_location)
    if not destination_location.is_active:
        raise InvalidTransaction(f"{destination_location.code} is retired and cannot receive stock.")
    amount = _quantity(quantity)
    if source_location.pk == destination_location.pk:
        raise InvalidTransaction("Source and destination locations must differ.")
    payload = {
        "business_unit": business_unit.pk,
        "variant": variant.pk,
        "source_location": source_location.pk,
        "destination_location": destination_location.pk,
        "quantity": amount,
    }
    operation, created = _begin_idempotent_operation(idempotency_key, "transfer", payload)
    if not created:
        return operation.result
    try:
        source_id = StockBalance.objects.get(
            legal_entity_id=business_unit.legal_entity_id,
            business_unit=business_unit,
            variant=variant,
            location=source_location,
        ).pk
    except StockBalance.DoesNotExist:
        raise InsufficientStock("No source stock balance exists for this bucket.") from None
    destination, _ = StockBalance.objects.get_or_create(
        legal_entity_id=business_unit.legal_entity_id,
        business_unit=business_unit,
        variant=variant,
        location=destination_location,
    )
    ids = sorted({source_id, destination.pk})
    locked = {
        balance.pk: balance
        for balance in StockBalance.objects.select_for_update().filter(pk__in=ids).order_by("pk")
    }
    source = locked[source_id]
    destination = locked[destination.pk]
    if source.available_quantity - held_quantity(source) < amount:
        raise InsufficientStock("Transfer quantity exceeds source stock that is not reserved or held.")
    transferred_value = _value_for_quantity(source, amount)

    transfer = StockTransfer.objects.create(
        idempotency=operation,
        source_balance=source,
        destination_balance=destination,
        quantity=amount,
        transferred_value=transferred_value,
        currency="QAR",
    )
    StockMovement.objects.create(
        balance=source,
        idempotency=operation,
        transfer=transfer,
        kind=StockMovement.Kind.TRANSFER_OUT,
        quantity_delta=-amount,
        value_delta=-transferred_value,
        memo=f"{TEST_MEMO}: internal transfer out",
    )
    StockMovement.objects.create(
        balance=destination,
        idempotency=operation,
        transfer=transfer,
        kind=StockMovement.Kind.TRANSFER_IN,
        quantity_delta=amount,
        value_delta=transferred_value,
        memo=f"{TEST_MEMO}: internal transfer in",
    )
    source.on_hand_quantity -= amount
    source.on_hand_value -= transferred_value
    source.updated_at = timezone.now()
    source.save(update_fields=["on_hand_quantity", "on_hand_value", "updated_at"])
    destination.on_hand_quantity += amount
    destination.on_hand_value += transferred_value
    destination.updated_at = timezone.now()
    destination.save(update_fields=["on_hand_quantity", "on_hand_value", "updated_at"])

    return _complete(
        operation,
        {
            "transfer_id": transfer.pk,
            "source_balance_id": source.pk,
            "destination_balance_id": destination.pk,
            "quantity": format(amount, "f"),
            "transferred_value": format(transferred_value, "f"),
        },
    )
