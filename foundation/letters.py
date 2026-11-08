"""Staff letters: salary certificates and other letters about someone's employment, filled in and signed.

Tivoli's ask (from Sam, 2 Nov 2026): staff download letters about their employment, filled
from their records with a preset signature: a bank letter (account or credit), a loan
certificate, to whom it may concern, an embassy letter, a letter for family residence, a
POLO / OWWA letter and a salary certificate for a bank in their home country. The letters show
the name, QID number, passport number, joining date, current job title and current pay, and
the IBAN when a bank asks for it.

Provisional rules (synthetic demo only):

- Anyone linked to a staff record asks for their own letters. HR can ask on someone's behalf.
- HR sets up each unit's letters once: the company name and address at the top, and the
  person who signs, with their signature as a picture.
- A letter is ready to download as soon as it is asked for (Sam, 2 Nov 2026), as long as the
  record has everything the letter needs. If something is missing it waits for HR instead.
  HR can switch on "HR checks each letter first" for a unit if that is ever needed.
- Bank, loan and home-bank letters say plainly that the company carries no liability for any
  loan or facility (Sam, 2 Nov 2026). Every letter says it is issued without responsibility.
- Issuing needs both "Add and change staff records" and "See salaries and payroll", and nobody
  issues their own letter.
- The details are copied into the letter when it is issued, with a number such as
  HRL-TVL-2026-0001, so a later pay change does not change a letter already given. Each
  download is counted.
"""

import base64
import binascii
import textwrap
import zlib
from decimal import Decimal
from io import BytesIO

from django.db import transaction
from django.utils import timezone

from .cash_accounts import _text
from .leave import _me, _require_demo_user
from .models import Employee, EmployeeDocument, LetterSettings, StaffLetter
from .pay_records import PARTS, can_view_pay, current_pay, total
from .purchasing_services import WorkflowPermissionDenied
from .reporting_exports import _pdf_escape, _pdf_text
from .staff_records import _live_documents, _name, can_manage
from .transaction_services import InvalidTransaction

K = StaffLetter.Kind
S = StaffLetter.Status
MAX_SIGNATURE_BYTES = 300_000
PART_LABELS = {"basic": "Basic salary", "housing": "Housing allowance", "transport": "Transport allowance",
               "mobile": "Mobile allowance", "other": "Other allowance"}
KINDS = {
    K.BANK: {"addressee": "", "needs_addressee": True, "iban": True,
             "subject": "Salary certificate", "purpose": "opening a bank account or applying for credit"},
    K.LOAN: {"addressee": "", "needs_addressee": True, "iban": True,
             "subject": "Salary certificate for financing", "purpose": "applying for financing"},
    K.TO_WHOM: {"addressee": "To whom it may concern", "needs_addressee": False, "iban": False,
                "subject": "Certificate of employment", "purpose": ""},
    K.EMBASSY: {"addressee": "", "needs_addressee": True, "iban": False,
                "subject": "Certificate of employment", "purpose": "submission to the embassy"},
    K.FAMILY: {"addressee": "Ministry of Interior, State of Qatar", "needs_addressee": False, "iban": False,
               "subject": "Salary certificate for family residence", "purpose": "a family residence application"},
    K.POLO: {"addressee": "Philippine Overseas Labor Office (POLO) / OWWA, Doha", "needs_addressee": False, "iban": False,
             "subject": "Certificate of employment", "purpose": "submission to POLO / OWWA"},
    K.HOME_BANK: {"addressee": "", "needs_addressee": True, "iban": True,
                  "subject": "Salary certificate", "purpose": "the bank in the employee's home country"},
}
CREDIT_KINDS = (K.BANK, K.LOAN, K.HOME_BANK)
POLICY_LABEL = ("TEST ONLY provisional rules: letters are filled from the staff record and current pay and signed with the "
                "unit's preset signature; ready to download at once unless the record is missing something; the details are fixed "
                "when the letter is issued.")


