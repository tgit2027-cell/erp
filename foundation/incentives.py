"""Commission and rewards: the TOS-V2 incentive model for one legal entity and year (synthetic only).

The calculations are in incentive_rules.py. This module keeps the year's records and runs the steps:

- One incentive year per legal entity and calendar year. Its policy (rates, bands, weights) starts as the
  brief's working figures, marked provisional; each change makes a new version, kept in the history, and a
  person other than the last editor approves it.
- Central figures, entered once: company EBITDA and the four operational achievements; per unit, revenue and
  direct-cost targets from the approved budget and actuals from the posted ledger, plus collections and
  operational achievement entered and validated by Finance. A blank stays blank and makes results pending.
- Each person's plan (an entitlement): proposed, then approved by someone else. Two plans for one person
  cannot cover the same month.
- A run works everything out after the year ends: prepared (Finance), checked (HR), approved (management,
  which posts the cost) and released (payroll), each by a different person, and never by someone the run pays.
  Pending lines are not paid; a later run can pick them up once their inputs are complete.
- Released commission is paid by payroll in April, August and December of the next year; fixed rewards in the
  reward payment month set for the year.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from . import incentive_rules as rules
from .access import active_grants
from .access_levels import is_master_admin
from .approval_limits import _number
from .budgets import actuals as ledger_actuals
from .leave import _require_demo_user
from .models import (
    AdjustmentNote, Budget, BusinessUnit, CustomerCreditNote, CustomerInvoice, Employee, IncentiveEntitlement, IncentiveEvent,
    IncentiveInstalment, IncentiveRun, IncentiveTeam, IncentiveUnit, IncentiveYear, JournalEntry, UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import _name
from .transaction_services import InvalidTransaction, _begin_idempotent_operation, _complete, _post_journal

A = UserActionGrant.Action
E = IncentiveEntitlement
S = IncentiveRun.Status
ZERO = Decimal("0.00")
COMMISSION_PLANS = {E.Plan.SALES, E.Plan.HYBRID}
REWARD_PLANS = {E.Plan.REWARD}
LATER_PLANS = {E.Plan.GROWTH: "Business growth fees", E.Plan.MANAGEMENT: "Management scorecards"}
PLAN_TERMS = ("plan", "business_unit", "channel", "reward_band", "category", "first_month", "last_month", "revenue_target",
              "gp_target", "team", "team_share")
POLICY_LABEL = ("TEST ONLY: The TOS-V2 working model from the incentive brief. Every rate, band and weight is a Finance-test "
                "figure, provisional until Finance, HR and management approve it. Nothing is paid until a run is approved "
                "and released.")


def _money(value):
    return "" if value is None else format(rules.money(value), "f")


def _num(value):
    return "" if value is None else rules.text(value)


# ---------------------------------------------------------------- who may do what


def _holds(user, action, legal_entity):
    return active_grants(user, action).filter(business_unit__legal_entity=legal_entity, business_unit__is_active=True).exists()


def can_prepare(user, legal_entity):
    return is_master_admin(user) or _holds(user, A.PREPARE_INCENTIVES, legal_entity)


def can_check(user, legal_entity):
    return is_master_admin(user) or _holds(user, A.CHECK_INCENTIVES, legal_entity)


def can_approve(user, legal_entity):
    return is_master_admin(user) or _holds(user, A.APPROVE_INCENTIVES, legal_entity)


def can_release(user, legal_entity):
    return is_master_admin(user) or _holds(user, A.APPROVE_PAY, legal_entity)


def _full_view(user, legal_entity):
    return (can_prepare(user, legal_entity) or can_check(user, legal_entity) or can_approve(user, legal_entity)
            or can_release(user, legal_entity))


def _require(allowed, message):
    if not allowed:
        raise WorkflowPermissionDenied(message)


def _entity(business_unit):
    if business_unit is None:
        raise WorkflowPermissionDenied("Choose a business unit.")
    return business_unit.legal_entity


def _year_number(value):
    if value in (None, ""):
        return timezone.localdate().year - 1
    try:
        year = int(value)
    except (TypeError, ValueError):
        raise InvalidTransaction("Give the year as a number, for example 2026.") from None
    if not 2000 <= year <= 2100:
        raise InvalidTransaction("Give a year between 2000 and 2100.")
    return year


def _log(year, actor, what, **detail):
    IncentiveEvent.objects.create(year=year, what=what, by=actor, detail=detail)


def _get_year(legal_entity, year, *, lock=False):
    rows = IncentiveYear.objects.filter(legal_entity=legal_entity, year=year)
    return (rows.select_for_update() if lock else rows).first()


def _frozen(year):
    """True once a run of the year has been approved: its policy and figures can no longer change."""
    return year.runs.exclude(status__in=[S.PREPARED, S.CHECKED]).exists()


def _optional(data, key, label, *, top=None, signed=False):
    value = data.get(key)
    if value in (None, ""):
        return None
    if signed:
        try:
            number = Decimal(str(value).replace(",", "").strip())
        except Exception:
            raise InvalidTransaction(f"{label} must be a number.") from None
        if not number.is_finite() or abs(number) >= Decimal("100000000000"):
            raise InvalidTransaction(f"{label} must be a number.")
        return number.quantize(Decimal("0.01"))
    return _number(value, label, top=top)


def _fraction(data, key, label):
    value = data.get(key)
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).strip())
    except Exception:
        raise InvalidTransaction(f"{label} must be a number between 0 and 1.") from None
    if not number.is_finite() or not Decimal(0) <= number <= 1:
        raise InvalidTransaction(f"{label} must be a number between 0 and 1.")
    return number.quantize(Decimal("0.0001"))


# ---------------------------------------------------------------- central figures


def unit_figures(unit_row):
    """Targets from the approved budget, actuals from the ledger, and what was entered. None means missing."""
    unit, year = unit_row.business_unit, unit_row.year.year
    budget = Budget.objects.filter(business_unit=unit, year=year, status=Budget.Status.APPROVED).prefetch_related("amounts").first()
    planned = defaultdict(lambda: ZERO)
    if budget is not None:
        for amount in budget.amounts.all():
            planned[amount.line] += amount.amount
    actual = ledger_actuals(unit, year)
    revenue_actual = sum(actual["sales"], ZERO)
    cost_actual = sum(actual["cost_of_sales"], ZERO)
    figures = {
        "revenue_target": planned["sales"] if budget else None,
        "revenue_actual": revenue_actual,
        "cost_target": planned["cost_of_sales"] if budget else None,
        "cost_actual": cost_actual,
        "collections_target": unit_row.collections_target,
        "collections_actual": unit_row.collections_actual,
        "operational": unit_row.operational_achievement,
    }
    figures["gp_target"] = None if budget is None else figures["revenue_target"] - figures["cost_target"]
    figures["gp_actual"] = revenue_actual - cost_actual
    return figures, budget is not None


def _figures_text(figures):
    return {key: _money(value) if key != "operational" else _num(value) for key, value in figures.items()}


def _unit_results(year):
    """{unit id: (row, figures, Result, validated)} for every unit set up in the year."""
    out = {}
    for row in year.units.select_related("business_unit", "year", "validated_by"):
        figures, has_budget = unit_figures(row)
        validated = bool(row.validated_at) and row.validated_figures == _figures_text(figures)
        result = rules.unit_factor(year.policy, revenue_target=figures["revenue_target"], revenue_actual=figures["revenue_actual"],
                                   gp_target=figures["gp_target"], gp_actual=figures["gp_actual"],
                                   collections_target=figures["collections_target"], collections_actual=figures["collections_actual"],
                                   operational=figures["operational"])
        if not has_budget:
            result.pending.insert(0, f"{row.business_unit.name} has no approved budget for {year.year}.")
            result.amount = None
        if not validated:
            result.pending.append(f"Finance has not validated {row.business_unit.name}'s figures"
                                  + (" since they changed." if row.validated_at else "."))
            result.amount = None
        out[row.business_unit_id] = (row, figures, result, validated)
    return out


def _company(year, units):
    counted = [(row, figures, validated) for row, figures, _, validated in units.values() if row.counts_in_company]
    revenue_target = revenue_actual = None
    problems = []
    if not counted:
        problems.append("No revenue-generating unit is set up for the year.")
    elif any(figures["revenue_target"] is None for _, figures, _ in counted):
        problems.append("Company revenue: a unit has no approved budget, so the revenue target is incomplete.")
    elif not all(validated for _, _, validated in counted):
        problems.append("Company revenue: Finance has not validated every unit's figures.")
    else:
        revenue_target = sum((figures["revenue_target"] for _, figures, _ in counted), ZERO)
        revenue_actual = sum((figures["revenue_actual"] for _, figures, _ in counted), ZERO)
    result = rules.company_index(year.policy, revenue_target=revenue_target, revenue_actual=revenue_actual,
                                 ebitda_target=year.ebitda_target, ebitda_actual=year.ebitda_actual, operational=year.operational)
    if problems:
        result.pending = problems + [reason for reason in result.pending if not reason.startswith("Company revenue")]
        result.amount = None
    result.figures.update({"revenue_target": revenue_target, "revenue_actual": revenue_actual})
    return result


def erp_sales(legal_entity, year):
    """{user id: qualifying revenue suggested by the ERP}: invoices on their own quotes less returns and credit notes, before tax."""
    tz = timezone.get_current_timezone()
    start = datetime.datetime(year, 1, 1, tzinfo=tz)
    stop = datetime.datetime(year + 1, 1, 1, tzinfo=tz)
    totals = defaultdict(lambda: ZERO)
    window = {"business_unit__legal_entity": legal_entity}
    for row in (CustomerInvoice.objects.filter(**window, issued_at__gte=start, issued_at__lt=stop)
                .values("quote__created_by").annotate(total=Sum("net_total"))):
        totals[row["quote__created_by"]] += row["total"] or ZERO
    for row in (CustomerCreditNote.objects.filter(**window, issued_at__gte=start, issued_at__lt=stop)
                .values("invoice__quote__created_by").annotate(total=Sum("returned_value"))):
        totals[row["invoice__quote__created_by"]] -= row["total"] or ZERO
    for row in (AdjustmentNote.objects.filter(**window, kind=AdjustmentNote.Kind.CUSTOMER_CREDIT, status=AdjustmentNote.Status.APPROVED,
                                              decided_at__gte=start, decided_at__lt=stop)
                .values("invoice__quote__created_by").annotate(total=Sum("value"))):
        totals[row["invoice__quote__created_by"]] -= row["total"] or ZERO
    return totals


# ---------------------------------------------------------------- working it out


def _proration(entitlement):
    return Decimal(entitlement.last_month - entitlement.first_month + 1) / Decimal(12)


def _team_inputs(team, members):
    rows = [row for row in members.get(team.pk, [])]
    if not rows:
        return None, ["The team has no members with figures."]
    missing = [row.employee.full_name for row in rows if row.revenue_actual is None or row.gp_actual is None]
    if missing:
        return None, [f"Team {team.code}: validated revenue or GP is missing for {', '.join(missing)}."]
    return {"revenue_target": team.revenue_target, "revenue_actual": sum((row.revenue_actual for row in rows), ZERO),
            "gp_target": team.gp_target, "gp_actual": sum((row.gp_actual for row in rows), ZERO),
            "collection_fraction": team.collection_fraction}, []


def _line(entitlement, *, policy, company, units, members):
    plan = entitlement.plan
    unit = units.get(entitlement.business_unit_id)
    kind = "commission" if plan in COMMISSION_PLANS else "reward"
    if entitlement.status != E.Status.APPROVED:
        result = rules.Result(pending=["This plan is not approved yet."])
    elif plan in LATER_PLANS:
        result = rules.Result(pending=[f"{LATER_PLANS[plan]} are worked out in the next stage of the build."])
    elif plan == E.Plan.SALES:
        result = rules.sales_commission(policy, company=company, channel=entitlement.channel,
                                        revenue_target=entitlement.revenue_target, revenue_actual=entitlement.revenue_actual,
                                        gp_target=entitlement.gp_target, gp_actual=entitlement.gp_actual,
                                        collection_fraction=entitlement.collection_fraction)
    elif plan == E.Plan.HYBRID:
        team, problems = (None, []) if entitlement.team is None else _team_inputs(entitlement.team, members)
        result = rules.supervisor_hybrid(
            policy, company=company, channel=entitlement.channel, team_share=entitlement.team_share,
            personal={"revenue_target": entitlement.revenue_target, "revenue_actual": entitlement.revenue_actual,
                      "gp_target": entitlement.gp_target, "gp_actual": entitlement.gp_actual,
                      "collection_fraction": entitlement.collection_fraction},
            team=team)
        if problems:
            result.pending = problems + [p for p in result.pending if p != "The supervisor is not linked to a team."]
            result.amount = None
    else:
        result = rules.fixed_reward(policy, company=company, unit=unit[2] if unit else None, band_code=entitlement.reward_band,
                                    category=entitlement.category, rating=entitlement.rating, proration=_proration(entitlement))
    return {
        "entitlement": entitlement.pk, "employee": entitlement.employee_id, "name": entitlement.employee.full_name,
        "staff_number": entitlement.employee.staff_number, "unit": entitlement.business_unit.name,
        "paid_by_unit": entitlement.employee.business_unit_id, "plan": plan, "plan_label": entitlement.get_plan_display(),
        "months": f"{datetime.date(2000, entitlement.first_month, 1):%b} to {datetime.date(2000, entitlement.last_month, 1):%b}",
        "kind": kind, "amount": "" if result.is_pending else _money(result.amount), "pending": result.pending,
        "steps": result.steps, "user": entitlement.employee.user_id,
    }


def work_out(year, *, exclude_paid_plans=True):
    """(company Result, unit rows, lines) for the year as the records stand now."""
    policy = year.policy
    units = _unit_results(year)
    company = _company(year, units)
    entitlements = (year.entitlements.exclude(status=E.Status.WITHDRAWN)
                    .select_related("employee", "business_unit", "team").order_by("employee__full_name", "first_month", "pk"))
    if exclude_paid_plans:
        done = {line["entitlement"] for run in year.runs.exclude(status__in=[S.PREPARED, S.CHECKED]) for line in run.lines
                if not line["pending"]}
        entitlements = [row for row in entitlements if row.pk not in done]
    members = defaultdict(list)
    # A team's figures are its salespeople's; a supervisor's own sales count only in their personal part.
    for row in year.entitlements.filter(status=E.Status.APPROVED, team__isnull=False, plan=E.Plan.SALES).select_related("employee"):
        members[row.team_id].append(row)
    lines = [_line(row, policy=policy, company=company, units=units, members=members) for row in entitlements]
    return company, units, lines


def _company_row(company, year):
    f = company.figures
    return {
        "pending": company.pending, "steps": company.steps,
        "revenue_target": _money(f.get("revenue_target")), "revenue_actual": _money(f.get("revenue_actual")),
        "ebitda_target": _money(year.ebitda_target), "ebitda_actual": _money(year.ebitda_actual),
        "operational": {key: _num(Decimal(str(value))) for key, value in (year.operational or {}).items() if value not in (None, "")},
        "cpi": _num(f["cpi"].quantize(Decimal("0.01"))) if "cpi" in f else "",
        "company_factor": _num(f["company_factor"]) if "company_factor" in f else "",
        "negative_ebitda": bool(f.get("negative_ebitda")),
    }


def _unit_rows(units):
    rows = []
    for row, figures, result, validated in units.values():
        rows.append({
            "id": row.pk, "unit_id": row.business_unit_id, "unit": row.business_unit.name, "counts_in_company": row.counts_in_company,
            "figures": _figures_text(figures),
            "gp_margin": _num((figures["gp_actual"] * 100 / figures["revenue_actual"]).quantize(Decimal("0.1")))
            if figures["revenue_actual"] else "",
            "minimum_margin": _num(row.minimum_margin), "validated": validated,
            "validated_by": _name(row.validated_by) if validated else "",
            "unit_factor": "" if result.is_pending else _num(result.amount.quantize(Decimal("0.01"))),
            "pending": result.pending, "steps": result.steps,
        })
    return rows


# ---------------------------------------------------------------- what people see


def _entitlement_row(row, suggested):
    return {
        "id": row.pk, "employee": row.employee_id, "name": row.employee.full_name, "staff_number": row.employee.staff_number,
        "grade": row.employee.grade, "unit_id": row.business_unit_id, "unit": row.business_unit.name, "plan": row.plan,
        "plan_label": row.get_plan_display(), "channel": row.channel, "reward_band": row.reward_band, "category": row.category,
        "first_month": row.first_month, "last_month": row.last_month, "revenue_target": _money(row.revenue_target),
        "gp_target": _money(row.gp_target), "revenue_actual": _money(row.revenue_actual), "gp_actual": _money(row.gp_actual),
        "erp_revenue": _money(suggested) if suggested is not None else "",
        "collection_fraction": _num(row.collection_fraction), "rating": row.rating, "team": row.team_id,
        "team_share": _num(row.team_share), "note": row.note, "status": row.status, "status_label": row.get_status_display(),
        "proposed_by": _name(row.proposed_by), "approved_by": _name(row.approved_by),
    }


def _run_row(run, actor):
    entity = run.year.legal_entity
    paid_people = {line["user"] for line in run.lines if line["amount"] and Decimal(line["amount"]) > 0 and line["user"]}
    involved = {run.prepared_by_id, run.checked_by_id, run.approved_by_id}
    mine = actor.pk in paid_people
    return {
        "id": run.pk, "number": run.number, "status": run.status, "status_label": run.get_status_display(),
        "policy_version": run.policy_version, "company": run.company, "units": run.units, "lines": run.lines,
        "commission_total": _money(run.commission_total), "reward_total": _money(run.reward_total), "pending_count": run.pending_count,
        "prepared_by": _name(run.prepared_by), "prepared_at": run.prepared_at.isoformat(),
        "checked_by": _name(run.checked_by), "approved_by": _name(run.approved_by), "released_by": _name(run.released_by),
        "instalments": [{"employee": row.employee.full_name, "kind": row.get_kind_display(), "month": row.due_month.strftime("%Y-%m"),
                         "amount": _money(row.amount), "paid": bool(row.paid_in_id)}
                        for row in run.instalments.select_related("employee")],
        "can_redo": run.status == S.PREPARED and can_prepare(actor, entity),
        "can_check": run.status == S.PREPARED and can_check(actor, entity) and actor.pk != run.prepared_by_id and not mine,
        "can_approve": (run.status == S.CHECKED and can_approve(actor, entity) and actor.pk not in involved and not mine),
        "can_release": (run.status == S.APPROVED and can_release(actor, entity) and actor.pk not in involved and not mine),
    }


def _statement(actor, year):
    """A person's own plans, results and instalments."""
    employee = Employee.objects.filter(user=actor).first()
    if employee is None:
        return {"plans": [], "lines": [], "instalments": []}
    plans = list(year.entitlements.filter(employee=employee).exclude(status=E.Status.WITHDRAWN).select_related("employee", "business_unit"))
    lines = [line for run in year.runs.exclude(status__in=[S.PREPARED, S.CHECKED]) for line in run.lines
             if line["employee"] == employee.pk]
    instalments = IncentiveInstalment.objects.filter(run__year=year, employee=employee)
    return {
        "plans": [{"plan_label": row.get_plan_display(), "months": f"{row.first_month}–{row.last_month}", "status_label": row.get_status_display(),
                   "revenue_target": _money(row.revenue_target), "revenue_actual": _money(row.revenue_actual)} for row in plans],
        "lines": lines,
        "instalments": [{"kind": row.get_kind_display(), "month": row.due_month.strftime("%Y-%m"), "amount": _money(row.amount),
                         "paid": bool(row.paid_in_id)} for row in instalments],
    }


