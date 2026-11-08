"""Tenders and bonds (Projects & Fit-Out, Phase 2, behind the same switch; synthetic only).

Tenders (Sam, 7 Nov 2026): the estimation team keeps one list of every tender sent to a client, grouped
as Submitted, Won, Lost and Cancelled, with the client's contact, who prepared it and who is responsible,
the dates, quotation reference and revision, brands, ex-works value, exchange rate, total, the internal
progress of won work (production, shipping, completed), remarks and the last update. A won tender opens a
project from its signed BOQ and then runs like any other project.

Bonds: bid bonds, performance bonds, advance payment guarantees, guarantee cheques, warranties and
retention guarantees, given for a tender or a project. Each is kept with its amount and validity; active
bonds are flagged when they expire within the reminder period (30 days, Project setup), and when they are
past their date and still not returned.
"""

import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .factory import _day, _f, _int, _name, _text
from .models import Project, ProjectBond, ProjectSettings, SyntheticCounterparty, Tender
from .projects import (
    FINANCE,
    RUN,
    ZERO,
    _decimal,
    bond_row,
    can_finance,
    can_run,
    money,
    optional_day,
    people,
    person,
    project_settings,
    require,
    require_viewer,
)
from .transaction_services import InvalidTransaction

T = Tender.Status
B = ProjectBond.Status
OPEN_TENDER = (T.PREPARING, T.SUBMITTED)
STATUS_ORDER = [T.PREPARING, T.SUBMITTED, T.WON, T.LOST, T.CANCELLED]


def _can_bond(actor, business_unit):
    return can_run(actor, business_unit) or can_finance(actor, business_unit)


# Tenders.

def _tender_row(tender, actor, today, reminder):
    bonds = list(tender.bonds.all())
    return {
        "id": tender.pk, "number": tender.number, "client_name": tender.client_name,
        "customer_id": tender.customer_id, "project_name": tender.project_name,
        "contact_person": tender.contact_person, "email": tender.email, "phone": tender.phone,
        "assignee": {"id": tender.assignee_id, "name": _name(tender.assignee)} if tender.assignee_id else None,
        "responsible": ({"id": tender.responsible_id, "name": _name(tender.responsible)}
                        if tender.responsible_id else None),
        "start_date": tender.start_date.isoformat() if tender.start_date else "",
        "due_date": tender.due_date.isoformat() if tender.due_date else "",
        "overdue": tender.status == T.PREPARING and tender.due_date is not None and tender.due_date < today,
        "quote_reference": tender.quote_reference, "revision": tender.revision, "brands": tender.brands,
        "exworks": _f(tender.exworks) if tender.exworks is not None else "",
        "rate": format(tender.rate.normalize(), "f") if tender.rate is not None else "",
        "total": _f(tender.total), "status": tender.status, "status_label": tender.get_status_display(),
        "progress": tender.progress, "progress_label": tender.get_progress_display() if tender.progress else "",
        "remarks": tender.remarks, "client_order": tender.client_order,
        "decided_on": tender.decided_on.isoformat() if tender.decided_on else "",
        "updated_by": _name(tender.updated_by or tender.created_by),
        "updated_at": tender.updated_at.isoformat(),
        "projects": [{"id": project.pk, "number": project.number} for project in tender.projects.all()],
        "bonds": [bond_row(bond, today, reminder) for bond in bonds],
        "can_edit": can_run(actor, tender.business_unit),
    }


def _tenders(business_unit):
    return (Tender.objects.filter(business_unit=business_unit, is_synthetic=True)
            .select_related("assignee", "responsible", "created_by", "updated_by")
            .prefetch_related("bonds", "projects"))


