"""Customers, identified by a client number, a project number, or both.

A customer gets a client number (C-000123) for retail purchases, a project
number (P-000123) for projects and fitout, or both. Both numbers sit on the same
record, so the two histories stay linked even though their finances are kept
separately. Numbers are permanent (a database trigger refuses any change), so a
client's quotes and invoices stay linked to one record even when their phone
number or name changes; earlier contact details are kept as change history.

A customer may decline to give their Qatar ID (QID). Staff then mark it as
declined, and the record shows why it is blank. A QID that is given is unique
and, once set, never changes; one can be added later if the customer agrees.

Each customer can have a title (Mr, Sheikh, Sheikha...) chosen from a fixed
list, so customers can be filtered and counted by title.

Provisional defaults, to confirm with Sam:
- Client numbers follow Tivoli's existing C-000000 format; project numbers use
  P-000000 to match, until we see Tivoli's project format.
- Customers registered before this change bought retail, so each got a client number.
- A customer who declines the QID must leave a phone number or other contact
  detail, so staff can find them again and avoid registering them twice.
- Anyone who can create quotes in a business unit can register and update
  customers, and customers are shared by the whole company.
- History, and the "bought" counts by title, use only quotes and invoices from
  the business units the viewer can quote in.
- A QID is 11 digits and starts with 2 or 3.
"""

import re

from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Q

from .access import active_grants
from .models import (
    CUSTOMER_TITLE_ARABIC,
    CustomerContactChange,
    CustomerInvoice,
    CustomerTitle,
    CustomerType,
    SalesQuote,
    SyntheticCounterparty,
    UserActionGrant,
    next_customer_number,
)
from .purchasing_services import WorkflowPermissionDenied
from .supplier_kyc import _demo_user
from .transaction_services import InvalidTransaction

QID_PATTERN = re.compile(r"[23][0-9]{10}")
TEXT_FIELDS = {"name": 110, "name_ar": 120, "contact_en": 500, "contact_ar": 500}
CONTACT_FIELDS = ("title", "customer_type", "name", "name_ar", "contact_en", "contact_ar")
NUMBER_KINDS = {"client": "client_number", "project": "project_number"}
REGISTER_FOR = {"client": ("client",), "project": ("project",), "both": ("client", "project")}
PAGE = 50
HISTORY = 50


def normalize_qid(value):
    """Accept the QID as typed (spaces or dashes allowed) and return its 11 digits."""
    digits = re.sub(r"[\s-]", "", value) if isinstance(value, str) else ""
    if not QID_PATTERN.fullmatch(digits):
        raise InvalidTransaction("Enter the customer's 11-digit Qatar ID (QID).")
    return digits


def _qid_choice(data):
    """The QID as given, or the customer's choice not to give one. Returns (qid, declined)."""
    raw = data.get("qid")
    if isinstance(raw, str) and raw.strip():
        return normalize_qid(raw), False
    if data.get("qid_declined") is True:
        return None, True
    raise InvalidTransaction("Enter the customer's QID, or tick that the customer declined to give it.")


def _refuse_duplicate_qid(qid):
    existing = _customers().filter(qid=qid).first()
    if existing:
        raise InvalidTransaction(
            f"This QID is already registered to {existing.name} ({_numbers_label(existing)}). Open that customer instead."
        )


def _quoting_units(user):
    return active_grants(user, UserActionGrant.Action.CREATE_ORDER).filter(
        business_unit__is_active=True,
    ).values("business_unit_id")


def _require_customer_user(user):
    _demo_user(user)
    if not _quoting_units(user).exists():
        raise WorkflowPermissionDenied("You do not have access to customers. Ask a system administrator.")


def _customers():
    return SyntheticCounterparty.objects.filter(party_type=SyntheticCounterparty.Type.CUSTOMER, is_synthetic=True)