def overview(*, actor, business_unit, year=None):
    _require_demo_user(actor)
    entity = _entity(business_unit)
    number = _year_number(year)
    record = _get_year(entity, number)
    full = _full_view(actor, entity)
    base = {
        "entity": entity.name, "year": number, "policy_label": POLICY_LABEL, "full": full, "exists": record is not None,
        "can_prepare": can_prepare(actor, entity), "can_check": can_check(actor, entity), "can_approve": can_approve(actor, entity),
        "year_over": timezone.localdate().year > number,
        "choices": {"plans": rules.PLANS, "channels": rules.CHANNELS, "ratings": rules.RATINGS, "categories": rules.CATEGORIES,
                    "operational": rules.OPERATIONAL_PARTS},
        "years": list(IncentiveYear.objects.filter(legal_entity=entity).values_list("year", flat=True)),
    }
    if record is None:
        return base
    base["mine"] = _statement(actor, record)
    if not full:
        return base
    company, units, lines = work_out(record)
    sales = erp_sales(entity, number)
    entitlements = record.entitlements.select_related("employee", "business_unit", "proposed_by", "approved_by")
    base.update({
        "policy": record.policy, "policy_version": record.policy_version, "policy_status": record.policy_status,
        "policy_status_label": record.get_policy_status_display(), "policy_approved_by": _name(record.policy_approved_by),
        "reward_payment_month": record.reward_payment_month.strftime("%Y-%m") if record.reward_payment_month else "",
        "frozen": _frozen(record),
        "company": _company_row(company, record), "units": _unit_rows(units),
        "teams": [{"id": team.pk, "code": team.code, "name": team.name, "unit_id": team.business_unit_id, "unit": team.business_unit.name,
                   "revenue_target": _money(team.revenue_target), "gp_target": _money(team.gp_target),
                   "collection_fraction": _num(team.collection_fraction)} for team in record.teams.select_related("business_unit")],
        "entitlements": [_entitlement_row(row, sales.get(row.employee.user_id) if row.employee.user_id else None) for row in entitlements],
        "preview": {"lines": lines, "pending": sum(1 for line in lines if line["pending"]),
                    "commission": _money(sum((Decimal(line["amount"]) for line in lines if line["amount"] and line["kind"] == "commission"), ZERO)),
                    "reward": _money(sum((Decimal(line["amount"]) for line in lines if line["amount"] and line["kind"] == "reward"), ZERO))},
        "runs": [_run_row(run, actor) for run in record.runs.select_related("year__legal_entity", "prepared_by", "checked_by",
                                                                            "approved_by", "released_by")],
        "units_available": [{"id": unit.pk, "name": unit.name} for unit in
                            BusinessUnit.objects.filter(legal_entity=entity, is_active=True).exclude(incentive_units__year=record)],
        "staff": [{"id": person.pk, "name": person.full_name, "staff_number": person.staff_number, "grade": person.grade,
                   "unit_id": person.business_unit_id}
                  for person in Employee.objects.filter(business_unit__legal_entity=entity).exclude(status=Employee.Status.LEFT)],
        "history": [{"what": event.what, "by": _name(event.by), "at": event.at.isoformat(), "detail": event.detail}
                    for event in record.events.select_related("by")[:40]],
    })
    return base


