"""Go-live imports: bring customers, suppliers, items, opening stock and the opening trial balance over in one go.

How it works: download the Excel template for what you want to bring over, fill it in (or
paste from the old system's export), and upload it. Every row is checked straight away and
nothing is saved: the screen lists each row's problems so the file can be fixed. A clean file
is sent for approval; a second person approves it, and only then are the records created, all
together or not at all. The rows are checked again at approval, in case something changed.

Provisional rules until Tivoli confirms them:

- Preparing needs "Prepare go-live imports"; approving needs "Approve go-live imports", and
  the approver can never be the person who prepared the file. A grant in any business unit
  counts, because customers and suppliers are shared by the whole company.
- Customers keep the client and project numbers they had in the old system (C-000123,
  P-000123); a customer without one gets the next new number. New numbers carry on after the
  highest one imported, so a number is never given twice.
- A customer without a QID is marked as having declined it, and then needs a phone number or
  other contact detail, as on the customer screen.
- Suppliers that come over are already in use, so their KYC file is created as approved by
  the approver of the import, with the CR and VAT details from the file. The documents must
  be attached within 3 months of the import (agreed with Sam); after that the supplier is
  frozen, so no new orders, bills or payments, until they are.
- Items: see go_live_items.py. Opening stock and the trial balance: see go_live_balances.py.
  Unpaid old invoices and supplier bills: see old_balances.py.
  The fixed asset register and staff loans, one by one: see go_live_assets_loans.py.
- At most 5,000 rows and 5 MB per file; .xlsx or .csv.
"""

import csv
import datetime
import hashlib
import io
import re

from django.db import connection, transaction
from django.utils import timezone

from . import go_live_assets_loans, go_live_balances, go_live_items, old_balances
from .access import active_grants
from .models import ImportBatch, SupplierKyc, SupplierKycEvent, SyntheticCounterparty, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .supplier_kyc import _demo_user
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action
K = ImportBatch.Kind
S = ImportBatch.Status
MAX_ROWS = 5000
MAX_BYTES = 5 * 1024 * 1024
SHOWN_PROBLEMS = 200
SHOWN_ROWS = 20
RECENT = 20
DOCUMENTS_WITHIN_DAYS = 90
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
POLICY_LABEL = ("TEST ONLY provisional rules: a second person approves every import and nothing is created before then; "
                "customers keep their old client and project numbers; customers without a QID need a contact detail; "
                "imported suppliers get an approved KYC file and 3 months to attach their documents, after which they are frozen; "
                "items keep their old codes (TVF- Tivoli, RBB- Roche Bobois, NAT- Natuzzi), a repeated item code adds a finish, "
                "and each item is priced only in its own business unit; opening stock and the trial balance are booked on the day "
                "given, each business unit must balance, and stock, customer and supplier lines wait on 'opening balances' until "
                "their own import brings them in; customer and supplier balances come in one unpaid old invoice or bill at a time "
                "and are aged from its date; fixed assets come in asset by asset and keep the old system's monthly depreciation, and staff loans loan by employee; "
                "at most 5,000 rows per file.")
TITLES = {"mr": "mr", "mrs": "mrs", "ms": "ms", "miss": "miss", "dr": "dr", "sheikh": "sheikh", "sheikha": "sheikha",
          "eng": "eng"}
YES = {"yes", "y", "true", "1", "نعم"}
NO = {"no", "n", "false", "0", "", "لا"}

