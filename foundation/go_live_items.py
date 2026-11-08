"""Go-live import of items: the item list from the old system, with each finish and its selling price.

Part of the go-live imports (go_live_imports.py): the same template, check, approve flow.
Provisional rules until Tivoli confirms them:

- Items keep the item code they had in the old system (Sam, 4 Nov 2026). The code says whose
  item it is: TVF- is Tivoli, RBB- is Roche Bobois and NAT- is Natuzzi. Each brand has its own
  items and its own prices. A row with the same item code as an
  earlier row adds another finish (variant) to that item; the item details come from the first row.
- The brand is matched by name; a brand that does not exist yet is created. The supplier is
  matched by its supplier code (as imported with the suppliers) and is optional.
- A finish code (SKU) left blank is made from the item code and the finish, as for catalogue items.
- A selling price (QAR) is set only for the business unit the item belongs to: the one given in
  the file, or else the one its code starts with. It is approved by the person who approves the import.
- The supplier's price is kept as the item's supplier price, with its currency.
- Opening stock is brought over separately, with the opening balances.
"""

import re
from decimal import Decimal, InvalidOperation

from django.db.models.functions import Lower
from django.utils import timezone

from .models import ApprovedSellingPrice, BusinessUnit, Item, ProductBrand, SyntheticCounterparty, Variant

# The business unit each old item code belongs to, by the start of the code.
PREFIX_UNITS = {"TVF": "TVL", "RBB": "RBB", "NAT": "NAT"}
CODE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,39}$")
SKU = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,47}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
UNITS = {"piece": "piece", "pieces": "piece", "pc": "piece", "pcs": "piece", "set": "set", "sets": "set",
         "pair": "pair", "pairs": "pair", "metre": "metre", "meter": "metre", "m": "metre",
         "square metre": "square_metre", "square meter": "square_metre", "sqm": "square_metre", "m2": "square_metre",
         "square_metre": "square_metre"}

COLUMNS = [
    ("Item code", "item_code", True, "The old system's item code: capital letters, digits and dashes. Repeat it on "
     "another row to add another finish of the same item.", "TVF-0142"),
    ("Description", "description", True, "What the item is, as it should appear on quotes.", "Three-seater sofa, oak legs"),
    ("Description in Arabic", "name_ar", False, "Optional.", "كنبة ثلاثية بأرجل من خشب البلوط"),
    ("Category", "category", False, "Optional, for example Sofas or Lighting.", "Sofas"),
    ("Brand", "brand", True, "The brand's name. A brand that is not in the ERP yet is created.", "Tivoli Home"),
    ("Supplier code", "supplier", False, "The supplier's code, as in the supplier import. Optional.", ""),
    ("Brand item code", "brand_item_code", False, "The brand's own code for the item. Optional.", "TH-SOF-3"),
    ("Barcode", "barcode", False, "Optional. Must not be on another item.", ""),
    ("Unit", "unit", False, "Piece, set, pair, metre or square metre. Blank means piece.", "Piece"),
    ("Finish", "finish", False, "Colour or finish of this row. Blank means Standard.", "Sand linen"),
    ("Finish code (SKU)", "sku", False, "Optional. Blank makes one from the item code and the finish.", ""),
    ("Material", "material", False, "Optional.", "Oak, linen"),
    ("Width cm", "width_cm", False, "Optional.", "220"),
    ("Depth cm", "depth_cm", False, "Optional.", "95"),
    ("Height cm", "height_cm", False, "Optional.", "82"),
    ("Selling price QAR", "selling_price", False, "Optional. The price on quotes, in QAR.", "8500"),
    ("Business unit", "business_unit", False, "The business unit code for the selling price. Blank means the unit the item code starts with "
     "(TVF- Tivoli, RBB- Roche Bobois, NAT- Natuzzi).", ""),
    ("Supplier price", "supplier_price", False, "Optional. What the supplier charges us.", "3100"),
    ("Supplier price currency", "supplier_currency", False, "Needed with a supplier price, for example EUR.", "EUR"),
]
ITEM_FIELDS = ("description", "name_ar", "category", "brand", "supplier", "brand_item_code", "barcode", "unit",
               "material", "width_cm", "depth_cm", "height_cm", "supplier_price", "supplier_currency")


def _limit(value, label, size, problems):
    if len(value) > size:
        problems.append(f"{label} is longer than {size} characters.")
    return value[:size]