def _text(data, field, *, required=False):
    value = data.get(field, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise InvalidTransaction(f"{field.replace('_', ' ').capitalize()} must be text.")
    value = " ".join(value.split())
    if required and not value:
        raise InvalidTransaction("Enter the customer's name.")
    if len(value) > TEXT_FIELDS[field]:
        raise InvalidTransaction(f"{field.replace('_', ' ').capitalize()} is too long.")
    return value


def number_search(term):
    """A typed client or project number, with or without the dash or leading zeros (c12 finds C-000012)."""
    match = re.fullmatch(r"([CcPp])-?(\d{1,9})", term.replace(" ", ""))
    if not match:
        return Q(pk__in=[])
    number = f"{match.group(1).upper()}-{int(match.group(2)):06d}"
    return Q(client_number=number) | Q(project_number=number)


def _numbers_label(customer):
    return " / ".join(number for number in (customer.client_number, customer.project_number) if number)


def _title(data, current=""):
    value = data.get("title", current)
    if value in (None, ""):
        return ""
    if value not in CustomerTitle.values:
        raise InvalidTransaction("Choose a title from the list, or leave it blank.")
    return value


def _customer_type(data, current=CustomerType.INDIVIDUAL):
    value = data.get("customer_type", current)
    if value in (None, ""):
        return current
    if value not in CustomerType.values:
        raise InvalidTransaction("Choose the customer type: individual, company or government.")
    return value


def _contact(data, customer=None):
    values = {}
    for field in CONTACT_FIELDS:
        if field == "title":
            values[field] = _title(data, customer.title if customer else "")
        elif field == "customer_type":
            values[field] = _customer_type(data, customer.customer_type if customer else CustomerType.INDIVIDUAL)
        elif customer is None or field in data:
            values[field] = _text(data, field, required=field == "name")
        else:
            values[field] = getattr(customer, field)
    values["name"] = _display_name(values["name"])
    return values


def _display_name(name):
    # Demo records must carry the TEST ONLY marker; staff type the real-looking name.
    return name if name.startswith("TEST ONLY") else f"TEST ONLY {name}"


def _row(customer):
    return {
        "id": customer.pk, "client_number": customer.client_number, "project_number": customer.project_number,
        "qid": customer.qid, "qid_declined": customer.qid_declined, "code": customer.code,
        "title": customer.title, "title_label": customer.get_title_display() if customer.title else "",
        "title_ar": CUSTOMER_TITLE_ARABIC.get(customer.title, ""), "name": customer.name,
        "customer_type": customer.customer_type, "customer_type_label": customer.get_customer_type_display(),
        "name_ar": customer.name_ar, "contact_en": customer.contact_en, "contact_ar": customer.contact_ar,
        "registered_at": customer.created_at.isoformat(),
    }


def _title_counts(units):
    """How many customers have each title, and how many of them have bought (an invoice in the viewer's units)."""
    bought = CustomerInvoice.objects.filter(customer=OuterRef("pk"), business_unit_id__in=units)
    rows = (
        _customers().annotate(bought=Exists(bought)).values("title")
        .annotate(customers=Count("pk"), bought_count=Count("pk", filter=Q(bought=True)))
    )
    counted = {row["title"]: row for row in rows}
    labels = [("", "No title")] + list(CustomerTitle.choices)
    return [
        {
            "title": code, "label": label, "label_ar": CUSTOMER_TITLE_ARABIC.get(code, ""),
            "customers": counted.get(code, {}).get("customers", 0), "bought": counted.get(code, {}).get("bought_count", 0),
        }
        for code, label in labels if code in counted or code
    ]


def customer_overview(*, actor, search="", title=""):
    _require_customer_user(actor)
    term = " ".join(search.split()) if isinstance(search, str) else ""
    if len(term) > 80:
        raise InvalidTransaction("Search text is too long.")
    if title not in ("", "none", *CustomerTitle.values):
        raise InvalidTransaction("Choose a title from the list.")
    customers = _customers().order_by("-created_at", "-pk")
    if title:
        customers = customers.filter(title="" if title == "none" else title)
    if term:
        digits = re.sub(r"[\s-]", "", term)
        match = (
            Q(name__icontains=term) | Q(name_ar__icontains=term) | Q(contact_en__icontains=term)
            | Q(code__icontains=term) | number_search(term)
        )
        if digits.isdigit():
            match |= Q(qid__startswith=digits)
        customers = customers.filter(match)
    rows = list(customers[:PAGE + 1])
    return {
        "customers": [_row(row) for row in rows[:PAGE]], "more": len(rows) > PAGE,
        "titles": [{"code": code, "label": label, "label_ar": CUSTOMER_TITLE_ARABIC[code]} for code, label in CustomerTitle.choices],
        "customer_types": [{"code": code, "label": label} for code, label in CustomerType.choices],
        "title_counts": _title_counts(_quoting_units(actor)), "synthetic_only": True,
    }


@transaction.atomic
def register_customer(*, actor, data):
    _require_customer_user(actor)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the customer details.")
    qid, declined = _qid_choice(data)
    if qid:
        _refuse_duplicate_qid(qid)
    kinds = REGISTER_FOR.get(data.get("register_for", "client"))
    if kinds is None:
        raise InvalidTransaction("Choose whether the customer is registered for retail, projects and fitout, or both.")
    contact = _contact(data)
    if declined and not (contact["contact_en"] or contact["contact_ar"]):
        raise InvalidTransaction("When the customer declines to give a QID, enter a phone number or other contact detail so they can be found again.")
    numbers = {NUMBER_KINDS[kind]: next_customer_number(kind) for kind in kinds}
    try:
        with transaction.atomic():
            customer = SyntheticCounterparty.objects.create(
                party_type=SyntheticCounterparty.Type.CUSTOMER, code=f"TEST-CUST-{next(iter(numbers.values()))}",
                qid=qid, qid_declined=declined, **numbers, **contact,
            )
    except IntegrityError:
        # Two people registering the same QID at once: the unique index decides.
        raise InvalidTransaction("This QID is already registered. Search for it and open that customer instead.") from None
    return customer_detail(actor=actor, customer_id=customer.pk)


@transaction.atomic
def update_customer(*, actor, customer_id, data):
    _require_customer_user(actor)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the customer details.")
    customer = _customers().select_for_update().filter(pk=customer_id).first()
    if customer is None:
        raise InvalidTransaction("Customer not found.")
    typed_qid = str(data.get("qid") or "").replace(" ", "").replace("-", "") if "qid" in data else ""
    added_qid = None
    if customer.qid:
        if "qid" in data and typed_qid != customer.qid:
            raise InvalidTransaction("A customer's QID cannot be changed. Register a new customer if this is a different person.")
    elif typed_qid:
        # The customer has now agreed to give their QID.
        added_qid = normalize_qid(typed_qid)
        _refuse_duplicate_qid(added_qid)
    added_number = None
    if data.get("add_number") not in (None, ""):
        field = NUMBER_KINDS.get(data["add_number"])
        if field is None:
            raise InvalidTransaction("Choose a client number or a project number to add.")
        if getattr(customer, field):
            raise InvalidTransaction(f"This customer already has a {data['add_number']} number.")
        added_number = (field, next_customer_number(data["add_number"]))
    after = _contact(data, customer)
    before = {field: getattr(customer, field) for field in CONTACT_FIELDS}
    if added_qid is None and customer.qid_declined and not (after["contact_en"] or after["contact_ar"]):
        raise InvalidTransaction("A customer without a QID needs a phone number or other contact detail.")
    if after != before or added_qid or added_number:
        for field, value in after.items():
            setattr(customer, field, value)
        fields = list(CONTACT_FIELDS)
        if added_qid:
            before["qid"], after["qid"] = "", added_qid
            customer.qid, customer.qid_declined = added_qid, False
            fields += ["qid", "qid_declined"]
        if added_number:
            field, number = added_number
            before[field], after[field] = "", number
            setattr(customer, field, number)
            fields.append(field)
        try:
            with transaction.atomic():
                customer.save(update_fields=fields)
        except IntegrityError:
            raise InvalidTransaction("This QID is already registered. Search for it and open that customer instead.") from None
        CustomerContactChange.objects.create(customer=customer, changed_by=actor, before=before, after=after)
    return customer_detail(actor=actor, customer_id=customer.pk)


def customer_detail(*, actor, customer_id):
    _require_customer_user(actor)
    customer = _customers().filter(pk=customer_id).first()
    if customer is None:
        raise InvalidTransaction("Customer not found.")
    units = _quoting_units(actor)
    quotes = SalesQuote.objects.filter(customer=customer, business_unit_id__in=units).select_related("business_unit")
    invoices = CustomerInvoice.objects.filter(customer=customer, business_unit_id__in=units).select_related("business_unit")
    credit_notes = customer.credit_notes.filter(business_unit_id__in=units).select_related("business_unit", "invoice")
    changes = customer.contact_changes.select_related("changed_by")[:HISTORY]
    return {
        **_row(customer),
        "quotes": [
            {
                "id": quote.pk, "business_unit": quote.business_unit.code, "kind": quote.get_kind_display(),
                "status": quote.get_status_display(), "total": format(quote.total_quote_value, "f"),
                "currency": quote.currency, "created_at": quote.created_at.isoformat(),
            } for quote in quotes[:HISTORY]
        ],
        "quote_count": quotes.count(),
        "invoices": [
            {
                "id": invoice.pk, "number": invoice.number, "business_unit": invoice.business_unit.code,
                "total": format(invoice.invoice_total, "f"), "balance_due": format(invoice.balance_due, "f"),
                "currency": invoice.currency, "issued_at": invoice.issued_at.isoformat(),
            } for invoice in invoices[:HISTORY]
        ],
        "invoice_count": invoices.count(),
        "credit_notes": [
            {
                "id": note.pk, "number": note.number, "business_unit": note.business_unit.code,
                "invoice_number": note.invoice.number, "credit_total": format(note.credit_total, "f"),
                "refunded_amount": format(note.refunded_amount, "f"), "currency": note.currency,
                "issued_at": note.issued_at.isoformat(),
            } for note in credit_notes[:HISTORY]
        ],
        "contact_changes": [
            {
                "changed_by": change.changed_by.get_full_name() or change.changed_by.get_username(),
                "changed_at": change.changed_at.isoformat(),
                "fields": [
                    {"field": field, "before": change.before.get(field, ""), "after": change.after.get(field, "")}
                    for field in (*CONTACT_FIELDS, "qid", *NUMBER_KINDS.values()) if change.before.get(field) != change.after.get(field)
                ],
            } for change in changes
        ],
        "synthetic_only": True,
    }