# Each column: (heading in the template, key, required, what to put in it, example).
COLUMNS = {
    K.CUSTOMERS: [
        ("Name", "name", True, "The customer's full name, or the company name.", "Ahmed Al-Kuwari"),
        ("Name in Arabic", "name_ar", False, "Optional.", "أحمد الكواري"),
        ("Title", "title", False, "Optional: Mr, Mrs, Ms, Miss, Dr, Sheikh, Sheikha or Eng.", "Mr"),
        ("Customer type", "customer_type", False, "Individual, company or government. Blank means individual.", "Individual"),
        ("QID", "qid", False, "The 11-digit Qatar ID. Leave blank if the customer declined to give it.", "28763400123"),
        ("Phone or other contact", "contact_en", False, "Needed when there is no QID.", "+974 5555 0101"),
        ("Contact in Arabic", "contact_ar", False, "Optional.", ""),
        ("Registered for", "register_for", False, "Retail, projects or both. Blank means retail.", "Retail"),
        ("Client number", "client_number", False, "The old system's client number, for example C-000123. "
         "Blank gives a new one when the customer is registered for retail.", "C-004512"),
        ("Project number", "project_number", False, "The old system's project number, for example P-000045. "
         "Blank gives a new one when the customer is registered for projects.", ""),
    ],
    K.SUPPLIERS: [
        ("Supplier code", "code", False, "The old system's supplier code. Blank makes one from the CR number.", "SUP-0042"),
        ("Legal name", "legal_name", True, "As on the commercial registration.", "Gulf Timber Trading WLL"),
        ("CR number", "cr_number", True, "Commercial registration number: letters, digits, / and -.", "45123"),
        ("CR expiry", "cr_expiry", False, "Optional date, for example 2027-06-30.", "2027-06-30"),
        ("VAT registered", "vat_registered", False, "Yes or no. Blank means no.", "No"),
        ("VAT number", "vat_number", False, "Needed when VAT registered.", ""),
        ("Address", "address", False, "Optional.", "Street 12, Industrial Area, Doha"),
        ("Signatory name", "signatory_name", True, "The person who signs for the supplier.", "Rashid Mahmoud"),
        ("Signatory title", "signatory_title", False, "Optional.", "General Manager"),
        ("Email", "contact_email", False, "Optional.", "orders@example.com"),
        ("Phone", "contact_phone", False, "Optional.", "+974 4444 0202"),
    ],
    K.ITEMS: go_live_items.COLUMNS,
    K.OPENING_STOCK: go_live_balances.STOCK_COLUMNS,
    K.TRIAL_BALANCE: go_live_balances.TB_COLUMNS,
    K.CUSTOMER_BALANCES: old_balances.CUSTOMER_COLUMNS,
    K.SUPPLIER_BALANCES: old_balances.SUPPLIER_COLUMNS,
    K.FIXED_ASSETS: go_live_assets_loans.ASSET_COLUMNS,
    K.STAFF_LOANS: go_live_assets_loans.LOAN_COLUMNS,
}
BALANCE_KINDS = (K.OPENING_STOCK, K.TRIAL_BALANCE, K.CUSTOMER_BALANCES, K.SUPPLIER_BALANCES, K.FIXED_ASSETS, K.STAFF_LOANS)


def can_prepare(user):
    """Only the Master Admin brings a file in (Sam, 5 Nov 2026); a second person still approves it."""
    from .access_levels import is_master_admin
    return is_master_admin(user) and active_grants(user, A.IMPORT_DATA).filter(business_unit__is_active=True).exists()


def can_approve(user):
    return active_grants(user, A.APPROVE_IMPORT).filter(business_unit__is_active=True).exists()


def can_use(user):
    """Seeing the screen. System administrators always see it, so they can follow imports and grant the two permissions."""
    return getattr(user, "is_authenticated", False) and (user.is_superuser or can_prepare(user) or can_approve(user))


def _require_user(actor):
    _demo_user(actor)
    if not can_use(actor):
        raise WorkflowPermissionDenied("You do not have access to go-live imports. Ask a system administrator.")


def _kind(value):
    if value not in K.values:
        raise InvalidTransaction("Choose what you are bringing over.")
    return value


# Reading the file.

def _cell(value):
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return " ".join(str(value).split())


def _table(name, content):
    """[(row number, [cell text])] from an .xlsx or .csv file, header row first."""
    if len(content) > MAX_BYTES:
        raise InvalidTransaction("The file is larger than 5 MB. Split it into smaller files.")
    lower = (name or "").lower()
    if lower.endswith(".xlsx"):
        from openpyxl import load_workbook

        try:
            book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception:
            raise InvalidTransaction("This file could not be read as an Excel workbook.") from None
        sheet = book["Data"] if "Data" in book.sheetnames else book.worksheets[0]
        rows = [(number, [_cell(value) for value in row])
                for number, row in enumerate(sheet.iter_rows(values_only=True), start=1)]
        book.close()
    elif lower.endswith(".csv"):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise InvalidTransaction("Save the CSV file as UTF-8 so Arabic names come through.") from None
        rows = [(number, [_cell(value) for value in row]) for number, row in enumerate(csv.reader(io.StringIO(text)), start=1)]
    else:
        raise InvalidTransaction("Upload the filled-in template as an .xlsx or .csv file.")
    return [(number, cells) for number, cells in rows if any(cells)]