# ---------------------------------------------------------------- setting the year up


def _load_year(actor, business_unit, data, *, lock=True):
    _require_demo_user(actor)
    entity = _entity(business_unit)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the details.")
    record = _get_year(entity, _year_number(data.get("year")), lock=lock)
    if record is None:
        raise InvalidTransaction("Set the incentive year up first.")
    return entity, record


def _not_frozen(record):
    if _frozen(record):
        raise InvalidTransaction(f"A run for {record.year} is already approved, so its policy and figures are frozen.")


@transaction.atomic
def open_year(*, actor, business_unit, data):
    _require_demo_user(actor)
    entity = _entity(business_unit)
    _require(can_prepare(actor, entity), "Only people who prepare commission and rewards can set up an incentive year.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the year.")
    number = _year_number(data.get("year"))
    if _get_year(entity, number) is not None:
        raise InvalidTransaction(f"{number} is already set up.")
    record = IncentiveYear.objects.create(legal_entity=entity, year=number, policy=rules.default_policy(), created_by=actor,
                                          updated_by=actor)
    _log(record, actor, "set up the year", policy_version=1)
    return overview(actor=actor, business_unit=business_unit, year=number)


@transaction.atomic
def save_policy(*, actor, business_unit, data):
    entity, record = _load_year(actor, business_unit, data)
    _require(is_master_admin(actor), "Only the Master Admin can change the commission and rewards policy.")
    _not_frozen(record)
    policy = data.get("policy")
    if not isinstance(policy, dict) or set(policy) != set(rules.DEFAULT_POLICY):
        raise InvalidTransaction("Send the whole policy.")
    try:
        problems = rules.check_policy(policy)
        rules.instalments(policy, Decimal("100"))
    except Exception:
        raise InvalidTransaction("The policy has a value that is not a number.") from None
    if problems:
        raise InvalidTransaction(" ".join(problems))
    if policy == record.policy:
        raise InvalidTransaction("Nothing changed.")
    changed = sorted(key for key in policy if policy[key] != record.policy.get(key))
    record.policy, record.policy_version = policy, record.policy_version + 1
    record.policy_status, record.policy_approved_by, record.policy_approved_at = IncentiveYear.PolicyStatus.PROVISIONAL, None, None
    record.updated_by, record.updated_at = actor, timezone.now()
    record.save()
    _log(record, actor, "changed the policy", policy_version=record.policy_version, changed=changed, reason=str(data.get("reason") or "")[:200])
    return overview(actor=actor, business_unit=business_unit, year=record.year)


