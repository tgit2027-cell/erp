"""The stock item list: what every user sees about an item, and what only some may see or change.

Everyone who can read stock in a unit sees, per item and finish (variant): the
photo, item code, description, company (the supplier we buy it from), brand,
where it is and how many, and the unit selling price. Costs (average cost,
last landed cost per unit, stock value) are shown only to people with 'see
item costs' for that unit. People with 'edit item details and photos' can
change an item's details and upload its photo.

The photo is served through the API to signed-in users only, and the same
path is what quotations and invoices show.
"""

import hashlib
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .access import active_grants, context_units
from .models import (
    ApprovedSellingPrice,
    DemoUserMarker,
    Item,
    Location,
    ProductBrand,
    PurchaseReceiptLine,
    StockBalance,
    SyntheticCounterparty,
    UserActionGrant,
    Variant,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction, _require_demo

READ = UserActionGrant.Action.READ_STOCK
EDIT = UserActionGrant.Action.MANAGE_ITEMS
COSTS = UserActionGrant.Action.VIEW_COSTS
ZERO = Decimal("0.00")
MAX_PHOTO_BYTES = 5 * 1024 * 1024
PHOTO_TYPES = {
    ".png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ".jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".webp": ("image/webp", (b"RIFF",)),
}
EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
TEXT_FIELDS = {
    "description": 240, "name_en": 240, "name_ar": 240, "description_ar": 240, "category_label": 80, "category_ar": 80,
    "supplier_item_code": 60, "barcode": 40, "material": 120, "colour": 80, "hs_code": 12,
}
DECIMAL_FIELDS = {"width_cm": 1, "depth_cm": 1, "height_cm": 1, "weight_kg": 2, "volume_m3": 3}
MAX_ROWS = 300


def _granted(user, business_unit, action):
    return active_grants(user, action).filter(business_unit=business_unit).exists()


def can_edit(user, business_unit):
    return _granted(user, business_unit, EDIT)


def can_see_costs(user, business_unit):
    return _granted(user, business_unit, COSTS)


def _require_user(user, business_unit):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Stock items run only in the synthetic demo environment.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can see stock items.")
    _require_demo(business_unit, business_unit.legal_entity)
    if not (_granted(user, business_unit, READ) or can_edit(user, business_unit)):
        raise WorkflowPermissionDenied("You need permission to read stock or edit items for this unit.")


def _require_editor(user, business_unit):
    _require_user(user, business_unit)
    if not can_edit(user, business_unit):
        raise WorkflowPermissionDenied("You need the 'edit item details and photos' permission for this unit.")


def photo_url(item):
    if not item.photo or not item.photo_sha256:
        return ""
    return f"/api/items/{item.pk}/photo/{item.photo_sha256[:16]}{EXTENSIONS.get(item.photo_content_type, '.jpg')}"


def _number(value):
    return format(value, "f") if value is not None else ""


def _unit_variants(business_unit, search):
    stocked = StockBalance.objects.filter(business_unit=business_unit).values("variant_id")
    priced = ApprovedSellingPrice.objects.filter(business_unit=business_unit, is_current=True).values("variant_id")
    variants = Variant.objects.filter(Q(pk__in=stocked) | Q(pk__in=priced) | Q(item__added_in=business_unit), is_synthetic=True)
    term = (search or "").strip()
    if term:
        variants = variants.filter(
            Q(sku__icontains=term) | Q(item__item_code__icontains=term) | Q(item__description__icontains=term)
            | Q(item__name_en__icontains=term) | Q(item__brand__name__icontains=term)
            | Q(item__supplier__name__icontains=term) | Q(item__supplier_item_code__icontains=term)
            | Q(item__barcode__iexact=term)
        )
    return variants.select_related("item__brand", "item__supplier").order_by("item__item_code", "sku")


def items_overview(*, actor, business_unit, search="", include_inactive=False):
    _require_user(actor, business_unit)
    costs = can_see_costs(actor, business_unit)
    variants = _unit_variants(business_unit, search)
    if not include_inactive:
        variants = variants.filter(item__is_active=True)
    return _overview(actor, business_unit, variants, costs)


def _overview(actor, business_unit, variants, costs):
    total = variants.count()
    variants = list(variants[:MAX_ROWS])
    ids = [variant.pk for variant in variants]
    prices = dict(ApprovedSellingPrice.objects.filter(business_unit=business_unit, variant_id__in=ids, is_current=True)
                  .values_list("variant_id", "amount"))
    balances = StockBalance.objects.filter(business_unit=business_unit, variant_id__in=ids, on_hand_quantity__gt=0) \
        .exclude(location__kind=Location.Kind.VIRTUAL).select_related("location").order_by("location__code")
    places, value = {}, {}
    for balance in balances:
        places.setdefault(balance.variant_id, []).append({
            "location_id": balance.location_id, "code": balance.location.code, "name": balance.location.name,
            "quantity": format(balance.on_hand_quantity, "f"),
            "free": format(balance.on_hand_quantity - balance.reserved_quantity, "f"),
        })
        value[balance.variant_id] = value.get(balance.variant_id, ZERO) + balance.on_hand_value
    last_cost = {}
    if costs:
        receipts = PurchaseReceiptLine.objects.filter(receipt__business_unit=business_unit, variant_id__in=ids) \
            .order_by("variant_id", "-receipt__created_at", "-pk").values(
                "variant_id", "quantity", "purchase_value", "landed_cost_allocation", "receipt__created_at")
        for line in receipts:
            if line["variant_id"] not in last_cost and line["quantity"]:
                landed = line["purchase_value"] + line["landed_cost_allocation"]
                last_cost[line["variant_id"]] = {
                    "unit_cost": format((line["purchase_value"] / line["quantity"]).quantize(Decimal("0.01")), "f"),
                    "landed_unit_cost": format((landed / line["quantity"]).quantize(Decimal("0.01")), "f"),
                    "received": line["receipt__created_at"].date().isoformat(),
                }
    rows = []
    for variant in variants:
        item = variant.item
        quantity = sum((Decimal(place["quantity"]) for place in places.get(variant.pk, [])), ZERO)
        row = {
            "item_id": item.pk, "variant_id": variant.pk, "item_code": item.item_code, "sku": variant.sku,
            "description": item.name_en or item.description, "variant_description": variant.description,
            "brand": item.brand.name, "company": item.supplier.name if item.supplier_id else "",
            "photo_url": photo_url(item), "unit": item.get_unit_of_measure_display(),
            "locations": places.get(variant.pk, []), "quantity": format(quantity, "f"),
            "selling_price": format(prices[variant.pk], "f") if variant.pk in prices else "",
            "is_active": item.is_active, "barcode": item.barcode, "name_ar": item.name_ar,
        }
        if costs:
            stock_value = value.get(variant.pk, ZERO)
            row["costs"] = {
                "stock_value": format(stock_value, "f"),
                "average_cost": format((stock_value / quantity).quantize(Decimal("0.01")), "f") if quantity else "",
                **last_cost.get(variant.pk, {"unit_cost": "", "landed_unit_cost": "", "received": ""}),
                "supplier_price": _number(item.supplier_price), "supplier_price_currency": item.supplier_price_currency,
            }
        rows.append(row)
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "rows": rows, "total": total, "shown": len(rows),
        "can_edit": can_edit(actor, business_unit), "can_see_costs": costs, "currency": "QAR",
    }