def _records(kind, name, content):
    table = _table(name, content)
    if not table:
        raise InvalidTransaction("The file is empty.")
    header_number, header = table[0]
    wanted = {heading.lower(): key for heading, key, *_ in COLUMNS[kind]}
    positions = {}
    for index, heading in enumerate(header):
        key = wanted.get(heading.strip().lower().rstrip("*").strip())
        if key:
            positions[key] = index
    missing = [heading for heading, key, required, *_ in COLUMNS[kind] if required and key not in positions]
    if missing:
        raise InvalidTransaction(f"Row {header_number} must be the template's headings. Missing: {', '.join(missing)}. "
                                 "Download the template and copy the data into it.")
    rows = table[1:]
    if not rows:
        raise InvalidTransaction("The file has headings but no rows.")
    if len(rows) > MAX_ROWS:
        raise InvalidTransaction(f"The file has {len(rows)} rows. Import at most {MAX_ROWS} at a time.")
    return [(number, {key: cells[index] if index < len(cells) else "" for key, index in positions.items()})
            for number, cells in rows]


# Checking each row.

def _limit(value, field, size, problems):
    if len(value) > size:
        problems.append(f"{field} is longer than {size} characters.")
    return value[:size]


def _customer(values, seen, problems):
    from .customers import QID_PATTERN

    row = {}
    name = _limit(values.get("name", ""), "Name", 110, problems)
    if not name:
        problems.append("Name is missing.")
    row["name"] = name if name.startswith("TEST ONLY") else f"TEST ONLY {name}"
    row["name_ar"] = _limit(values.get("name_ar", ""), "Name in Arabic", 120, problems)
    row["contact_en"] = _limit(values.get("contact_en", ""), "Contact", 500, problems)
    row["contact_ar"] = _limit(values.get("contact_ar", ""), "Contact in Arabic", 500, problems)
    title = values.get("title", "").lower().rstrip(".")
    row["title"] = TITLES.get(title, "")
    if title and not row["title"]:
        problems.append(f"Title “{values['title']}” is not on the list.")
    kind = values.get("customer_type", "").lower() or "individual"
    if kind not in ("individual", "company", "government"):
        problems.append("Customer type must be individual, company or government.")
    row["customer_type"] = kind
    qid = re.sub(r"[\s-]", "", values.get("qid", ""))
    if qid:
        if not QID_PATTERN.fullmatch(qid):
            problems.append("QID must be 11 digits starting with 2 or 3.")
        elif qid in seen["qid"]:
            problems.append(f"QID {qid} is also on row {seen['qid'][qid]}.")
        elif SyntheticCounterparty.objects.filter(qid=qid).exists():
            problems.append(f"QID {qid} is already registered.")
    elif not (row["contact_en"] or row["contact_ar"]):
        problems.append("Give a QID, or a phone number or other contact when the customer declined the QID.")
    row["qid"] = qid or None
    register = (values.get("register_for", "").lower() or "retail").replace("project", "projects").replace("projectss", "projects")
    if register not in ("retail", "projects", "both"):
        problems.append("Registered for must be retail, projects or both.")
    row["register_for"] = register
    for field, prefix, label, wanted in (("client_number", "C", "Client number", ("retail", "both")),
                                         ("project_number", "P", "Project number", ("projects", "both"))):
        raw = values.get(field, "").upper().replace(" ", "")
        number = ""
        if raw:
            match = re.fullmatch(rf"{prefix}-?(\d{{1,6}})", raw)
            if not match:
                problems.append(f"{label} must look like {prefix}-000123.")
            else:
                number = f"{prefix}-{int(match.group(1)):06d}"
                if number in seen[field]:
                    problems.append(f"{label} {number} is also on row {seen[field][number]}.")
                elif SyntheticCounterparty.objects.filter(**{field: number}).exists():
                    problems.append(f"{label} {number} is already in use.")
                if register not in wanted:
                    problems.append(f"{label} is given but the customer is not registered for "
                                    f"{'retail' if prefix == 'C' else 'projects'}.")
        row[field] = number
    return row