def can_issue(user, business_unit):
    return can_manage(user, business_unit) and can_view_pay(user, business_unit)


def _money(value):
    return f"{value.quantize(Decimal('0.01')):,.2f}"


def _settings_row(row, with_signature=False):
    if row is None:
        return None
    data = {"company_name": row.company_name, "address": row.address, "signatory_name": row.signatory_name,
            "signatory_title": row.signatory_title, "has_signature": bool(row.signature_png),
            "hr_checks_first": row.hr_checks_first, "set_by": _name(row.set_by), "set_at": row.set_at.isoformat()}
    if with_signature and row.signature_png:
        data["signature"] = "data:image/png;base64," + base64.b64encode(bytes(row.signature_png)).decode()
    return data


def _document(employee, kind):
    found = [document for document in _live_documents(employee) if document.kind == kind]
    return max(found, key=lambda document: document.added_at) if found else None


def missing(employee, letter_kind=None, include_iban=False, include_salary=True, on=None):
    """What the record lacks for this letter, in words."""
    gaps = []
    if _document(employee, EmployeeDocument.Kind.QID) is None:
        gaps.append("QID")
    if _document(employee, EmployeeDocument.Kind.PASSPORT) is None:
        gaps.append("passport")
    pay = current_pay(employee, on)
    if include_salary and pay is None:
        gaps.append("current pay")
    if include_iban and (pay is None or not pay.iban):
        gaps.append("bank account (IBAN)")
    return gaps


def _content(letter, settings, today):
    """Everything printed on the letter, fixed at the moment it is issued."""
    employee = letter.employee
    kind = KINDS[letter.kind]
    qid = _document(employee, EmployeeDocument.Kind.QID)
    passport = _document(employee, EmployeeDocument.Kind.PASSPORT)
    pay = current_pay(employee, today)
    since = employee.joined_on.strftime("%d %B %Y")
    opening = (f"This is to certify that {employee.full_name}, a {employee.nationality} national holding Qatar ID "
               f"{qid.number} and passport {passport.number}, has been working with {settings.company_name} since "
               f"{since}, and currently holds the position of {employee.job_title}.")
    paragraphs = [opening]
    details = [["Name", employee.full_name], ["Employee code", employee.staff_number], ["Nationality", employee.nationality],
               ["QID number", qid.number], ["Passport number", passport.number], ["Joining date", since],
               ["Designation", employee.job_title]]
    salary = []
    if letter.include_salary:
        paragraphs.append(f"The employee's current gross monthly salary is QAR {_money(total(pay))}, made up as follows.")
        for part in PARTS:
            amount = getattr(pay, part)
            if amount:
                label = f"Other allowance ({pay.other_label})" if part == "other" and pay.other_label else PART_LABELS[part]
                salary.append([label, _money(amount)])
        salary.append(["Total monthly salary", _money(total(pay))])
    if letter.include_iban:
        paragraphs.append(f"The salary is paid monthly into the employee's account with {pay.bank_name or 'the bank below'}.")
        details.append(["Bank", pay.bank_name or "-"])
        details.append(["IBAN", pay.iban])
    if letter.kind in CREDIT_KINDS:
        paragraphs.append(f"This certificate is not a guarantee. {settings.company_name} accepts no liability or obligation for any "
                          "loan, credit card, financing or other facility given to the employee, and does not undertake to pay, "
                          "hold back or transfer any amount on the employee's behalf.")
    purpose = letter.purpose or kind["purpose"]
    paragraphs.append(f"This letter is issued at the employee's request{f' for {purpose}' if purpose else ''}, without any "
                      f"financial or other responsibility on the part of {settings.company_name}.")
    return {
        "company_name": settings.company_name, "address": settings.address, "date": today.strftime("%d %B %Y"),
        "addressee": letter.addressee or kind["addressee"], "subject": kind["subject"], "paragraphs": paragraphs,
        "details": details, "salary": salary, "signatory_name": settings.signatory_name,
        "signatory_title": settings.signatory_title,
        "signature": base64.b64encode(bytes(settings.signature_png)).decode() if settings.signature_png else "",
    }


