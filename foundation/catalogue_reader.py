"""Read a supplier catalogue or price list with AI and turn it into draft items (synthetic only, provisional).

Someone with 'edit item details and photos' picks the company (supplier) the
file comes from and uploads a PDF catalogue, a photo of a page, an Excel price
list or a CSV. The AI lists every product it finds: brand, the brand's item
code, description, size, material, colour, unit, price and currency, and for
PDFs and photos where on the page the product's picture is. The picture is
cut out of the page here, on the server.

Nothing becomes an item until a person has checked the drafts, ticked the ones
to keep and pressed create. Each draft gets the next free Tivoli code (TVF-…)
as a suggestion, and the review shows when a brand code or barcode is already
on an existing item. The price from the list is stored as the item's supplier
price, which only people with 'see item costs' can see; for anyone else the
price is neither shown nor saved.
"""

import base64
import hashlib
import io
import re
import uuid
from decimal import Decimal, InvalidOperation

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models.functions import Lower
from django.utils import timezone

from .document_ai import MAX_TEXT_CHARS, DocumentReaderUnavailable, ask, is_available
from .models import CatalogueImport, Item, ProductBrand, SyntheticCounterparty, Variant
from .stock_items import TEXT_FIELDS, _clean_decimal, _require_editor, _require_user, can_edit, can_see_costs, photo_url
from .transaction_services import InvalidTransaction

MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_PAGES = 40
MAX_DRAFTS = 300
RENDER_SCALE = 2.0  # about 144 dots per inch, enough for a clear product photo
MAX_PHOTO_SIDE = 1200
MAX_SEND_SIDE = 2400
PREFIX = re.compile(r"^[A-Z]{2,5}$")
CODE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,39}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
UNITS = set(Item.Unit.values)
DIMENSIONS = {"width_cm": 1, "depth_cm": 1, "height_cm": 1}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
TEXT_EXTENSIONS = {".csv", ".txt", ".tsv"}

CATALOGUE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer"},
                    "brand": {"type": "string"},
                    "brand_item_code": {"type": "string"},
                    "description": {"type": "string"},
                    "category": {"type": "string"},
                    "width_cm": {"type": "string"},
                    "depth_cm": {"type": "string"},
                    "height_cm": {"type": "string"},
                    "material": {"type": "string"},
                    "colour": {"type": "string"},
                    "unit_of_measure": {"type": "string", "enum": sorted(UNITS)},
                    "price": {"type": "string"},
                    "currency": {"type": "string"},
                    "barcode": {"type": "string"},
                    "has_photo": {"type": "boolean"},
                    "photo_box": {
                        "type": "object",
                        "properties": {
                            "left": {"type": "integer"}, "top": {"type": "integer"},
                            "right": {"type": "integer"}, "bottom": {"type": "integer"},
                        },
                        "required": ["left", "top", "right", "bottom"],
                        "additionalProperties": False,
                    },
                    "check": {"type": "string"},
                },
                "required": ["page", "brand", "brand_item_code", "description", "category", "width_cm", "depth_cm",
                             "height_cm", "material", "colour", "unit_of_measure", "price", "currency", "barcode",
                             "has_photo", "photo_box", "check"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "items"],
    "additionalProperties": False,
}