@transaction.atomic
def approve_policy(*, actor, business_unit, data):
    entity, record = _load_year(actor, business_unit, data)
    _require(can_approve(actor, entity), "Only people who approve commission and rewards can approve the policy.")
    if record.policy_status == IncentiveYear.PolicyStatus.APPROVED:
        raise InvalidTransaction("This policy version is already approved.")
    if record.updated_by_id == actor.pk:
        raise WorkflowPermissionDenied("You made the last change to the policy, so someone else must approve it.")
    record.policy_status, record.policy_approved_by, record.policy_approved_at = IncentiveYear.PolicyStatus.APPROVED, actor, timezone.now()
    record.save(update_fields=["policy_status", "policy_approved_by", "policy_approved_at"])
    _log(record, actor, "approved the policy", policy_version=record.policy_version)
    return overview(actor=actor, business_unit=business_unit, year=record.year)


@transaction.atomic
def save_company(*, actor, business_unit, data):
    entity, record = _load_year(actor, business_unit, data)
    _require(can_prepare(actor, entity), "Only people who prepare commission and rewards enter company figures.")
    _not_frozen(record)
    record.ebitda_target = _optional(data, "ebitda_target", "The EBITDA target", signed=True)
    record.ebitda_actual = _optional(data, "ebitda_actual", "Actual EBITDA", signed=True)
    operational = data.get("operational") or {}
    if not isinstance(operational, dict):
        raise InvalidTransaction("Send the operational achievements.")
    record.operational = {key: _num(_optional(operational, key, label, top=1000)) for key, label in rules.OPERATIONAL_PARTS.items()
                          if _optional(operational, key, label, top=1000) is not None}
    record.updated_by, record.updated_at = actor, timezone.now()
    record.save()
    _log(record, actor, "entered company figures", ebitda_target=_money(record.ebitda_target), ebitda_actual=_money(record.ebitda_actual),
         operational=record.operational)
    return overview(actor=actor, business_unit=business_unit, year=record.year)