def _supplier(values, seen, problems):
    row = {}
    for key, label, size in (("legal_name", "Legal name", 160), ("address", "Address", 300), ("signatory_name", "Signatory name", 120),
                             ("signatory_title", "Signatory title", 120), ("contact_email", "Email", 160),
                             ("contact_phone", "Phone", 40)):
        row[key] = _limit(values.get(key, ""), label, size, problems)
    for key, label in (("legal_name", "Legal name"), ("signatory_name", "Signatory name")):
        if not row[key]:
            problems.append(f"{label} is missing.")
    cr = values.get("cr_number", "").upper().replace(" ", "")
    if not cr:
        problems.append("CR number is missing.")
    elif not re.fullmatch(r"[A-Z0-9/-]{3,40}", cr):
        problems.append("CR number may use letters, digits, / and - only, at least 3.")
    elif cr in seen["cr"]:
        problems.append(f"CR number {cr} is also on row {seen['cr'][cr]}.")
    elif SupplierKyc.objects.filter(cr_number=cr).exists():
        problems.append(f"A supplier with CR number {cr} is already on file.")
    row["cr_number"] = cr
    expiry = values.get("cr_expiry", "")
    row["cr_expiry"] = ""
    if expiry:
        try:
            row["cr_expiry"] = datetime.date.fromisoformat(expiry[:10]).isoformat()
        except ValueError:
            day = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", expiry)
            try:
                row["cr_expiry"] = datetime.date(int(day.group(3)), int(day.group(2)), int(day.group(1))).isoformat() if day else ""
            except ValueError:
                pass
            if not row["cr_expiry"]:
                problems.append("CR expiry must be a date, for example 2027-06-30 or 30/06/2027.")
    vat = values.get("vat_registered", "").lower()
    if vat not in YES | NO:
        problems.append("VAT registered must be yes or no.")
    row["vat_registered"] = vat in YES
    row["vat_number"] = values.get("vat_number", "").upper().replace(" ", "") if row["vat_registered"] else ""
    if row["vat_registered"] and not row["vat_number"]:
        problems.append("VAT number is missing for a VAT registered supplier.")
    _limit(row["vat_number"], "VAT number", 40, problems)
    if row["contact_email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", row["contact_email"]):
        problems.append("Email does not look like an email address.")
    raw = re.sub(r"[^A-Z0-9-]", "-", (values.get("code", "") or cr).upper()).strip("-")
    code = raw if raw.startswith("TEST-SUP-") else f"TEST-SUP-{raw}"
    if len(code) > 40:
        problems.append("Supplier code is too long.")
    elif raw and code in seen["code"]:
        problems.append(f"Supplier code {code} is also on row {seen['code'][code]}.")
    elif raw and SyntheticCounterparty.objects.filter(party_type=SyntheticCounterparty.Type.SUPPLIER, code=code).exists():
        problems.append(f"Supplier code {code} is already in use.")
    row["code"] = code
    return row


def check(kind, records, day=None):
    """[{row, values, problems}] for every row, with duplicates checked inside the file and against what exists."""
    seen = {"qid": {}, "client_number": {}, "project_number": {}, "cr": {}, "code": {}, "items": {}, "barcode": {}, "sku": {}, "stock": {}, "tb": {},
            "old": {}, "asset": {}, "day": day}
    checked = []
    for number, values in records:
        problems = []
        row = {K.CUSTOMERS: _customer, K.SUPPLIERS: _supplier, K.ITEMS: go_live_items.check_row,
               K.OPENING_STOCK: go_live_balances.check_stock_row, K.TRIAL_BALANCE: go_live_balances.check_tb_row,
               K.CUSTOMER_BALANCES: lambda *args: old_balances.check_row("customer", *args),
               K.SUPPLIER_BALANCES: lambda *args: old_balances.check_row("supplier", *args),
               K.FIXED_ASSETS: go_live_assets_loans.check_asset_row, K.STAFF_LOANS: go_live_assets_loans.check_loan_row}[kind](
            values, seen, problems)
        if kind == K.ITEMS:
            go_live_items.remember(row, number, seen)
        elif kind == K.OPENING_STOCK:
            go_live_balances.remember_stock(row, number, seen)
        elif kind == K.TRIAL_BALANCE:
            go_live_balances.remember_tb(row, number, seen)
        elif kind in (K.CUSTOMER_BALANCES, K.SUPPLIER_BALANCES):
            old_balances.remember(row, number, seen)
        elif kind == K.FIXED_ASSETS:
            go_live_assets_loans.remember_asset(row, number, seen)
        elif kind == K.STAFF_LOANS:
            pass
        elif kind == K.CUSTOMERS:
            for field in ("qid", "client_number", "project_number"):
                if row[field]:
                    seen[field].setdefault(row[field], number)
        else:
            seen["cr"].setdefault(row["cr_number"], number)
            seen["code"].setdefault(row["code"], number)
        checked.append({"row": number, "values": row, "problems": problems})
    if kind == K.TRIAL_BALANCE:
        go_live_balances.finish_tb(checked, seen)
    return checked