def _next_number(business_unit, today):
    prefix = f"HRL-{business_unit.code}-{today.year}-"
    last = (StaffLetter.objects.filter(number__startswith=prefix).select_for_update()
            .order_by("-number").values_list("number", flat=True).first())
    return f"{prefix}{int(last[len(prefix):]) + 1 if last else 1:04d}"


def _issue(letter, actor, today):
    settings = LetterSettings.objects.filter(business_unit=letter.employee.business_unit).first()
    if settings is None:
        raise InvalidTransaction("HR has not set up the letters for this unit yet: the company lines and who signs.")
    gaps = missing(letter.employee, letter.kind, letter.include_iban, letter.include_salary, today)
    if gaps:
        raise InvalidTransaction(f"{letter.employee.full_name}'s record has no {' or '.join(gaps)}. Add it first.")
    letter.content = _content(letter, settings, today)
    letter.number = _next_number(letter.employee.business_unit, today)
    letter.status = S.ISSUED
    letter.decided_by = actor
    letter.decided_at = timezone.now()
    letter.save(update_fields=["content", "number", "status", "decided_by", "decided_at"])


def _row(letter, actor):
    employee = letter.employee
    hr = can_issue(actor, employee.business_unit)
    mine = employee.user_id == actor.pk
    return {
        "id": letter.pk, "employee": {"id": employee.pk, "name": employee.full_name, "staff_number": employee.staff_number},
        "kind": letter.kind, "kind_label": letter.get_kind_display(), "addressee": letter.addressee or KINDS[letter.kind]["addressee"],
        "purpose": letter.purpose, "include_salary": letter.include_salary, "include_iban": letter.include_iban,
        "status": letter.status, "status_label": letter.get_status_display(), "number": letter.number,
        "requested_by": _name(letter.requested_by), "requested_at": letter.requested_at.isoformat(),
        "decided_by": (_name(letter.decided_by) or "Issued automatically") if letter.status == S.ISSUED else _name(letter.decided_by),
        "decided_at": letter.decided_at.isoformat() if letter.decided_at else None, "rejection_reason": letter.rejection_reason,
        "downloads": letter.downloads,
        "missing": (missing(employee, letter.kind, letter.include_iban, letter.include_salary)
                    if letter.status == S.REQUESTED and hr else []),
        "can_decide": letter.status == S.REQUESTED and hr and not mine,
        "can_download": letter.status == S.ISSUED and (mine or hr),
    }


def _letters():
    return StaffLetter.objects.select_related("employee__business_unit", "requested_by", "decided_by")


def letters_overview(*, actor, business_unit=None):
    _require_demo_user(actor)
    me = _me(actor)
    hr = business_unit is not None and can_issue(actor, business_unit)
    if me is None and not hr:
        raise WorkflowPermissionDenied("Your ERP sign-in is not linked to a staff record, so there are no letters to ask for. "
                                       "Ask HR to link it.")
    my_unit = me.business_unit if me else None
    setup = LetterSettings.objects.filter(business_unit=business_unit if hr else my_unit).select_related("set_by").first()
    people = (Employee.objects.filter(business_unit=business_unit).exclude(status=Employee.Status.LEFT).order_by("full_name")
              if hr else Employee.objects.none())
    unit_letters = _letters().filter(employee__business_unit=business_unit) if hr else StaffLetter.objects.none()
    return {
        "unit": {"id": business_unit.pk, "name": business_unit.name} if hr else None,
        "me": ({"id": me.pk, "name": me.full_name, "staff_number": me.staff_number, "job_title": me.job_title,
                "missing": missing(me, include_salary=False)} if me else None),
        "mine": [_row(letter, actor) for letter in _letters().filter(employee=me)[:50]] if me else [],
        "to_issue": [_row(letter, actor) for letter in unit_letters.filter(status=S.REQUESTED).exclude(employee__user=actor)],
        "recent": [_row(letter, actor) for letter in unit_letters.exclude(status=S.REQUESTED)[:50]],
        "people": [{"id": person.pk, "name": person.full_name, "staff_number": person.staff_number} for person in people],
        "kinds": [{"value": value, "label": label, "needs_addressee": KINDS[value]["needs_addressee"],
                   "addressee": KINDS[value]["addressee"], "iban": KINDS[value]["iban"]} for value, label in K.choices],
        "settings": _settings_row(setup, with_signature=hr),
        "can_issue": hr, "policy": POLICY_LABEL,
    }