def scan_item(*, actor, business_unit, code):
    """Find what a scanned or typed code is: our SKU (printed on our labels), the maker's barcode, or the item code."""
    _require_user(actor, business_unit)
    code = str(code or "").strip()
    if not code or len(code) > 60:
        raise InvalidTransaction("Scan or type a barcode, SKU or item code.")
    base = Variant.objects.filter(is_synthetic=True).select_related("item__brand", "item__supplier").order_by("item__item_code", "sku")
    matched_by = "sku"
    variants = base.filter(sku__iexact=code)
    if not variants.exists():
        matched_by, variants = "barcode", base.filter(item__barcode=code)
    if not variants.exists():
        matched_by, variants = "item_code", base.filter(item__item_code__iexact=code)
    if not variants.exists():
        raise InvalidTransaction(f"Nothing found for {code}. Check the label, or search by name.")
    return _overview(actor, business_unit, variants, can_see_costs(actor, business_unit)) | {"code": code, "matched_by": matched_by}


def _choices():
    suppliers = SyntheticCounterparty.objects.filter(party_type=SyntheticCounterparty.Type.SUPPLIER, is_synthetic=True) \
        .prefetch_related("brands").order_by("name")
    return {
        "suppliers": [{"id": each.pk, "name": each.name, "brand_ids": sorted(brand.pk for brand in each.brands.all())}
                      for each in suppliers],
        "brands": [{"id": each.pk, "name": each.name} for each in ProductBrand.objects.filter(is_synthetic=True).order_by("name")],
        "units": [{"value": value, "label": label} for value, label in Item.Unit.choices],
    }