def _number(value, label, places, problems, *, positive=False):
    if not value:
        return ""
    try:
        number = Decimal(value.replace(",", ""))
    except InvalidOperation:
        problems.append(f"{label} must be a number.")
        return ""
    if not number.is_finite() or number < 0 or (positive and number <= 0):
        problems.append(f"{label} must be {'more than zero' if positive else 'zero or more'}.")
        return ""
    if number >= Decimal(10) ** (12 - places):
        problems.append(f"{label} is too large.")
        return ""
    return format(number.quantize(Decimal(1).scaleb(-places)), "f")


def find_supplier(code):
    raw = re.sub(r"[^A-Z0-9-]", "-", code.upper()).strip("-")
    full = raw if raw.startswith("TEST-SUP-") else f"TEST-SUP-{raw}"
    return SyntheticCounterparty.objects.filter(party_type=SyntheticCounterparty.Type.SUPPLIER, code=full).first()


def _finish_part(values, row, first, seen, problems):
    """What each row has of its own: the finish, its code and its selling price."""
    row["finish"] = _limit(values.get("finish", ""), "Finish", 80, problems) or "Standard"
    sku = values.get("sku", "").upper().replace(" ", "")
    if sku:
        if not SKU.fullmatch(sku):
            problems.append("Finish code may use capital letters, digits and dashes only, up to 48 characters.")
        elif sku in seen["sku"]:
            problems.append(f"Finish code {sku} is also on row {seen['sku'][sku]}.")
        elif Variant.objects.filter(sku=sku).exists():
            problems.append(f"Finish code {sku} is already in the ERP.")
    row["sku"] = sku
    if first is not None and row["finish"].lower() in first["_finishes"]:
        problems.append(f"Item {row['item_code']} already has the finish {row['finish']} on row "
                        f"{first['_finishes'][row['finish'].lower()]}.")
    row["selling_price"] = _number(values.get("selling_price", ""), "Selling price", 2, problems, positive=True)
    unit = values.get("business_unit", "").strip()
    found = BusinessUnit.objects.filter(code__iexact=unit, is_active=True).first() if unit else None
    row["business_unit"] = found.code if found else unit
    if unit and found is None:
        problems.append(f"There is no active business unit with the code {unit}.")
    if unit and not row["selling_price"]:
        problems.append("A business unit is given without a selling price.")
    owner = PREFIX_UNITS.get(row.get("item_code", "").split("-")[0])
    if found is not None and owner and found.code != owner:
        problems.append(f"Item {row['item_code']} belongs to {owner} by its code, but the business unit given is {found.code}.")
    if not unit and row["selling_price"]:
        if owner and BusinessUnit.objects.filter(code=owner, is_active=True).exists():
            row["business_unit"] = owner
        else:
            problems.append("Give the business unit for the selling price, because the item code does not start with "
                            "TVF-, RBB- or NAT-.")


def check_row(values, seen, problems):
    """One row of the items file, cleaned, with its problems."""
    row = {}
    code = values.get("item_code", "").upper().replace(" ", "")
    first = seen["items"].get(code) if code else None
    if not code:
        problems.append("Item code is missing.")
    elif not CODE.fullmatch(code):
        problems.append("Item code may use capital letters, digits and dashes only, 2 to 40 characters.")
    elif first is None and Item.objects.filter(item_code=code).exists():
        problems.append(f"Item code {code} is already in the ERP.")
    row["item_code"] = code
    if first is not None:
        # Another finish of an item already on an earlier row: the item details come from that row.
        row.update({key: first[key] for key in ITEM_FIELDS})
        _finish_part(values, row, first, seen, problems)
        return row
    for key, label, size in (("description", "Description", 240), ("name_ar", "Description in Arabic", 240),
                             ("category", "Category", 80), ("brand", "Brand", 120), ("brand_item_code", "Brand item code", 60),
                             ("barcode", "Barcode", 40), ("material", "Material", 120)):
        row[key] = _limit(values.get(key, ""), label, size, problems)
    for key, label in (("description", "Description"), ("brand", "Brand")):
        if not row[key]:
            problems.append(f"{label} is missing.")
    unit_text = values.get("unit", "").lower().replace("_", " ").strip() or "piece"
    row["unit"] = UNITS.get(unit_text, "")
    if not row["unit"]:
        problems.append("Unit must be piece, set, pair, metre or square metre.")
    if row["barcode"]:
        if row["barcode"] in seen["barcode"]:
            problems.append(f"Barcode {row['barcode']} is also on row {seen['barcode'][row['barcode']]}.")
        elif Item.objects.filter(barcode=row["barcode"]).exists():
            problems.append(f"Barcode {row['barcode']} is already on another item.")
    row["supplier"] = values.get("supplier", "").upper().replace(" ", "")
    if row["supplier"] and find_supplier(row["supplier"]) is None:
        problems.append(f"No supplier has the code {values['supplier']}. Import the suppliers first or leave it blank.")
    for key, label in (("width_cm", "Width"), ("depth_cm", "Depth"), ("height_cm", "Height")):
        row[key] = _number(values.get(key, ""), label, 1, problems)
    row["supplier_price"] = _number(values.get("supplier_price", ""), "Supplier price", 2, problems)
    currency = values.get("supplier_currency", "").upper().strip()
    row["supplier_currency"] = currency if row["supplier_price"] else ""
    if row["supplier_price"] and not CURRENCY.fullmatch(currency):
        problems.append("Give the supplier price's currency as three letters, for example EUR or QAR.")
    _finish_part(values, row, None, seen, problems)
    return row