@transaction.atomic
def set_reward_month(*, actor, business_unit, data):
    """The payroll month that pays fixed rewards. It can be set after a run is approved, until rewards are released."""
    entity, record = _load_year(actor, business_unit, data)
    _require(can_prepare(actor, entity) or can_release(actor, entity),
             "Only people who prepare commission and rewards or approve payroll set the reward payment month.")
    if IncentiveInstalment.objects.filter(run__year=record, kind=IncentiveInstalment.Kind.REWARD).exists():
        raise InvalidTransaction("Rewards for this year are already released to payroll, so their month cannot change.")
    month = data.get("reward_payment_month")
    if month in (None, ""):
        record.reward_payment_month = None
    else:
        try:
            y, m = (int(part) for part in str(month).split("-"))
            record.reward_payment_month = datetime.date(y, m, 1)
        except (TypeError, ValueError):
            raise InvalidTransaction("Give the reward payment month as YYYY-MM.") from None
        if record.reward_payment_month.year <= record.year:
            raise InvalidTransaction("Rewards are paid after the year they are for.")
    record.save(update_fields=["reward_payment_month"])
    _log(record, actor, "set the reward payment month", month=str(month or ""))
    return overview(actor=actor, business_unit=business_unit, year=record.year)


@transaction.atomic
def save_unit(*, actor, business_unit, data):
    entity, record = _load_year(actor, business_unit, data)
    _require(can_prepare(actor, entity), "Only people who prepare commission and rewards enter unit figures.")
    _not_frozen(record)
    unit_id = data.get("unit_id")
    unit = BusinessUnit.objects.filter(pk=unit_id if type(unit_id) is int else None, legal_entity=entity).first()
    if unit is None:
        raise InvalidTransaction("That business unit was not found.")
    row, _ = IncentiveUnit.objects.get_or_create(year=record, business_unit=unit)
    row.counts_in_company = data.get("counts_in_company", True) is not False
    row.collections_target = _optional(data, "collections_target", "The collections target")
    row.collections_actual = _optional(data, "collections_actual", "Actual collections")
    row.operational_achievement = _optional(data, "operational_achievement", "Operational achievement", top=1000)
    row.minimum_margin = _optional(data, "minimum_margin", "The minimum margin", top=100)
    row.save()
    _log(record, actor, "entered unit figures", unit=unit.name)
    return overview(actor=actor, business_unit=business_unit, year=record.year)


@transaction.atomic
def validate_unit(*, actor, business_unit, data):
    """Finance confirms a unit's figures as they stand. Any later change shows as not validated."""
    entity, record = _load_year(actor, business_unit, data)
    _require(can_prepare(actor, entity) or can_approve(actor, entity), "Only Finance can validate a unit's figures.")
    _not_frozen(record)
    row = IncentiveUnit.objects.filter(pk=data.get("id") if type(data.get("id")) is int else None, year=record).select_related(
        "business_unit", "year").first()
    if row is None:
        raise InvalidTransaction("That unit is not set up for the year.")
    figures, has_budget = unit_figures(row)
    if not has_budget:
        raise InvalidTransaction(f"{row.business_unit.name} has no approved budget for {record.year}, so there is nothing to validate.")
    row.validated_by, row.validated_at, row.validated_figures = actor, timezone.now(), _figures_text(figures)
    row.save(update_fields=["validated_by", "validated_at", "validated_figures"])
    _log(record, actor, "validated unit figures", unit=row.business_unit.name, figures=row.validated_figures)
    return overview(actor=actor, business_unit=business_unit, year=record.year)


