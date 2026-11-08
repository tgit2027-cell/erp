"""Staff records: who works where, in which job, on what contract, and their identity documents.

Provisional rules (synthetic demo only, waiting for HR's approval):

- Staff records are private. Seeing them needs the "see staff records"
  permission for the unit the person works in, and adding or changing them
  needs "add and change staff records". Other permissions, even the general
  ledger or user administration, do not open them.
- Pay is not kept here. It belongs to payroll, which waits for Tivoli's approved
  pay rules and has its own permission.
- Every change is recorded with who made it and what changed. Records are never
  deleted: someone who leaves keeps their record, marked as left.
- Warnings: documents and fixed-term contracts that end within 60 days, and
  probation that ends within 30 days. Anything already past is shown as expired.
- A QID number has 11 digits and starts with 2 or 3.
"""

import datetime
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .access import active_grants
from .cash_accounts import _text
from .general_ledger import _day
from .models import DemoUserMarker, Employee, EmployeeDocument, EmployeeEvent, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction, _require_demo

VIEW = UserActionGrant.Action.VIEW_STAFF
MANAGE = UserActionGrant.Action.MANAGE_STAFF
S = Employee.Status
DOCUMENT_WARNING_DAYS = 60
CONTRACT_WARNING_DAYS = 60
PROBATION_WARNING_DAYS = 30
POLICY_LABEL = ("TEST ONLY provisional rules: staff records need their own permissions; no pay is kept here; every change "
                "is recorded; warnings 60 days before documents and contracts end and 30 days before probation ends.")
FIELDS = {
    "full_name": "name", "arabic_name": "Arabic name", "job_title": "job title", "department": "department",
    "grade": "grade", "manager": "manager", "user": "ERP sign-in", "nationality": "nationality",
    "date_of_birth": "date of birth", "work_email": "work email", "mobile": "mobile", "joined_on": "joining date",
    "contract": "contract", "contract_end": "contract end", "probation_end": "probation end",
}


def _granted(user, business_unit, action):
    return active_grants(user, action).filter(business_unit=business_unit).exists()


def can_manage(user, business_unit):
    return _granted(user, business_unit, MANAGE)


def can_view(user, business_unit):
    return can_manage(user, business_unit) or _granted(user, business_unit, VIEW)


def _require_user(user, business_unit):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Staff records run only in the synthetic demo environment.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can use staff records.")
    _require_demo(business_unit, business_unit.legal_entity)
    if not can_view(user, business_unit):
        raise WorkflowPermissionDenied("You need permission to see staff records in this business unit.")


def _require_manager(user, business_unit):
    _require_user(user, business_unit)
    if not can_manage(user, business_unit):
        raise WorkflowPermissionDenied("You need the 'add and change staff records' permission for this unit.")


def _iso(day):
    return day.isoformat() if day else None


def _name(user):
    return (user.get_full_name() or user.username) if user else ""


def _warnings(employee, documents, today):
    """What ends soon or has already ended, soonest first."""
    rows = []

    def add(what, day, window):
        if day is not None and day <= today + datetime.timedelta(days=window):
            rows.append({"what": what, "date": day.isoformat(), "days": (day - today).days, "expired": day < today})

    for document in documents:
        add(f"{document.get_kind_display()} {document.number}", document.expires_on, DOCUMENT_WARNING_DAYS)
    if employee.status == S.ACTIVE:
        add("Fixed-term contract", employee.contract_end, CONTRACT_WARNING_DAYS)
        if employee.probation_end and employee.probation_end >= today:
            add("Probation", employee.probation_end, PROBATION_WARNING_DAYS)
    return sorted(rows, key=lambda row: row["date"])


def _live_documents(employee):
    return [document for document in employee.documents.all() if document.removed_at is None]