def _item_row(item):
    return {
        "id": item.pk, "item_code": item.item_code, "description": item.description, "name_en": item.name_en,
        "name_ar": item.name_ar, "description_ar": item.description_ar, "category_label": item.category_label,
        "category_ar": item.category_ar, "brand_id": item.brand_id, "brand": item.brand.name,
        "supplier_id": item.supplier_id, "company": item.supplier.name if item.supplier_id else "",
        "supplier_item_code": item.supplier_item_code, "barcode": item.barcode, "unit_of_measure": item.unit_of_measure,
        **{field: _number(getattr(item, field)) for field in DECIMAL_FIELDS},
        "material": item.material, "colour": item.colour, "hs_code": item.hs_code,
        "lead_time_days": item.lead_time_days if item.lead_time_days is not None else "",
        "is_active": item.is_active, "photo_url": photo_url(item),
        "variants": [{"id": variant.pk, "sku": variant.sku, "description": variant.description} for variant in item.variants.all()],
        "updated_by": item.updated_by.username if item.updated_by_id else "",
        "updated_at": item.updated_at.isoformat() if item.updated_at else "",
    }


def _item(item_id):
    item = Item.objects.filter(pk=item_id, is_synthetic=True).select_related("brand", "supplier", "updated_by").first()
    if item is None:
        raise InvalidTransaction("That item was not found.")
    return item


def item_detail(*, actor, business_unit, item_id):
    _require_user(actor, business_unit)
    item = _item(item_id)
    row = _item_row(item)
    if can_see_costs(actor, business_unit):
        row["supplier_price"] = {"amount": _number(item.supplier_price), "currency": item.supplier_price_currency,
                                 "date": item.supplier_price_date.isoformat() if item.supplier_price_date else ""}
    return {"item": row, "can_edit": can_edit(actor, business_unit)} | _choices()


def _clean_decimal(value, field, places):
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        raise InvalidTransaction(f"Give {field.replace('_', ' ')} as a number.") from None
    if not number.is_finite() or number < 0:
        raise InvalidTransaction(f"Give {field.replace('_', ' ')} as a number of zero or more.")
    if number != number.quantize(Decimal(1).scaleb(-places)):
        raise InvalidTransaction(f"Give {field.replace('_', ' ')} with at most {places} decimals.")
    return number