@transaction.atomic
def save_team(*, actor, business_unit, data):
    entity, record = _load_year(actor, business_unit, data)
    _require(can_prepare(actor, entity), "Only people who prepare commission and rewards set up teams.")
    _not_frozen(record)
    code, name = str(data.get("code") or "").strip()[:20], str(data.get("name") or "").strip()[:80]
    if not code or not name:
        raise InvalidTransaction("Give the team a code and a name.")
    unit = BusinessUnit.objects.filter(pk=data.get("unit_id") if type(data.get("unit_id")) is int else None, legal_entity=entity).first()
    if unit is None:
        raise InvalidTransaction("Choose the team's business unit.")
    team_id = data.get("id")
    team = IncentiveTeam.objects.filter(pk=team_id, year=record).first() if type(team_id) is int else IncentiveTeam(year=record)
    if team is None:
        raise InvalidTransaction("That team was not found.")
    if IncentiveTeam.objects.filter(year=record, code__iexact=code).exclude(pk=team.pk).exists():
        raise InvalidTransaction(f"Another team already uses the code {code}.")
    team.code, team.name, team.business_unit = code, name, unit
    team.revenue_target = _optional(data, "revenue_target", "The team revenue target")
    team.gp_target = _optional(data, "gp_target", "The team GP target")
    team.collection_fraction = _fraction(data, "collection_fraction", "The team collection-eligible fraction")
    team.save()
    _log(record, actor, "saved a team", team=code)
    return overview(actor=actor, business_unit=business_unit, year=record.year)


def _locked_in_run(entitlement):
    return any(line["entitlement"] == entitlement.pk and not line["pending"]
               for run in entitlement.year.runs.exclude(status__in=[S.PREPARED, S.CHECKED]) for line in run.lines)


@transaction.atomic
def save_entitlement(*, actor, business_unit, data):
    """Propose a plan for a person, or change one. Changing a plan's terms sends it back for approval."""
    entity, record = _load_year(actor, business_unit, data)
    _require(can_prepare(actor, entity), "Only people who prepare commission and rewards set people's plans.")
    row_id = data.get("id")
    if row_id is not None:
        row = IncentiveEntitlement.objects.select_for_update().filter(pk=row_id if type(row_id) is int else None, year=record).first()
        if row is None:
            raise InvalidTransaction("That plan was not found.")
        if row.status == E.Status.WITHDRAWN:
            raise InvalidTransaction("This plan was withdrawn. Propose a new one instead.")
        if _locked_in_run(row):
            raise InvalidTransaction("This plan is in an approved run, so it can no longer change.")
        before = {key: getattr(row, key if key not in ("business_unit", "team") else f"{key}_id") for key in PLAN_TERMS}
    else:
        employee_id = data.get("employee")
        employee = Employee.objects.filter(pk=employee_id if type(employee_id) is int else None, business_unit__legal_entity=entity).first()
        if employee is None:
            raise InvalidTransaction("Choose the person from the staff records.")
        row, before = IncentiveEntitlement(year=record, employee=employee, proposed_by=actor), None
    plan = data.get("plan")
    if plan not in E.Plan.values:
        raise InvalidTransaction("Choose the plan.")
    unit = BusinessUnit.objects.filter(pk=data.get("unit_id") if type(data.get("unit_id")) is int else None, legal_entity=entity).first()
    if unit is None:
        raise InvalidTransaction("Choose the business unit whose results count for this person.")
    try:
        first, last = int(data.get("first_month") or 1), int(data.get("last_month") or 12)
    except (TypeError, ValueError):
        raise InvalidTransaction("Give the first and last month as numbers from 1 to 12.") from None
    if not 1 <= first <= last <= 12:
        raise InvalidTransaction("The months must run from 1 to 12, first before last.")
    channel = str(data.get("channel") or "")
    band_code = str(data.get("reward_band") or "")
    category = str(data.get("category") or "")
    rating = str(data.get("rating") or "")
    if channel not in rules.CHANNELS:
        raise InvalidTransaction("Choose a sales channel from the list.")
    if band_code and band_code not in record.policy["reward_bands"]:
        raise InvalidTransaction("Choose a reward band from the policy.")
    if category and category not in rules.CATEGORIES:
        raise InvalidTransaction("Choose a category from the list.")
    if rating and rating not in rules.RATINGS:
        raise InvalidTransaction("Choose a rating from the list.")
    if plan in COMMISSION_PLANS and not channel:
        raise InvalidTransaction("Commission plans need a sales channel.")
    if plan == E.Plan.REWARD and channel:
        raise InvalidTransaction("Performance rewards have no sales channel or revenue target: support functions are not given one.")
    team = None
    if data.get("team") not in (None, ""):
        team = IncentiveTeam.objects.filter(pk=data.get("team") if type(data.get("team")) is int else None, year=record).first()
        if team is None:
            raise InvalidTransaction("That team was not found.")
    share = _optional(data, "team_share", "The share of the team pool", top=100)
    if plan == E.Plan.HYBRID and team is not None and share is not None:
        taken = (IncentiveEntitlement.objects.filter(year=record, team=team, plan=E.Plan.HYBRID).exclude(status=E.Status.WITHDRAWN)
                 .exclude(pk=row.pk).aggregate(total=Sum("team_share"))["total"] or ZERO)
        if taken + share > 100:
            raise InvalidTransaction(f"Supervisors would get {rules.text(taken + share)}% of team {team.code}'s pool. It cannot be more than 100%.")
    clash = (IncentiveEntitlement.objects.filter(year=record, employee=row.employee).exclude(status=E.Status.WITHDRAWN).exclude(pk=row.pk)
             .filter(first_month__lte=last, last_month__gte=first))
    if clash.exists():
        raise InvalidTransaction(f"{row.employee.full_name} already has a plan covering some of these months. "
                                 "One person has one plan at a time, so nothing is rewarded twice.")
    row.plan, row.business_unit, row.channel, row.reward_band, row.category = plan, unit, channel, band_code, category
    row.first_month, row.last_month = first, last
    row.revenue_target = None if plan == E.Plan.REWARD else _optional(data, "revenue_target", "The revenue target")
    row.gp_target = None if plan == E.Plan.REWARD else _optional(data, "gp_target", "The GP target")
    row.revenue_actual = None if plan == E.Plan.REWARD else _optional(data, "revenue_actual", "Validated revenue")
    row.gp_actual = None if plan == E.Plan.REWARD else _optional(data, "gp_actual", "Validated GP", signed=True)
    row.collection_fraction = _fraction(data, "collection_fraction", "The collection-eligible fraction")
    row.rating, row.team, row.team_share = rating, team, share
    row.note = str(data.get("note") or "")[:300]
    after = {key: getattr(row, key if key not in ("business_unit", "team") else f"{key}_id") for key in PLAN_TERMS}
    if before is not None and before != after and row.status == E.Status.APPROVED:
        row.status, row.approved_by, row.approved_at = E.Status.PROPOSED, None, None
        row.proposed_by, row.proposed_at = actor, timezone.now()
    row.save()
    _log(record, actor, "proposed a plan" if before is None else "changed a plan", person=row.employee.full_name, plan=row.plan,
         months=f"{first}-{last}", status=row.status)
    return overview(actor=actor, business_unit=business_unit, year=record.year)


