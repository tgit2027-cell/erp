"""Projects & Fit-Out (Phase 2): signed projects from their BOQ, budget control, schedule, money (synthetic only).

The section is built behind a switch. Until the Master Admin turns it on in Project setup, only the
Master Admin can see or use it, so the teams testing Phase 1 are not disturbed.

How a project runs (Sam, 7 Nov 2026):

- Fit-out projects are priced outside the ERP on Tivoli's BOQ Excel template. Once the client signs, the
  BOQ is uploaded: every item with its zone, area, floor, selling price, cost (supplier price plus
  installation), supplier and RFQ date. Selling prices stay as they are; the discount is shown on its own
  (and can be changed), then the final price.
- The approved cost is the budget, item by item. The Construction Manager approves it; then the project
  is in progress.
- Every order to a supplier or subcontractor is checked against the budget of its items. Within budget it
  is approved at once. Over budget, the Project Manager registers the excess with its reason, the
  Construction Manager approves it, then Finance approves it or asks for more details. Nobody approves
  their own. Payments never exceed the approved order.
- Items in the Joinery category go to the Factory as a job order.
- Each item is tracked from order to handover: ordered, paid, shipped or made, delivered, installed,
  handed over, each with a planned and an actual date.
- Cash in (the client's advance and claim payments) and cash out (payments on orders) are shown week by
  week, with what is planned next.
- Client terms (Sam, 7 Nov 2026): a down payment of 20% up to 50%; progress payments as the site moves,
  each requested by the site team and certified by the consultant before it is invoiced; then 5% retention
  paid after handover, either at once or after the warranty period (usually 400 days).
- Supplier and subcontractor terms differ by company and agreement: each order carries its own instalments
  (on order, before production, on shipping, on delivery, on installation, at handover, or on a date) and
  whether it is delivered at once or made and shipped first.
- Changes are variations.

Provisional until confirmed: the down payment is taken back pro rata from each progress claim; clients pay
certified claims within 30 days; the Project Manager may change the discount until the budget is approved,
the Construction Manager after.

Accounting:

    client advance:     Dr bank and cash                Cr advances from project clients
    progress claim:     Dr project clients to collect   (net)
                        Dr retention held by clients    (retention)
                        Dr advances from clients        (advance taken back)
                                                        Cr project revenue (work this claim, after discount)
    retention release:  Dr project clients to collect   Cr retention held by clients
    claim paid:         Dr bank and cash                Cr project clients to collect
    order paid:         Dr project costs                Cr bank and cash
"""