def _row(employee, today, *, full=False, actor=None):
    documents = _live_documents(employee)
    row = {
        "id": employee.pk, "staff_number": employee.staff_number, "full_name": employee.full_name,
        "arabic_name": employee.arabic_name, "job_title": employee.job_title, "department": employee.department,
        "grade": employee.grade, "nationality": employee.nationality,
        "manager": {"id": employee.manager_id, "name": employee.manager.full_name} if employee.manager_id else None,
        "joined_on": _iso(employee.joined_on), "contract": employee.contract, "contract_label": employee.get_contract_display(),
        "contract_end": _iso(employee.contract_end), "probation_end": _iso(employee.probation_end),
        "status": employee.status, "status_label": employee.get_status_display(), "last_day": _iso(employee.last_day),
        "warnings": _warnings(employee, documents, today) if employee.status != S.LEFT else [],
    }
    if full:
        row |= {
            "date_of_birth": _iso(employee.date_of_birth), "work_email": employee.work_email, "mobile": employee.mobile,
            "leaving_reason": employee.leaving_reason,
            "user": {"id": employee.user_id, "username": employee.user.username} if employee.user_id else None,
            "documents": [{"id": document.pk, "kind": document.kind, "kind_label": document.get_kind_display(),
                           "number": document.number, "issued_by": document.issued_by, "expires_on": _iso(document.expires_on),
                           "note": document.note, "added_by": _name(document.added_by)} for document in documents],
            "reports": [{"id": person.pk, "name": person.full_name, "job_title": person.job_title}
                        for person in employee.reports.exclude(status=S.LEFT).order_by("full_name")],
            "history": [{"at": event.at.isoformat(), "by": _name(event.actor), "change": event.change, "what": event.what}
                        for event in employee.events.select_related("actor")[:40]],
            "created_by": _name(employee.created_by), "created_at": employee.created_at.isoformat(),
            "can_edit": can_manage(actor, employee.business_unit) if actor else False,
            "portal_reads": _portal_reads(employee),
            "portal_enabled": _portal_enabled(),
        }
    return row


def _staff(business_unit):
    return (Employee.objects.filter(business_unit=business_unit)
            .select_related("manager", "user", "created_by", "business_unit").prefetch_related("documents"))


def staff_overview(*, actor, business_unit, status="current", search="", department=""):
    _require_user(actor, business_unit)
    today = timezone.localdate()
    close_finished_notices(today)
    everyone = list(_staff(business_unit))
    current = [person for person in everyone if person.status != S.LEFT]
    shown = {"current": current, "left": [person for person in everyone if person.status == S.LEFT]}.get(status, everyone)
    if department:
        shown = [person for person in shown if person.department == department]
    term = " ".join((search or "").split()).lower()[:80]
    if term:
        shown = [person for person in shown
                 if term in f"{person.full_name} {person.arabic_name} {person.staff_number} {person.job_title}".lower()
                 or any(term in document.number.lower() for document in _live_documents(person))]
    rows = [_row(person, today) for person in shown[:500]]
    warnings = [{"employee": {"id": person.pk, "name": person.full_name}, **warning}
                for person in current for warning in _warnings(person, _live_documents(person), today)]
    manager = can_manage(actor, business_unit)
    linked = Employee.objects.filter(user__isnull=False).values_list("user_id", flat=True)
    users = (get_user_model().objects.filter(demo_marker__is_synthetic=True, is_active=True).exclude(pk__in=linked)
             .order_by("username")) if manager else []
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "staff": rows,
        "totals": {"working": sum(1 for person in current if person.status == S.ACTIVE),
                   "notice": sum(1 for person in current if person.status == S.NOTICE),
                   "expiring": sum(1 for warning in warnings if not warning["expired"]),
                   "expired": sum(1 for warning in warnings if warning["expired"])},
        "warnings": sorted(warnings, key=lambda row: row["date"])[:50],
        "departments": sorted({person.department for person in everyone}),
        "managers": [{"id": person.pk, "name": person.full_name, "job_title": person.job_title}
                     for person in Employee.objects.filter(business_unit__legal_entity=business_unit.legal_entity)
                     .exclude(status=S.LEFT).order_by("full_name")] if manager else [],
        "users": [{"id": user.pk, "username": user.username, "name": _name(user)} for user in users],
        "document_kinds": [{"value": value, "label": label} for value, label in EmployeeDocument.Kind.choices],
        "can_manage": manager, "policy": POLICY_LABEL,
    }


