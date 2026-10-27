"""The first full proof as one repeatable synthetic scenario.

Buy abroad in EUR, pay shipping in USD and customs in QAR, receive into the
showroom, sell with a deposit, deliver with an invoice, then reconcile. Every
step goes through the normal services with fixed idempotency keys, so running
it again replays the same results instead of adding records.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import (
    ApprovedSellingPrice,
    BusinessUnit,
    BusinessUnitRoleAssignment,
    DemoUserMarker,
    Location,
    SalesQuote,
    SyntheticCounterparty,
    UserActionGrant,
    Variant,
)
from .purchasing_services import (
    approve_quote_discount,
    configure_selling_price,
    create_purchase_commitment,
    create_sales_quote,
    deliver_sales_quote,
    notify_quote_ready,
    receive_imported_purchase,
    record_simulated_payment,
)
from .scenario_reconciliation import build_scenario_reconciliation


KEY = "first-proof-v1"
UNIT_CODE = "TVL"
LOCATION_CODE = "STIV-G"
VARIANT_SKU = "TVF-002-LIN"
SELLING_PRICE = Decimal("9500.00")
EUR_RATE = "4.21500000"
USD_RATE = "3.64000000"


def _person(username, first_name, unit, admin, role):
    user, created = get_user_model().objects.get_or_create(
        username=username, defaults={"first_name": first_name, "last_name": "TEST ONLY"},
    )
    if created:
        # Scenario actors never sign in; the scenario runs through the services.
        user.set_unusable_password()
        user.save(update_fields=["password"])
    DemoUserMarker.objects.get_or_create(user=user)
    for action in (UserActionGrant.Action.READ_STOCK, UserActionGrant.Action.CREATE_ORDER):
        UserActionGrant.objects.get_or_create(user=user, business_unit=unit, action=action)
    if not BusinessUnitRoleAssignment.objects.filter(
        user=user, business_unit=unit, role=role, revoked_at__isnull=True,
    ).exists():
        BusinessUnitRoleAssignment.objects.create(user=user, business_unit=unit, role=role, assigned_by=admin)
    return user


def _step(steps, name, detail):
    steps.append({"step": name, "detail": detail})


@transaction.atomic
def run_first_proof(*, admin):
    """Run the scenario for the seeded Tivoli unit and return what each step produced."""
    unit = BusinessUnit.objects.get(code=UNIT_CODE)
    showroom = Location.objects.get(code=LOCATION_CODE)
    variant = Variant.objects.get(sku=VARIANT_SKU)
    today = timezone.localdate().isoformat()
    buyer = _person("proof_buyer", "Proof buyer", unit, admin, BusinessUnitRoleAssignment.Role.SALESPERSON)
    gm = _person("proof_gm", "Proof general manager", unit, admin, BusinessUnitRoleAssignment.Role.BU_GM)
    supplier, _ = SyntheticCounterparty.objects.get_or_create(
        party_type=SyntheticCounterparty.Type.SUPPLIER, code="TEST-SUP-PROOF-IT",
        defaults={"name": "TEST ONLY Milan furniture supplier"},
    )
    customer, _ = SyntheticCounterparty.objects.get_or_create(
        party_type=SyntheticCounterparty.Type.CUSTOMER, code="TEST-CUST-PROOF-1",
        defaults={"name": "TEST ONLY Al Waab family"},
    )
    held_customer, _ = SyntheticCounterparty.objects.get_or_create(
        party_type=SyntheticCounterparty.Type.CUSTOMER, code="TEST-CUST-PROOF-2",
        defaults={"name": "TEST ONLY Lusail apartment"},
    )
    steps = []

    if not ApprovedSellingPrice.objects.filter(
        business_unit=unit, variant=variant, is_current=True, amount=SELLING_PRICE,
    ).exists():
        configure_selling_price(actor=admin, business_unit=unit, variant=variant, amount=SELLING_PRICE)
    _step(steps, "Selling price", f"QAR {SELLING_PRICE} per {variant.sku}")

    commitment = create_purchase_commitment(
        actor=buyer, idempotency_key=f"{KEY}-commitment", business_unit=unit, supplier=supplier,
        lines=[{"variant": variant, "ordered_quantity": Decimal("6.00"), "unit_cost": Decimal("1200.00")}],
        currency="EUR", exchange_rate="4.20000000", rate_source="TEST ONLY bank rate on order", rate_date=today,
    )
    _step(steps, "Purchase order abroad", f"6 × EUR 1200.00 from {supplier.name}")

    line = commitment["lines"][0]
    receipt = receive_imported_purchase(
        actor=buyer, idempotency_key=f"{KEY}-receipt", business_unit=unit, supplier=supplier,
        purchase_commitment=_commitment(commitment),
        lines=[{"variant": variant, "location": showroom, "quantity": Decimal("6.00"),
                "commitment_line": _commitment_line(line)}],
        charges=[
            {"category": "shipping", "amount": "850.00", "eligible": True, "currency": "USD",
             "exchange_rate": USD_RATE, "rate_source": "TEST ONLY freight invoice rate", "rate_date": today},
            {"category": "clearance", "amount": "1800.00", "eligible": True},
        ],
        exchange_rate=EUR_RATE, rate_source="TEST ONLY bank rate on arrival", rate_date=today,
    )
    _step(steps, "Receipt with landed cost",
          f"supplier QAR {receipt['purchase_value']} + charges QAR {receipt['eligible_landed_charges']}"
          f" = stock QAR {receipt['inventory_value']}")

    sale = _quote(buyer, gm, unit, customer, variant, showroom, "sale", "2.00")
    record_simulated_payment(actor=buyer, quote=sale, idempotency_key=f"{KEY}-sale-deposit",
                             amount=_part(sale, "0.30"), method="simulated_card", purpose="quote")
    sale.refresh_from_db()
    record_simulated_payment(actor=buyer, quote=sale, idempotency_key=f"{KEY}-sale-balance",
                             amount=sale.total_quote_value - _part(sale, "0.30"),
                             method="simulated_card", purpose="quote")
    _step(steps, "Sale paid before delivery", f"quote {sale.pk}, QAR {sale.total_quote_value} in two payments")
    sale.refresh_from_db()
    notify_quote_ready(actor=buyer, quote=sale, idempotency_key=f"{KEY}-sale-ready")
    sale.refresh_from_db()
    delivery = deliver_sales_quote(actor=buyer, quote=sale, idempotency_key=f"{KEY}-sale-deliver")
    _step(steps, "Delivery and invoice", f"invoice {delivery['invoice_number']} for QAR {delivery['invoice_total']}")

    held = _quote(buyer, gm, unit, held_customer, variant, showroom, "held", "1.00")
    record_simulated_payment(actor=buyer, quote=held, idempotency_key=f"{KEY}-held-deposit",
                             amount=_part(held, "0.30"), method="simulated_card", purpose="quote")
    _step(steps, "Deposit held on an open order", f"quote {held.pk}, QAR {_part(held, '0.30')} owed to the customer")

    report = build_scenario_reconciliation(unit)
    matched = sum(1 for check in report["checks"] if check["matches"])
    _step(steps, "Reconciliation", f"{matched} of {len(report['checks'])} checks match")
    return {
        "steps": steps,
        "receipt": receipt,
        "invoice_number": delivery["invoice_number"],
        "reconciliation": report,
    }


def _commitment(result):
    from .models import PurchaseCommitment
    return PurchaseCommitment.objects.get(pk=result["purchase_commitment_id"])


def _commitment_line(line):
    from .models import PurchaseCommitmentLine
    return PurchaseCommitmentLine.objects.get(pk=line["commitment_line_id"])


def _part(quote, share):
    return (quote.total_quote_value * Decimal(share)).quantize(Decimal("0.01"))


def _quote(buyer, gm, unit, customer, variant, location, name, quantity):
    quote = SalesQuote.objects.get(pk=create_sales_quote(
        actor=buyer, idempotency_key=f"{KEY}-{name}-quote", business_unit=unit, customer=customer,
        kind=SalesQuote.Kind.STOCK,
        lines=[{"variant": variant, "location": location, "quantity": Decimal(quantity), "specifications": {}}],
        discount_percent=Decimal("5.00"), discount_reason="TEST ONLY opening-week offer",
    )["quote_id"])
    if quote.status == SalesQuote.Status.PENDING_APPROVAL:
        approve_quote_discount(actor=gm, quote=quote, idempotency_key=f"{KEY}-{name}-discount",
                               reason="TEST ONLY approved opening-week offer")
        quote.refresh_from_db()
    return quote