@transaction.atomic
def request_letter(*, actor, business_unit=None, data):
    _require_demo_user(actor)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the letter details.")
    me = _me(actor)
    employee_id = data.get("employee_id")
    if employee_id in (None, "") or (me is not None and employee_id == me.pk):
        employee = me
        if employee is None:
            raise WorkflowPermissionDenied("Your ERP sign-in is not linked to a staff record. Ask HR to link it.")
    else:
        employee = Employee.objects.filter(pk=employee_id if type(employee_id) is int else None).select_related("business_unit").first()
        if employee is None or not can_issue(actor, employee.business_unit):
            raise WorkflowPermissionDenied("Only HR can ask for a letter on someone else's behalf.")
    if employee.status == Employee.Status.LEFT:
        raise InvalidTransaction(f"{employee.full_name} has left. Letters for people who have left are written by HR outside the ERP.")
    kind = data.get("kind")
    if kind not in K.values:
        raise InvalidTransaction("Choose which letter you need.")
    addressee = _text(data.get("addressee"), "addressee", 120)
    if KINDS[kind]["needs_addressee"] and not addressee:
        raise InvalidTransaction({K.EMBASSY: "Give the embassy the letter is for, for example Embassy of India, Doha."}.get(
            kind, "Give the bank the letter is for, for example QNB, Doha."))
    include_iban = data.get("include_iban") is True
    if include_iban and not KINDS[kind]["iban"]:
        raise InvalidTransaction("The bank account is shown only on bank letters.")
    letter = StaffLetter.objects.create(
        employee=employee, kind=kind, addressee=addressee, purpose=_text(data.get("purpose"), "purpose", 200),
        include_salary=data.get("include_salary") is not False, include_iban=include_iban, requested_by=actor,
    )
    today = timezone.localdate()
    setup = LetterSettings.objects.filter(business_unit=employee.business_unit).first()
    note = ""
    hr_now = can_issue(actor, employee.business_unit) and employee.user_id != actor.pk
    if hr_now or setup is None or not setup.hr_checks_first:
        gaps = missing(employee, kind, include_iban, letter.include_salary, today)
        if setup is not None and not gaps:
            _issue(letter, None if not hr_now else actor, today)
        else:
            note = ("HR has not set up the letters yet, so it waits for HR." if setup is None
                    else f"The record has no {' or '.join(gaps)}, so it waits for HR to add it.")
    return {**_row(letter, actor), "note": note}


@transaction.atomic
def decide_letter(*, actor, letter_id, action, reason=""):
    _require_demo_user(actor)
    letter = _letters().select_for_update(of=("self",)).filter(pk=letter_id if type(letter_id) is int else None).first()
    if letter is None or not can_issue(actor, letter.employee.business_unit):
        raise WorkflowPermissionDenied("Only HR with access to salaries can issue letters in this unit.")
    if letter.status != S.REQUESTED:
        raise InvalidTransaction("This letter has already been dealt with.")
    if letter.employee.user_id == actor.pk:
        raise WorkflowPermissionDenied("Someone else in HR issues your own letters.")
    if action == "issue":
        _issue(letter, actor, timezone.localdate())
    elif action == "reject":
        reason = _text(reason, "reason", 200)
        if not reason:
            raise InvalidTransaction("Say why the letter is not issued, so the employee knows what to do.")
        letter.status, letter.rejection_reason = S.REJECTED, reason
        letter.decided_by, letter.decided_at = actor, timezone.now()
        letter.save(update_fields=["status", "rejection_reason", "decided_by", "decided_at"])
    else:
        raise InvalidTransaction("Choose issue or reject.")
    return _row(letter, actor)