def _load(business_unit, employee_id, *, lock=False):
    rows = _staff(business_unit).filter(pk=employee_id if type(employee_id) is int else None)
    if lock:
        rows = rows.select_for_update(of=("self",))
    employee = rows.first()
    if employee is None:
        raise InvalidTransaction("That person was not found in this business unit.")
    return employee


def _portal_enabled():
    from .portal import is_enabled
    return is_enabled()


def _portal_reads(employee):
    from .portal import reads_for_employee
    return reads_for_employee(employee)


def staff_detail(*, actor, business_unit, employee_id):
    _require_user(actor, business_unit)
    close_finished_notices()
    return _row(_load(business_unit, employee_id), timezone.localdate(), full=True, actor=actor)


def _optional_day(value, field):
    return _day(value, None, field) if value not in (None, "") else None


def _clean(business_unit, data, employee=None):
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the person's details.")
    today = timezone.localdate()
    values = {
        "full_name": _text(data.get("full_name"), "name", 120, required=True),
        "arabic_name": _text(data.get("arabic_name"), "Arabic name", 120),
        "job_title": _text(data.get("job_title"), "job title", 80, required=True),
        "department": _text(data.get("department"), "department", 60, required=True),
        "grade": _text(data.get("grade"), "grade", 20),
        "nationality": _text(data.get("nationality"), "nationality", 60, required=True),
        "work_email": _text(data.get("work_email"), "work email", 254).lower(),
        "mobile": _text(data.get("mobile"), "mobile", 20),
        "joined_on": _day(data.get("joined_on"), None, "joining date") if data.get("joined_on") else None,
        "date_of_birth": _optional_day(data.get("date_of_birth"), "date of birth"),
        "contract": data.get("contract") or Employee.Contract.UNLIMITED,
        "contract_end": _optional_day(data.get("contract_end"), "contract end"),
        "probation_end": _optional_day(data.get("probation_end"), "probation end"),
    }
    if values["joined_on"] is None:
        raise InvalidTransaction("Give the joining date.")
    if values["work_email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", values["work_email"]):
        raise InvalidTransaction("The work email does not look right.")
    if values["mobile"] and not re.fullmatch(r"\+?[0-9 ]{7,20}", values["mobile"]):
        raise InvalidTransaction("Give the mobile as digits, optionally starting with +.")
    if values["date_of_birth"] and not (today.replace(year=today.year - 80) <= values["date_of_birth"]
                                        <= today.replace(year=today.year - 16)):
        raise InvalidTransaction("The date of birth must make the person between 16 and 80 years old.")
    if values["contract"] not in Employee.Contract.values:
        raise InvalidTransaction("Choose open-ended or fixed-term.")
    if values["contract"] == Employee.Contract.LIMITED:
        if values["contract_end"] is None or values["contract_end"] <= values["joined_on"]:
            raise InvalidTransaction("A fixed-term contract needs an end date after the joining date.")
    else:
        values["contract_end"] = None
    if values["probation_end"] and values["probation_end"] <= values["joined_on"]:
        raise InvalidTransaction("Probation must end after the joining date.")
    manager_id = data.get("manager_id")
    values["manager"] = None
    if manager_id not in (None, ""):
        manager = (Employee.objects.filter(pk=manager_id if type(manager_id) is int else None,
                                           business_unit__legal_entity=business_unit.legal_entity)
                   .exclude(status=S.LEFT).first())
        if manager is None:
            raise InvalidTransaction("Choose a manager who works in the same company.")
        if employee is not None:
            boss, seen = manager, set()
            while boss is not None and boss.pk not in seen:
                if boss.pk == employee.pk:
                    raise InvalidTransaction("A person cannot report to themselves or to someone who reports to them.")
                seen.add(boss.pk)
                boss = boss.manager
        values["manager"] = manager
    user_id = data.get("user_id")
    values["user"] = None
    if user_id not in (None, ""):
        user = get_user_model().objects.filter(pk=user_id if type(user_id) is int else None,
                                               demo_marker__is_synthetic=True).first()
        taken = Employee.objects.filter(user=user).exclude(pk=employee.pk if employee else None).exists()
        if user is None or taken:
            raise InvalidTransaction("Choose an ERP sign-in that is not linked to someone else.")
        values["user"] = user
    return values