def _instructions(supplier_name, kind):
    where = (
        "The document is a supplier catalogue or price list. Pages are numbered from 1 in the order you received them."
        if kind in ("pdf", "image") else
        "The document is a supplier price list given as text (from a spreadsheet or CSV). Use page 0 for every item."
    )
    return f"""You are helping a furniture and interiors retailer in Qatar add products to its stock system.
{where} It comes from the company "{supplier_name}", which may sell one or more brands.

List every distinct product you can see, in the order they appear. If a product is offered in several sizes or finishes that each have their own code or price, list each one separately. Skip headings, page footers, terms and conditions, and anything that is not a product. List at most {MAX_DRAFTS} products.

For each product give:
- page: the page number where the product's main picture or row is.
- brand: the brand name of the product as printed (often the catalogue's own brand). "" if unclear.
- brand_item_code: the manufacturer's or brand's own article code / reference / SKU as printed, exactly. "" if none.
- description: a short, clear English product description (type, model name, key feature), at most 200 characters. Translate if the catalogue is not in English.
- category: one or two words, for example "Dining chair", "Sofa", "Coffee table", "Lamp", "Rug". "" if unclear.
- width_cm, depth_cm, height_cm: overall dimensions in centimetres as plain numbers with at most one decimal. Convert from millimetres or inches. If only a diameter is given, use it for width and depth. "" for anything not shown.
- material and colour: main material(s) and colour or finish as printed, short. "" if not shown.
- unit_of_measure: piece, set, pair, metre or square_metre; piece unless the price is clearly per set, pair, metre or square metre.
- price: the purchase or list price as a plain number with no thousands separators and no currency sign. If several prices are shown, use the net / purchase / ex-works price for this buyer rather than a retail price, and say which you picked in "check". "" if none.
- currency: the ISO code of the price (EUR, USD, GBP, AED, QAR, ...). "" if no price.
- barcode: the EAN / UPC / GTIN if printed, digits only. "" otherwise.
- has_photo: true only if the product has its own picture on that page.
- photo_box: where that picture is on the page, on a 0 to 1000 grid: left and right measured from the page's left edge (0) to its right edge (1000), top and bottom from the page's top edge (0) to its bottom edge (1000). Draw the box tightly around the product picture only, not the text. Use 0, 0, 0, 0 when has_photo is false.
- check: a short note for the person checking, for anything uncertain (unreadable code, price unclear, size guessed). "" if all clear.

Also give a one-sentence summary of the document (what it is, season or year, currency and price basis if shown).
Copy codes and figures as printed; do not invent or correct them."""


def _text(value, limit):
    return " ".join(str(value or "").split())[:limit]


def _decimal_text(value, places):
    text = str(value or "").replace(",", "").replace(" ", "").strip()
    if not text:
        return ""
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite() or number < 0:
        return None
    return format(number.quantize(Decimal(1).scaleb(-places)), "f")


def _pages(text, total):
    """'' means all pages (when there are few enough); otherwise '5' or '3-20'."""
    text = (text or "").replace(" ", "")
    if not text:
        if total > MAX_PAGES:
            raise InvalidTransaction(
                f"The catalogue has {total} pages. Choose up to {MAX_PAGES} pages at a time, for example 1-{MAX_PAGES}.")
        return list(range(total))
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", text)
    if not match:
        raise InvalidTransaction("Give the pages as one number or a range, for example 12-30.")
    first, last = int(match.group(1)), int(match.group(2) or match.group(1))
    if first < 1 or last < first or last > total:
        raise InvalidTransaction(f"Choose pages between 1 and {total}.")
    if last - first + 1 > MAX_PAGES:
        raise InvalidTransaction(f"Choose up to {MAX_PAGES} pages at a time.")
    return list(range(first - 1, last))


