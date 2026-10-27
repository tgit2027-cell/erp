"""Quotation-only public snapshots. No commercial gates or legal defaults."""
import re
from datetime import date

from django.utils import timezone
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .transaction_services import InvalidTransaction


REGISTERED_COMPANY_NAME = "Tivoli Group L.L.C"
TRADEMARK_NAME = "Tivoli"

CUSTOMER_FIELDS = {
    "contact_person_en", "contact_person_ar", "project_en", "project_ar",
    "address_en", "address_ar", "phone", "mobile", "email",
}
PRESENTATION_FIELDS = {
    "valid_until", "payment_terms_en", "payment_terms_ar",
    "delivery_time_en", "delivery_time_ar", "department_en", "department_ar",
    "notes_en", "notes_ar", "line_units",
} | CUSTOMER_FIELDS


def item_presentation(item):
    # No remote image fetch, data URL, executable content, or path traversal.
    path = item.image_path
    if not re.fullmatch(r"/(?:media|assets)/[A-Za-z0-9_/-]+\.(?:png|jpg|jpeg|webp)", path or ""):
        path = ""
    return {
        "item_code": item.item_code,
        "name_en": item.name_en or item.description,
        "name_ar": item.name_ar,
        "category_en": item.category_label,
        "category_ar": item.category_ar,
        "description_en": item.description,
        "description_ar": item.description_ar,
        "image_path": path,
    }


def clean_presentation(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - PRESENTATION_FIELDS:
        raise InvalidTransaction("Quotation presentation accepts only the documented display fields.")
    cleaned = {}
    for key, text in value.items():
        if key == "line_units":
            if not isinstance(text, list) or len(text) > 100:
                raise InvalidTransaction("Line units must be a list of at most 100 explicit display-unit pairs.")
            units = []
            for row in text:
                if (not isinstance(row, dict) or set(row) - {"en", "ar"}
                        or any(not isinstance(v, str) or len(v) > 80 for v in row.values())):
                    raise InvalidTransaction("Each display unit accepts only English/Arabic text of at most 80 characters.")
                units.append({"en": row.get("en", "").strip(), "ar": row.get("ar", "").strip()})
            cleaned[key] = units
            continue
        if not isinstance(text, str) or len(text) > (10 if key == "valid_until" else 1000):
            raise InvalidTransaction(f"{key} must be text within the quotation display limit.")
        cleaned[key] = text.strip()
    if cleaned.get("email"):
        try:
            validate_email(cleaned["email"])
        except ValidationError:
            raise InvalidTransaction("Quotation email must be a valid email address.") from None
    validity = cleaned.get("valid_until")
    if validity:
        try:
            parsed = date.fromisoformat(validity)
        except ValueError:
            raise InvalidTransaction("Quote validity must be an explicit YYYY-MM-DD date.") from None
    return cleaned


def quotation_document(*, actor, unit, customer, prepared, discount, total_list, total_discount, total, display):
    # Operator configuration only. Never accept company identity overrides from
    # a salesperson's payload or copy arbitrary configuration into a document.
    headers = getattr(settings, "QUOTATION_COMPANY_HEADERS", {})
    if not isinstance(headers, dict):
        raise InvalidTransaction("Quotation company-header configuration must be a mapping.")
    header = headers.get(unit.legal_entity.code, {})
    if not isinstance(header, dict) or any(
        not isinstance(header.get(key, ""), str) or len(header.get(key, "")) > 1000
        for key in ("contact_en", "contact_ar")
    ):
        raise InvalidTransaction("Quotation company-header display configuration is invalid.")
    line_units = display.get("line_units", [])
    if line_units and len(line_units) != len(prepared):
        raise InvalidTransaction("Explicit display units must match the submitted quotation lines in order.")
    return {
        "quote_date": timezone.localdate().isoformat(),
        "valid_until": display.get("valid_until", ""),
        "company": {
            "name_en": REGISTERED_COMPANY_NAME,
            "trademark_en": TRADEMARK_NAME,
            # The owner confirmed only the English registered name. Do not
            # translate it or reuse a potentially unrelated configured Arabic name.
            "name_ar": "",
            **{key: header.get(key, "") for key in ("contact_en", "contact_ar")},
            "identity_status": "owner_confirmed_registered_name",
        },
        "client": {
            "account": customer.code,
            "name_en": customer.name, "name_ar": customer.name_ar,
            "contact_en": customer.contact_en, "contact_ar": customer.contact_ar,
            **{key: display.get(key, "") for key in CUSTOMER_FIELDS},
        },
        "salesperson": actor.get_username(),
        "business_unit": {"code": unit.code, "name": unit.name},
        **{key: display.get(key, "") for key in PRESENTATION_FIELDS
           if key not in CUSTOMER_FIELDS | {"valid_until", "line_units"}},
        "terms_status": "pending_owner_text",
        "lines": [
            {
                **item_presentation(row["variant"].item),
                "variant_id": row["variant"].pk,
                "variant_sku": row["variant"].sku,
                "specifications": row["specifications"],
                "unit_en": line_units[index]["en"] if line_units else "",
                "unit_ar": line_units[index]["ar"] if line_units else "",
                "quantity": format(row["quantity"], "f"),
                "unit_selling_price": format(row["approved_price"].amount, "f"),
                "discount_percent": format(discount, "f"),
                "discount_amount": format(row["discount_amount"], "f"),
                "net_total": format(row["net_total"], "f"),
            } for index, row in enumerate(prepared)
        ],
        "currency": "QAR",
        "total_list_value": format(total_list, "f"),
        "total_discount": format(total_discount, "f"),
        "total_quote_value": format(total, "f"),
    }