@transaction.atomic
def decide_entitlement(*, actor, business_unit, data):
    entity, record = _load_year(actor, business_unit, data)
    row = IncentiveEntitlement.objects.select_for_update().filter(
        pk=data.get("id") if type(data.get("id")) is int else None, year=record).select_related("employee").first()
    if row is None:
        raise InvalidTransaction("That plan was not found.")
    action = data.get("decision")
    if action == "approve":
        _require(can_approve(actor, entity) or can_check(actor, entity), "Only HR or management approve people's plans.")
        if row.status != E.Status.PROPOSED:
            raise InvalidTransaction("Only a proposed plan can be approved.")
        if row.proposed_by_id == actor.pk:
            raise WorkflowPermissionDenied("You proposed this plan, so someone else must approve it.")
        if row.employee.user_id == actor.pk:
            raise WorkflowPermissionDenied("This is your own plan, so someone else must approve it.")
        row.status, row.approved_by, row.approved_at = E.Status.APPROVED, actor, timezone.now()
    elif action == "withdraw":
        _require(can_prepare(actor, entity) or can_approve(actor, entity), "Only Finance or management withdraw a plan.")
        if row.status == E.Status.WITHDRAWN:
            raise InvalidTransaction("This plan is already withdrawn.")
        if _locked_in_run(row):
            raise InvalidTransaction("This plan is in an approved run, so it cannot be withdrawn.")
        row.status = E.Status.WITHDRAWN
    else:
        raise InvalidTransaction("Choose approve or withdraw.")
    row.save()
    _log(record, actor, f"{'approved' if action == 'approve' else 'withdrew'} a plan", person=row.employee.full_name, plan=row.plan)
    return overview(actor=actor, business_unit=business_unit, year=record.year)


# ---------------------------------------------------------------- runs


def _snapshot(record, run):
    company, units, lines = work_out(record)
    run.policy_version, run.policy = record.policy_version, record.policy
    run.company, run.units, run.lines = _company_row(company, record), _unit_rows(units), lines
    run.commission_total = sum((Decimal(line["amount"]) for line in lines if line["amount"] and line["kind"] == "commission"), ZERO)
    run.reward_total = sum((Decimal(line["amount"]) for line in lines if line["amount"] and line["kind"] == "reward"), ZERO)
    run.pending_count = sum(1 for line in lines if line["pending"])
    return lines


def _paid_by(run, actor):
    return any(line["user"] == actor.pk and line["amount"] and Decimal(line["amount"]) > 0 for line in run.lines)


def _load_run(record, data):
    run_id = data.get("run_id")
    run = IncentiveRun.objects.select_for_update().filter(pk=run_id if type(run_id) is int else None, year=record).first()
    if run is None:
        raise InvalidTransaction("That run was not found.")
    return run


def _same_as_now(record, run):
    _, _, lines = work_out(record)
    keep = ("entitlement", "amount", "pending")
    return [{key: line[key] for key in keep} for line in lines] == [{key: line[key] for key in keep} for line in run.lines]


@transaction.atomic
def prepare_run(*, actor, business_unit, data):
    entity, record = _load_year(actor, business_unit, data)
    _require(can_prepare(actor, entity), "Only people who prepare commission and rewards can work them out.")
    if timezone.localdate().year <= record.year:
        raise InvalidTransaction(f"Commission and rewards for {record.year} are worked out once the year is over.")
    run = record.runs.select_for_update().filter(status__in=[S.PREPARED, S.CHECKED]).first()
    if run is not None and run.status == S.CHECKED:
        raise InvalidTransaction("A run is checked and waiting for approval. Approve it or send it back first.")
    if run is None:
        run = IncentiveRun(year=record, number=(record.runs.count() + 1), policy_version=record.policy_version, prepared_by=actor)
    lines = _snapshot(record, run)
    if not lines:
        raise InvalidTransaction("There is nobody left to work out: every plan is already in an approved run.")
    run.prepared_by, run.prepared_at = actor, timezone.now()
    run.save()
    _log(record, actor, "prepared a run", run=run.number, commission=_money(run.commission_total), reward=_money(run.reward_total),
         pending=run.pending_count)
    return overview(actor=actor, business_unit=business_unit, year=record.year)


@transaction.atomic
def discard_run(*, actor, business_unit, data):
    entity, record = _load_year(actor, business_unit, data)
    run = _load_run(record, data)
    _require(can_prepare(actor, entity) or can_check(actor, entity) or can_approve(actor, entity),
             "Only people who prepare, check or approve commission and rewards can send a run back.")
    if run.status not in (S.PREPARED, S.CHECKED):
        raise InvalidTransaction("An approved run cannot be sent back.")
    number = run.number
    run.delete()
    _log(record, actor, "sent a run back", run=number, reason=str(data.get("reason") or "")[:200])
    return overview(actor=actor, business_unit=business_unit, year=record.year)


@transaction.atomic
def check_run(*, actor, business_unit, data):
    entity, record = _load_year(actor, business_unit, data)
    run = _load_run(record, data)
    _require(can_check(actor, entity), "Only people who check commission and rewards (HR) can check a run.")
    if run.status != S.PREPARED:
        raise InvalidTransaction("Only a run waiting to be checked can be checked.")
    if actor.pk == run.prepared_by_id:
        raise WorkflowPermissionDenied("You prepared this run, so someone else must check it.")
    if _paid_by(run, actor):
        raise WorkflowPermissionDenied("This run pays you, so someone else must check it.")
    if not _same_as_now(record, run):
        raise InvalidTransaction("Figures or plans changed since this run was prepared. Prepare it again first.")
    run.status, run.checked_by, run.checked_at = S.CHECKED, actor, timezone.now()
    run.save(update_fields=["status", "checked_by", "checked_at"])
    _log(record, actor, "checked a run", run=run.number)
    return overview(actor=actor, business_unit=business_unit, year=record.year)