def tenders_overview(*, actor, business_unit):
    """Every tender, grouped by status, with the win rate and the values won and still open."""
    require_viewer(actor, business_unit)
    today = timezone.localdate()
    reminder = project_settings(business_unit.legal_entity).bond_reminder_days
    rows = [_tender_row(tender, actor, today, reminder) for tender in _tenders(business_unit)]
    year = str(today.year)

    def value(status, this_year=False):
        return sum((Decimal(row["total"]) for row in rows if row["status"] == status
                    and (not this_year or (row["decided_on"] or row["start_date"]).startswith(year))), ZERO)

    decided = [row for row in rows if row["status"] in (T.WON, T.LOST)]
    won = sum(1 for row in decided if row["status"] == T.WON)
    return {
        "today": today.isoformat(),
        "groups": [{"status": status, "label": T(status).label,
                    "rows": [row for row in rows if row["status"] == status]} for status in STATUS_ORDER],
        "tiles": {
            "open": sum(1 for row in rows if row["status"] in OPEN_TENDER),
            "open_value": _f(value(T.SUBMITTED) + value(T.PREPARING)),
            "won_this_year": _f(value(T.WON, True)),
            "lost_this_year": _f(value(T.LOST, True)),
            "win_rate": round(100 * won / len(decided)) if decided else None,
            "overdue": sum(1 for row in rows if row["overdue"]),
            "bonds_expiring": sum(1 for row in rows for bond in row["bonds"] if bond["expiring"]),
        },
        "statuses": [{"id": value, "label": label} for value, label in T.choices],
        "progress": [{"id": value, "label": label} for value, label in Tender.Progress.choices if value],
        "people": people(business_unit), "can_edit": can_run(actor, business_unit),
        "can_bond": _can_bond(actor, business_unit),
    }


def _apply(tender, business_unit, data):
    if "client_name" in data or tender.pk is None:
        tender.client_name = _text(data.get("client_name"), "client name", 160, required=True)
    if "customer_id" in data:
        customer = None
        if data["customer_id"] not in (None, ""):
            customer = SyntheticCounterparty.objects.filter(
                pk=_int(data["customer_id"]), party_type=SyntheticCounterparty.Type.CUSTOMER,
                is_synthetic=True).first()
            if customer is None:
                raise InvalidTransaction("Choose a customer from the list, or leave it empty.")
        tender.customer = customer
    for key, label, limit in (("project_name", "project name", 200), ("contact_person", "contact person", 120),
                              ("email", "email", 120), ("phone", "phone", 60),
                              ("quote_reference", "quotation reference", 80), ("revision", "revision", 10),
                              ("brands", "brands", 200), ("remarks", "remarks", 300),
                              ("client_order", "client order reference", 80)):
        if key in data:
            setattr(tender, key, _text(data[key], label, limit))
    if tender.email and ("@" not in tender.email or " " in tender.email):
        raise InvalidTransaction("The email does not look right.")
    for key, label in (("assignee_id", "person who prepares the tender"), ("responsible_id", "project responsible")):
        if key in data:
            setattr(tender, key[:-3], person(business_unit, data[key], label))
    for key, label in (("start_date", "start date"), ("due_date", "due date")):
        if key in data:
            setattr(tender, key, optional_day(data[key], label))
    if tender.start_date and tender.due_date and tender.due_date < tender.start_date:
        raise InvalidTransaction("The due date cannot be before the start date.")
    if "exworks" in data:
        tender.exworks = None if data["exworks"] in (None, "") else money(data["exworks"], "ex-works value")
    if "rate" in data:
        rate = _decimal(data["rate"])
        if data["rate"] not in (None, "") and (rate is None or rate <= 0 or rate >= 10000):
            raise InvalidTransaction("The rate must be a number above 0.")
        tender.rate = None if rate is None else rate.quantize(Decimal("0.0001"))
    if "total" in data:
        tender.total = money(data["total"], "project total")
    if "progress" in data:
        if data["progress"] not in (None, "") and data["progress"] not in Tender.Progress.values:
            raise InvalidTransaction("Choose production, shipping or completed.")
        tender.progress = data["progress"] or ""