def _summary(kind, checked):
    bad = [row for row in checked if row["problems"]]
    return {
        "kind": kind, "rows": len(checked), "good": len(checked) - len(bad), "bad": len(bad),
        "problems": [{"row": row["row"], "problems": row["problems"]} for row in bad[:SHOWN_PROBLEMS]],
        "more_problems": max(len(bad) - SHOWN_PROBLEMS, 0),
        "sample": [{"row": row["row"], **row["values"]} for row in checked[:SHOWN_ROWS]],
    }


# Screens and actions.

def _name(user):
    return (user.get_full_name() or user.username) if user else ""


def _batch_row(batch, actor):
    return {
        "id": batch.pk, "kind": batch.kind, "kind_label": batch.get_kind_display(), "status": batch.status,
        "status_label": batch.get_status_display(), "file_name": batch.file_name, "row_count": batch.row_count,
        "created": len(batch.created_ids), "prepared_by": _name(batch.prepared_by), "prepared_at": batch.prepared_at.isoformat(),
        "decided_by": _name(batch.decided_by), "decided_at": batch.decided_at.isoformat() if batch.decided_at else "",
        "decision_note": batch.decision_note, "balances_on": batch.balances_on.isoformat() if batch.balances_on else "",
        "sample": [{"row": index + 1, **row} for index, row in enumerate(batch.rows[:SHOWN_ROWS])],
        "can_approve": batch.status == S.WAITING and can_approve(actor) and actor.pk != batch.prepared_by_id,
        "can_withdraw": batch.status == S.WAITING and actor.pk == batch.prepared_by_id,
    }


def imports_overview(*, actor):
    _require_user(actor)
    batches = ImportBatch.objects.select_related("prepared_by", "decided_by")
    waiting = list(batches.filter(status=S.WAITING))
    done = list(batches.exclude(status=S.WAITING)[:RECENT])
    return {
        "kinds": [{"kind": kind, "label": K(kind).label, "needs_date": kind in BALANCE_KINDS,
                   "columns": [{"heading": heading, "required": required, "help": help_text}
                               for heading, _, required, help_text, _ in COLUMNS[kind]]} for kind in K.values],
        "waiting": [_batch_row(batch, actor) for batch in waiting],
        "recent": [_batch_row(batch, actor) for batch in done],
        "can_prepare": can_prepare(actor), "can_approve": can_approve(actor), "policy": POLICY_LABEL,
        "opening_left": go_live_balances.still_to_bring_in(),
    }