@transaction.atomic
def approve_run(*, actor, business_unit, data):
    """Approve the run and post the cost: commission and rewards owed, paid later through payroll."""
    entity, record = _load_year(actor, business_unit, data)
    run = _load_run(record, data)
    _require(can_approve(actor, entity), "Only people who approve commission and rewards can approve a run.")
    if run.status != S.CHECKED:
        raise InvalidTransaction("Only a checked run can be approved.")
    if actor.pk in (run.prepared_by_id, run.checked_by_id):
        raise WorkflowPermissionDenied("You prepared or checked this run, so someone else must approve it.")
    if _paid_by(run, actor):
        raise WorkflowPermissionDenied("This run pays you, so someone else must approve it.")
    if not _same_as_now(record, run):
        raise InvalidTransaction("Figures or plans changed since this run was prepared. Send it back and prepare it again.")
    paying = [line for line in run.lines if line["amount"] and Decimal(line["amount"]) > 0]
    by_unit = defaultdict(lambda: {"commission": ZERO, "reward": ZERO, "people": 0})
    for line in paying:
        by_unit[line["paid_by_unit"]][line["kind"]] += Decimal(line["amount"])
        by_unit[line["paid_by_unit"]]["people"] += 1
    when = min(datetime.date(record.year, 12, 31), timezone.localdate())
    for unit in BusinessUnit.objects.filter(pk__in=by_unit).select_related("legal_entity"):
        sums = by_unit[unit.pk]
        label = f"{record.year} commission and rewards (run {run.number})"
        journal = []
        if sums["commission"] > 0:
            journal.append(("TEST_EXP_COMMISSION", sums["commission"], ZERO, f"{label}: commission"))
        if sums["reward"] > 0:
            journal.append(("TEST_EXP_REWARDS", sums["reward"], ZERO, f"{label}: performance rewards"))
        journal.append(("TEST_COMMISSION_PAYABLE", ZERO, sums["commission"] + sums["reward"], f"{label}: paid with payroll"))
        operation, _ = _begin_idempotent_operation(f"incentive-run-{run.pk}-unit-{unit.pk}", "incentive_run", {"run": run.pk, "unit": unit.pk})
        entry = _post_journal(legal_entity=unit.legal_entity, business_unit=unit, idempotency=operation, kind=JournalEntry.Kind.COMMISSION,
                              description=f"TEST ONLY: {label} for {unit.name}, {sums['people']} people"[:240], lines=journal,
                              document_date=when)
        _complete(operation, {"run": run.pk, "journal_entry_id": entry.pk})
        run.journal_entries.add(entry)
    run.status, run.approved_by, run.approved_at = S.APPROVED, actor, timezone.now()
    run.save(update_fields=["status", "approved_by", "approved_at"])
    _log(record, actor, "approved a run", run=run.number, commission=_money(run.commission_total), reward=_money(run.reward_total))
    return overview(actor=actor, business_unit=business_unit, year=record.year)


@transaction.atomic
def release_run(*, actor, business_unit, data):
    """Release an approved run to payroll: commission in instalments the next year, rewards in the reward month."""
    entity, record = _load_year(actor, business_unit, data)
    run = _load_run(record, data)
    _require(can_release(actor, entity), "Only people who approve payroll can release commission and rewards to it.")
    if run.status != S.APPROVED:
        raise InvalidTransaction("Only an approved run can be released.")
    if actor.pk in (run.prepared_by_id, run.checked_by_id, run.approved_by_id):
        raise WorkflowPermissionDenied("You prepared, checked or approved this run, so someone else must release it.")
    if _paid_by(run, actor):
        raise WorkflowPermissionDenied("This run pays you, so someone else must release it.")
    paying = [line for line in run.lines if line["amount"] and Decimal(line["amount"]) > 0]
    if any(line["kind"] == "reward" for line in paying) and record.reward_payment_month is None:
        raise InvalidTransaction("Set the month fixed rewards are paid in before releasing this run.")
    entitlements = {row.pk: row for row in IncentiveEntitlement.objects.filter(pk__in=[line["entitlement"] for line in paying])}
    for line in paying:
        entitlement = entitlements[line["entitlement"]]
        if line["kind"] == "commission":
            parts = [(datetime.date(record.year + 1, month, 1), amount) for month, amount in rules.instalments(run.policy, line["amount"])]
        else:
            parts = [(record.reward_payment_month, Decimal(line["amount"]))]
        for month, amount in parts:
            if amount > 0:
                IncentiveInstalment.objects.create(run=run, employee_id=line["employee"], entitlement=entitlement, kind=line["kind"],
                                                   due_month=month, amount=amount)
    run.status, run.released_by, run.released_at = S.RELEASED, actor, timezone.now()
    if not paying:
        run.status = S.PAID
    run.save(update_fields=["status", "released_by", "released_at"])
    _log(record, actor, "released a run to payroll", run=run.number)
    return overview(actor=actor, business_unit=business_unit, year=record.year)


# ---------------------------------------------------------------- payroll


def due_for_payroll(business_unit, month):
    """{employee id: [instalment]}: released commission and rewards due by this payroll month and not yet paid."""
    owed = defaultdict(list)
    for row in (IncentiveInstalment.objects.filter(employee__business_unit=business_unit, due_month__lte=month, paid_in__isnull=True)
                .select_related("run__year").order_by("due_month", "pk")):
        owed[row.employee_id].append(row)
    return owed


def mark_paid(payroll_run, instalment_ids):
    IncentiveInstalment.objects.filter(pk__in=instalment_ids, paid_in__isnull=True).update(paid_in=payroll_run)
    for run in IncentiveRun.objects.filter(instalments__pk__in=instalment_ids, status=S.RELEASED).distinct():
        if not run.instalments.filter(paid_in__isnull=True).exists():
            run.status = S.PAID
            run.save(update_fields=["status"])
