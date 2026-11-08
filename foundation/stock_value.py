"""What the stock is worth, per business unit, on any date (synthetic only, provisional design).

The value comes from the stock movements themselves (receipts, landed cost,
transfers, deliveries, counts, returns), added up to the end of the chosen day,
so the report can be run for today or for a past month end. Each line is shown
at the average cost the system carries. The total is set against the balance
of the stock account in the general ledger on the same date, so finance can see
straight away whether the warehouse and the books agree.

Seeing it needs 'read stock' and 'see costs' for the unit; the ledger figure
also needs 'view general ledger'.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .access import active_grants
from .models import DemoUserMarker, JournalEntry, JournalLine, Location, StockMovement, UserActionGrant, Variant
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction, _require_demo

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
A = UserActionGrant.Action
STOCK_ACCOUNT = "TEST_INVENTORY"
GROUPS = {"location": "Location", "brand": "Brand", "category": "Category"}
MAX_LINES = 5000
POLICY_LABEL = (
    "TEST ONLY provisional rules: value is the sum of stock movements up to the end of the day, at the "
    "average cost carried; in-transit and missing-stock locations are shown separately and included in the total."
)
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _granted(user, business_unit, action):
    return active_grants(user, action).filter(business_unit=business_unit).exists()


def _require_user(user, business_unit):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("The stock value report runs only in the synthetic demo environment.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can see the stock value report.")
    _require_demo(business_unit, business_unit.legal_entity)
    if not _granted(user, business_unit, A.READ_STOCK):
        raise WorkflowPermissionDenied("You need permission to read stock for this unit.")
    if not _granted(user, business_unit, A.VIEW_COSTS):
        raise WorkflowPermissionDenied("The stock value report shows costs, so it needs the 'see costs' permission for this unit.")


def _as_of(value):
    today = timezone.localdate()
    if value in (None, ""):
        return today
    try:
        day = datetime.date.fromisoformat(str(value))
    except ValueError:
        raise InvalidTransaction("Give the date as YYYY-MM-DD.") from None
    if day > today:
        raise InvalidTransaction("Choose today or an earlier date.")
    return day


def _money(value):
    return format((value or ZERO).quantize(CENT), "f")


def _end_of(day):
    """The first moment after the chosen day, in local time."""
    return timezone.make_aware(datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time.min))


def _lines(business_unit, day):
    """Quantity and value per (variant, location) from movements up to the end of the day."""
    sums = (
        StockMovement.objects.filter(balance__business_unit=business_unit, created_at__lt=_end_of(day), is_synthetic=True)
        .values("balance__variant_id", "balance__location_id")
        .annotate(quantity=Sum("quantity_delta"), value=Sum("value_delta"))
    )
    found = [row for row in sums if row["quantity"] or row["value"]]
    variants = {each.pk: each for each in Variant.objects.filter(
        pk__in={row["balance__variant_id"] for row in found}).select_related("item__brand")}
    locations = {each.pk: each for each in Location.objects.filter(pk__in={row["balance__location_id"] for row in found})}
    lines = []
    for row in found:
        variant, location = variants[row["balance__variant_id"]], locations[row["balance__location_id"]]
        quantity, value = row["quantity"] or ZERO, row["value"] or ZERO
        item = variant.item
        lines.append({
            "variant_id": variant.pk, "sku": variant.sku, "item_code": item.item_code, "description": item.description,
            "variant_description": variant.description, "brand": item.brand.name,
            "category": item.category_label or "No category",
            "location_id": location.pk, "location": location.name,
            "in_transit": location.kind == Location.Kind.VIRTUAL,
            "quantity": format(quantity.quantize(CENT), "f"), "value": _money(value),
            "average_cost": _money(value / quantity) if quantity else "",
        })
    lines.sort(key=lambda line: (-Decimal(line["value"]), line["sku"], line["location"]))
    return lines


def _groups(lines, key):
    totals = defaultdict(lambda: {"quantity": ZERO, "value": ZERO, "items": set()})
    for line in lines:
        bucket = totals[line[key]]
        bucket["quantity"] += Decimal(line["quantity"])
        bucket["value"] += Decimal(line["value"])
        bucket["items"].add(line["variant_id"])
        if key == "location":
            bucket["in_transit"] = line["in_transit"]
    grand = sum((bucket["value"] for bucket in totals.values()), ZERO)
    rows = [{
        "name": name, "quantity": format(bucket["quantity"], "f"), "value": _money(bucket["value"]),
        "item_count": len(bucket["items"]),
        "share": format((bucket["value"] / grand * 100).quantize(Decimal("0.1")), "f") if grand else "0.0",
        **({"in_transit": bucket["in_transit"]} if key == "location" else {}),
    } for name, bucket in totals.items()]
    rows.sort(key=lambda row: (-Decimal(row["value"]), row["name"]))
    return rows


def _ledger_balance(business_unit, day):
    totals = JournalLine.objects.filter(
        entry__business_unit=business_unit, entry__status=JournalEntry.Status.POSTED, entry__is_synthetic=True,
        entry__document_date__lte=day, account__code=STOCK_ACCOUNT,
    ).aggregate(debit=Coalesce(Sum("debit"), Value(ZERO)), credit=Coalesce(Sum("credit"), Value(ZERO)))
    return totals["debit"] - totals["credit"]


def _report(actor, business_unit, as_of, *, every_line=False):
    _require_user(actor, business_unit)
    day = _as_of(as_of)
    lines = _lines(business_unit, day)
    total = sum((Decimal(line["value"]) for line in lines), ZERO)
    payload = {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "as_of": day.isoformat(), "today": timezone.localdate().isoformat(),
        "total": {
            "value": _money(total), "quantity": format(sum((Decimal(line["quantity"]) for line in lines), ZERO), "f"),
            "item_count": len({line["variant_id"] for line in lines}),
            "in_transit": _money(sum((Decimal(line["value"]) for line in lines if line["in_transit"]), ZERO)),
        },
        "groups": {key: _groups(lines, key) for key in GROUPS},
        "lines": lines if every_line else lines[:MAX_LINES], "line_count": len(lines),
        "policy_label": POLICY_LABEL,
    }
    if _granted(actor, business_unit, A.VIEW_LEDGER):
        ledger = _ledger_balance(business_unit, day)
        difference = total - ledger
        payload["ledger"] = {"balance": _money(ledger), "difference": _money(difference), "agrees": difference == 0}
    else:
        payload["ledger"] = None
    return payload


def stock_value_report(*, actor, business_unit, as_of=None):
    return _report(actor, business_unit, as_of)


def export_stock_value(*, actor, business_unit, as_of=None):
    from .reporting_exports import render_table_xlsx

    data = _report(actor, business_unit, as_of, every_line=True)
    fields = ["SKU", "Item code", "Description", "Finish", "Brand", "Category", "Location", "Quantity", "Average cost", "Value"]
    rows = [{
        "SKU": line["sku"], "Item code": line["item_code"], "Description": line["description"],
        "Finish": line["variant_description"], "Brand": line["brand"], "Category": line["category"],
        "Location": line["location"] + (" (in transit)" if line["in_transit"] else ""),
        "Quantity": line["quantity"], "Average cost": line["average_cost"], "Value": line["value"],
    } for line in data["lines"]]
    summary = {"Total stock value": data["total"]["value"], "Of which in transit": data["total"]["in_transit"]}
    if data["ledger"]:
        summary["Stock account in the ledger"] = data["ledger"]["balance"]
        summary["Difference"] = data["ledger"]["difference"]
    content = render_table_xlsx(
        title=f"Stock value: {data['unit']['name']}", note=f"As of the end of {data['as_of']}, amounts in QAR",
        policy_label=data["policy_label"], fields=fields, rows=rows,
        numeric={"Quantity", "Average cost", "Value", *summary}, summary=summary,
    )
    return f"stock-value-{data['unit']['code']}-{data['as_of']}.xlsx", content