@transaction.atomic
def update_item(*, actor, business_unit, item_id, data):
    """Change an item's details. Choosing a company and brand links that brand to that company."""
    _require_editor(actor, business_unit)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the item's details.")
    item = Item.objects.select_for_update().get(pk=_item(item_id).pk)
    for field, limit in TEXT_FIELDS.items():
        if field in data:
            value = " ".join(str(data[field] or "").split())
            if len(value) > limit:
                raise InvalidTransaction(f"Keep {field.replace('_', ' ')} under {limit} characters.")
            setattr(item, field, value)
    if not item.description:
        raise InvalidTransaction("The description cannot be empty.")
    for field, places in DECIMAL_FIELDS.items():
        if field in data:
            setattr(item, field, _clean_decimal(data[field], field, places))
    if "lead_time_days" in data:
        days = _clean_decimal(data["lead_time_days"], "lead time", 0)
        if days is not None and days > 730:
            raise InvalidTransaction("Give the lead time in days, at most 730.")
        item.lead_time_days = int(days) if days is not None else None
    if "unit_of_measure" in data:
        if data["unit_of_measure"] not in Item.Unit.values:
            raise InvalidTransaction("Choose a unit of measure.")
        item.unit_of_measure = data["unit_of_measure"]
    if "is_active" in data:
        item.is_active = bool(data["is_active"])
    if "brand_id" in data:
        brand = ProductBrand.objects.filter(pk=data["brand_id"], is_synthetic=True).first()
        if brand is None:
            raise InvalidTransaction("Choose a brand.")
        item.brand = brand
    if "supplier_id" in data:
        if data["supplier_id"] in (None, ""):
            item.supplier = None
        else:
            supplier = SyntheticCounterparty.objects.filter(
                pk=data["supplier_id"], party_type=SyntheticCounterparty.Type.SUPPLIER, is_synthetic=True).first()
            if supplier is None:
                raise InvalidTransaction("Choose a company we buy from.")
            item.supplier = supplier
    if item.barcode and Item.objects.filter(barcode=item.barcode).exclude(pk=item.pk).exists():
        raise InvalidTransaction(f"Barcode {item.barcode} is already on another item.")
    item.updated_by, item.updated_at = actor, timezone.now()
    item.save()
    if item.supplier_id:
        item.brand.suppliers.add(item.supplier)
    return _item_row(_item(item.pk))


def _check_photo(upload):
    name = Path(getattr(upload, "name", "") or "").name
    suffix = Path(name).suffix.lower()
    if suffix not in PHOTO_TYPES:
        raise InvalidTransaction("Upload a PNG, JPG or WebP photo.")
    size = getattr(upload, "size", 0) or 0
    if size <= 0:
        raise InvalidTransaction("The photo is empty.")
    if size > MAX_PHOTO_BYTES:
        raise InvalidTransaction("Photos can be at most 5 MB.")
    content_type, signatures = PHOTO_TYPES[suffix]
    upload.seek(0)
    head = upload.read(16)
    if not any(head.startswith(signature) for signature in signatures) or (suffix == ".webp" and head[8:12] != b"WEBP"):
        raise InvalidTransaction("The file's content is not the photo type its name says.")
    upload.seek(0)
    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)
    return content_type, digest.hexdigest()


@transaction.atomic
def upload_photo(*, actor, business_unit, item_id, upload):
    """Replace the item's photo; quotations and invoices show the new one from then on."""
    _require_editor(actor, business_unit)
    if upload is None:
        raise InvalidTransaction("Choose a photo to upload.")
    item = Item.objects.select_for_update().get(pk=_item(item_id).pk)
    content_type, sha256 = _check_photo(upload)
    old = item.photo.name if item.photo else ""
    item.photo.save(f"photo{EXTENSIONS[content_type]}", upload, save=False)
    item.photo_content_type, item.photo_sha256 = content_type, sha256
    item.image_path = photo_url(item)
    item.updated_by, item.updated_at = actor, timezone.now()
    item.save()
    if old and old != item.photo.name:
        transaction.on_commit(lambda: item.photo.storage.delete(old))
    return _item_row(_item(item.pk))


def photo_for_download(*, actor, item_id, name):
    """Any signed-in synthetic user with access to a unit may see item photos (they appear on quotations)."""
    if not getattr(actor, "is_authenticated", False) or not context_units(actor).exists():
        raise WorkflowPermissionDenied("Sign-in is required.")
    item = Item.objects.filter(pk=item_id, is_synthetic=True).first()
    if item is None or not item.photo or photo_url(item).rsplit("/", 1)[-1] != name:
        raise InvalidTransaction("Photo not found.")
    return item