def _signature(value):
    if value in (None, ""):
        return b""
    if not isinstance(value, str):
        raise InvalidTransaction("Send the signature as a PNG picture.")
    head, _, body = value.partition(",")
    if head != "data:image/png;base64" or not body:
        raise InvalidTransaction("The signature must be a PNG picture.")
    try:
        raw = base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError) as error:
        raise InvalidTransaction("The signature picture could not be read.") from error
    if len(raw) > MAX_SIGNATURE_BYTES:
        raise InvalidTransaction("The signature picture is too large. Use one under 300 KB.")
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format != "PNG":
                raise InvalidTransaction("The signature must be a PNG picture.")
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise InvalidTransaction("The signature picture could not be read.") from error
    return raw


@transaction.atomic
def set_letter_settings(*, actor, business_unit, data):
    _require_demo_user(actor)
    if business_unit is None or not can_manage(actor, business_unit):
        raise WorkflowPermissionDenied("Only HR (Add and change staff records) sets up the letters.")
    from .access_levels import require_master_admin
    require_master_admin(actor, "change the letter templates and signatures")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the letter settings.")
    company = _text(data.get("company_name"), "company name", 120)
    signatory = _text(data.get("signatory_name"), "name of the person who signs", 120)
    title = _text(data.get("signatory_title"), "job title of the person who signs", 80)
    if not company or not signatory or not title:
        raise InvalidTransaction("Give the company name, and the name and job title of the person who signs.")
    row = LetterSettings.objects.select_for_update().filter(business_unit=business_unit).first()
    signature = _signature(data.get("signature")) if "signature" in data else (bytes(row.signature_png) if row else b"")
    values = {"company_name": company, "address": _text(data.get("address"), "address", 200), "signatory_name": signatory,
              "signatory_title": title, "signature_png": signature, "hr_checks_first": data.get("hr_checks_first") is True,
              "set_by": actor, "set_at": timezone.now()}
    row, _ = LetterSettings.objects.update_or_create(business_unit=business_unit, defaults=values)
    return _settings_row(row, with_signature=True)


@transaction.atomic
def letter_pdf(*, actor, letter_id):
    _require_demo_user(actor)
    letter = _letters().select_for_update(of=("self",)).filter(pk=letter_id if type(letter_id) is int else None).first()
    if letter is None or not (letter.employee.user_id == actor.pk or can_issue(actor, letter.employee.business_unit)):
        raise WorkflowPermissionDenied("You can download only your own letters.")
    if letter.status != S.ISSUED:
        raise InvalidTransaction("This letter is not ready to download yet.")
    letter.downloads += 1
    letter.last_downloaded_at = timezone.now()
    letter.save(update_fields=["downloads", "last_downloaded_at"])
    return f"{letter.number}.pdf", render_letter(letter.number, letter.content)


# The letter itself: one A4 page, written directly as PDF with the built-in Helvetica fonts.
WIDTH, HEIGHT, MARGIN = 595, 842, 64


def _wrap(text, size, width):
    return textwrap.wrap(_pdf_text(text), max(int(width / (size * 0.5)), 10)) or [""]