@transaction.atomic
def create_tender(*, actor, business_unit, data):
    require(actor, business_unit, can_run, RUN)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the tender's details.")
    found = ProjectSettings.objects.select_for_update().get(pk=project_settings(business_unit.legal_entity).pk)
    status = data.get("status") or T.SUBMITTED
    if status not in OPEN_TENDER:
        raise InvalidTransaction("A new tender is either being prepared or submitted.")
    tender = Tender(business_unit=business_unit, number=f"TND-{found.next_tender:04d}", created_by=actor,
                    updated_by=actor, status=status)
    _apply(tender, business_unit, data)
    found.next_tender += 1
    found.save(update_fields=["next_tender"])
    tender.save()
    return tenders_overview(actor=actor, business_unit=business_unit)


@transaction.atomic
def update_tender(*, actor, business_unit, tender_id, data):
    """Change a tender's details, or move it: submitted, won (with the client's order), lost or cancelled."""
    require(actor, business_unit, can_run, RUN)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the changes.")
    tender = Tender.objects.select_for_update().filter(pk=_int(tender_id), business_unit=business_unit).first()
    if tender is None:
        raise InvalidTransaction("That tender was not found.")
    _apply(tender, business_unit, data)
    status = data.get("status")
    if status not in (None, "", tender.status):
        if status not in T.values:
            raise InvalidTransaction("Choose a tender status from the list.")
        if tender.projects.exists():
            raise InvalidTransaction("A project is open for this tender, so it stays won.")
        tender.status = status
        if status in (T.WON, T.LOST, T.CANCELLED):
            tender.decided_on = (_day(data["decided_on"], "decision date") if data.get("decided_on")
                                 else timezone.localdate())
        else:
            tender.decided_on = None
    if tender.progress and tender.status != T.WON:
        raise InvalidTransaction("Production, shipping and completed are only for won tenders.")
    tender.updated_by, tender.updated_at = actor, timezone.now()
    tender.save()
    return tenders_overview(actor=actor, business_unit=business_unit)


# Bonds.

def bonds_overview(*, actor, business_unit):
    """Every bond, guarantee cheque and warranty given, with its validity, for tenders and projects."""
    require_viewer(actor, business_unit)
    today = timezone.localdate()
    reminder = project_settings(business_unit.legal_entity).bond_reminder_days
    bonds = (ProjectBond.objects.filter(Q(tender__business_unit=business_unit) | Q(project__business_unit=business_unit),
                                        is_synthetic=True).select_related("tender", "project"))
    rows = []
    for bond in bonds:
        row = bond_row(bond, today, reminder)
        row["for"] = (f"{bond.project.number} {bond.project.name}" if bond.project_id
                      else f"{bond.tender.number} {bond.tender.project_name or bond.tender.client_name}")
        rows.append(row)
    active = [row for row in rows if row["status"] == B.ACTIVE]
    by_kind = {}
    for row in active:
        by_kind.setdefault(row["kind"], {"kind": row["kind"], "label": row["kind_label"], "count": 0, "amount": ZERO})
        by_kind[row["kind"]]["count"] += 1
        by_kind[row["kind"]]["amount"] += Decimal(row["amount"])
    return {
        "today": today.isoformat(), "reminder_days": reminder, "bonds": rows,
        "tiles": {"active": len(active), "active_amount": _f(sum((Decimal(row["amount"]) for row in active), ZERO)),
                  "expiring": sum(1 for row in active if row["expiring"]),
                  "expired": sum(1 for row in active if row["expired"]),
                  "claimed": sum(1 for row in rows if row["status"] == B.CLAIMED)},
        "by_kind": [{**item, "amount": _f(item["amount"])} for item in by_kind.values()],
        "kinds": [{"id": value, "label": label} for value, label in ProjectBond.Kind.choices],
        "statuses": [{"id": value, "label": label} for value, label in B.choices],
        "tenders": [{"id": tender.pk, "label": f"{tender.number} {tender.project_name or tender.client_name}"}
                    for tender in Tender.objects.filter(business_unit=business_unit).exclude(status=T.CANCELLED)
                    .order_by("-pk")[:300]],
        "projects": [{"id": project.pk, "label": f"{project.number} {project.name}"}
                     for project in Project.objects.filter(business_unit=business_unit).order_by("-pk")[:300]],
        "can_bond": _can_bond(actor, business_unit),
    }