def template(*, actor, kind):
    """The Excel template: the headings and an example row on 'Data', and what goes in each column on 'How to fill it'."""
    _require_user(actor)
    kind = _kind(kind)
    from openpyxl import Workbook
    from openpyxl.styles import Font

    book = Workbook()
    sheet = book.active
    sheet.title = "Data"
    columns = COLUMNS[kind]
    sheet.append([f"{heading}*" if required else heading for heading, _, required, _, _ in columns])
    sheet.append([example for *_, example in columns])
    if kind == K.TRIAL_BALANCE:
        sheet.append(["TVL", "TEST_GRNI", "", "125000", "", "Suppliers control"])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for index, (heading, *_rest) in enumerate(columns, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = max(16, len(heading) + 4)
    sheet.freeze_panes = "A2"
    guide = book.create_sheet("How to fill it")
    guide.append(["Column", "Needed", "What to put in it"])
    for cell in guide[1]:
        cell.font = Font(bold=True)
    for heading, _, required, help_text, _ in columns:
        guide.append([heading, "Yes" if required else "No", help_text])
    guide.append([])
    guide.append(["Replace the example on row 2 with your data, one row each. Columns marked * are needed. "
                  "Keep the headings on row 1."])
    guide.column_dimensions["A"].width = 26
    guide.column_dimensions["C"].width = 90
    if kind == K.TRIAL_BALANCE:
        accounts = book.create_sheet("ERP accounts")
        accounts.append(["Account code", "Name", "How it is booked"])
        for cell in accounts[1]:
            cell.font = Font(bold=True)
        for code, label in go_live_balances.ACCOUNTS.items():
            where = go_live_balances.BROUGHT_IN_ELSEWHERE.get(code)
            accounts.append([code, label, f"Goes to opening balances until the {where} import brings it in" if where else "As given"])
        accounts.column_dimensions["A"].width = 32
        accounts.column_dimensions["B"].width = 60
        accounts.column_dimensions["C"].width = 60
    out = io.BytesIO()
    book.save(out)
    return f"tivoli-{kind}-import-template.xlsx", out.getvalue()


def _balances_on(kind, value):
    return go_live_balances.balances_day(value) if kind in BALANCE_KINDS else None


def preview(*, actor, kind, file_name, content, balances_on=None):
    """Check the file and say what is wrong with each row. Nothing is saved."""
    _require_user(actor)
    kind = _kind(kind)
    day = _balances_on(kind, balances_on)
    return _summary(kind, check(kind, _records(kind, file_name, content), day))


@transaction.atomic
def submit(*, actor, kind, file_name, content, balances_on=None):
    """Send a clean file for approval. A file with any problem is refused, with the problems."""
    _demo_user(actor)
    if not can_prepare(actor):
        raise WorkflowPermissionDenied("Only the Master Admin can send a go-live file for approval.")
    kind = _kind(kind)
    day = _balances_on(kind, balances_on)
    checked = check(kind, _records(kind, file_name, content), day)
    summary = _summary(kind, checked)
    if summary["bad"]:
        raise InvalidTransaction(f"{summary['bad']} of {summary['rows']} rows have problems. Fix them and upload the file again.")
    name = " ".join(str(file_name or "import").split())[:200]
    batch = ImportBatch.objects.create(kind=kind, file_name=name, file_sha256=hashlib.sha256(content).hexdigest(),
                                       rows=[row["values"] for row in checked], row_count=len(checked), prepared_by=actor, balances_on=day)
    return _batch_row(batch, actor)


def _bump(sequence, prefix, field, imported):
    """Make new numbers carry on after the highest number in use or being imported, so none is given twice."""
    highest = 0
    existing = SyntheticCounterparty.objects.exclude(**{f"{field}__isnull": True}).values_list(field, flat=True)
    for number in [*existing, *imported]:
        match = re.fullmatch(rf"{prefix}-(\d+)", number or "")
        if match:
            highest = max(highest, int(match.group(1)))
    if highest:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT last_value, is_called FROM {sequence}")
            last, called = cursor.fetchone()
            if highest > (last if called else last - 1):
                cursor.execute("SELECT setval(%s, %s, true)", [sequence, highest])


def _create_customers(batch):
    from .models import next_customer_number

    _bump("foundation_client_number_seq", "C", "client_number", [row["client_number"] for row in batch.rows])
    _bump("foundation_project_number_seq", "P", "project_number", [row["project_number"] for row in batch.rows])
    created = []
    for row in batch.rows:
        numbers = {}
        if row["register_for"] in ("retail", "both"):
            numbers["client_number"] = row["client_number"] or next_customer_number("client")
        if row["register_for"] in ("projects", "both"):
            numbers["project_number"] = row["project_number"] or next_customer_number("project")
        fields = {key: row[key] for key in ("name", "name_ar", "contact_en", "contact_ar", "title", "customer_type")}
        customer = SyntheticCounterparty.objects.create(
            party_type=SyntheticCounterparty.Type.CUSTOMER, code=f"TEST-CUST-{next(iter(numbers.values()))}",
            qid=row["qid"], qid_declined=row["qid"] is None, **numbers, **fields)
        created.append(customer.pk)
    return created


def _create_suppliers(batch, actor):
    now = timezone.now()
    created = []
    for row in batch.rows:
        supplier = SyntheticCounterparty.objects.create(party_type=SyntheticCounterparty.Type.SUPPLIER, code=row["code"],
                                                        name=f"TEST ONLY {row['legal_name']}"[:120])
        kyc = SupplierKyc.objects.create(
            supplier=supplier, legal_name=row["legal_name"], address=row["address"], cr_number=row["cr_number"],
            cr_expiry=datetime.date.fromisoformat(row["cr_expiry"]) if row["cr_expiry"] else None,
            vat_registered=row["vat_registered"], vat_number=row["vat_number"], signatory_name=row["signatory_name"],
            signatory_title=row["signatory_title"], contact_email=row["contact_email"], contact_phone=row["contact_phone"],
            status=SupplierKyc.Status.APPROVED, opened_by=batch.prepared_by, opened_at=batch.prepared_at, submitted_at=batch.prepared_at,
            decided_by=actor, decided_at=now, documents_due=timezone.localdate() + datetime.timedelta(days=DOCUMENTS_WITHIN_DAYS),
            decision_note=f"Imported at go-live (import {batch.pk}, {batch.file_name})"[:500])
        note = f"Go-live import {batch.pk}"
        SupplierKycEvent.objects.create(kyc=kyc, actor=batch.prepared_by, event="imported", note=note, occurred_at=batch.prepared_at)
        SupplierKycEvent.objects.create(kyc=kyc, actor=actor, event="approved", note=note, occurred_at=now)
        created.append(supplier.pk)
    return created


def _again(batch):
    """Check the saved rows again against what exists now."""
    records = []
    for index, row in enumerate(batch.rows, start=1):
        values = dict(row)
        if batch.kind == K.CUSTOMERS:
            values["name"] = row["name"].removeprefix("TEST ONLY ")
            values["qid"] = row["qid"] or ""
        elif batch.kind == K.SUPPLIERS:
            values["vat_registered"] = "yes" if row["vat_registered"] else "no"
        records.append((index, values))
    return [row for row in check(batch.kind, records, batch.balances_on) if row["problems"]]


@transaction.atomic
def decide(*, actor, batch_id, action, note=""):
    _demo_user(actor)
    batch = (ImportBatch.objects.select_for_update().filter(pk=batch_id if type(batch_id) is int else None)
             .select_related("prepared_by").first())
    if batch is None:
        raise InvalidTransaction("That import was not found.")
    if batch.status != S.WAITING:
        raise InvalidTransaction("This import is already decided.")
    note = " ".join(note.split())[:300] if isinstance(note, str) else ""
    if action == "withdraw":
        if actor.pk != batch.prepared_by_id:
            raise WorkflowPermissionDenied("Only the person who sent the file can withdraw it.")
        batch.status = S.WITHDRAWN
    elif action in ("approve", "reject"):
        if not can_approve(actor):
            raise WorkflowPermissionDenied("You need 'Approve go-live imports' to approve or reject a file.")
        if actor.pk == batch.prepared_by_id:
            raise WorkflowPermissionDenied("You sent this file, so someone else must approve it.")
        if action == "reject":
            if not note:
                raise InvalidTransaction("Give a reason for the rejection.")
            batch.status = S.REJECTED
        else:
            problems = _again(batch)
            if problems:
                first = problems[0]
                raise InvalidTransaction(f"{len(problems)} rows no longer pass the checks, for example row {first['row']}: "
                                         f"{first['problems'][0]} Reject it and upload a corrected file.")
            batch.created_ids = {K.CUSTOMERS: lambda: _create_customers(batch), K.SUPPLIERS: lambda: _create_suppliers(batch, actor),
                                 K.ITEMS: lambda: go_live_items.create(batch, actor),
                                 K.OPENING_STOCK: lambda: go_live_balances.create_stock(batch, actor),
                                 K.TRIAL_BALANCE: lambda: go_live_balances.create_trial_balance(batch, actor),
                                 K.CUSTOMER_BALANCES: lambda: old_balances.create("customer", batch, actor),
                                 K.SUPPLIER_BALANCES: lambda: old_balances.create("supplier", batch, actor),
                                 K.FIXED_ASSETS: lambda: go_live_assets_loans.create_assets(batch, actor),
                                 K.STAFF_LOANS: lambda: go_live_assets_loans.create_loans(batch, actor)}[batch.kind]()
            batch.status = S.IMPORTED
    else:
        raise InvalidTransaction("Choose approve, reject or withdraw.")
    batch.decided_by, batch.decided_at, batch.decision_note = actor, timezone.now(), note
    batch.save(update_fields=["status", "decided_by", "decided_at", "decision_note", "created_ids"])
    return _batch_row(batch, actor)