def remember(row, number, seen):
    if row["item_code"] and row["item_code"] not in seen["items"] and CODE.fullmatch(row["item_code"]):
        seen["items"][row["item_code"]] = {**row, "_finishes": {}}
    if row["item_code"] in seen["items"]:
        seen["items"][row["item_code"]]["_finishes"].setdefault(row["finish"].lower(), number)
    if row["item_code"] in seen["items"] and seen["items"][row["item_code"]]["barcode"] == row["barcode"] and row["barcode"]:
        seen["barcode"].setdefault(row["barcode"], number)
    if row["sku"]:
        seen["sku"].setdefault(row["sku"], number)


def _sku(item_code, finish, taken):
    letters = "STD" if finish == "Standard" else re.sub(r"[^A-Z0-9]", "", finish.upper())[:3] or "STD"
    base = f"{item_code}-{letters}"[:44]
    sku, number = base, 2
    while sku in taken or Variant.objects.filter(sku=sku).exists():
        sku, number = f"{base}{number}", number + 1
    taken.add(sku)
    return sku


def _brand_code(name, taken):
    letters = (re.sub(r"[^A-Z0-9]", "", name.upper()) or "BRAND")[:5]
    code, number = letters, 2
    while code in taken or ProductBrand.objects.filter(code=code).exists():
        code, number = f"{letters[:8 - len(str(number))]}{number}", number + 1
    taken.add(code)
    return code


def create(batch, actor):
    """Create the items, their finishes and selling prices. Returns the new item ids."""
    now = timezone.now()
    brands, codes, skus, items, created = {}, set(), set(), {}, []
    by_code = {unit.code: unit for unit in BusinessUnit.objects.filter(is_active=True)}
    for row in batch.rows:
        item = items.get(row["item_code"])
        if item is None:
            key = row["brand"].lower()
            brand = brands.get(key) or ProductBrand.objects.annotate(lower=Lower("name")).filter(lower=key).first()
            if brand is None:
                brand = ProductBrand.objects.create(name=row["brand"], code=_brand_code(row["brand"], codes))
            brands[key] = brand
            supplier = find_supplier(row["supplier"]) if row["supplier"] else None
            if supplier is not None:
                brand.suppliers.add(supplier)
            dimensions = {key: Decimal(row[key]) if row[key] else None for key in ("width_cm", "depth_cm", "height_cm")}
            item = Item.objects.create(
                brand=brand, supplier=supplier, item_code=row["item_code"], description=row["description"],
                name_en=row["description"], name_ar=row["name_ar"], description_ar=row["name_ar"], category_label=row["category"],
                supplier_item_code=row["brand_item_code"], barcode=row["barcode"], unit_of_measure=row["unit"],
                material=row["material"], colour=row["finish"] if row["finish"] != "Standard" else "", **dimensions,
                supplier_price=Decimal(row["supplier_price"]) if row["supplier_price"] else None,
                supplier_price_currency=row["supplier_currency"],
                supplier_price_date=now.date() if row["supplier_price"] else None, updated_by=actor, updated_at=now)
            items[row["item_code"]] = item
            created.append(item.pk)
        variant = Variant.objects.create(item=item, sku=row["sku"] or _sku(item.item_code, row["finish"], skus),
                                         description=row["finish"][:160])
        skus.add(variant.sku)
        if row["selling_price"]:
            ApprovedSellingPrice.objects.create(business_unit=by_code[row["business_unit"]], variant=variant,
                                                amount=Decimal(row["selling_price"]), currency="QAR", revision=1, is_current=True, approved_by=actor, approved_at=now)
    return created