def _bond_fields(bond, business_unit, data):
    if "kind" in data or bond.pk is None:
        if data.get("kind") not in ProjectBond.Kind.values:
            raise InvalidTransaction("Choose the kind of bond.")
        bond.kind = data["kind"]
    if "reference" in data or bond.pk is None:
        bond.reference = _text(data.get("reference"), "bond or cheque number", 80, required=True)
    if "issuer" in data:
        bond.issuer = _text(data["issuer"], "bank or issuer", 120)
    if "note" in data:
        bond.note = _text(data["note"], "note", 300)
    if "amount" in data or bond.pk is None:
        bond.amount = money(data.get("amount"), "bond amount", allow_zero=bond.kind == ProjectBond.Kind.WARRANTY)
    if "issued_on" in data:
        bond.issued_on = optional_day(data["issued_on"], "issue date")
    if "expires_on" in data:
        bond.expires_on = optional_day(data["expires_on"], "expiry date")
    if bond.issued_on and bond.expires_on and bond.expires_on < bond.issued_on:
        raise InvalidTransaction("The expiry date cannot be before the issue date.")
    if bond.kind != ProjectBond.Kind.WARRANTY and bond.expires_on is None:
        raise InvalidTransaction("Give the date the bond is valid until.")


@transaction.atomic
def add_bond(*, actor, business_unit, data):
    require(actor, business_unit, _can_bond, RUN)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the bond's details.")
    tender = project = None
    if data.get("project_id") not in (None, ""):
        project = Project.objects.filter(pk=_int(data["project_id"]), business_unit=business_unit).first()
        if project is None:
            raise InvalidTransaction("That project was not found.")
    elif data.get("tender_id") not in (None, ""):
        tender = Tender.objects.filter(pk=_int(data["tender_id"]), business_unit=business_unit).first()
        if tender is None:
            raise InvalidTransaction("That tender was not found.")
    else:
        raise InvalidTransaction("Choose the tender or project the bond is for.")
    bond = ProjectBond(tender=tender, project=project, recorded_by=actor)
    _bond_fields(bond, business_unit, data)
    bond.save()
    return bonds_overview(actor=actor, business_unit=business_unit)


@transaction.atomic
def update_bond(*, actor, business_unit, bond_id, data):
    """Correct a bond, extend it, or mark it released, claimed by the client, or expired."""
    require(actor, business_unit, _can_bond, FINANCE)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the changes.")
    bond = (ProjectBond.objects.select_for_update(of=("self",))
            .filter(Q(tender__business_unit=business_unit) | Q(project__business_unit=business_unit),
                    pk=_int(bond_id)).first())
    if bond is None:
        raise InvalidTransaction("That bond was not found.")
    status = data.get("status")
    if status not in (None, "", bond.status):
        if status not in B.values:
            raise InvalidTransaction("Choose a bond status from the list.")
        if bond.status != B.ACTIVE and status != B.ACTIVE:
            raise InvalidTransaction("Only an active bond can be released, claimed or marked expired.")
        bond.status = status
    if bond.status == B.ACTIVE or not status:
        _bond_fields(bond, business_unit, {key: value for key, value in data.items() if key != "status"})
    bond.save()
    return bonds_overview(actor=actor, business_unit=business_unit)


def expiring_bonds(business_unit, today=None):
    """Active bonds that run out within the reminder period, for the daily alerts."""
    today = today or timezone.localdate()
    reminder = project_settings(business_unit.legal_entity).bond_reminder_days
    bonds = ProjectBond.objects.filter(Q(tender__business_unit=business_unit) | Q(project__business_unit=business_unit),
                                       status=B.ACTIVE, expires_on__isnull=False,
                                       expires_on__lte=today + datetime.timedelta(days=reminder))
    return [bond_row(bond, today, reminder) for bond in bonds]