def _show(field, value):
    if value is None or value == "":
        return "none"
    if field == "manager":
        return value.full_name
    if field == "user":
        return value.username
    if field == "contract":
        return Employee.Contract(value).label.lower()
    return value.isoformat() if isinstance(value, datetime.date) else str(value)


def _employee_code(value, employee=None):
    """An existing 5-digit employee number, with or without EMP in front, stored as EMP-01234. Used only by the import."""
    text = "".join(str(value or "").split()).upper()
    digits = text[3:].lstrip("-") if text.startswith("EMP") else text
    if not digits.isdigit() or len(digits) > 5 or int(digits) == 0:
        raise InvalidTransaction("Give the employee code as a number of up to 5 digits, for example 01234 or EMP-01234.")
    code = f"EMP-{int(digits):05d}"
    if Employee.objects.filter(staff_number=code).exclude(pk=employee.pk if employee else None).exists():
        raise InvalidTransaction(f"Employee code {code} is already used by someone else.")
    return code


def _next_number():
    last = Employee.objects.select_for_update().filter(staff_number__startswith="EMP-").order_by("-staff_number").first()
    following = int(last.staff_number.split("-")[1]) + 1 if last else 1
    return f"EMP-{following:05d}"


@transaction.atomic
def save_employee(*, actor, business_unit, data, employee_id=None):
    _require_manager(actor, business_unit)
    if employee_id is None:
        values = _clean(business_unit, data)
        employee = Employee.objects.create(business_unit=business_unit, staff_number=_next_number(), created_by=actor, **values)
        EmployeeEvent.objects.create(employee=employee, actor=actor, change="added",
                                     what=f"{employee.job_title}, {employee.department}, joined {employee.joined_on:%d %b %Y}")
    else:
        employee = _load(business_unit, employee_id, lock=True)
        values = _clean(business_unit, data, employee)
        changes = []
        for field, value in values.items():
            before = getattr(employee, field)
            if before != value:
                changes.append(f"{FIELDS[field]}: {_show(field, before)} → {_show(field, value)}")
                setattr(employee, field, value)
        if not changes:
            raise InvalidTransaction("Nothing was changed.")
        if employee.last_day and employee.last_day < employee.joined_on:
            raise InvalidTransaction("The joining date cannot be after the last day.")
        employee.save()
        EmployeeEvent.objects.create(employee=employee, actor=actor, change="changed", what="; ".join(changes)[:400])
    return _row(_load(business_unit, employee.pk), timezone.localdate(), full=True, actor=actor)


@transaction.atomic
def import_existing_staff(*, actor, business_unit, rows):
    """Load the existing staff list once, keeping each person's 5-digit number as their EMP code.

    All or nothing: one bad row stops the whole load. People added later through the screen get the next number in the series.
    """
    _require_manager(actor, business_unit)
    added = []
    for line, data in enumerate(rows, start=2):
        try:
            code = _employee_code(data.get("employee_number"))
            values = _clean(business_unit, {**data, "manager_id": None, "user_id": None})
        except InvalidTransaction as error:
            raise InvalidTransaction(f"Line {line}: {error}") from None
        employee = Employee.objects.create(business_unit=business_unit, staff_number=code, created_by=actor, **values)
        EmployeeEvent.objects.create(employee=employee, actor=actor, change="added",
                                     what=f"imported with existing number {code}; {employee.job_title}, {employee.department}")
        added.append(employee)
    return {"added": len(added), "next_number": _next_number() if added else None}