class _Source:
    """The uploaded file prepared for the AI, and able to cut photos out of its pages."""

    def __init__(self, name, data, pages_text):
        import pypdfium2
        from PIL import Image

        self.name = name
        lower = name.lower()
        extension = lower[lower.rfind("."):] if "." in lower else ""
        self.pdf = None
        self.image = None
        self.page_indexes = []
        self.pages_read = ""
        self._rendered = {}
        if extension == ".pdf":
            self.kind = "pdf"
            try:
                self.pdf = pypdfium2.PdfDocument(data)
            except pypdfium2.PdfiumError:
                raise InvalidTransaction("The PDF could not be opened. It may be damaged or password-protected.") from None
            total = len(self.pdf)
            if total == 0:
                raise InvalidTransaction("The PDF has no pages.")
            self.page_indexes = _pages(pages_text, total)
            if len(self.page_indexes) == total:
                send = data
            else:
                part = pypdfium2.PdfDocument.new()
                part.import_pages(self.pdf, self.page_indexes)
                buffer = io.BytesIO()
                part.save(buffer)
                send = buffer.getvalue()
                part.close()
            if len(send) > MAX_FILE_BYTES:
                raise InvalidTransaction("Those pages are too large to read at once. Choose fewer pages.")
            self.pages_read = f"{self.page_indexes[0] + 1}-{self.page_indexes[-1] + 1}"
            self.block = {"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf", "data": base64.standard_b64encode(send).decode("ascii")}}
        elif extension in IMAGE_EXTENSIONS:
            self.kind = "image"
            try:
                self.image = Image.open(io.BytesIO(data))
                self.image.load()
            except (OSError, Image.DecompressionBombError):
                raise InvalidTransaction("The photo could not be opened.") from None
            self.image = self.image.convert("RGB")
            self.page_indexes = [0]
            self.pages_read = "1"
            send = self.image.copy()
            send.thumbnail((MAX_SEND_SIDE, MAX_SEND_SIDE))
            buffer = io.BytesIO()
            send.save(buffer, "JPEG", quality=88)
            self.block = {"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(buffer.getvalue()).decode("ascii")}}
        elif extension == ".xlsx":
            self.kind = "spreadsheet"
            self.block = {"type": "text", "text": f"<document name=\"{name}\">\n{_spreadsheet_text(data)}\n</document>"}
        elif extension in TEXT_EXTENSIONS:
            self.kind = "text"
            text = None
            for encoding in ("utf-8-sig", "cp1252"):
                try:
                    text = data.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None or len(text) > MAX_TEXT_CHARS:
                raise InvalidTransaction("The file could not be read as text, or has too much text. Split it into smaller files.")
            self.block = {"type": "text", "text": f"<document name=\"{name}\">\n{text}\n</document>"}
        else:
            raise InvalidTransaction("Upload a PDF catalogue, a photo of a page (PNG, JPG or WebP), an Excel file (.xlsx) or a CSV.")

    def page_image(self, page):
        """The page (1 = first page sent) as an image, or None."""
        if self.kind == "image":
            return self.image if page == 1 else None
        if self.kind != "pdf" or not 1 <= page <= len(self.page_indexes):
            return None
        if page not in self._rendered:
            self._rendered[page] = self.pdf[self.page_indexes[page - 1]].render(scale=RENDER_SCALE).to_pil().convert("RGB")
        return self._rendered[page]

    def cut_photo(self, page, box):
        image = self.page_image(page)
        if image is None or not isinstance(box, dict):
            return None
        try:
            left, top, right, bottom = (max(0, min(1000, int(box[key]))) for key in ("left", "top", "right", "bottom"))
        except (KeyError, TypeError, ValueError):
            return None
        if right - left < 30 or bottom - top < 30:
            return None
        width, height = image.size
        pad_x, pad_y = width * 0.01, height * 0.01  # a little margin in case the box is slightly tight
        crop = image.crop((
            int(max(0, left * width / 1000 - pad_x)), int(max(0, top * height / 1000 - pad_y)),
            int(min(width, right * width / 1000 + pad_x)), int(min(height, bottom * height / 1000 + pad_y)),
        ))
        if crop.size[0] < 40 or crop.size[1] < 40:
            return None
        crop.thumbnail((MAX_PHOTO_SIDE, MAX_PHOTO_SIDE))
        buffer = io.BytesIO()
        crop.save(buffer, "JPEG", quality=85)
        data = buffer.getvalue()
        name = default_storage.save(f"catalogue-drafts/{uuid.uuid4().hex}.jpg", ContentFile(data))
        return {"name": name, "sha256": hashlib.sha256(data).hexdigest()}

    def close(self):
        if self.pdf is not None:
            self.pdf.close()


def _spreadsheet_text(data):
    from openpyxl import load_workbook

    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:  # openpyxl raises several kinds for a damaged or non-Excel file
        raise InvalidTransaction("The Excel file could not be opened. Save it as .xlsx or CSV and try again.") from None
    parts, size = [], 0
    try:
        for sheet in book.worksheets:
            parts.append(f"## Sheet: {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if value is None else " ".join(str(value).split()) for value in row]
                while cells and cells[-1] == "":
                    cells.pop()
                if not cells:
                    continue
                line = " | ".join(cells)
                size += len(line) + 1
                if size > MAX_TEXT_CHARS:
                    raise InvalidTransaction("The price list is too long to read at once. Split it into smaller files.")
                parts.append(line)
    finally:
        book.close()
    if len(parts) <= len(book.worksheets):
        raise InvalidTransaction("The Excel file is empty.")
    return "\n".join(parts)


def _next_codes(prefix, count, taken=()):
    """The next free codes PREFIX-001, PREFIX-002, ... after the highest one in use."""
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    used = [code for code in Item.objects.filter(item_code__startswith=f"{prefix}-").values_list("item_code", flat=True)]
    used += list(taken)
    numbers = [int(match.group(1)) for code in used if (match := pattern.match(code))]
    width = max([3] + [len(match.group(1)) for code in used if (match := pattern.match(code))])
    start = max(numbers, default=0) + 1
    return [f"{prefix}-{number:0{width}d}" for number in range(start, start + count)]


def _supplier(supplier_id):
    supplier = SyntheticCounterparty.objects.filter(
        pk=supplier_id, party_type=SyntheticCounterparty.Type.SUPPLIER, is_synthetic=True).first() if supplier_id else None
    if supplier is None:
        raise InvalidTransaction("Choose the company this catalogue comes from.")
    return supplier


def _clean_draft(index, raw, source):
    check = [_text(raw.get("check"), 200)] if _text(raw.get("check"), 200) else []
    draft = {
        "index": index, "page": raw.get("page") if isinstance(raw.get("page"), int) else 0,
        "brand": _text(raw.get("brand"), 120),
        "brand_item_code": _text(raw.get("brand_item_code"), TEXT_FIELDS["supplier_item_code"]),
        "description": _text(raw.get("description"), TEXT_FIELDS["description"]),
        "category": _text(raw.get("category"), TEXT_FIELDS["category_label"]),
        "material": _text(raw.get("material"), TEXT_FIELDS["material"]),
        "colour": _text(raw.get("colour"), TEXT_FIELDS["colour"]),
        "unit_of_measure": raw.get("unit_of_measure") if raw.get("unit_of_measure") in UNITS else Item.Unit.PIECE,
        "barcode": re.sub(r"\D", "", str(raw.get("barcode") or ""))[:TEXT_FIELDS["barcode"]],
        "currency": str(raw.get("currency") or "").strip().upper()[:3],
    }
    for field, places in DIMENSIONS.items():
        value = _decimal_text(raw.get(field), places)
        if value is None:
            check.append(f"The {field.split('_')[0]} could not be read.")
        draft[field] = value or ""
    price = _decimal_text(raw.get("price"), 2)
    if price is None:
        check.append("The price could not be read.")
    draft["cost"] = price or ""
    if draft["cost"] and not CURRENCY.match(draft["currency"]):
        check.append("The currency is not clear.")
        draft["currency"] = ""
    if not draft["description"]:
        check.append("No description was found.")
    draft["photo"] = source.cut_photo(draft["page"], raw.get("photo_box")) if raw.get("has_photo") else None
    draft["check"] = check
    return draft


def read_catalogue(*, actor, business_unit, supplier_id, upload, pages="", prefix="TVF"):
    """Read the file with AI and save the drafts for review. Nothing becomes an item here."""
    _require_editor(actor, business_unit)
    supplier = _supplier(supplier_id)
    prefix = (prefix or "").strip().upper()
    if not PREFIX.match(prefix):
        raise InvalidTransaction("The code prefix is two to five letters, for example TVF.")
    if upload is None:
        raise InvalidTransaction("Choose a catalogue or price list to upload.")
    if (getattr(upload, "size", 0) or 0) > MAX_FILE_BYTES:
        raise InvalidTransaction("The file is larger than 30 MB. Upload part of it, or a smaller file.")
    if not is_available():
        raise DocumentReaderUnavailable("Reading catalogues with AI is not switched on here. Add items by hand for now.")
    data = upload.read()
    if not data:
        raise InvalidTransaction("The file is empty.")
    name = _text(getattr(upload, "name", "") or "catalogue", 200)
    source = _Source(name, data, pages)
    saved = []
    try:
        result = ask([source.block], instructions=_instructions(supplier.name, source.kind),
                     schema=CATALOGUE_SCHEMA, max_tokens=64000)
        raw_items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(raw_items, list) or not raw_items:
            raise InvalidTransaction("The AI reader did not find any products. Check the file or the pages chosen.")
        drafts = []
        for index, raw in enumerate(raw_items[:MAX_DRAFTS]):
            if isinstance(raw, dict):
                draft = _clean_draft(len(drafts), raw, source)
                if draft["photo"]:
                    saved.append(draft["photo"]["name"])
                drafts.append(draft)
        for draft, code in zip(drafts, _next_codes(prefix, len(drafts))):
            draft["item_code"] = code
        summary = _text(result.get("summary"), 400)
        if len(raw_items) > MAX_DRAFTS:
            summary = f"{summary} Only the first {MAX_DRAFTS} products were kept; read the rest in a second upload.".strip()
        record = CatalogueImport.objects.create(
            business_unit=business_unit, supplier=supplier, file_name=name, pages_read=source.pages_read,
            code_prefix=prefix, drafts=drafts, summary=summary[:500], uploaded_by=actor,
        )
    except Exception:
        for stored in saved:
            default_storage.delete(stored)
        raise
    finally:
        source.close()
    return import_detail(actor=actor, business_unit=business_unit, import_id=record.pk)


def _draft_photo_url(record, draft):
    if not draft.get("photo"):
        return ""
    return f"/api/inventory/catalogue-imports/{record.pk}/photo/{draft['index']}/{draft['photo']['sha256'][:16]}.jpg"


def _duplicates(drafts):
    """What each draft shares with an existing item or another draft, as notes."""
    codes = {draft["brand_item_code"].lower() for draft in drafts if draft["brand_item_code"]}
    barcodes = {draft["barcode"] for draft in drafts if draft["barcode"]}
    by_code, by_barcode = {}, {}
    for item in Item.objects.annotate(code=Lower("supplier_item_code")).filter(code__in=codes).select_related("brand"):
        by_code.setdefault(item.code, []).append(item)
    for item in Item.objects.filter(barcode__in=barcodes):
        by_barcode.setdefault(item.barcode, []).append(item)
    taken = set(Item.objects.filter(item_code__in=[draft.get("item_code", "") for draft in drafts])
                .values_list("item_code", flat=True))
    notes = {}
    seen = {}
    for draft in drafts:
        found = []
        code = draft["brand_item_code"].lower()
        for item in by_code.get(code, []):
            found.append(f"Brand code {draft['brand_item_code']} is already item {item.item_code} ({item.brand.name}).")
        for item in by_barcode.get(draft["barcode"], []):
            found.append(f"Barcode {draft['barcode']} is already on item {item.item_code}.")
        if code and code in seen:
            found.append(f"Same brand code as draft {seen[code] + 1}.")
        if code:
            seen.setdefault(code, draft["index"])
        if draft.get("item_code") in taken:
            found.append(f"Code {draft['item_code']} has been taken since; another will be suggested when you create.")
        notes[draft["index"]] = found
    return notes


def _brand_match(drafts):
    brands = {brand.name.lower(): brand for brand in ProductBrand.objects.filter(is_synthetic=True)}
    return {draft["index"]: brands.get(draft["brand"].lower()) for draft in drafts if draft["brand"]}


def _import_row(record):
    created = record.created_items.count() if record.status == CatalogueImport.Status.DONE else 0
    return {
        "id": record.pk, "supplier_id": record.supplier_id, "supplier": record.supplier.name,
        "file_name": record.file_name, "pages_read": record.pages_read, "code_prefix": record.code_prefix,
        "status": record.status, "status_label": record.get_status_display(), "summary": record.summary,
        "draft_count": len(record.drafts), "created_count": created,
        "uploaded_by": record.uploaded_by.username, "created_at": record.created_at.isoformat(),
        "finished_by": record.finished_by.username if record.finished_by_id else "",
        "finished_at": record.finished_at.isoformat() if record.finished_at else "",
    }


def _record(business_unit, import_id, *, lock=False):
    records = CatalogueImport.objects.filter(business_unit=business_unit, pk=import_id)
    if lock:
        records = records.select_for_update(of=("self",))
    record = records.select_related("supplier", "uploaded_by", "finished_by").first()
    if record is None:
        raise InvalidTransaction("That catalogue upload was not found.")
    return record


def _choices(supplier):
    brands = list(ProductBrand.objects.filter(is_synthetic=True).prefetch_related("suppliers").order_by("name"))
    return [{"id": brand.pk, "name": brand.name, "sold_by_company": any(each.pk == supplier.pk for each in brand.suppliers.all())}
            for brand in brands]


def import_detail(*, actor, business_unit, import_id):
    _require_editor(actor, business_unit)
    record = _record(business_unit, import_id)
    costs = can_see_costs(actor, business_unit)
    duplicates = _duplicates(record.drafts)
    matched = _brand_match(record.drafts)
    drafts = []
    for draft in record.drafts:
        row = {key: value for key, value in draft.items() if key != "photo"}
        if not costs:
            row.pop("cost", None)
            row.pop("currency", None)
        brand = matched.get(draft["index"])
        row["brand_id"] = brand.pk if brand else None
        row["photo_url"] = _draft_photo_url(record, draft) if record.status == CatalogueImport.Status.REVIEW else ""
        row["duplicates"] = duplicates.get(draft["index"], []) if record.status == CatalogueImport.Status.REVIEW else []
        drafts.append(row)
    payload = {
        "import": _import_row(record), "drafts": drafts, "can_see_costs": costs,
        "brands": _choices(record.supplier), "units": [{"value": value, "label": label} for value, label in Item.Unit.choices],
    }
    if record.status == CatalogueImport.Status.DONE:
        payload["created"] = [_created_row(item) for item in record.created_items.select_related("brand").order_by("item_code")]
    return payload


def overview(*, actor, business_unit):
    _require_user(actor, business_unit)
    editor = can_edit(actor, business_unit)
    payload = {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "can_edit": editor, "ai_available": is_available(), "max_pages": MAX_PAGES,
        "imports": [], "suppliers": [],
    }
    if editor:
        payload["imports"] = [_import_row(record) for record in CatalogueImport.objects.filter(business_unit=business_unit)
                              .select_related("supplier", "uploaded_by", "finished_by")[:20]]
        payload["suppliers"] = [
            {"id": each.pk, "name": each.name}
            for each in SyntheticCounterparty.objects.filter(party_type=SyntheticCounterparty.Type.SUPPLIER, is_synthetic=True)
            .order_by("name")
        ]
    return payload


def draft_photo(*, actor, business_unit, import_id, index, name):
    _require_editor(actor, business_unit)
    record = _record(business_unit, import_id)
    draft = next((each for each in record.drafts if each.get("index") == index), None)
    if (record.status != CatalogueImport.Status.REVIEW or draft is None or not draft.get("photo")
            or name != f"{draft['photo']['sha256'][:16]}.jpg"):
        raise InvalidTransaction("Photo not found.")
    return default_storage.open(draft["photo"]["name"], "rb")


def _created_row(item):
    variant = item.variants.order_by("pk").first()
    return {"item_id": item.pk, "item_code": item.item_code, "sku": variant.sku if variant else "",
            "description": item.description, "brand": item.brand.name, "photo_url": photo_url(item)}


def _brand_code(name):
    letters = re.sub(r"[^A-Z0-9]", "", name.upper()) or "BRAND"
    base = letters[:5]
    taken = set(ProductBrand.objects.values_list("code", flat=True))
    if base not in taken:
        return base
    for number in range(2, 1000):
        code = f"{base[:8 - len(str(number))]}{number}"
        if code not in taken:
            return code
    raise InvalidTransaction("A short code for the new brand could not be found. Add the brand by hand first.")


def _finish(colour, item_code):
    letters = re.sub(r"[^A-Z0-9]", "", (colour or "").upper())[:3] or "STD"
    sku = f"{item_code}-{letters}"
    number = 2
    while Variant.objects.filter(sku=sku).exists():
        sku = f"{item_code}-{letters}{number}"
        number += 1
    return sku


def _cleanup_photos(drafts):
    names = [draft["photo"]["name"] for draft in drafts if draft.get("photo")]
    transaction.on_commit(lambda: [default_storage.delete(name) for name in names])


@transaction.atomic
def create_items(*, actor, business_unit, import_id, choices):
    """Create the ticked drafts as items (each with one finish) with the details as checked."""
    _require_editor(actor, business_unit)
    record = _record(business_unit, import_id, lock=True)
    if record.status != CatalogueImport.Status.REVIEW:
        raise InvalidTransaction("This catalogue upload has already been finished.")
    if not isinstance(choices, list) or not choices:
        raise InvalidTransaction("Tick at least one item to create.")
    costs = can_see_costs(actor, business_unit)
    drafts = {draft["index"]: draft for draft in record.drafts}
    chosen, codes = [], set()
    for choice in choices:
        if not isinstance(choice, dict) or choice.get("index") not in drafts:
            raise InvalidTransaction("A ticked item is not in this upload.")
        draft = drafts[choice["index"]]
        number = draft["index"] + 1
        values = {}
        for field, limit in (("description", TEXT_FIELDS["description"]), ("category", TEXT_FIELDS["category_label"]),
                             ("brand_item_code", TEXT_FIELDS["supplier_item_code"]), ("material", TEXT_FIELDS["material"]),
                             ("colour", TEXT_FIELDS["colour"]), ("barcode", TEXT_FIELDS["barcode"])):
            value = " ".join(str(choice.get(field, draft.get(field, "")) or "").split())
            if len(value) > limit:
                raise InvalidTransaction(f"Item {number}: keep the {field.replace('_', ' ')} under {limit} characters.")
            values[field] = value
        if not values["description"]:
            raise InvalidTransaction(f"Item {number}: the description cannot be empty.")
        for field, places in DIMENSIONS.items():
            values[field] = _clean_decimal(choice.get(field, draft.get(field)), field, places)
        unit = choice.get("unit_of_measure", draft["unit_of_measure"])
        if unit not in UNITS:
            raise InvalidTransaction(f"Item {number}: choose a unit of measure.")
        values["unit_of_measure"] = unit
        code = str(choice.get("item_code", draft.get("item_code", "")) or "").strip().upper()
        if code and not CODE.match(code):
            raise InvalidTransaction(f"Item {number}: the item code uses capital letters, digits and dashes, for example TVF-014.")
        if code and (code in codes or Item.objects.filter(item_code=code).exists()):
            if code == draft.get("item_code"):
                code = ""  # the suggestion was taken meanwhile: give the next free one
            else:
                raise InvalidTransaction(f"Item {number}: code {code} is already used.")
        values["item_code"] = code
        if code:
            codes.add(code)
        if values["barcode"] and (Item.objects.filter(barcode=values["barcode"]).exists()
                                  or any(each[1]["barcode"] == values["barcode"] for each in chosen)):
            raise InvalidTransaction(f"Item {number}: barcode {values['barcode']} is already on another item.")
        brand_id, brand_name = choice.get("brand_id"), " ".join(str(choice.get("brand_name") or "").split())[:120]
        if brand_id:
            brand = ProductBrand.objects.filter(pk=brand_id, is_synthetic=True).first()
            if brand is None:
                raise InvalidTransaction(f"Item {number}: choose a brand.")
            values["brand"] = brand
        elif brand_name:
            values["brand_name"] = brand_name
        else:
            raise InvalidTransaction(f"Item {number}: choose a brand or give the new brand's name.")
        if costs:
            cost = _clean_decimal(choice.get("cost", draft.get("cost")), "cost", 2)
            currency = str(choice.get("currency", draft.get("currency", "")) or "").strip().upper()
            if cost is not None and not CURRENCY.match(currency):
                raise InvalidTransaction(f"Item {number}: give the price's currency, for example EUR.")
            values["cost"], values["currency"] = cost, currency if cost is not None else ""
        values["use_photo"] = bool(choice.get("use_photo", True)) and bool(draft.get("photo"))
        chosen.append((draft, values))
    free = iter(_next_codes(record.code_prefix, len(chosen), taken=codes))
    new_brands, created, now = {}, [], timezone.now()
    for draft, values in chosen:
        brand = values.get("brand")
        if brand is None:
            key = values["brand_name"].lower()
            brand = ProductBrand.objects.filter(name__iexact=values["brand_name"], is_synthetic=True).first() or new_brands.get(key)
            if brand is None:
                brand = ProductBrand.objects.create(name=values["brand_name"], code=_brand_code(values["brand_name"]))
                new_brands[key] = brand
        brand.suppliers.add(record.supplier)
        item = Item.objects.create(
            brand=brand, supplier=record.supplier, item_code=values["item_code"] or next(free),
            description=values["description"], name_en=values["description"], category_label=values["category"],
            supplier_item_code=values["brand_item_code"], barcode=values["barcode"],
            unit_of_measure=values["unit_of_measure"], material=values["material"], colour=values["colour"],
            **{field: values[field] for field in DIMENSIONS},
            supplier_price=values.get("cost"), supplier_price_currency=values.get("currency", ""),
            supplier_price_date=now.date() if values.get("cost") is not None else None,
            added_in=business_unit, updated_by=actor, updated_at=now,
        )
        Variant.objects.create(item=item, sku=_finish(values["colour"], item.item_code),
                               description=values["colour"] or "Standard")
        if values["use_photo"]:
            with default_storage.open(draft["photo"]["name"], "rb") as handle:
                item.photo.save("photo.jpg", ContentFile(handle.read()), save=False)
            item.photo_content_type, item.photo_sha256 = "image/jpeg", draft["photo"]["sha256"]
            item.image_path = photo_url(item)
            item.save(update_fields=["photo", "photo_content_type", "photo_sha256", "image_path"])
        created.append(item)
    record.created_items.add(*created)
    record.status, record.finished_by, record.finished_at = CatalogueImport.Status.DONE, actor, now
    record.save(update_fields=["status", "finished_by", "finished_at"])
    _cleanup_photos(record.drafts)
    return {"created": [_created_row(item) for item in created], "import": _import_row(record)}


@transaction.atomic
def discard_import(*, actor, business_unit, import_id):
    _require_editor(actor, business_unit)
    record = _record(business_unit, import_id, lock=True)
    if record.status != CatalogueImport.Status.REVIEW:
        raise InvalidTransaction("This catalogue upload has already been finished.")
    record.status, record.finished_by, record.finished_at = CatalogueImport.Status.DISCARDED, actor, timezone.now()
    record.save(update_fields=["status", "finished_by", "finished_at"])
    _cleanup_photos(record.drafts)
    return {"import": _import_row(record)}