def _image(png):
    """The signature as a PDF image: RGB pixels on white, compressed."""
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        image = image.convert("RGBA")
        flat = Image.new("RGB", image.size, (255, 255, 255))
        flat.paste(image, mask=image.split()[3])
    data = zlib.compress(flat.tobytes())
    head = (f"<< /Type /XObject /Subtype /Image /Width {flat.width} /Height {flat.height} /ColorSpace /DeviceRGB "
            f"/BitsPerComponent 8 /Filter /FlateDecode /Length {len(data)} >>\nstream\n").encode()
    return head + data + b"\nendstream", flat.width, flat.height


def render_letter(number, content):
    ops = []

    def text(x, y, value, size=10.5, bold=False, right=False):
        value = _pdf_text(value)
        if right:
            x -= len(value) * size * 0.5
        ops.append(f"BT /{'F2' if bold else 'F1'} {size} Tf {x:.1f} {y:.1f} Td ({_pdf_escape(value)}) Tj ET")

    right_edge = WIDTH - MARGIN
    y = HEIGHT - MARGIN
    text(MARGIN, y, content["company_name"], size=15, bold=True)
    y -= 15
    if content.get("address"):
        text(MARGIN, y, content["address"], size=9)
        y -= 10
    ops.append(f"0.8 w {MARGIN} {y:.1f} m {right_edge} {y:.1f} l S")
    y -= 26
    text(MARGIN, y, f"Ref: {number}", size=10)
    text(right_edge, y, f"Date: {content['date']}", size=10, right=True)
    y -= 30
    text(MARGIN, y, content["addressee"], bold=True)
    y -= 26
    text(MARGIN, y, f"Subject: {content['subject']}", bold=True)
    y -= 24
    usable = WIDTH - 2 * MARGIN

    def paragraph(value):
        nonlocal y
        for line in _wrap(value, 10.5, usable):
            text(MARGIN, y, line)
            y -= 15
        y -= 7

    paragraphs = list(content["paragraphs"])
    paragraph(paragraphs.pop(0))

    def table(rows, total_last=False):
        nonlocal y
        for index, (label, value) in enumerate(rows):
            last = total_last and index == len(rows) - 1
            if last:
                ops.append(f"0.5 w {MARGIN + 160} {y + 10:.1f} m {MARGIN + 330} {y + 10:.1f} l S")
            text(MARGIN + 12, y, label, bold=last)
            text(MARGIN + 160, y, value if not total_last else f"QAR {value}", bold=last)
            y -= 15
        y -= 10

    table(content["details"])
    if content["salary"]:
        paragraph(paragraphs.pop(0))
        table(content["salary"], total_last=True)
    for value in paragraphs:
        paragraph(value)
    y -= 6
    text(MARGIN, y, "Yours faithfully,")
    y -= 12
    objects_extra = []
    if content.get("signature"):
        image, width, height = _image(base64.b64decode(content["signature"]))
        scale = min(150 / width, 55 / height)
        drawn_w, drawn_h = width * scale, height * scale
        ops.append(f"q {drawn_w:.1f} 0 0 {drawn_h:.1f} {MARGIN:.1f} {y - drawn_h:.1f} cm /Sig Do Q")
        objects_extra.append(image)
        y -= drawn_h + 14
    else:
        y -= 50
    text(MARGIN, y, content["signatory_name"], bold=True)
    y -= 14
    text(MARGIN, y, content["signatory_title"])
    y -= 14
    text(MARGIN, y, content["company_name"])
    text(MARGIN, 40, f"{number} - TEST ONLY synthetic demo letter, not valid for any real use.", size=7)

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [5 0 R] /Count 1 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    stream = "\n".join(ops).encode("latin-1")
    xobject = " /XObject << /Sig 7 0 R >>" if objects_extra else ""
    objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {WIDTH} {HEIGHT}] "
                   f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >>{xobject} >> /Contents 6 0 R >>".encode())
    objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.extend(objects_extra)
    objects.append(f"<< /Title ({_pdf_escape(_pdf_text(content['subject']))}) /Producer (Tivoli ERP synthetic letter) >>".encode("latin-1"))
    out = BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {len(objects)} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()