import datetime
import io
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .access import active_grants
from .access_levels import is_master_admin, require_master_admin
from .cash_accounts import _account as _cash_account
from .cash_accounts import _reconciled
from .factory import S as JobStatus
from .factory import (
    _actual,
    _apply_plan,
    _boq_code,
    _day,
    _f,
    _int,
    _name,
    _number,
    _past_day,
    _round,
    _text,
    factory_settings,
)
from .models import (
    CashAccount,
    CashLineAssignment,
    CustomerContactChange,
    DemoUserMarker,
    FactorySettings,
    JournalEntry,
    ProductionOrder,
    Project,
    ProjectBoqLine,
    ProjectClaim,
    ProjectLineStep,
    ProjectOrder,
    ProjectPayment,
    ProjectReceipt,
    ProjectSettings,
    ProjectVariation,
    SyntheticCounterparty,
    Tender,
    UserActionGrant,
    next_customer_number,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import (
    InvalidTransaction,
    _begin_idempotent_operation,
    _complete,
    _post_journal,
    _require_demo,
)

A = UserActionGrant.Action
PS = Project.Status
V = ProjectVariation.Status
C = ProjectClaim.Status
K = ProjectClaim.Kind
O = ProjectOrder.Status
SRC = ProjectBoqLine.Source
STEP = ProjectLineStep.Step
STEPS = [value for value, _ in STEP.choices]
RUN, APPROVE, FINANCE = A.RUN_PROJECTS, A.APPROVE_PROJECTS, A.INVOICE_PROJECTS
PROJECT_ACTIONS = (RUN, APPROVE, FINANCE)
ZERO = Decimal("0.00")
HUNDRED = Decimal(100)
MONEY_LIMIT = Decimal(1000000000)
OPEN_CLAIM = (C.WAITING, C.APPROVED, C.SUBMITTED, C.CERTIFIED)
LIVE = (PS.SETUP, PS.RUNNING, PS.HANDOVER)
WORKING = (PS.RUNNING, PS.HANDOVER)
WAITING_ORDER = (O.MANAGER, O.FINANCE, O.DETAILS)
ACTIVE_ORDER = (*WAITING_ORDER, O.APPROVED)
MAX_LINES = 3000
MAX_BYTES = 5 * 1024 * 1024
CASH_WEEKS = 12
RELEASES = ("handover", "warranty")
EVENTS = {"order": "On order", "production": "Before production", "shipped": "On shipping",
          "delivered": "On delivery", "installed": "On installation", "handed_over": "At handover", "date": "On a date"}
DELIVERIES = ("immediate", "production")
POLICY_LABEL = (
    "Rules: a project opens from its signed BOQ and the approved cost is its budget. An order within its items' "
    "budget is approved at once; over budget, the Project Manager gives the reason, the Construction Manager "
    "approves, then Finance approves or asks for more details; nobody approves their own. Payments never exceed the "
    "approved order. Joinery items go to the factory. Each progress claim is approved by the Construction Manager, "
    "certified by the consultant, then invoiced by Finance. Provisional: the down payment is taken back pro rata "
    "from each claim; certified claims are due in 30 days."
)


# Who may do what.

def _granted(user, business_unit, action):
    return active_grants(user, action).filter(business_unit=business_unit).exists()


def can_run(user, business_unit):
    return _granted(user, business_unit, RUN)


def can_approve(user, business_unit):
    return _granted(user, business_unit, APPROVE)


def can_finance(user, business_unit):
    return _granted(user, business_unit, FINANCE)


def project_settings(legal_entity):
    found, created = ProjectSettings.objects.get_or_create(legal_entity=legal_entity)
    if created:
        found.refresh_from_db()
    return found


def is_live(legal_entity):
    return ProjectSettings.objects.filter(legal_entity=legal_entity, is_live=True).exists()


def visibility(user):
    """What the sign-in tells the screen: whether this person sees Projects & Fit-Out, and whether it is live."""
    if getattr(settings, "APP_ENV", None) != "demo" or not getattr(user, "is_authenticated", False):
        return {"can_see_projects": False, "projects_live": False}
    live = ProjectSettings.objects.filter(is_live=True, is_synthetic=True).exists()
    if is_master_admin(user):
        return {"can_see_projects": True, "projects_live": live}
    holds = active_grants(user).filter(action__in=PROJECT_ACTIONS,
                                       business_unit__legal_entity__project_settings__is_live=True)
    return {"can_see_projects": live and holds.exists(), "projects_live": live}


def require_user(actor, business_unit):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Projects run only in the synthetic demo environment.")
    if not getattr(actor, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=actor, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can use projects.")
    if business_unit is None:
        raise WorkflowPermissionDenied("Choose a business unit.")
    _require_demo(business_unit, business_unit.legal_entity)
    if not is_master_admin(actor) and not is_live(business_unit.legal_entity):
        raise WorkflowPermissionDenied("The Projects & Fit-Out section is not live yet.")


def require_viewer(actor, business_unit):
    require_user(actor, business_unit)
    if is_master_admin(actor):
        return
    if not any(_granted(actor, business_unit, action) for action in PROJECT_ACTIONS):
        raise WorkflowPermissionDenied("Projects are shown only to people with a project permission in this unit.")


def require(actor, business_unit, allowed, action):
    require_user(actor, business_unit)
    if not allowed(actor, business_unit):
        raise WorkflowPermissionDenied(f"You need the '{A(action).label}' permission in this unit.")


def _percent(value, field, limit=HUNDRED):
    return _number(value, field, limit=limit)


def money(value, field, *, allow_zero=True, allow_negative=False):
    if allow_negative and value not in (None, ""):
        try:
            amount = Decimal(str(value).replace(",", ""))
        except (ArithmeticError, InvalidOperation):
            raise InvalidTransaction(f"The {field} must be a number.") from None
        if not amount.is_finite() or abs(amount) >= MONEY_LIMIT:
            raise InvalidTransaction(f"The {field} is not a usable amount.")
        if amount == 0 and not allow_zero:
            raise InvalidTransaction(f"The {field} cannot be zero.")
        return _round(amount)
    return _number(value, field, allow_zero=allow_zero, limit=MONEY_LIMIT)


def optional_day(value, field):
    return None if value in (None, "") else _day(value, field)


def _decimal(value):
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (ArithmeticError, InvalidOperation):
        return None
    return number if number.is_finite() else None


def people(business_unit):
    """People who work on projects in this unit, to choose a project manager or tender owner from."""
    user_ids = (UserActionGrant.objects.filter(business_unit=business_unit, action__in=PROJECT_ACTIONS,
                                               user__isnull=False).values_list("user_id", flat=True))
    users = get_user_model().objects.filter(Q(pk__in=user_ids) | Q(is_superuser=True), is_active=True,
                                            demo_marker__isnull=False).distinct().order_by("first_name", "username")
    return [{"id": user.pk, "name": _name(user)} for user in users[:200]]


def person(business_unit, value, field="project manager"):
    if value in (None, ""):
        return None
    if not any(row["id"] == _int(value) for row in people(business_unit)):
        raise InvalidTransaction(f"Choose a {field} who works on projects in this unit.")
    return get_user_model().objects.get(pk=_int(value))


def _round3(value):
    return value.quantize(Decimal("0.001"))


# Reading the signed BOQ.

_HEAD = {
    "code": re.compile(r"^item\s*(number|no\.?|#)$|^ref", re.IGNORECASE),
    "description": re.compile(r"^desc", re.IGNORECASE),
    "quantity": re.compile(r"^(qty|quantity)", re.IGNORECASE),
    "unit": re.compile(r"^(unit|uom)$", re.IGNORECASE),
    "rate": re.compile(r"^unit\s*(price|rate)|^rate$", re.IGNORECASE),
    "amount": re.compile(r"^total\s*amount|^amount$", re.IGNORECASE),
    "category": re.compile(r"^categor", re.IGNORECASE),
    "sub_category": re.compile(r"^sub[\s-]*categor", re.IGNORECASE),
    "work_type": re.compile(r"^item$", re.IGNORECASE),
    "area": re.compile(r"^area$", re.IGNORECASE),
    "floor": re.compile(r"^floor$", re.IGNORECASE),
    "eur": re.compile(r"^eur$", re.IGNORECASE),
    "supply_cost": re.compile(r"^qar$|^supply\s*cost", re.IGNORECASE),
    "installation": re.compile(r"^install", re.IGNORECASE),
    "cost_rate": re.compile(r"^(unit\s*)?cost$", re.IGNORECASE),
    "rfq_date": re.compile(r"^rfq", re.IGNORECASE),
    "supplier": re.compile(r"^supplier|contractor", re.IGNORECASE),
}
_LEVEL = re.compile(r"^[123]$")


def _header(rows):
    """Where the item table starts, and which column holds what (the headings may run over two rows)."""
    for number, cells in enumerate(rows[:40]):
        texts = [" ".join(str(cell or "").split()) for cell in cells]
        if not any(_HEAD["quantity"].search(text) for text in texts):
            continue
        if not any(_HEAD["rate"].search(text) for text in texts):
            continue
        columns = {}
        for row in rows[number:number + 2]:
            for index, cell in enumerate(row):
                text = " ".join(str(cell or "").split())
                for key, pattern in _HEAD.items():
                    if text and key not in columns and pattern.search(text):
                        columns[key] = index
                        break
        second = rows[number + 1] if len(rows) > number + 1 else []
        start = number + 2 if any(_HEAD["category"].search(str(cell or "")) or _HEAD["supplier"].search(str(cell or ""))
                                  for cell in second) else number + 1
        return start, columns
    return None, {}


def _cell_text(cells, index):
    if index is None or index >= len(cells) or cells[index] is None:
        return ""
    value = cells[index]
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()[:10]
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _cell_number(cells, index):
    return _decimal(cells[index]) if index is not None and index < len(cells) else None


def guess_source(category, supplier=""):
    """Items in the Joinery category are made in our factory (Sam, 7 Nov 2026)."""
    if category.strip().lower() == "joinery":
        return SRC.FACTORY
    return SRC.SUPPLY


def read_signed_boq(content, file_name):
    """Items, prices, costs and the discount from Tivoli's fit-out BOQ template (the first sheet with an item table)."""
    if len(content) > MAX_BYTES:
        raise InvalidTransaction("The file is larger than 5 MB.")
    if not (file_name or "").lower().endswith((".xlsx", ".xlsm")):
        raise InvalidTransaction("Upload the signed BOQ as an Excel .xlsx file.")
    from openpyxl import load_workbook

    try:
        book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        raise InvalidTransaction("This file could not be read as an Excel workbook.") from None
    try:
        for sheet in book.worksheets:
            rows = [list(row) for row in sheet.iter_rows(values_only=True)]
            start, columns = _header(rows)
            if start is not None and "code" in columns:
                return _read_items(rows, start, columns, sheet.title, file_name)
    finally:
        book.close()
    raise InvalidTransaction("No BOQ table was found. The sheet needs headings such as Item Number, Description, "
                             "Quantity, Unit and Unit Price.")


def _read_items(rows, start, columns, sheet_name, file_name):
    code_at, qty_at, rate_at = columns["code"], columns["quantity"], columns["rate"]
    supplier_at = columns.get("supplier")
    lines, problems, levels = [], [], {1: "", 2: "", 3: ""}
    totals = {"subtotal": None, "discount": None, "discount_percent": None, "total": None}
    for number, cells in enumerate(rows[start:], start=start + 1):
        texts = [_cell_text(cells, index) for index in range(len(cells))]
        label = " ".join(texts[:qty_at + 4]).lower()
        after = [_decimal(value) for value in cells[qty_at:]]
        after = [value for value in after if value is not None]
        if "sub total" in label or "subtotal" in label:
            totals["subtotal"] = next((value for value in after if value >= 1), totals["subtotal"])
            continue
        if "discount" in label:
            totals["discount"] = next((value for value in after if value >= 1), None)
            totals["discount_percent"] = next((value * HUNDRED for value in after if 0 < value < 1), None)
            continue
        if "total amount" in label:
            totals["total"] = next((value for value in after if value >= 1), totals["total"])
            continue
        code = texts[code_at] if code_at < len(texts) else ""
        description = next((texts[index] for index in range(code_at + 1, qty_at) if index < len(texts) and texts[index]),
                           "")
        quantity = _cell_number(cells, qty_at)
        if _LEVEL.match(code) and quantity is None:
            level = int(code)
            levels[level] = " ".join(description.split())[:160]
            for deeper in range(level + 1, 4):
                levels[deeper] = ""
            continue
        if not code or quantity is None:
            continue
        rate = _cell_number(cells, rate_at)
        if quantity <= 0:
            problems.append(f"Row {number} ({code}): the quantity must be more than 0, so it was left off")
            continue
        if rate is None or rate < 0:
            problems.append(f"Row {number} ({code}): no unit price, so it was left off")
            continue
        installation = _cell_number(cells, columns.get("installation"))
        supply = _cell_number(cells, columns.get("supply_cost"))
        # Tivoli's template keeps the item's full unit cost (supply in QAR + installation) in the column right
        # after Installation, which has no heading. Without it, supply + installation is the cost.
        cost = _cell_number(cells, columns.get("cost_rate"))
        if cost is None and "installation" in columns:
            cost = _cell_number(cells, columns["installation"] + 1)
        if cost is None and (supply is not None or installation is not None):
            cost = (supply or ZERO) + (installation or ZERO)
        if cost is None:
            problems.append(f"Row {number} ({code}): no cost, so its budget is 0 until it is filled in")
            cost = ZERO
        supplier = _cell_text(cells, supplier_at)[:160]
        remarks = (" · ".join(text for text in texts[supplier_at + 1:] if text)[:300] if supplier_at is not None else "")
        category = _cell_text(cells, columns.get("category"))[:40]
        rfq = cells[columns["rfq_date"]] if "rfq_date" in columns and columns["rfq_date"] < len(cells) else None
        lines.append({
            "position": len(lines) + 1, "code": code[:20], "section": levels[1],
            "area": _cell_text(cells, columns.get("area"))[:160] or levels[2],
            "floor": _cell_text(cells, columns.get("floor"))[:120],
            "work_type": (_cell_text(cells, columns.get("work_type")) or levels[3].title())[:60],
            "price_group": category, "category": _cell_text(cells, columns.get("sub_category"))[:80],
            "description": description[:2000] or code, "quantity": format(_round3(quantity), "f"),
            "unit": (_cell_text(cells, columns.get("unit")) or "piece")[:20],
            "rate": format(rate.quantize(Decimal("0.0001")), "f"),
            "cost_rate": format(cost.quantize(Decimal("0.0001")), "f"),
            "supplier": supplier, "remarks": remarks,
            "rfq_date": rfq.date().isoformat() if isinstance(rfq, datetime.datetime) else "",
            "source": guess_source(category, supplier),
        })
    if not lines:
        raise InvalidTransaction("No priced items were found under the BOQ headings.")
    if len(lines) > MAX_LINES:
        raise InvalidTransaction(f"The BOQ has {len(lines)} items; a project can hold at most {MAX_LINES}.")
    value = sum((_round(Decimal(line["quantity"]) * Decimal(line["rate"])) for line in lines), ZERO)
    cost = sum((_round(Decimal(line["quantity"]) * Decimal(line["cost_rate"])) for line in lines), ZERO)
    subtotal = totals["subtotal"]
    if subtotal is not None and abs(subtotal - value) > 1:
        problems.append(f"The items add up to QAR {_f(value)} but the sheet's sub total is QAR {_f(subtotal)}. "
                        "Check for items without a quantity or price.")
    percent = totals["discount_percent"]
    if percent is None and totals["discount"] and subtotal:
        percent = totals["discount"] * HUNDRED / subtotal
    percent = (percent or ZERO).quantize(Decimal("0.0001"))
    final = _round(value * (HUNDRED - percent) / HUNDRED)
    return {
        "file_name": file_name, "sheet": sheet_name, "lines": lines, "problems": problems[:50],
        "more_problems": max(0, len(problems) - 50),
        "totals": {"items": len(lines), "value": _f(value), "discount_percent": format(percent.normalize(), "f"),
                   "discount": _f(value - final), "contract_value": _f(final), "cost": _f(cost),
                   "margin": _f(final - cost), "factory_items": sum(1 for line in lines if line["source"] == SRC.FACTORY),
                   "sheet_total": _f(totals["total"]) if totals["total"] is not None else ""},
    }


def read_boq(*, actor, business_unit, file_name, content):
    """Preview a signed BOQ before opening the project (nothing is saved)."""
    require(actor, business_unit, can_run, RUN)
    return read_signed_boq(content, file_name)


# Numbers. Selling prices are shown as in the BOQ; the discount applies to the total (Sam, 7 Nov 2026).

def _keep(project):
    return (HUNDRED - project.discount_percent) / HUNDRED


def line_value(line):
    """The item's selling value before the discount, as in the signed BOQ."""
    return _round(line.quantity * line.rate)


def line_budget(line):
    return _round(line.quantity * line.cost_rate)


def boq_value(project):
    return sum((line_value(line) for line in project.boq_lines.all()), ZERO)


def final_value(project, gross=None):
    gross = boq_value(project) if gross is None else gross
    return _round(gross * _keep(project))


def _variation_done(variation):
    """What a signed variation adds to work done: an omission counts in full, added work at its done %."""
    if variation.value < 0:
        return variation.value
    return _round(variation.value * variation.done_percent / HUNDRED)


def work_to_date(project):
    """Work done so far at the contract's prices after the discount, plus signed variations."""
    lines = sum((_round(line_value(line) * line.done_percent / HUNDRED) for line in project.boq_lines.all()), ZERO)
    return _round(lines * _keep(project)) + sum((_variation_done(variation) for variation in project.variations.all()
                                                if variation.status == V.SIGNED), ZERO)


def job_cost(job):
    if job.status == JobStatus.CANCELLED:
        return ZERO
    if job.status not in (JobStatus.DRAFT, JobStatus.WAITING, JobStatus.IN_PROGRESS):
        return job.actual_total
    materials, bought, labour = _actual(job)
    return materials + bought + labour + _round((materials + bought + labour) * job.overhead_percent / HUNDRED)


def _paid(order):
    return sum((payment.amount for payment in order.payments.all()), ZERO)


def _spread(amount, ids, budgets):
    pool = sum((budgets.get(key, ZERO) for key in ids), ZERO)
    return {key: (_round(amount * budgets.get(key, ZERO) / pool) if pool > 0 else _round(amount / len(ids)))
            for key in ids}


def _line_use(project, exclude=None):
    """Per item: what active orders and the factory have used of its budget. An order on several items counts
    against each in proportion to their budgets."""
    budgets = {line.pk: line_budget(line) for line in project.boq_lines.all()}
    used = dict.fromkeys(budgets, ZERO)
    for order in project.orders.all():
        ids = [line.pk for line in order.lines.all()]
        if order.status in ACTIVE_ORDER and ids and order.pk != getattr(exclude, "pk", None):
            for key, share in _spread(order.amount, ids, budgets).items():
                used[key] += share
    for job in project.factory_jobs.all():
        ids = [line.pk for line in job.project_lines.all()]
        if ids:
            for key, share in _spread(job_cost(job), ids, budgets).items():
                used[key] += share
    return used, budgets


def totals(project):
    lines = list(project.boq_lines.all())
    variations = list(project.variations.all())
    claims = list(project.claims.all())
    orders = list(project.orders.all())
    gross = boq_value(project)
    signed = sum((v.value for v in variations if v.status == V.SIGNED), ZERO)
    at_risk = sum((v.value for v in variations if v.status == V.AT_RISK), ZERO)
    variation_cost = sum((v.cost_estimate for v in variations if v.status in (V.SIGNED, V.AT_RISK)), ZERO)
    contract = project.contract_value + signed
    boq_budget = project.budget_cost if project.approved_at else sum((line_budget(line) for line in lines), ZERO)
    budget = boq_budget + variation_cost
    ordered = sum((order.amount for order in orders if order.status in ACTIVE_ORDER), ZERO)
    factory = sum((job_cost(job) for job in project.factory_jobs.all()), ZERO)
    paid = sum((_paid(order) for order in orders), ZERO)
    invoiced = [claim for claim in claims if claim.status == C.INVOICED]
    billed = sum((claim.gross for claim in invoiced if claim.kind == K.PROGRESS), ZERO)
    retention = (sum((claim.retention for claim in invoiced), ZERO)
                 - sum((claim.retention_released for claim in invoiced), ZERO))
    recovered = sum((claim.advance_recovery for claim in invoiced), ZERO)
    receipts = list(project.receipts.all())
    advance = sum((receipt.amount for receipt in receipts if receipt.kind == ProjectReceipt.Kind.ADVANCE), ZERO)
    collected = sum((receipt.amount for receipt in receipts), ZERO)
    claim_paid = sum((receipt.amount for receipt in receipts if receipt.kind == ProjectReceipt.Kind.CLAIM), ZERO)
    done = work_to_date(project)
    committed = ordered + factory
    forecast = max(budget, committed)
    expected = contract + at_risk
    return {
        "boq_value": _f(gross), "discount_percent": format(project.discount_percent.normalize(), "f"),
        "discount": _f(gross - project.contract_value), "contract_value": _f(project.contract_value),
        "variations_signed": _f(signed), "variations_at_risk": _f(at_risk),
        "contract": _f(contract), "expected": _f(expected),
        "budget": _f(budget), "boq_budget": _f(boq_budget), "variation_budget": _f(variation_cost),
        "ordered": _f(ordered), "factory_cost": _f(factory), "committed": _f(committed),
        "budget_left": _f(budget - committed), "paid_out": _f(paid),
        "over_budget_approved": _f(sum((order.over_budget for order in orders if order.status == O.APPROVED), ZERO)),
        "over_budget_waiting": _f(sum((order.over_budget for order in orders if order.status in WAITING_ORDER), ZERO)),
        "expected_margin": _f(expected - forecast),
        "margin_percent": _f((expected - forecast) * HUNDRED / expected) if expected > 0 else "0.00",
        "work_to_date": _f(done), "done_percent": _f(done * HUNDRED / contract) if contract > 0 else "0.00",
        "billed": _f(billed), "to_bill": _f(max(done - billed, ZERO)), "retention_held": _f(retention),
        "advance_expected": _f(_round(project.contract_value * project.advance_percent / HUNDRED)),
        "advance_received": _f(advance), "advance_recovered": _f(recovered), "advance_left": _f(advance - recovered),
        "collected": _f(collected),
        "to_collect": _f(sum((claim.net for claim in invoiced), ZERO) - claim_paid),
        "cash_position": _f(collected - paid),
        "over_budget": committed > budget > 0,
    }


# Reading.

def _projects(business_unit):
    return (Project.objects.filter(business_unit=business_unit)
            .select_related("business_unit__legal_entity", "customer", "tender", "project_manager", "created_by",
                            "approved_by")
            .prefetch_related("boq_lines__factory_job", "boq_lines__steps", "variations__requested_by",
                              "variations__decided_by", "orders__lines__steps", "orders__payments__account",
                              "orders__requested_by", "orders__manager_by", "orders__finance_by",
                              "claims__prepared_by", "claims__approved_by", "claims__invoiced_by", "claims__receipts",
                              "receipts__account", "receipts__recorded_by", "receipts__claim", "bonds",
                              "factory_jobs__material_requests__store", "factory_jobs__material_requests__issues",
                              "factory_jobs__project_lines"))


def _claim_row(claim, actor, unit):
    received = sum((receipt.amount for receipt in claim.receipts.all()), ZERO)
    return {
        "id": claim.pk, "number": claim.number, "kind": claim.kind, "kind_label": claim.get_kind_display(),
        "period_end": claim.period_end.isoformat(), "status": claim.status, "status_label": claim.get_status_display(),
        "work_to_date": _f(claim.work_to_date), "previous": _f(claim.previous), "gross": _f(claim.gross),
        "retention": _f(claim.retention), "advance_recovery": _f(claim.advance_recovery),
        "retention_released": _f(claim.retention_released), "net": _f(claim.net), "lines": claim.lines,
        "received": _f(received), "note": claim.note, "decision_note": claim.decision_note,
        "prepared_by": _name(claim.prepared_by), "prepared_at": claim.prepared_at.isoformat(),
        "approved_by": _name(claim.approved_by), "invoice_number": claim.invoice_number,
        "invoiced_by": _name(claim.invoiced_by), "invoiced_on": claim.invoiced_on.isoformat() if claim.invoiced_on else "",
        "due_on": claim.due_on.isoformat() if claim.due_on else "",
        "requested_gross": _f(claim.requested_gross),
        "submitted_on": claim.submitted_on.isoformat() if claim.submitted_on else "",
        "certified_on": claim.certified_on.isoformat() if claim.certified_on else "",
        "consultant_reference": claim.consultant_reference,
        "can_submit": claim.status == C.APPROVED and (can_run(actor, unit) or can_approve(actor, unit)),
        "can_certify": claim.status == C.SUBMITTED and (can_run(actor, unit) or can_approve(actor, unit)),
        "can_decide": claim.status == C.WAITING and can_approve(actor, unit) and claim.prepared_by_id != actor.pk,
        "can_withdraw": claim.status == C.WAITING and claim.prepared_by_id == actor.pk,
        "can_invoice": claim.status == C.CERTIFIED and can_finance(actor, unit) and claim.prepared_by_id != actor.pk,
        "can_receive": claim.status == C.INVOICED and received < claim.net and can_finance(actor, unit),
    }


def _order_row(order, actor, unit):
    paid = _paid(order)
    return {
        "id": order.pk, "number": order.number, "kind": order.kind, "kind_label": order.get_kind_display(),
        "supplier": order.supplier, "description": order.description, "amount": _f(order.amount),
        "lines": [{"id": line.pk, "code": line.code, "area": line.area} for line in order.lines.all()],
        "budget_left": _f(order.budget_left), "over_budget": _f(order.over_budget), "over_reason": order.over_reason,
        "status": order.status, "status_label": order.get_status_display(), "reference": order.reference,
        "payment_due": order.payment_due.isoformat() if order.payment_due else "", "paid": _f(paid),
        "delivery": order.delivery, "expected_delivery": order.expected_delivery.isoformat() if order.expected_delivery else "",
        "instalments": [{**row, "amount": _f(row["amount"]), "paid": _f(row["paid"]),
                         "due_on": row["due_on"].isoformat() if row["due_on"] else ""} for row in instalments(order)],
        "to_pay": _f(order.amount - paid if order.status == O.APPROVED else ZERO),
        "payments": [{"id": payment.pk, "amount": _f(payment.amount), "paid_on": payment.paid_on.isoformat(),
                      "reference": payment.reference, "account": payment.account.name}
                     for payment in order.payments.all()],
        "requested_by": _name(order.requested_by), "requested_at": order.requested_at.isoformat(),
        "manager_by": _name(order.manager_by), "finance_by": _name(order.finance_by),
        "decision_note": order.decision_note, "finance_question": order.finance_question,
        "details_answer": order.details_answer,
        "can_manager": order.status == O.MANAGER and can_approve(actor, unit) and order.requested_by_id != actor.pk,
        "can_finance": (order.status == O.FINANCE and can_finance(actor, unit)
                        and actor.pk not in (order.requested_by_id, order.manager_by_id)),
        "can_answer": order.status == O.DETAILS and (order.requested_by_id == actor.pk or can_run(actor, unit)),
        "can_pay": order.status == O.APPROVED and paid < order.amount and can_finance(actor, unit),
        "can_cancel": order.status in ACTIVE_ORDER and paid == 0 and (can_run(actor, unit) or can_approve(actor, unit)),
    }


def _steps(line, today):
    """The item's schedule: each step's planned and actual date. What the factory did fills itself in."""
    found = {step.step: step for step in line.steps.all()}
    job = line.factory_job if line.factory_job_id and line.factory_job.status != JobStatus.CANCELLED else None
    automatic = {}
    if job is not None:
        automatic[STEP.ORDERED] = timezone.localtime(job.created_at).date()
        if job.finished_at:
            automatic[STEP.SHIPPED] = timezone.localtime(job.finished_at).date()
        if job.received_on:
            automatic[STEP.DELIVERED] = job.received_on
    rows = []
    for value, label in STEP.choices:
        step = found.get(value)
        done = step.done_on if step and step.done_on else automatic.get(value)
        planned = step.planned_on if step else None
        skip = bool(step and step.not_needed)
        rows.append({"step": value,
                     "label": "Made in the factory" if value == STEP.SHIPPED and line.source == SRC.FACTORY else label,
                     "planned_on": planned.isoformat() if planned else "", "done_on": done.isoformat() if done else "",
                     "not_needed": skip, "late": bool(planned and not done and not skip and planned < today),
                     "automatic": value in automatic and not (step and step.done_on), "note": step.note if step else ""})
    return rows


def _line_row(line, use, budgets, today):
    steps = _steps(line, today)
    reached = [row for row in steps if row["done_on"]]
    return {
        "id": line.pk, "position": line.position, "code": line.code, "section": line.section, "area": line.area,
        "floor": line.floor, "work_type": line.work_type, "price_group": line.price_group, "category": line.category,
        "description": line.description, "quantity": format(line.quantity.normalize(), "f"), "unit": line.unit,
        "rate": _f(line.rate), "cost_rate": _f(line.cost_rate), "value": _f(line_value(line)),
        "budget": _f(budgets[line.pk]), "used": _f(use[line.pk]), "left": _f(budgets[line.pk] - use[line.pk]),
        "over": use[line.pk] > budgets[line.pk], "supplier": line.supplier, "remarks": line.remarks,
        "rfq_date": line.rfq_date.isoformat() if line.rfq_date else "", "source": line.source,
        "source_label": line.get_source_display(), "done_percent": _f(line.done_percent),
        "stage": reached[-1]["label"] if reached else "Not started",
        "late": any(row["late"] for row in steps), "steps": steps,
        "factory_job": ({"id": line.factory_job_id, "number": line.factory_job.number,
                         "status_label": line.factory_job.get_status_display()}
                        if line.factory_job_id and line.factory_job.status != JobStatus.CANCELLED else None),
    }


def _job_row(job):
    return {"id": job.pk, "number": job.number, "status": job.status, "status_label": job.get_status_display(),
            "due_date": job.due_date.isoformat(), "cost": _f(job_cost(job)),
            "estimate": _f(job.estimate_materials + job.estimate_labour + job.estimate_overhead),
            "lines": [line.code for line in job.project_lines.all()],
            "slips": [{"id": slip.pk, "number": slip.number, "status_label": slip.get_status_display(),
                       "store": slip.store.code if slip.store_id else "", "lines": len(slip.issues.all())}
                      for slip in job.material_requests.all()]}


def cash_plan(projects, today=None):
    """Cash in and cash out, week by week: the last four weeks as paid, then what is planned for the next twelve."""
    today = today or timezone.localdate()
    start = today - datetime.timedelta(days=today.weekday())
    weeks = [start + datetime.timedelta(weeks=index) for index in range(-4, CASH_WEEKS)]
    rows = {week: {"week": week.isoformat(), "in_actual": ZERO, "out_actual": ZERO, "in_planned": ZERO,
                   "out_planned": ZERO, "items": []} for week in weeks}
    overdue = {"in": ZERO, "out": ZERO}
    opening = ZERO   # cash in less cash out before the first week shown

    def week_of(day):
        return day - datetime.timedelta(days=day.weekday())

    def plan(day, kind, amount, text):
        if amount <= 0:
            return
        if day < start:
            overdue[kind] += amount
            day = start
        week = week_of(day)
        if week in rows:
            rows[week][f"{kind}_planned"] += amount
            rows[week]["items"].append({"kind": kind, "text": text, "amount": _f(amount), "on": day.isoformat()})

    for project in projects:
        found = project_settings(project.business_unit.legal_entity)
        for receipt in project.receipts.all():
            week = week_of(receipt.received_on)
            if week in rows:
                rows[week]["in_actual"] += receipt.amount
            elif week < weeks[0]:
                opening += receipt.amount
        for order in project.orders.all():
            for payment in order.payments.all():
                week = week_of(payment.paid_on)
                if week in rows:
                    rows[week]["out_actual"] += payment.amount
                elif week < weeks[0]:
                    opening -= payment.amount
            if order.status == O.APPROVED:
                for row in instalments(order, today):
                    plan(row["due_on"], "out", row["amount"] - row["paid"],
                         f"{project.number} {order.number} {order.supplier} ({row['label'].lower()})")
        if project.status in LIVE:
            numbers = totals(project)
            plan(project.signed_on, "in",
                 Decimal(numbers["advance_expected"]) - Decimal(numbers["advance_received"]), f"{project.number} advance")
        for claim in project.claims.all():
            received = sum((receipt.amount for receipt in claim.receipts.all()), ZERO)
            if claim.status == C.INVOICED:
                plan(claim.due_on or today, "in", claim.net - received,
                     f"{project.number} {claim.number} {claim.invoice_number}")
            elif claim.status in OPEN_CLAIM:
                plan(today + datetime.timedelta(days=found.claim_due_days), "in", claim.net,
                     f"{project.number} {claim.number} (not invoiced yet)")
    running = opening
    result = []
    for week in weeks:
        row = rows[week]
        net = row["in_actual"] - row["out_actual"] + row["in_planned"] - row["out_planned"]
        running += net
        result.append({**row, "in_actual": _f(row["in_actual"]), "out_actual": _f(row["out_actual"]),
                       "in_planned": _f(row["in_planned"]), "out_planned": _f(row["out_planned"]),
                       "net": _f(net), "running": _f(running), "past": week < start, "current": week == start})
    return {"weeks": result, "opening": _f(opening), "overdue_in": _f(overdue["in"]), "overdue_out": _f(overdue["out"])}


def bond_row(bond, today=None, reminder_days=30):
    today = today or timezone.localdate()
    days = (bond.expires_on - today).days if bond.expires_on else None
    return {"id": bond.pk, "kind": bond.kind, "kind_label": bond.get_kind_display(), "reference": bond.reference,
            "issuer": bond.issuer, "amount": _f(bond.amount),
            "issued_on": bond.issued_on.isoformat() if bond.issued_on else "",
            "expires_on": bond.expires_on.isoformat() if bond.expires_on else "", "days_left": days,
            "expiring": bond.status == "active" and days is not None and 0 <= days <= reminder_days,
            "expired": bond.status == "active" and days is not None and days < 0,
            "status": bond.status, "status_label": bond.get_status_display(), "note": bond.note,
            "tender_id": bond.tender_id, "project_id": bond.project_id}


def _project_row(project, actor, *, full=False):
    unit, today = project.business_unit, timezone.localdate()
    runs, approves, finance = can_run(actor, unit), can_approve(actor, unit), can_finance(actor, unit)
    status = project.status
    claims = list(project.claims.all())
    open_claim = any(claim.status in OPEN_CLAIM for claim in claims)
    numbers = totals(project)
    defects_end = (project.handed_over_on + datetime.timedelta(days=project.warranty_days)
                   if project.handed_over_on else None)
    use, budgets = _line_use(project)
    lines = [_line_row(line, use, budgets, today) for line in project.boq_lines.all()]
    orders = list(project.orders.all())
    reminder = project_settings(unit.legal_entity).bond_reminder_days
    row = {
        "id": project.pk, "number": project.number, "name": project.name, "status": status,
        "status_label": project.get_status_display(),
        "customer": {"id": project.customer_id, "name": project.customer.name,
                     "project_number": project.customer.project_number or ""},
        "tender": ({"id": project.tender_id, "number": project.tender.number} if project.tender_id else None),
        "site": project.site, "design_reference": project.design_reference,
        "quote_reference": project.quote_reference, "boq_file_name": project.boq_file_name,
        "project_manager": ({"id": project.project_manager_id, "name": _name(project.project_manager)}
                            if project.project_manager_id else None),
        "start_date": project.start_date.isoformat() if project.start_date else "",
        "handover_due": project.handover_due.isoformat() if project.handover_due else "",
        "handed_over_on": project.handed_over_on.isoformat() if project.handed_over_on else "",
        "defects_end": defects_end.isoformat() if defects_end else "",
        "late": status == PS.RUNNING and project.handover_due is not None and project.handover_due < today,
        "note": project.note, "contract_reference": project.contract_reference,
        "signed_on": project.signed_on.isoformat(),
        "advance_percent": _f(project.advance_percent), "retention_percent": _f(project.retention_percent),
        "retention_release": project.retention_release, "warranty_days": project.warranty_days,
        "cancel_reason": project.cancel_reason,
        "created_by": _name(project.created_by), "approved_by": _name(project.approved_by),
        "totals": numbers, "items": len(lines), "late_items": sum(1 for line in lines if line["late"]),
        "over_items": sum(1 for line in lines if line["over"]),
        "factory_items": sum(1 for line in lines if line["source"] == SRC.FACTORY),
        "waiting": ((status == PS.SETUP) + sum(1 for claim in claims if claim.status in OPEN_CLAIM)
                    + sum(1 for variation in project.variations.all() if variation.status == V.WAITING)
                    + sum(1 for order in orders if order.status in WAITING_ORDER)),
        "bonds_expiring": sum(1 for bond in project.bonds.all() if bond_row(bond, today, reminder)["expiring"]),
        "can_edit": runs and status in LIVE,
        "can_edit_budget": runs and status == PS.SETUP,
        "can_discount": (runs and status == PS.SETUP) or (approves and status in LIVE),
        "can_approve": approves and status == PS.SETUP and project.created_by_id != actor.pk,
        "can_order": runs and status in WORKING,
        "can_schedule": runs and status in LIVE,
        "can_progress": runs and status in WORKING,
        "can_vary": runs and status in LIVE,
        "can_claim": runs and status in WORKING and not open_claim,
        "can_release": runs and status == PS.HANDOVER and not open_claim and Decimal(numbers["retention_held"]) > 0,
        "can_handover": runs and status == PS.RUNNING,
        "can_close": ((runs or approves) and status == PS.HANDOVER and not open_claim
                      and Decimal(numbers["retention_held"]) == 0),
        "can_cancel": ((runs or approves) and status == PS.SETUP and not project.receipts.exists()
                       and not project.factory_jobs.exclude(status=JobStatus.CANCELLED).exists()),
        "can_receive": finance and status in LIVE,
        "can_send_to_factory": runs and status in WORKING,
        "can_bond": runs or finance,
    }
    if not full:
        return row
    row.update({
        "boq": lines,
        "variations": [{"id": v.pk, "number": v.number, "description": v.description, "value": _f(v.value),
                        "cost_estimate": _f(v.cost_estimate), "status": v.status, "status_label": v.get_status_display(),
                        "client_reference": v.client_reference,
                        "client_signed_on": v.client_signed_on.isoformat() if v.client_signed_on else "",
                        "done_percent": _f(v.done_percent), "decision_note": v.decision_note,
                        "requested_by": _name(v.requested_by), "decided_by": _name(v.decided_by),
                        "can_decide": v.status == V.WAITING and approves and v.requested_by_id != actor.pk,
                        "can_sign": v.status == V.AT_RISK and (runs or approves)}
                       for v in project.variations.all()],
        "orders": [_order_row(order, actor, unit) for order in orders],
        "claims": [_claim_row(claim, actor, unit) for claim in claims],
        "receipts": [{"id": receipt.pk, "kind": receipt.kind, "kind_label": receipt.get_kind_display(),
                      "claim": receipt.claim.number if receipt.claim_id else "", "amount": _f(receipt.amount),
                      "received_on": receipt.received_on.isoformat(), "reference": receipt.reference,
                      "account": receipt.account.name, "recorded_by": _name(receipt.recorded_by)}
                     for receipt in project.receipts.all()],
        "factory_jobs": [_job_row(job) for job in project.factory_jobs.all()],
        "cash": cash_plan([project], today),
        "bonds": [bond_row(bond, today, reminder) for bond in project.bonds.all()],
    })
    return row


def _settings_row(found):
    return {"is_live": found.is_live, "advance_percent": _f(found.advance_percent),
            "retention_percent": _f(found.retention_percent), "retention_release": found.retention_release,
            "warranty_days": found.warranty_days,
            "bond_reminder_days": found.bond_reminder_days, "claim_due_days": found.claim_due_days,
            "updated_by": _name(found.updated_by), "updated_at": found.updated_at.isoformat() if found.updated_at else ""}


def options(actor, business_unit):
    """The lists the project screens choose from."""
    customers = SyntheticCounterparty.objects.filter(party_type=SyntheticCounterparty.Type.CUSTOMER,
                                                     is_synthetic=True).order_by("name")[:500]
    accounts = [{"id": account.pk, "name": account.name, "kind_label": account.get_kind_display()}
                for account in CashAccount.objects.filter(business_unit=business_unit, is_synthetic=True)
                .order_by("kind", "name")] if can_finance(actor, business_unit) else []
    return {
        "customers": [{"id": customer.pk, "name": customer.name,
                       "number": customer.project_number or customer.client_number or ""} for customer in customers],
        "people": people(business_unit), "accounts": accounts,
        "sources": [{"id": value, "label": label} for value, label in SRC.choices],
        "steps": [{"id": value, "label": label} for value, label in STEP.choices],
        "order_kinds": [{"id": value, "label": label} for value, label in ProjectOrder.Kind.choices],
        "can_run": can_run(actor, business_unit), "can_approve": can_approve(actor, business_unit),
        "can_finance": can_finance(actor, business_unit), "is_master_admin": is_master_admin(actor),
    }


def projects_overview(*, actor, business_unit):
    require_viewer(actor, business_unit)
    found = project_settings(business_unit.legal_entity)
    rank = {PS.SETUP: 0, PS.RUNNING: 1, PS.HANDOVER: 2, PS.CLOSED: 3, PS.CANCELLED: 4}
    projects = sorted(_projects(business_unit), key=lambda project: (rank[project.status], project.number))
    rows = [_project_row(project, actor) for project in projects]
    working = [row for row in rows if row["status"] in WORKING]
    approvals = []
    for project in projects:
        if project.status == PS.SETUP and can_approve(actor, business_unit) and project.created_by_id != actor.pk:
            approvals.append({"type": "budget", "project": project.number, "project_id": project.pk,
                              "id": project.pk, "number": project.number, "description": project.name,
                              "amount": _f(sum((line_budget(line) for line in project.boq_lines.all()), ZERO)),
                              "status_label": project.get_status_display()})
        for order in project.orders.all():
            row = _order_row(order, actor, business_unit)
            if row["can_manager"] or row["can_finance"] or row["can_pay"] or row["can_answer"]:
                approvals.append({"type": "order", "project": project.number, "project_id": project.pk, **row})
        for variation in project.variations.all():
            if variation.status == V.WAITING and can_approve(actor, business_unit) and variation.requested_by_id != actor.pk:
                approvals.append({"type": "variation", "project": project.number, "project_id": project.pk,
                                  "id": variation.pk, "number": variation.number, "description": variation.description,
                                  "amount": _f(variation.value), "status_label": variation.get_status_display()})
        for claim in project.claims.all():
            row = _claim_row(claim, actor, business_unit)
            if row["can_decide"] or row["can_submit"] or row["can_certify"] or row["can_invoice"] or row["can_receive"]:
                approvals.append({"type": "claim", "project": project.number, "project_id": project.pk, **row})
    live = [project for project in projects if project.status in LIVE]
    return {
        "unit": {"id": business_unit.pk, "name": business_unit.name}, "today": timezone.localdate().isoformat(),
        "settings": _settings_row(found),
        "tiles": {
            "running": len(working),
            "contract_value": _f(sum((Decimal(row["totals"]["contract"]) for row in working), ZERO)),
            "budget_left": _f(sum((Decimal(row["totals"]["budget_left"]) for row in working), ZERO)),
            "to_bill": _f(sum((Decimal(row["totals"]["to_bill"]) for row in working), ZERO)),
            "to_collect": _f(sum((Decimal(row["totals"]["to_collect"]) for row in rows), ZERO)),
            "retention_held": _f(sum((Decimal(row["totals"]["retention_held"]) for row in rows), ZERO)),
            "waiting": len(approvals),
            "late_items": sum(row["late_items"] for row in rows),
            "over_budget": sum(1 for row in rows if row["totals"]["over_budget"] or row["over_items"]),
        },
        "projects": rows, "approvals": approvals, "cash": cash_plan(live),
        "tenders": [{"id": tender.pk, "number": tender.number, "client_name": tender.client_name,
                     "project_name": tender.project_name, "total": _f(tender.total), "customer_id": tender.customer_id,
                     "quote_reference": tender.quote_reference}
                    for tender in Tender.objects.filter(business_unit=business_unit, status=Tender.Status.WON)
                    .order_by("-pk")[:200]],
        **options(actor, business_unit), "policy": POLICY_LABEL,
    }


def project_detail(*, actor, business_unit, project_id):
    require_viewer(actor, business_unit)
    project = _projects(business_unit).filter(pk=_int(project_id)).first()
    if project is None:
        raise InvalidTransaction("That project was not found.")
    return _project_row(project, actor, full=True)


def _detail(project, actor):
    return _project_row(_projects(project.business_unit).get(pk=project.pk), actor, full=True)


# Opening a project from its signed BOQ.

def _customer(value):
    customer = SyntheticCounterparty.objects.select_for_update().filter(
        pk=_int(value), party_type=SyntheticCounterparty.Type.CUSTOMER, is_synthetic=True).first()
    if customer is None:
        raise InvalidTransaction("Choose the client. Register them under Customers first if they are new.")
    return customer


def give_project_number(customer, actor):
    """A project client gets a project number (P-000123) the first time a project is opened for them."""
    if customer.project_number:
        return
    number = next_customer_number("project")
    customer.project_number = number
    customer.save(update_fields=["project_number"])
    CustomerContactChange.objects.create(customer=customer, changed_by=actor, before={"project_number": ""},
                                         after={"project_number": number})


def _boq(value):
    if not isinstance(value, list) or not value:
        raise InvalidTransaction("Upload the signed BOQ: the project needs its items.")
    if len(value) > MAX_LINES:
        raise InvalidTransaction(f"A project can hold at most {MAX_LINES} items.")
    lines = []
    for number, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise InvalidTransaction(f"BOQ item {number} is not readable.")
        code = _text(raw.get("code"), f"code of item {number}", 20, required=True)
        source = raw.get("source") or guess_source(str(raw.get("price_group") or ""))
        if source not in SRC.values:
            raise InvalidTransaction(f"Choose who supplies {code}.")
        quantity = _decimal(raw.get("quantity"))
        if quantity is None or quantity <= 0 or quantity >= Decimal(100000000):
            raise InvalidTransaction(f"The quantity of {code} must be more than 0.")
        rates = []
        for key, field in (("rate", "selling price"), ("cost_rate", "cost")):
            amount = _decimal(raw.get(key) if raw.get(key) not in (None, "") else "0")
            if amount is None or amount < 0 or amount >= MONEY_LIMIT:
                raise InvalidTransaction(f"The {field} of {code} must be 0 or more.")
            rates.append(amount.quantize(Decimal("0.0001")))
        lines.append(ProjectBoqLine(
            position=number, code=code, section=_text(raw.get("section"), "zone", 160),
            area=_text(raw.get("area"), "area", 160), floor=_text(raw.get("floor"), "floor", 120),
            work_type=_text(raw.get("work_type"), "kind of work", 60),
            price_group=_text(raw.get("price_group"), "category", 40),
            category=_text(raw.get("category"), "sub-category", 80),
            description=str(raw.get("description") or "").strip()[:2000] or code,
            quantity=_round3(quantity), unit=_text(raw.get("unit"), "unit", 20) or "piece",
            rate=rates[0], cost_rate=rates[1], supplier=_text(raw.get("supplier"), "supplier", 160),
            remarks=_text(raw.get("remarks"), "remarks", 300),
            rfq_date=optional_day(raw.get("rfq_date"), "RFQ date"), source=source))
    return lines


@transaction.atomic
def create_project(*, actor, business_unit, data):
    """Open a signed project from its BOQ. Its budget then waits for the Construction Manager."""
    require(actor, business_unit, can_run, RUN)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the project's details.")
    customer = _customer(data.get("customer_id"))
    tender = None
    if data.get("tender_id") not in (None, ""):
        tender = Tender.objects.filter(pk=_int(data.get("tender_id")), business_unit=business_unit,
                                       status=Tender.Status.WON).first()
        if tender is None:
            raise InvalidTransaction("Choose a won tender of this unit, or leave it empty.")
    lines = _boq(data.get("boq"))
    found = ProjectSettings.objects.select_for_update().get(pk=project_settings(business_unit.legal_entity).pk)
    discount = _percent(data.get("discount_percent") or "0", "discount percent").quantize(Decimal("0.0001"))
    days = data.get("warranty_days", found.warranty_days)
    if _int(days) is None or not 0 <= days <= 2000:
        raise InvalidTransaction("The warranty period is between 0 and 2,000 days.")
    release = data.get("retention_release") or found.retention_release
    if release not in RELEASES:
        raise InvalidTransaction("Choose when the retention is paid: at handover or after the warranty period.")
    advance = _percent(data.get("advance_percent", found.advance_percent), "down payment percent")
    number = f"PRJ-{found.next_number:04d}"
    found.next_number += 1
    found.save(update_fields=["next_number"])
    give_project_number(customer, actor)
    gross = sum((_round(line.quantity * line.rate) for line in lines), ZERO)
    project = Project.objects.create(
        business_unit=business_unit, number=number, customer=customer, tender=tender, created_by=actor,
        name=_text(data.get("name"), "project name", 160, required=True),
        site=_text(data.get("site"), "site", 160),
        design_reference=_text(data.get("design_reference"), "design package reference", 120),
        quote_reference=_text(data.get("quote_reference"), "quotation reference", 80),
        boq_file_name=_text(data.get("boq_file_name"), "BOQ file name", 200),
        project_manager=person(business_unit, data.get("project_manager_id")),
        contract_reference=_text(data.get("contract_reference"), "contract or LPO reference", 60, required=True),
        signed_on=_past_day(data.get("signed_on"), "date signed"),
        start_date=optional_day(data.get("start_date"), "start date"),
        handover_due=optional_day(data.get("handover_due"), "handover date"),
        note=_text(data.get("note"), "note", 300), discount_percent=discount,
        contract_value=_round(gross * (HUNDRED - discount) / HUNDRED),
        advance_percent=advance,
        retention_percent=_percent(data.get("retention_percent", found.retention_percent), "retention percent",
                                   Decimal(50)),
        retention_release=release, warranty_days=days)
    for line in lines:
        line.project = project
    ProjectBoqLine.objects.bulk_create(lines)
    if tender is not None and tender.customer_id is None:
        tender.customer = customer
        tender.save(update_fields=["customer"])
    return _detail(project, actor)


def _load(business_unit, project_id):
    project = (Project.objects.select_for_update(of=("self",)).filter(pk=_int(project_id), business_unit=business_unit)
               .select_related("business_unit__legal_entity", "customer").first())
    if project is None:
        raise InvalidTransaction("That project was not found.")
    return project


@transaction.atomic
def update_project(*, actor, business_unit, project_id, data):
    """Change the details; until the budget is approved, also each item's cost; who supplies an item until the
    factory has it."""
    require(actor, business_unit, can_run, RUN)
    project = _load(business_unit, project_id)
    if project.status not in LIVE:
        raise InvalidTransaction(f"{project.number} is {project.get_status_display().lower()} and can no longer change.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the project's details.")
    if "name" in data:
        project.name = _text(data.get("name"), "project name", 160, required=True)
    for key, limit in (("site", 160), ("design_reference", 120), ("note", 300)):
        if key in data:
            setattr(project, key, _text(data.get(key), key.replace("_", " "), limit))
    if "project_manager_id" in data:
        project.project_manager = person(business_unit, data.get("project_manager_id"))
    for key in ("start_date", "handover_due"):
        if key in data:
            setattr(project, key, optional_day(data.get(key), key.replace("_", " ")))
    project.save()
    if "boq" in data:
        _update_lines(project, data.get("boq"))
    return _detail(project, actor)


def _update_lines(project, value):
    if not isinstance(value, list):
        raise InvalidTransaction("Send the BOQ items as a list.")
    lines = {line.pk: line for line in project.boq_lines.select_for_update(of=("self",)).select_related("factory_job")}
    for raw in value:
        if not isinstance(raw, dict) or _int(raw.get("id")) not in lines:
            raise InvalidTransaction("An item was not found on this project. Changes to the contract are variations.")
        line = lines[_int(raw.get("id"))]
        changed = []
        if "cost_rate" in raw:
            cost = _decimal(raw.get("cost_rate"))
            if cost is None or cost < 0:
                raise InvalidTransaction(f"The cost of {line.code} must be 0 or more.")
            if cost.quantize(Decimal("0.0001")) != line.cost_rate:
                if project.status != PS.SETUP:
                    raise InvalidTransaction("The budget is approved. Going over it is an order over budget, approved "
                                             "by the Construction Manager and Finance.")
                line.cost_rate = cost.quantize(Decimal("0.0001"))
                changed.append("cost_rate")
        if raw.get("source") not in (None, "", line.source):
            if raw["source"] not in SRC.values:
                raise InvalidTransaction(f"Choose who supplies {line.code}.")
            if line.factory_job_id and line.factory_job.status != JobStatus.CANCELLED:
                raise InvalidTransaction(f"{line.code} is already with the factory on {line.factory_job.number}.")
            line.source = raw["source"]
            changed.append("source")
        if "supplier" in raw:
            line.supplier = _text(raw.get("supplier"), "supplier", 160)
            changed.append("supplier")
        if changed:
            line.save(update_fields=changed)


@transaction.atomic
def project_action(*, actor, business_unit, project_id, data):
    require_user(actor, business_unit)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send what to do.")
    project = _load(business_unit, project_id)
    action, today = data.get("action"), timezone.localdate()
    if action == "approve":
        require(actor, business_unit, can_approve, APPROVE)
        if project.status != PS.SETUP:
            raise InvalidTransaction("This project's budget is not waiting for approval.")
        if project.created_by_id == actor.pk:
            raise WorkflowPermissionDenied("You opened this project, so the Construction Manager approves its budget.")
        budget = sum((line_budget(line) for line in project.boq_lines.all()), ZERO)
        if budget <= 0:
            raise InvalidTransaction("The items have no cost yet: the budget is what the BOQ's items cost.")
        project.budget_cost, project.approved_by, project.approved_at = budget, actor, timezone.now()
        project.start_date = project.start_date or today
        project.status = PS.RUNNING
    elif action == "discount":
        if project.status not in LIVE:
            raise InvalidTransaction("The discount changes only while the project is open.")
        if not ((project.status == PS.SETUP and can_run(actor, business_unit)) or can_approve(actor, business_unit)):
            raise WorkflowPermissionDenied("Once the budget is approved, only the Construction Manager changes the "
                                           "discount.")
        project.discount_percent = _percent(data.get("discount_percent"), "discount percent").quantize(Decimal("0.0001"))
        project.contract_value = final_value(project)
        if project.claims.filter(status__in=(*OPEN_CLAIM, C.INVOICED)).exists() and project.contract_value < sum(
                (claim.gross for claim in project.claims.filter(status=C.INVOICED, kind=K.PROGRESS)), ZERO):
            raise InvalidTransaction("With this discount the contract would be less than what is already invoiced.")
    elif action == "handover":
        require(actor, business_unit, can_run, RUN)
        if project.status != PS.RUNNING:
            raise InvalidTransaction("Only a project in progress can be handed over.")
        project.handed_over_on = _past_day(data.get("date"), "handover date")
        if project.start_date and project.handed_over_on < project.start_date:
            raise InvalidTransaction("The handover date cannot be before the project started.")
        project.status = PS.HANDOVER
    elif action == "close":
        if not (can_run(actor, business_unit) or can_approve(actor, business_unit)):
            raise WorkflowPermissionDenied("You need a project permission in this unit.")
        if project.status != PS.HANDOVER:
            raise InvalidTransaction("Only a handed-over project can be closed.")
        if project.claims.filter(status__in=OPEN_CLAIM).exists():
            raise InvalidTransaction("Finish the open claim first.")
        if Decimal(totals(project)["retention_held"]) > 0:
            raise InvalidTransaction("The client still holds retention. Release it with a retention claim first.")
        project.status = PS.CLOSED
    elif action == "cancel":
        if not (can_run(actor, business_unit) or can_approve(actor, business_unit)):
            raise WorkflowPermissionDenied("You need a project permission in this unit.")
        if project.status != PS.SETUP:
            raise InvalidTransaction("Only a project whose budget is not yet approved can be cancelled here.")
        if project.receipts.exists() or project.factory_jobs.exclude(status=JobStatus.CANCELLED).exists():
            raise InvalidTransaction("The client has paid or the factory has a job for it, so it cannot be cancelled.")
        project.cancel_reason = _text(data.get("note"), "reason", 200, required=True)
        project.status = PS.CANCELLED
    else:
        raise InvalidTransaction("Choose approve, discount, handover, close or cancel.")
    project.save()
    return _detail(project, actor)


# Budget control: orders to suppliers and subcontractors, and paying them.

def _terms(value):
    """An order's payment terms: instalments that add up to 100%, each due on an event or a date."""
    if value in (None, "", []):
        return []
    if not isinstance(value, list) or len(value) > 8:
        raise InvalidTransaction("Give the payment terms as up to 8 instalments.")
    rows, total = [], ZERO
    for number, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise InvalidTransaction(f"Instalment {number} is not readable.")
        event = raw.get("event") or "date"
        if event not in EVENTS:
            raise InvalidTransaction(f"Choose when instalment {number} is paid.")
        percent = _percent(raw.get("percent"), f"percent of instalment {number}")
        if percent <= 0:
            raise InvalidTransaction(f"Instalment {number} needs a percent above 0.")
        due = optional_day(raw.get("due_on"), f"due date of instalment {number}")
        if event == "date" and due is None:
            raise InvalidTransaction(f"Give the date instalment {number} is due.")
        total += percent
        rows.append({"label": _text(raw.get("label"), f"name of instalment {number}", 60) or EVENTS[event],
                     "percent": _f(percent), "event": event, "due_on": due.isoformat() if due else ""})
    if total != HUNDRED:
        raise InvalidTransaction(f"The instalments add up to {total.normalize()}%, not 100%.")
    return rows


def instalments(order, today=None):
    """Each instalment with its amount, what is paid of it, and when it falls due: its date, or the planned (or
    actual) date of its event on the order's items; payments are counted against the instalments in turn."""
    today = today or timezone.localdate()
    ordered_on = timezone.localtime(order.requested_at).date()
    terms = order.terms or [{"label": "Full payment", "percent": "100.00", "event": "date",
                             "due_on": order.payment_due.isoformat() if order.payment_due else ""}]
    steps = {}
    for line in order.lines.all():
        for step in line.steps.all():
            day = step.done_on or step.planned_on
            if day and (step.step not in steps or day > steps[step.step]):
                steps[step.step] = day
    if order.expected_delivery and STEP.DELIVERED not in steps:
        steps[STEP.DELIVERED] = order.expected_delivery
    fallback = order.payment_due or today + datetime.timedelta(days=30)
    paid, given, rows = _paid(order), ZERO, []
    for index, term in enumerate(terms):
        amount = (order.amount - given if index == len(terms) - 1
                  else _round(order.amount * Decimal(term["percent"]) / HUNDRED))
        given += amount
        event = term.get("event") or "date"
        if term.get("due_on"):
            due = datetime.date.fromisoformat(term["due_on"])
        elif event in ("order", "production"):
            due = ordered_on
        else:
            due = steps.get(event) or fallback
        covered = min(max(paid, ZERO), amount)
        paid -= covered
        rows.append({"label": term.get("label") or EVENTS[event], "percent": term["percent"], "event": event,
                     "event_label": EVENTS[event], "amount": amount, "paid": covered, "due_on": due})
    return rows


def budget_left(project, lines, exclude=None):
    """The budget still free on these items (or, with no items, on approved variations)."""
    if not lines:
        variations = sum((v.cost_estimate for v in project.variations.all() if v.status in (V.SIGNED, V.AT_RISK)), ZERO)
        used = sum((order.amount for order in project.orders.all() if order.status in ACTIVE_ORDER
                    and not order.lines.exists() and order.pk != getattr(exclude, "pk", None)), ZERO)
        return variations - used
    use, budgets = _line_use(project, exclude)
    return sum((budgets[line.pk] - use[line.pk] for line in lines), ZERO)


@transaction.atomic
def create_order(*, actor, business_unit, project_id, data):
    """The Project Manager's order to a supplier or subcontractor. Within its items' budget it is approved at once;
    any excess is registered with its reason for the Construction Manager, then Finance."""
    require(actor, business_unit, can_run, RUN)
    project = _load(business_unit, project_id)
    if project.status not in WORKING:
        raise InvalidTransaction("Orders are placed once the budget is approved.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the order.")
    kind = data.get("kind")
    if kind not in ProjectOrder.Kind.values:
        raise InvalidTransaction("Choose a supplier order, a subcontract or another project cost.")
    ids = data.get("line_ids") or []
    if not isinstance(ids, list):
        raise InvalidTransaction("Send the items as a list.")
    lines = list(project.boq_lines.filter(pk__in=[_int(value) for value in ids]))
    if len(lines) != len(set(ids)):
        raise InvalidTransaction("An item was not found on this project.")
    if not lines and kind != ProjectOrder.Kind.OTHER:
        raise InvalidTransaction("Choose the BOQ items this order is for, so it is checked against their budget.")
    amount = money(data.get("amount"), "order amount (QAR)", allow_zero=False)
    left = max(budget_left(project, lines), ZERO)
    over = max(amount - left, ZERO)
    reason = _text(data.get("over_reason"), "reason for going over budget", 300, required=over > 0)
    delivery = data.get("delivery") or "immediate"
    if delivery not in DELIVERIES:
        raise InvalidTransaction("Choose whether it is delivered at once or made and shipped first.")
    found = ProjectSettings.objects.select_for_update().get(pk=project_settings(business_unit.legal_entity).pk)
    order = ProjectOrder.objects.create(
        project=project, number=f"PO-{found.next_order:04d}", kind=kind,
        supplier=_text(data.get("supplier"), "supplier or subcontractor", 160, required=True),
        description=_text(data.get("description"), "description", 300, required=True), amount=amount,
        budget_left=left, over_budget=over, over_reason=reason, status=O.MANAGER if over > 0 else O.APPROVED,
        reference=_text(data.get("reference"), "supplier quotation or LPO number", 80),
        payment_due=optional_day(data.get("payment_due"), "payment due date"), requested_by=actor,
        terms=_terms(data.get("terms")), delivery=delivery,
        expected_delivery=optional_day(data.get("expected_delivery"), "expected delivery date"))
    found.next_order += 1
    found.save(update_fields=["next_order"])
    order.lines.set(lines)
    if order.status == O.APPROVED:
        _approved(order, actor)
    return _detail(project, actor)


def _approved(order, actor):
    """An approved order marks its items ordered, and plans their delivery when the supplier gave a date."""
    lines = list(order.lines.all())
    _mark(lines, STEP.ORDERED, timezone.localdate(), actor)
    if order.expected_delivery:
        for line in lines:
            step, _ = ProjectLineStep.objects.get_or_create(line=line, step=STEP.DELIVERED)
            if not step.planned_on and not step.done_on:
                step.planned_on, step.updated_by, step.updated_at = order.expected_delivery, actor, timezone.now()
                step.save(update_fields=["planned_on", "updated_by", "updated_at"])


def _mark(lines, step, day, actor):
    for line in lines:
        found, _ = ProjectLineStep.objects.get_or_create(line=line, step=step)
        if not found.done_on:
            found.done_on, found.updated_by, found.updated_at = day, actor, timezone.now()
            found.save(update_fields=["done_on", "updated_by", "updated_at"])


@transaction.atomic
def order_action(*, actor, business_unit, order_id, data):
    require_user(actor, business_unit)
    order = (ProjectOrder.objects.select_for_update(of=("self",))
             .filter(pk=_int(order_id), project__business_unit=business_unit).first())
    if order is None:
        raise InvalidTransaction("That order was not found.")
    project = _load(business_unit, order.project_id)
    data = data if isinstance(data, dict) else {}
    action, now = data.get("action"), timezone.now()
    if action in ("approve", "reject") and order.status == O.MANAGER:
        require(actor, business_unit, can_approve, APPROVE)
        if order.requested_by_id == actor.pk:
            raise WorkflowPermissionDenied("You asked for this order, so the Construction Manager decides it.")
        order.manager_by, order.manager_at = actor, now
        order.status = O.FINANCE if action == "approve" else O.REJECTED
    elif action in ("approve", "reject", "ask") and order.status == O.FINANCE:
        require(actor, business_unit, can_finance, FINANCE)
        if actor.pk in (order.requested_by_id, order.manager_by_id):
            raise WorkflowPermissionDenied("You asked for or already approved this order, so someone else in Finance "
                                           "decides it.")
        if action == "ask":
            order.finance_question = _text(data.get("note"), "details Finance needs", 300, required=True)
            order.status = O.DETAILS
            order.save()
            return _detail(project, actor)
        order.finance_by, order.finance_at = actor, now
        order.status = O.APPROVED if action == "approve" else O.REJECTED
        if order.status == O.APPROVED:
            _approved(order, actor)
    elif action == "answer":
        if order.status != O.DETAILS:
            raise InvalidTransaction(f"Finance has not asked for details on {order.number}.")
        if order.requested_by_id != actor.pk:
            require(actor, business_unit, can_run, RUN)
        order.details_answer = _text(data.get("note"), "details for Finance", 500, required=True)
        order.status = O.FINANCE
        order.save()
        return _detail(project, actor)
    elif action in ("approve", "reject", "ask"):
        raise InvalidTransaction(f"{order.number} is not waiting for that decision.")
    elif action == "cancel":
        if not (can_run(actor, business_unit) or can_approve(actor, business_unit)):
            raise WorkflowPermissionDenied("You need a project permission in this unit.")
        if order.status not in ACTIVE_ORDER or order.payments.exists():
            raise InvalidTransaction("Only an order not yet paid can be cancelled.")
        order.status = O.CANCELLED
    elif action == "pay":
        return _pay(actor, business_unit, project, order, data)
    else:
        raise InvalidTransaction("Choose approve, reject, ask for details, answer, cancel or pay.")
    order.decision_note = _text(data.get("note"), "reason" if action in ("reject", "cancel") else "note", 200,
                                required=action in ("reject", "cancel"))
    order.save()
    return _detail(project, actor)


def _cash_day(business_unit, data, field):
    account = _cash_account(business_unit, _int(data.get("account_id")), "bank account or cash box")
    day = _past_day(data.get("date"), field)
    if day < account.opening_date:
        raise InvalidTransaction(f"{account.name} opened on {account.opening_date.isoformat()}, after this date.")
    if _reconciled(account, day):
        raise InvalidTransaction(f"{day:%B %Y} is reconciled and approved for {account.name}. Use a later date.")
    return account, day


def _post(project, key, payload, description, lines, day):
    operation, _ = _begin_idempotent_operation(key, "project", payload)
    entry = _post_journal(legal_entity=project.business_unit.legal_entity, business_unit=project.business_unit,
                          idempotency=operation, kind=JournalEntry.Kind.PROJECT,
                          description=f"TEST ONLY: {project.number} {description}"[:240], lines=lines, document_date=day)
    return operation, entry


def _pay(actor, business_unit, project, order, data):
    """Finance pays a supplier or subcontractor, never more than the approved order."""
    require(actor, business_unit, can_finance, FINANCE)
    if order.status != O.APPROVED:
        raise InvalidTransaction("Only an approved order is paid. An order over budget waits for its approvals.")
    account, day = _cash_day(business_unit, data, "date paid")
    amount = money(data.get("amount"), "amount paid (QAR)", allow_zero=False)
    left = order.amount - _paid(order)
    if amount > left:
        raise InvalidTransaction(f"Only QAR {_f(left)} of {order.number} is left to pay. Anything more needs a new "
                                 "order, checked against the budget.")
    payment = ProjectPayment.objects.create(order=order, amount=amount, paid_on=day, account=account,
                                            reference=_text(data.get("reference"), "payment reference", 60,
                                                            required=True), recorded_by=actor)
    memo = f"{order.number} {order.supplier}"[:120]
    operation, payment.journal_entry = _post(project, f"project-payment-{payment.pk}", {"payment": payment.pk},
                                             "payment to supplier", [("TEST_PROJECT_COSTS", amount, ZERO, memo),
                                                                     ("TEST_CASH", ZERO, amount, memo)], day)
    CashLineAssignment.objects.create(journal_line=payment.journal_entry.lines.get(credit__gt=0), account=account,
                                      assigned_by=actor)
    _complete(operation, {"payment": payment.pk})
    payment.save(update_fields=["journal_entry"])
    if amount == left:
        _mark(order.lines.all(), STEP.PAID, day, actor)
    return _detail(project, actor)


# Cash in: the client's advance and claim payments.

@transaction.atomic
def record_receipt(*, actor, business_unit, project_id, data):
    require(actor, business_unit, can_finance, FINANCE)
    project = _load(business_unit, project_id)
    if project.status not in LIVE:
        raise InvalidTransaction("Money is recorded while the project is open.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the payment.")
    account, day = _cash_day(business_unit, data, "date received")
    amount = money(data.get("amount"), "amount received (QAR)", allow_zero=False)
    reference = _text(data.get("reference"), "receipt or cheque number", 60, required=True)
    kind, claim = data.get("kind"), None
    if kind == ProjectReceipt.Kind.ADVANCE:
        numbers = totals(project)
        left = Decimal(numbers["advance_expected"]) - Decimal(numbers["advance_received"])
        if amount > left:
            raise InvalidTransaction(f"The contract's advance has QAR {_f(max(left, ZERO))} left to receive.")
        if day < project.signed_on:
            raise InvalidTransaction("The advance cannot be received before the contract was signed.")
        memo = f"advance from {project.customer.name}"[:120]
        lines = [("TEST_CASH", amount, ZERO, memo), ("TEST_PROJECT_ADVANCES", ZERO, amount, memo)]
    elif kind == ProjectReceipt.Kind.CLAIM:
        claim = project.claims.filter(pk=_int(data.get("claim_id")), status=C.INVOICED).first()
        if claim is None:
            raise InvalidTransaction("Choose an invoiced claim of this project.")
        left = claim.net - (claim.receipts.aggregate(total=Sum("amount"))["total"] or ZERO)
        if amount > left:
            raise InvalidTransaction(f"Only QAR {_f(left)} of {claim.invoice_number} is left to collect.")
        memo = f"{claim.invoice_number} {project.customer.name}"[:120]
        lines = [("TEST_CASH", amount, ZERO, memo), ("TEST_PROJECT_RECEIVABLE", ZERO, amount, memo)]
    else:
        raise InvalidTransaction("Choose the advance or a claim payment.")
    receipt = ProjectReceipt.objects.create(project=project, kind=kind, claim=claim, amount=amount, received_on=day,
                                            account=account, reference=reference, recorded_by=actor)
    operation, receipt.journal_entry = _post(project, f"project-receipt-{receipt.pk}", {"receipt": receipt.pk},
                                             f"client {receipt.get_kind_display().lower()}", lines, day)
    CashLineAssignment.objects.create(journal_line=receipt.journal_entry.lines.get(debit__gt=0), account=account,
                                      assigned_by=actor)
    _complete(operation, {"receipt": receipt.pk})
    receipt.save(update_fields=["journal_entry"])
    return _detail(project, actor)


# The schedule and progress.

@transaction.atomic
def update_schedule(*, actor, business_unit, project_id, data):
    """Planned and actual dates of each item's steps, and how far each item and variation is done."""
    require(actor, business_unit, can_run, RUN)
    project = _load(business_unit, project_id)
    if project.status not in LIVE:
        raise InvalidTransaction("The schedule is kept while the project is open.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the schedule.")
    lines = {line.pk: line for line in project.boq_lines.select_for_update(of=("self",))}
    now = timezone.now()
    for raw in data.get("steps") or []:
        if not isinstance(raw, dict):
            raise InvalidTransaction("A schedule row is not readable.")
        ids = raw.get("line_ids") if isinstance(raw.get("line_ids"), list) else [raw.get("line_id")]
        if raw.get("step") not in STEPS:
            raise InvalidTransaction("Choose the step: ordered, paid, shipped, delivered, installed or handed over.")
        for value in ids:
            line = lines.get(_int(value))
            if line is None:
                raise InvalidTransaction("An item was not found on this project.")
            step, _ = ProjectLineStep.objects.get_or_create(line=line, step=raw["step"])
            if "planned_on" in raw:
                step.planned_on = optional_day(raw.get("planned_on"), "planned date")
            if "done_on" in raw:
                step.done_on = None if raw.get("done_on") in (None, "") else _past_day(raw.get("done_on"), "date done")
            if "not_needed" in raw:
                step.not_needed = bool(raw.get("not_needed"))
            if "note" in raw:
                step.note = _text(raw.get("note"), "note", 200)
            step.updated_by, step.updated_at = actor, now
            step.save()
            if raw["step"] == STEP.INSTALLED and step.done_on and project.status in WORKING and line.done_percent < HUNDRED:
                # Installed means the item is done for the client's claim.
                line.done_percent = HUNDRED
                line.save(update_fields=["done_percent"])
    if (data.get("progress") or data.get("variations")) and project.status not in WORKING:
        raise InvalidTransaction("Progress is recorded once the budget is approved.")
    for key, value in (data.get("progress") or {}).items():
        line = lines.get(int(key) if str(key).isdigit() else None)
        if line is None:
            raise InvalidTransaction("An item was not found on this project.")
        line.done_percent = _percent(value, f"done % of {line.code}")
        line.save(update_fields=["done_percent"])
    variations = {variation.pk: variation for variation in project.variations.select_for_update()}
    for key, value in (data.get("variations") or {}).items():
        variation = variations.get(int(key) if str(key).isdigit() else None)
        if variation is None or variation.status not in (V.AT_RISK, V.SIGNED):
            raise InvalidTransaction("Progress is recorded only on approved variations.")
        variation.done_percent = _percent(value, f"done % of {variation.number}")
        variation.save(update_fields=["done_percent"])
    return _detail(project, actor)


# Variations.

@transaction.atomic
def add_variation(*, actor, business_unit, project_id, data):
    require(actor, business_unit, can_run, RUN)
    project = _load(business_unit, project_id)
    if project.status not in LIVE:
        raise InvalidTransaction("Variations are added while the project is open.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the variation.")
    ProjectVariation.objects.create(
        project=project, number=f"VO-{project.variations.count() + 1:02d}",
        description=_text(data.get("description"), "description", 300, required=True),
        value=money(data.get("value"), "value (QAR, minus for work taken out)", allow_zero=False, allow_negative=True),
        cost_estimate=money(data.get("cost_estimate"), "cost estimate"), requested_by=actor)
    return _detail(project, actor)


@transaction.atomic
def variation_action(*, actor, business_unit, variation_id, data):
    require_user(actor, business_unit)
    variation = (ProjectVariation.objects.select_for_update().filter(pk=_int(variation_id),
                                                                      project__business_unit=business_unit).first())
    if variation is None:
        raise InvalidTransaction("That variation was not found.")
    project = _load(business_unit, variation.project_id)
    data = data if isinstance(data, dict) else {}
    action = data.get("action")
    if action in ("approve", "reject"):
        require(actor, business_unit, can_approve, APPROVE)
        if variation.status != V.WAITING:
            raise InvalidTransaction(f"{variation.number} is not waiting for approval.")
        if variation.requested_by_id == actor.pk:
            raise WorkflowPermissionDenied("You asked for this variation, so the Construction Manager decides it.")
        variation.decided_by, variation.decided_at = actor, timezone.now()
        if action == "reject":
            variation.status = V.REJECTED
            variation.decision_note = _text(data.get("note"), "reason", 200, required=True)
        else:
            variation.decision_note = _text(data.get("note"), "note", 200)
            reference = _text(data.get("client_reference"), "client's signed reference", 60)
            if reference:
                variation.client_reference = reference
                variation.client_signed_on = _past_day(data.get("client_signed_on"), "date the client signed")
                variation.status = V.SIGNED
            else:
                variation.status = V.AT_RISK
    elif action == "client_signed":
        if not (can_run(actor, business_unit) or can_approve(actor, business_unit)):
            raise WorkflowPermissionDenied("You need a project permission in this unit.")
        if variation.status != V.AT_RISK:
            raise InvalidTransaction(f"{variation.number} is not waiting for the client's signature.")
        variation.client_reference = _text(data.get("client_reference"), "client's signed reference", 60, required=True)
        variation.client_signed_on = _past_day(data.get("client_signed_on"), "date the client signed")
        variation.status = V.SIGNED
    else:
        raise InvalidTransaction("Choose approve, reject or client signed.")
    variation.save()
    return _detail(project, actor)


# The factory.

def project_for_factory(business_unit, project_id):
    """A project the factory may make items for: its budget is approved and it is not yet closed."""
    project = Project.objects.filter(pk=project_id, business_unit=business_unit).first()
    if project is None:
        raise InvalidTransaction("Choose a project of this unit.")
    if project.status not in WORKING:
        raise InvalidTransaction(f"{project.number} is not ready for the factory: its budget must be approved first.")
    return project


def factory_projects(business_unit):
    return [{"id": project.pk, "number": project.number, "name": project.name, "customer": project.customer.name}
            for project in Project.objects.filter(business_unit=business_unit, status__in=WORKING)
            .select_related("customer").order_by("number")]


def _factory_code(line, used):
    code = _boq_code(line.code)
    if not code or code in used:
        code = f"JD-{900 + line.position}"
    used.add(code)
    return code


@transaction.atomic
def send_to_factory(*, actor, business_unit, project_id, data):
    """Open a factory job order for the project's joinery items. The factory then plans and prepares it."""
    require(actor, business_unit, can_run, RUN)
    project = _load(business_unit, project_id)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the items for the factory.")
    project_for_factory(business_unit, project.pk)
    wanted = data.get("line_ids")
    if not isinstance(wanted, list) or not wanted:
        raise InvalidTransaction("Choose the joinery items the factory makes.")
    lines = list(project.boq_lines.select_for_update(of=("self",)).filter(pk__in=[_int(value) for value in wanted])
                 .select_related("factory_job"))
    if len(lines) != len(set(wanted)):
        raise InvalidTransaction("An item was not found on this project.")
    for line in lines:
        if line.source != SRC.FACTORY:
            raise InvalidTransaction(f"{line.code} ({line.area}) is not made in our factory. Change who supplies it first.")
        if line.factory_job_id and line.factory_job.status != JobStatus.CANCELLED:
            raise InvalidTransaction(f"{line.code} is already with the factory on {line.factory_job.number}.")
    due = _day(data.get("due_date"), "date the factory must finish")
    if due < timezone.localdate():
        raise InvalidTransaction("The date the factory must finish cannot be in the past.")
    found = FactorySettings.objects.select_for_update().get(pk=factory_settings(business_unit.legal_entity).pk)
    number = f"JOB-{found.next_number:04d}"
    found.next_number += 1
    found.save(update_fields=["next_number"])
    job = ProductionOrder.objects.create(
        business_unit=business_unit, number=number, purpose=ProductionOrder.Purpose.FITOUT, project=project,
        project_name=project.name, design_reference=project.design_reference, due_date=due,
        note=_text(data.get("note"), "note", 200) or f"Sent from {project.number}", created_by=actor)
    used = set()
    _apply_plan(job, {"boq": [{"code": _factory_code(line, used),
                               "description": f"{line.code} · {line.description}"[:500],
                               "quantity": format(max(_round(line.quantity), Decimal("0.01")), "f"), "unit": line.unit,
                               "details": {key: value for key, value in (("Project item", line.code),
                                                                         ("Category", line.category),
                                                                         ("Remarks", line.remarks)) if value},
                               "area": line.area[:80], "floor": line.floor[:80], "zone": line.section[:80]}
                              for line in lines],
                      "materials": [], "planned_hours": {}})
    ProjectBoqLine.objects.filter(pk__in=[line.pk for line in lines]).update(factory_job=job)
    return _detail(project, actor)


# Claims.

def _claim_lines(project):
    rows = [{"code": line.code, "area": line.area, "description": line.description[:120],
             "amount": _f(line_value(line)), "done_percent": _f(line.done_percent),
             "value": _f(_round(line_value(line) * line.done_percent / HUNDRED))}
            for line in project.boq_lines.all() if line.done_percent > 0]
    if project.discount_percent > 0:
        before = sum((Decimal(row["value"]) for row in rows), ZERO)
        rows.append({"code": "", "area": "", "description": f"Less discount {project.discount_percent.normalize()}%",
                     "amount": "", "done_percent": "", "value": _f(_round(before * _keep(project)) - before)})
    rows += [{"code": variation.number, "area": "", "description": variation.description[:120],
              "amount": _f(variation.value),
              "done_percent": "100.00" if variation.value < 0 else _f(variation.done_percent),
              "value": _f(_variation_done(variation))}
             for variation in project.variations.all() if variation.status == V.SIGNED]
    return rows


@transaction.atomic
def prepare_claim(*, actor, business_unit, project_id, data):
    """A progress claim from the progress recorded, or the release of retention after handover."""
    require(actor, business_unit, can_run, RUN)
    project = _load(business_unit, project_id)
    data = data if isinstance(data, dict) else {}
    if project.claims.filter(status__in=OPEN_CLAIM).exists():
        raise InvalidTransaction("A claim is already waiting. Finish it before preparing the next one.")
    invoiced = list(project.claims.filter(status=C.INVOICED))
    kind = data.get("kind") or K.PROGRESS
    period_end = _day(data.get("period_end"), "claim date")
    if period_end > timezone.localdate() + datetime.timedelta(days=31):
        raise InvalidTransaction("The claim date is too far ahead.")
    claim = ProjectClaim(project=project, number=f"PC-{project.claims.count() + 1:02d}", kind=kind,
                         period_end=period_end, status=C.WAITING, prepared_by=actor,
                         note=_text(data.get("note"), "note", 200))
    if kind == K.PROGRESS:
        if project.status not in WORKING:
            raise InvalidTransaction("Progress claims are made while the project is in progress.")
        claim.work_to_date = work_to_date(project)
        claim.previous = sum((row.gross for row in invoiced if row.kind == K.PROGRESS), ZERO)
        claim.gross = claim.work_to_date - claim.previous
        if claim.gross <= 0:
            raise InvalidTransaction("Nothing new to claim: record this month's progress first.")
        claim.retention = _round(claim.gross * project.retention_percent / HUNDRED)
        advance = project.receipts.filter(kind=ProjectReceipt.Kind.ADVANCE).aggregate(total=Sum("amount"))["total"] or ZERO
        recovered = sum((row.advance_recovery for row in invoiced), ZERO)
        claim.advance_recovery = min(_round(claim.gross * project.advance_percent / HUNDRED),
                                     max(advance - recovered, ZERO))
        claim.net = claim.gross - claim.retention - claim.advance_recovery
        claim.requested_gross = claim.gross
        claim.lines = _claim_lines(project)
    elif kind == K.RETENTION:
        if project.status != PS.HANDOVER:
            raise InvalidTransaction("Retention is released after handover.")
        held = sum((row.retention for row in invoiced), ZERO) - sum((row.retention_released for row in invoiced), ZERO)
        if held <= 0:
            raise InvalidTransaction("The client holds no retention on this project.")
        warranty_end = project.handed_over_on + datetime.timedelta(days=project.warranty_days)
        if project.retention_release == "warranty" and period_end < warranty_end:
            raise InvalidTransaction(f"The retention is paid after the warranty period ends on "
                                     f"{warranty_end.isoformat()}.")
        claim.retention_released = held
        claim.net = claim.requested_gross = claim.retention_released
    else:
        raise InvalidTransaction("Choose a progress claim or a retention release.")
    claim.save()
    return _detail(project, actor)


def _require_site(actor, business_unit):
    if not (can_run(actor, business_unit) or can_approve(actor, business_unit)):
        raise WorkflowPermissionDenied("The site team or the Construction Manager follows the claim with the consultant.")


@transaction.atomic
def claim_action(*, actor, business_unit, claim_id, data):
    require_user(actor, business_unit)
    claim = (ProjectClaim.objects.select_for_update().filter(pk=_int(claim_id), project__business_unit=business_unit)
             .first())
    if claim is None:
        raise InvalidTransaction("That claim was not found.")
    project = _load(business_unit, claim.project_id)
    data = data if isinstance(data, dict) else {}
    action = data.get("action")
    if action in ("approve", "reject"):
        require(actor, business_unit, can_approve, APPROVE)
        if claim.status != C.WAITING:
            raise InvalidTransaction(f"{claim.number} is not waiting for approval.")
        if claim.prepared_by_id == actor.pk:
            raise WorkflowPermissionDenied("You prepared this claim, so the Construction Manager approves it.")
        claim.decision_note = _text(data.get("note"), "reason", 200, required=action == "reject")
        claim.status = C.APPROVED if action == "approve" else C.REJECTED
        claim.approved_by, claim.approved_at = actor, timezone.now()
    elif action == "submit":
        _require_site(actor, business_unit)
        if claim.status != C.APPROVED:
            raise InvalidTransaction(f"{claim.number} is not approved yet, so it is not sent to the consultant.")
        claim.submitted_on = _past_day(data.get("date"), "date sent to the consultant")
        claim.consultant_reference = _text(data.get("consultant_reference"), "submittal reference", 80)
        claim.status = C.SUBMITTED
    elif action == "certify":
        _require_site(actor, business_unit)
        if claim.status != C.SUBMITTED:
            raise InvalidTransaction(f"{claim.number} is not with the consultant.")
        claim.certified_on = _past_day(data.get("date"), "date the consultant certified it")
        if claim.certified_on < claim.submitted_on:
            raise InvalidTransaction("The consultant cannot certify it before it was sent.")
        claim.consultant_reference = _text(data.get("consultant_reference") or claim.consultant_reference,
                                           "consultant's certificate reference", 80, required=True)
        if claim.kind == K.PROGRESS and data.get("certified") not in (None, ""):
            # The consultant may certify less than the site team asked for.
            certified = money(data.get("certified"), "amount certified (QAR, before retention)", allow_zero=False)
            if certified > claim.requested_gross:
                raise InvalidTransaction(f"The consultant cannot certify more than the QAR {_f(claim.requested_gross)} "
                                         "asked for.")
            if certified != claim.gross:
                share = certified / claim.gross
                claim.gross = certified
                claim.retention = _round(certified * project.retention_percent / HUNDRED)
                claim.advance_recovery = _round(claim.advance_recovery * share)
                claim.net = claim.gross - claim.retention - claim.advance_recovery
        claim.certified_by = actor
        claim.status = C.CERTIFIED
    elif action == "returned":
        _require_site(actor, business_unit)
        if claim.status != C.SUBMITTED:
            raise InvalidTransaction(f"{claim.number} is not with the consultant.")
        claim.decision_note = _text(data.get("note"), "consultant's reason", 200, required=True)
        claim.status = C.REJECTED
    elif action == "withdraw":
        if claim.status != C.WAITING or claim.prepared_by_id != actor.pk:
            raise InvalidTransaction("Only the person who prepared a waiting claim can withdraw it.")
        claim.status, claim.decision_note = C.REJECTED, "Withdrawn by the person who prepared it"
    elif action == "invoice":
        require(actor, business_unit, can_finance, FINANCE)
        if claim.status != C.CERTIFIED:
            raise InvalidTransaction("A claim is invoiced once the consultant has certified it.")
        if claim.prepared_by_id == actor.pk:
            raise WorkflowPermissionDenied("You prepared this claim, so someone else in Finance invoices it.")
        day = _past_day(data.get("date"), "invoice date")
        if day < claim.certified_on:
            raise InvalidTransaction("The invoice cannot be dated before the consultant certified the claim.")
        found = ProjectSettings.objects.select_for_update().get(pk=project_settings(business_unit.legal_entity).pk)
        claim.invoice_number = f"PINV-{found.next_invoice:04d}"
        found.next_invoice += 1
        found.save(update_fields=["next_invoice"])
        claim.due_on = day + datetime.timedelta(days=found.claim_due_days)
        memo = f"{claim.number} {claim.invoice_number} {project.customer.name}"[:120]
        if claim.kind == K.PROGRESS:
            lines = [("TEST_PROJECT_RECEIVABLE", claim.net, ZERO, memo),
                     ("TEST_RETENTION_RECEIVABLE", claim.retention, ZERO, memo),
                     ("TEST_PROJECT_ADVANCES", claim.advance_recovery, ZERO, memo),
                     ("TEST_PROJECT_REVENUE", ZERO, claim.gross, memo)]
            description = "progress claim invoice"
        else:
            lines = [("TEST_PROJECT_RECEIVABLE", claim.retention_released, ZERO, memo),
                     ("TEST_RETENTION_RECEIVABLE", ZERO, claim.retention_released, memo)]
            description = "retention release invoice"
        lines = [line for line in lines if line[1] > 0 or line[2] > 0]
        operation, claim.journal_entry = _post(project, f"project-claim-{claim.pk}", {"claim": claim.pk}, description,
                                               lines, day)
        _complete(operation, {"claim": claim.pk})
        claim.status, claim.invoiced_by, claim.invoiced_on = C.INVOICED, actor, day
    else:
        raise InvalidTransaction("Choose approve, reject, send to the consultant, certified, returned, withdraw or "
                                 "invoice.")
    claim.save()
    return _detail(project, actor)


# Setup.

@transaction.atomic
def update_settings(*, actor, business_unit, data):
    """The switch and the default client terms. Master Admin only."""
    require_master_admin(actor)
    require_user(actor, business_unit)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the settings.")
    found = ProjectSettings.objects.select_for_update().get(pk=project_settings(business_unit.legal_entity).pk)
    if "is_live" in data:
        if not isinstance(data["is_live"], bool):
            raise InvalidTransaction("Choose whether Projects & Fit-Out is live.")
        found.is_live = data["is_live"]
    if "advance_percent" in data:
        found.advance_percent = _percent(data["advance_percent"], "advance percent")
    if "retention_percent" in data:
        found.retention_percent = _percent(data["retention_percent"], "retention percent", Decimal(50))
    if "retention_release" in data:
        if data["retention_release"] not in RELEASES:
            raise InvalidTransaction("Choose when the retention is paid: at handover or after the warranty period.")
        found.retention_release = data["retention_release"]
    for key, top, label in (("warranty_days", 2000, "warranty period"), ("bond_reminder_days", 365, "bond reminder"),
                            ("claim_due_days", 365, "days a client has to pay")):
        if key in data:
            if _int(data[key]) is None or not 0 <= data[key] <= top:
                raise InvalidTransaction(f"The {label} is between 0 and {top}.")
            setattr(found, key, data[key])
    found.updated_by, found.updated_at = actor, timezone.now()
    found.save()
    return projects_overview(actor=actor, business_unit=business_unit)