@transaction.atomic
def record_leaving(*, actor, business_unit, employee_id, last_day=None, reason="", cancel=False):
    """Record that someone is leaving. Before their last day they are serving notice; after it they have left."""
    _require_manager(actor, business_unit)
    employee = _load(business_unit, employee_id, lock=True)
    today = timezone.localdate()
    if cancel:
        if employee.status != S.NOTICE:
            raise InvalidTransaction("Only notice that is still running can be withdrawn.")
        employee.status, employee.last_day, employee.leaving_reason = S.ACTIVE, None, ""
        employee.save(update_fields=["status", "last_day", "leaving_reason"])
        EmployeeEvent.objects.create(employee=employee, actor=actor, change="notice withdrawn")
        return _row(employee, today, full=True, actor=actor)
    if employee.status == S.LEFT:
        raise InvalidTransaction(f"{employee.full_name} has already left.")
    day = _day(last_day, None, "last day") if last_day else None
    if day is None:
        raise InvalidTransaction("Give the last working day.")
    if day < employee.joined_on:
        raise InvalidTransaction("The last day cannot be before the joining date.")
    reason = _text(reason, "reason", 200, required=True)
    if employee.reports.exclude(status=S.LEFT).exists() and day < today:
        raise InvalidTransaction(f"Give {employee.full_name}'s team a new manager first.")
    employee.status = S.LEFT if day < today else S.NOTICE
    employee.last_day, employee.leaving_reason = day, reason
    employee.save(update_fields=["status", "last_day", "leaving_reason"])
    EmployeeEvent.objects.create(employee=employee, actor=actor, change="left" if employee.status == S.LEFT else "notice",
                                 what=f"last day {day:%d %b %Y}: {reason}"[:400])
    return _row(employee, today, full=True, actor=actor)


def close_finished_notices(today=None):
    """Mark people whose last day has passed as left. Safe to run any number of times."""
    today = today or timezone.localdate()
    return Employee.objects.filter(status=S.NOTICE, last_day__lt=today).update(status=S.LEFT)


@transaction.atomic
def add_document(*, actor, business_unit, employee_id, data):
    _require_manager(actor, business_unit)
    employee = _load(business_unit, employee_id, lock=True)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the document's details.")
    kind = data.get("kind")
    if kind not in EmployeeDocument.Kind.values:
        raise InvalidTransaction("Choose the kind of document.")
    number = _text(data.get("number"), "document number", 40, required=True).upper().replace(" ", "")
    if kind == EmployeeDocument.Kind.QID and not re.fullmatch(r"[23][0-9]{10}", number):
        raise InvalidTransaction("A QID number has 11 digits and starts with 2 or 3.")
    clash = EmployeeDocument.objects.filter(kind=kind, number=number, removed_at__isnull=True).exclude(employee=employee).first()
    if clash is not None:
        raise InvalidTransaction(f"That {EmployeeDocument.Kind(kind).label} number is already on another staff record.")
    expires = _optional_day(data.get("expires_on"), "expiry date")
    if kind in (EmployeeDocument.Kind.QID, EmployeeDocument.Kind.PASSPORT, EmployeeDocument.Kind.RESIDENCE) and expires is None:
        raise InvalidTransaction("Give the expiry date.")
    # A renewed document replaces the old one of the same kind.
    replaced = [document for document in _live_documents(employee) if document.kind == kind and kind != EmployeeDocument.Kind.OTHER]
    now = timezone.now()
    for old in replaced:
        old.removed_by, old.removed_at = actor, now
        old.save(update_fields=["removed_by", "removed_at"])
    document = EmployeeDocument.objects.create(
        employee=employee, kind=kind, number=number, issued_by=_text(data.get("issued_by"), "issued by", 60),
        expires_on=expires, note=_text(data.get("note"), "note", 120), added_by=actor,
    )
    EmployeeEvent.objects.create(
        employee=employee, actor=actor, change="renewed document" if replaced else "added document",
        what=f"{document.get_kind_display()} {number}" + (f", expires {expires:%d %b %Y}" if expires else ""))
    return _row(_load(business_unit, employee.pk), timezone.localdate(), full=True, actor=actor)


@transaction.atomic
def remove_document(*, actor, business_unit, employee_id, document_id):
    _require_manager(actor, business_unit)
    employee = _load(business_unit, employee_id, lock=True)
    document = EmployeeDocument.objects.filter(pk=document_id if type(document_id) is int else None, employee=employee,
                                               removed_at__isnull=True).first()
    if document is None:
        raise InvalidTransaction("That document was not found.")
    document.removed_by, document.removed_at = actor, timezone.now()
    document.save(update_fields=["removed_by", "removed_at"])
    EmployeeEvent.objects.create(employee=employee, actor=actor, change="removed document",
                                 what=f"{document.get_kind_display()} {document.number}")
    return _row(_load(business_unit, employee.pk), timezone.localdate(), full=True, actor=actor)
