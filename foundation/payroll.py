"""Monthly payroll: work out each person's pay, approve and post it, and make the WPS salary file.

Tivoli's rules (from Sam, 1 Nov 2026):

- Pay is basic plus housing, transport, mobile and other allowances, paid on the 1st
  of the next month through WPS.
- Overtime, absences and late arrivals count up to the cut-off, 10 days before the end
  of the month; anything after it goes into the next month's payroll.
- Overtime = basic / 30 / 8 x hours x 1.25 on a working day, x 1.50 on weekends and
  public holidays. Its cost goes to the sales order it was approved for.
- An absent day costs one day's pay: total monthly pay / 30. Approved unpaid leave is
  treated the same way.
- Late arrivals and early leaving without approval: the first 3 in the period have no
  effect; the next 3 each get a warning letter; after that every minute is deducted
  twice (1 hour late = 2 hours' pay), at total monthly pay / 30 / 8 per hour.
- End of service for someone whose last day falls in the month = basic x years of
  service x 21 / 30, nothing before 1 full year, unpaid leave not counted as service.

Provisional until confirmed:

- Someone who joins or leaves during the month is paid for their days: monthly pay / 30
  per day. A full month is 30 days.
- Total deductions are capped at half the month's pay (Qatar Labour Law); the rest is
  not carried forward, and the line is flagged.
- Larger disciplinary penalties (several days' wage) are HR decisions and are not
  applied here.

Who: "Set salaries and prepare payroll" prepares it; someone else with "Approve and
post payroll" approves it, which posts the journal. Nobody approves a payroll that
pays themselves.
"""

import csv
import datetime
import io
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from .attendance import timing
from .cash_accounts import _text
from .leave import _require_demo_user, _working_days
from .models import (
    AttendanceRecord, Employee, EmployeeDocument, JournalEntry, LeaveRequest, OvertimeLine, OvertimeRequest, PayrollLine,
    PayrollRun, UserActionGrant, WpsSettings,
)
from .pay_records import PARTS, _granted, _iban, can_manage_pay, can_view_pay, current_pay, total
from .purchasing_services import WorkflowPermissionDenied
from .staff_records import _name
from .transaction_services import InvalidTransaction, _begin_idempotent_operation, _complete, _post_journal

A = UserActionGrant.Action
S = PayrollRun.Status
ZERO = Decimal("0.00")
CENT = Decimal("0.01")
CUT_OFF_DAYS = 10
FREE_LATE, WARNING_LATE, LATE_FACTOR = 3, 3, 2
EOS_DAYS_PER_YEAR = 21
DEDUCTION_CAP = Decimal("0.5")
# The WPS file names each bank by a short code (CBQ's 2015 WPS file specification, Annex 2),
# matched here from the bank code inside a Qatar IBAN.
WPS_BANKS = {
    "ABQQ": ("ABQ", "Ahli Bank"), "ARAB": ("ARB", "Arab Bank"), "BRWA": ("BBQ", "Dukhan Bank (formerly Barwa Bank)"),
    "BNPA": ("BNP", "BNP Paribas"), "CBQA": ("CBQ", "Commercial Bank of Qatar"), "DOHB": ("DBQ", "Doha Bank"),
    "BBME": ("HSB", "HSBC Bank Middle East"), "IBOQ": ("IBQ", "International Bank of Qatar"),
    "QIIB": ("IIB", "Qatar International Islamic Bank"), "KLJI": ("KCB", "Al Khaliji Bank"),
    "MAFR": ("MAR", "Masraf Al Rayan"), "MSHQ": ("MSQ", "Mashreq Bank"), "QDBA": ("QDB", "Qatar Development Bank"),
    "QISB": ("QIB", "Qatar Islamic Bank"), "QNBA": ("QNB", "Qatar National Bank"), "SCBL": ("SCB", "Standard Chartered Bank"),
    "UBLQ": ("UBL", "United Bank Limited"),
}
POLICY_LABEL = ("TEST ONLY Tivoli rules: paid on the 1st through WPS; overtime, absences and lateness count up to 10 days before "
                "month end; overtime = basic ÷ 30 ÷ 8 × hours × 1.25 (× 1.50 on weekends and holidays); an absent day = total pay "
                "÷ 30; after 3 free late arrivals and 3 warnings, minutes late are deducted twice; end of service = basic × years × "
                "21 ÷ 30. Provisional: part months paid by the day (÷ 30); deductions capped at half the month's pay.")


def _round(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _text_money(value):
    return format(_round(value), "f")


def _count(number, word):
    return f"{number} {word}{'' if number == 1 else 's'}"


def can_approve_pay(user, business_unit):
    return _granted(user, business_unit, A.APPROVE_PAY)


def _month(value, today):
    if value in (None, ""):
        return today.replace(day=1)
    try:
        year, month = (int(part) for part in str(value).split("-"))
        return datetime.date(year, month, 1)
    except (TypeError, ValueError):
        raise InvalidTransaction("Give the month as YYYY-MM.") from None


def _end(month):
    return (month.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)


def periods(month):
    """(first day, last day, first day counted for overtime and absences, cut-off, pay day)."""
    end = _end(month)
    cut_off = end - datetime.timedelta(days=CUT_OFF_DAYS)
    previous_cut_off = month - datetime.timedelta(days=1 + CUT_OFF_DAYS)
    return month, end, previous_cut_off + datetime.timedelta(days=1), cut_off, end + datetime.timedelta(days=1)


def _service_days(employee, last_day):
    unpaid = 0
    for request in employee.leave_requests.filter(kind=LeaveRequest.Kind.UNPAID, status=LeaveRequest.Status.APPROVED,
                                                  start__lte=last_day):
        unpaid += (min(request.end, last_day) - request.start).days + 1
    return (last_day - employee.joined_on).days + 1 - unpaid


def _qid(employee):
    document = (EmployeeDocument.objects.filter(employee=employee, kind=EmployeeDocument.Kind.QID, removed_at__isnull=True)
                .order_by("-pk").first())
    return document.number if document else ""


def _line(employee, pay, month, window):
    """One person's pay for the month, with how each amount was worked out."""
    start, end = month, _end(month)
    first, last = window
    stop = min(end, employee.last_day) if employee.last_day else end
    begin = max(start, employee.joined_on)
    days = 30 if begin == start and stop == end else min(30, (stop - begin).days + 1)
    monthly_total, monthly_basic = total(pay), pay.basic
    basic = _round(monthly_basic * days / 30)
    allowances = _round((monthly_total - monthly_basic) * days / 30)
    day_rate = monthly_total / 30
    hour_basic, hour_total = monthly_basic / 30 / 8, monthly_total / 30 / 8
    notes = []

    by_order, overtime, overtime_hours = {}, ZERO, Decimal("0")
    for line in (OvertimeLine.objects.filter(employee=employee, request__status=OvertimeRequest.Status.APPROVED,
                                             request__work_date__gte=first, request__work_date__lte=last)
                 .select_related("request__sales_order__customer")):
        request = line.request
        amount = _round(hour_basic * line.hours * request.rate_percent / 100)
        entry = by_order.setdefault(request.sales_order_id, {
            "order_id": request.sales_order_id, "label": f"Quote #{request.sales_order_id}",
            "customer": request.sales_order.customer.name, "hours": Decimal("0"), "amount": ZERO})
        entry["hours"] += line.hours
        entry["amount"] += amount
        overtime += amount
        overtime_hours += line.hours

    records = list(AttendanceRecord.objects.filter(employee=employee, day__gte=first, day__lte=last).order_by("day"))
    absent_days = [record.day.isoformat() for record in records if record.status == AttendanceRecord.Status.ABSENT]
    absences = _round(day_rate * len(absent_days))
    unpaid_days = 0
    for request in employee.leave_requests.filter(kind=LeaveRequest.Kind.UNPAID, status=LeaveRequest.Status.APPROVED,
                                                  start__lte=last, end__gte=first):
        unpaid_days += len(_working_days(employee.business_unit, max(request.start, first), min(request.end, last)))
    unpaid = _round(day_rate * unpaid_days)

    incidents, lateness, warnings = [], ZERO, 0
    for record in records:
        late, early = timing(record, employee.work_schedule)
        if not (late or early) or record.excused:
            continue
        count = len(incidents) + 1
        if count <= FREE_LATE:
            outcome, amount = "no effect", ZERO
        elif count <= FREE_LATE + WARNING_LATE:
            outcome, amount = "warning letter", ZERO
            warnings += 1
        else:
            outcome, amount = "deducted at double", _round(hour_total * (late + early) * LATE_FACTOR / 60)
        lateness += amount
        incidents.append({"day": record.day.isoformat(), "minutes": late + early, "outcome": outcome, "amount": _text_money(amount)})

    eos, eos_detail = ZERO, None
    if employee.last_day and start <= employee.last_day <= end:
        years = Decimal(_service_days(employee, employee.last_day)) / Decimal("365")
        if years >= 1:
            eos = _round(monthly_basic * years * EOS_DAYS_PER_YEAR / 30)
        eos_detail = {"years": format(years.quantize(Decimal("0.01")), "f"), "amount": _text_money(eos)}
        if years < 1:
            notes.append("Leaves before 1 full year of service, so no end of service pay.")

    deductions = absences + unpaid + lateness
    cap = _round((basic + allowances + overtime) * DEDUCTION_CAP)
    capped = deductions > cap
    if capped:
        notes.append(f"Deductions of {_text_money(deductions)} QAR are capped at half the month's pay, {_text_money(cap)} QAR.")
        deductions = cap
    gross = basic + allowances + overtime + eos
    return {
        "days_paid": days, "basic": basic, "allowances": allowances, "overtime": overtime, "overtime_hours": overtime_hours,
        "end_of_service": eos, "deductions": deductions, "net": gross - deductions,
        "detail": {
            "pay": {**{part: _text_money(getattr(pay, part)) for part in PARTS}, "total": _text_money(monthly_total),
                    "other_label": pay.other_label, "from": pay.effective_from.strftime("%Y-%m")},
            "day_rate": _text_money(day_rate), "hour_basic": _text_money(hour_basic), "hour_total": _text_money(hour_total),
            "overtime": [{**entry, "hours": format(entry["hours"].normalize(), "f"), "amount": _text_money(entry["amount"])}
                         for entry in by_order.values()],
            "absent_days": absent_days, "absences": _text_money(absences), "unpaid_days": unpaid_days, "unpaid": _text_money(unpaid),
            "lateness": incidents, "lateness_total": _text_money(lateness), "warning_letters": warnings,
            "end_of_service": eos_detail, "capped": capped, "notes": notes,
            "bank_name": pay.bank_name, "iban": pay.iban,
        },
    }


def _run_row(run, actor):
    lines = list(run.lines.select_related("employee").order_by("employee__department", "employee__full_name"))
    in_run = {line.employee.user_id for line in lines}
    unit = run.business_unit
    return {
        "id": run.pk, "month": run.month.strftime("%Y-%m"), "month_label": run.month.strftime("%B %Y"),
        "period_start": run.period_start.isoformat(), "period_end": run.period_end.isoformat(), "pay_date": run.pay_date.isoformat(),
        "status": run.status, "status_label": run.get_status_display(), "totals": run.totals, "warnings": run.warnings,
        "prepared_by": _name(run.prepared_by), "prepared_at": run.prepared_at.isoformat(),
        "approved_by": _name(run.approved_by), "approved_at": run.approved_at.isoformat() if run.approved_at else "",
        "journal_entry": run.journal_entry_id,
        "lines": [{"id": line.pk, "employee": {"id": line.employee_id, "name": line.employee.full_name,
                                               "department": line.employee.department, "job_title": line.employee.job_title},
                   "days_paid": line.days_paid, "basic": _text_money(line.basic), "allowances": _text_money(line.allowances),
                   "overtime": _text_money(line.overtime), "overtime_hours": format(line.overtime_hours.normalize(), "f"),
                   "end_of_service": _text_money(line.end_of_service), "deductions": _text_money(line.deductions),
                   "net": _text_money(line.net), "detail": {**line.detail, "iban": (f"{line.detail['iban'][:4]} •••• {line.detail['iban'][-4:]}"
                                                                                     if line.detail.get("iban") else "")}}
                  for line in lines],
        "can_recalculate": run.status == S.PREPARED and can_manage_pay(actor, unit),
        "can_approve": (run.status == S.PREPARED and can_approve_pay(actor, unit) and actor.pk != run.prepared_by_id
                        and actor.pk not in in_run),
        "can_download": run.status == S.POSTED and can_view_pay(actor, unit),
    }


def _wps_row(settings):
    if settings is None:
        return None
    return {"employer_eid": settings.employer_eid, "payer_bank": settings.payer_bank,
            "payer_iban": f"{settings.payer_iban[:4]} •••• {settings.payer_iban[-4:]}", "set_by": _name(settings.set_by)}


def payroll_overview(*, actor, business_unit, month=None):
    _require_demo_user(actor)
    if business_unit is None or not can_view_pay(actor, business_unit):
        raise WorkflowPermissionDenied("Payroll is shown only to people with payroll access in this unit.")
    today = timezone.localdate()
    start = _month(month, today)
    if month in (None, "") and today <= periods(start)[3]:
        start = (start - datetime.timedelta(days=1)).replace(day=1)
    first_day, end, window_start, cut_off, pay_date = periods(start)
    run = (PayrollRun.objects.filter(business_unit=business_unit, month=start)
           .select_related("business_unit", "prepared_by", "approved_by").first())
    return {
        "unit": {"id": business_unit.pk, "name": business_unit.name}, "today": today.isoformat(),
        "month": start.strftime("%Y-%m"), "month_label": start.strftime("%B %Y"),
        "period": {"start": window_start.isoformat(), "cut_off": cut_off.isoformat(), "pay_date": pay_date.isoformat()},
        "run": _run_row(run, actor) if run else None,
        "can_prepare": run is None and can_manage_pay(actor, business_unit) and today > cut_off,
        "cut_off_passed": today > cut_off,
        "recent": [{"month": row.month.strftime("%Y-%m"), "status_label": row.get_status_display(), "net": row.totals.get("net", "")}
                   for row in PayrollRun.objects.filter(business_unit=business_unit).order_by("-month")[:12]],
        "wps": _wps_row(WpsSettings.objects.filter(business_unit=business_unit).select_related("set_by").first()),
        "wps_banks": [{"code": code, "name": name} for code, name in sorted(set(WPS_BANKS.values()))],
        "can_manage": can_manage_pay(actor, business_unit), "policy": POLICY_LABEL,
    }


@transaction.atomic
def _settlement_detail(settlement):
    return {"settlement": settlement.pk, "years": settlement.detail.get("years", ""), "gratuity": _text_money(settlement.gratuity),
            "already_paid": _text_money(settlement.already_paid), "leave_days": format(settlement.leave_days.normalize(), "f"),
            "leave_pay": _text_money(settlement.leave_pay),
            "additions": _text_money(sum((Decimal(row["amount"]) for row in settlement.additions), ZERO)),
            "deductions": _text_money(sum((Decimal(row["amount"]) for row in settlement.deductions), ZERO)),
            "loans": [{"loan": row["loan"], "label": row["label"], "amount": row["amount"]}
                      for row in settlement.deductions if row.get("loan")],
            "amount": _text_money(settlement.total)}


def _apply_settlement(worked, settlement):
    """Pay the approved settlement in place of the end of service pay worked out by payroll."""
    worked["net"] += settlement.total - worked["end_of_service"]
    worked["end_of_service"] = settlement.total
    worked["detail"]["end_of_service"] = _settlement_detail(settlement)


def _settlement_only(pay, settlement):
    """A line for someone who left before this month: only their approved settlement."""
    detail = {"pay": {**{part: _text_money(getattr(pay, part)) for part in PARTS}, "total": _text_money(total(pay)),
                      "other_label": pay.other_label, "from": pay.effective_from.strftime("%Y-%m")},
              "day_rate": "0.00", "hour_basic": "0.00", "hour_total": "0.00", "overtime": [], "absent_days": [], "absences": "0.00",
              "unpaid_days": 0, "unpaid": "0.00", "lateness": [], "lateness_total": "0.00", "warning_letters": 0,
              "end_of_service": _settlement_detail(settlement), "capped": False,
              "notes": [f"Left on {settlement.last_day:%d %b %Y}; only the end-of-service settlement is paid this month."],
              "bank_name": pay.bank_name, "iban": pay.iban}
    return {"days_paid": 0, "basic": ZERO, "allowances": ZERO, "overtime": ZERO, "overtime_hours": Decimal("0"),
            "end_of_service": settlement.total, "deductions": ZERO, "net": settlement.total, "detail": detail}


def prepare_payroll(*, actor, business_unit, data):
    """Work out the month's payroll, or work it out again while it is still waiting for approval."""
    _require_demo_user(actor)
    if business_unit is None or not can_manage_pay(actor, business_unit):
        raise WorkflowPermissionDenied("Only people who prepare payroll in this unit can do this.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the month.")
    today = timezone.localdate()
    month = _month(data.get("month"), today)
    start, end, window_start, cut_off, pay_date = periods(month)
    if today <= cut_off:
        raise InvalidTransaction(f"{month:%B %Y} payroll can be prepared after the cut-off on {cut_off:%d %b %Y}.")
    run = PayrollRun.objects.select_for_update().filter(business_unit=business_unit, month=month).first()
    if run is not None and run.status == S.POSTED:
        raise InvalidTransaction(f"{month:%B %Y} payroll is already approved and posted.")
    from .incentives import due_for_payroll
    from .end_of_service import approved_for
    from .staff_loans import outstanding, take_instalments

    settlements = approved_for(business_unit, month)
    commission = due_for_payroll(business_unit, month)
    people = list(Employee.objects.filter(business_unit=business_unit, joined_on__lte=end).exclude(last_day__lt=start)
                  .select_related("business_unit", "work_schedule").order_by("department", "full_name"))
    people += [row.employee for row in settlements.values() if row.employee.last_day < start]
    if not people:
        raise InvalidTransaction(f"Nobody works in {business_unit.name} in {month:%B %Y}.")
    pays = {person.pk: current_pay(person, start) for person in people}
    missing = [person.full_name for person in people if pays[person.pk] is None]
    if missing:
        raise InvalidTransaction(f"Enter the pay for {', '.join(missing[:5])}{' and others' if len(missing) > 5 else ''} "
                                 "on the Salaries page first.")
    warnings = []
    no_hours = [person.full_name for person in people if person.work_schedule_id is None]
    if no_hours:
        warnings.append(f"{', '.join(no_hours[:5])}{' and others' if len(no_hours) > 5 else ''} "
                        f"{'has' if len(no_hours) == 1 else 'have'} no working hours on the Salaries page, so late arrivals "
                        "and early leaving are not counted for them.")
    if run is None:
        run = PayrollRun(business_unit=business_unit, month=month)
    else:
        run.lines.all().delete()
    run.period_start, run.period_end, run.pay_date = window_start, cut_off, pay_date
    run.prepared_by, run.prepared_at = actor, timezone.now()
    run.save()
    sums = defaultdict(lambda: ZERO)
    for person in people:
        settlement = settlements.get(person.pk)
        if person.last_day and person.last_day < start:
            worked = _settlement_only(pays[person.pk], settlement)
        else:
            worked = _line(person, pays[person.pk], month, (window_start, cut_off))
            if settlement is not None:
                _apply_settlement(worked, settlement)
            else:
                cap = _round((worked["basic"] + worked["allowances"] + worked["overtime"]) * DEDUCTION_CAP)
                take_instalments(worked, person, month, cap)
                if person.last_day and person.last_day <= end:
                    warnings.append(f"{person.full_name} leaves on {person.last_day:%d %b %Y} and has no approved end-of-service "
                                    "settlement yet, so only the end of service pay is included. Approve the settlement and work "
                                    "the payroll out again to pay unused leave too.")
                    owed = sum((amount for _, amount in outstanding(person)), ZERO) - sum(
                        (Decimal(row["amount"]) for row in worked["detail"]["loans"]), ZERO)
                    if owed > 0:
                        warnings.append(f"{person.full_name} still owes {_text_money(owed)} QAR in staff loans or advances, "
                                        "which the end-of-service settlement takes back.")
        earned = commission.get(person.pk, [])
        worked["detail"]["commission"] = [{"instalment": row.pk, "year": row.run.year.year, "kind": row.kind,
                                           "month": row.due_month.strftime("%Y-%m"), "amount": _text_money(row.amount)}
                                          for row in earned]
        worked["commission"] = sum((row.amount for row in earned), ZERO)
        worked["net"] += worked["commission"]
        recorded = set(AttendanceRecord.objects.filter(employee=person, day__gte=window_start, day__lte=cut_off)
                       .values_list("day", flat=True))
        on_leave = set()
        for request in person.leave_requests.filter(status=LeaveRequest.Status.APPROVED, start__lte=cut_off, end__gte=window_start):
            day = max(request.start, window_start)
            while day <= min(request.end, cut_off):
                on_leave.add(day)
                day += datetime.timedelta(days=1)
        gaps = [day for day in _working_days(person.business_unit, max(window_start, person.joined_on),
                                             min(cut_off, person.last_day or cut_off, today))
                if day not in recorded and day not in on_leave] if worked["days_paid"] else []
        if gaps:
            warnings.append(f"{person.full_name}: {len(gaps)} working day{'s' if len(gaps) != 1 else ''} without attendance "
                            f"between {window_start:%d %b} and {cut_off:%d %b} are paid as present.")
        if worked["detail"]["capped"]:
            warnings.append(f"{person.full_name}: deductions are capped at half the month's pay.")
        if not worked["detail"]["iban"]:
            warnings.append(f"{person.full_name} has no IBAN, so cannot be paid through WPS.")
        PayrollLine.objects.create(run=run, employee=person, days_paid=worked["days_paid"], basic=worked["basic"],
                                   allowances=worked["allowances"], overtime=worked["overtime"],
                                   overtime_hours=worked["overtime_hours"], end_of_service=worked["end_of_service"],
                                   deductions=worked["deductions"], net=worked["net"], detail=worked["detail"])
        for key in ("basic", "allowances", "overtime", "commission", "end_of_service", "deductions", "net"):
            sums[key] += worked[key]
    run.totals = {key: _text_money(value) for key, value in sums.items()} | {"people": len(people)}
    run.warnings = warnings
    run.save(update_fields=["totals", "warnings"])
    return _run_row(run, actor)


@transaction.atomic
def discard_payroll(*, actor, business_unit, run_id):
    _require_demo_user(actor)
    run = PayrollRun.objects.select_for_update().filter(pk=run_id if type(run_id) is int else None, business_unit=business_unit).first()
    if run is None or not can_manage_pay(actor, business_unit):
        raise WorkflowPermissionDenied("Only people who prepare payroll in this unit can discard it.")
    if run.status == S.POSTED:
        raise InvalidTransaction("An approved payroll cannot be discarded.")
    run.delete()
    return {"discarded": True}


@transaction.atomic
def approve_payroll(*, actor, business_unit, run_id):
    """Approve the payroll and post it: salaries, overtime per sales order and end of service against salaries to pay."""
    _require_demo_user(actor)
    run = (PayrollRun.objects.select_for_update().filter(pk=run_id if type(run_id) is int else None, business_unit=business_unit)
           .select_related("business_unit__legal_entity").first())
    if run is None:
        raise InvalidTransaction("That payroll was not found.")
    if run.status != S.PREPARED:
        raise InvalidTransaction("This payroll is already approved and posted.")
    if not can_approve_pay(actor, business_unit):
        raise WorkflowPermissionDenied("You need 'Approve and post payroll' in this unit.")
    if actor.pk == run.prepared_by_id:
        raise WorkflowPermissionDenied("You prepared this payroll, so someone else must approve it.")
    lines = list(run.lines.select_related("employee"))
    if any(line.employee.user_id == actor.pk for line in lines):
        raise WorkflowPermissionDenied("This payroll pays you, so someone else must approve it.")
    from .end_of_service import approved_for

    included = {(line.detail.get("end_of_service") or {}).get("settlement") for line in lines}
    missing = [row.employee.full_name for row in approved_for(run.business_unit, run.month).values() if row.pk not in included]
    if missing:
        raise InvalidTransaction(f"The end-of-service settlement for {', '.join(missing)} was approved after this payroll was "
                                 "worked out. Work the payroll out again first.")
    from .incentives import due_for_payroll, mark_paid

    paid_runs = {row["instalment"] for line in lines for row in line.detail.get("commission", [])}
    staff = {line.employee_id for line in lines}
    late = {row.pk for person, rows in due_for_payroll(run.business_unit, run.month).items() if person in staff for row in rows}
    if late - paid_runs:
        raise InvalidTransaction("Commission or rewards were released after this payroll was worked out. Work the payroll out again first.")
    commission = sum((Decimal(row["amount"]) for line in lines for row in line.detail.get("commission", [])), ZERO)
    fixed = sum((line.basic + line.allowances for line in lines), ZERO)
    deductions = sum((line.deductions for line in lines), ZERO)
    eos, leave_pay, settled_off = ZERO, ZERO, ZERO
    repayments = []  # (loan id, amount, from the settlement)
    for line in lines:
        settled = line.detail.get("end_of_service") or {}
        repayments += [(row["loan"], Decimal(row["amount"]), False) for row in line.detail.get("loans", [])
                       if Decimal(row["amount"]) > 0]
        if settled.get("settlement"):
            eos += Decimal(settled["gratuity"]) - Decimal(settled["already_paid"])
            leave_pay += Decimal(settled["leave_pay"]) + Decimal(settled["additions"])
            settled_off += Decimal(settled["deductions"])
            repayments += [(row["loan"], Decimal(row["amount"]), True) for row in settled.get("loans", [])]
        else:
            eos += line.end_of_service
    instalments = sum((amount for _, amount, from_settlement in repayments if not from_settlement), ZERO)
    settled_loans = sum((amount for _, amount, from_settlement in repayments if from_settlement), ZERO)
    deductions -= instalments
    settled_off -= settled_loans
    net = sum((line.net for line in lines), ZERO)
    orders = defaultdict(lambda: ZERO)
    labels = {}
    for line in lines:
        for entry in line.detail.get("overtime", []):
            orders[entry["order_id"]] += Decimal(entry["amount"])
            labels[entry["order_id"]] = f"{entry['label']} {entry['customer']}"
    label = f"{run.month:%B %Y} payroll"
    journal = [("TEST_EXP_SALARIES", fixed, ZERO, f"{label}: pay and allowances")]
    journal += [("TEST_EXP_OVERTIME", amount, ZERO, f"{label}: overtime for {labels[order]}"[:200])
                for order, amount in orders.items() if amount > 0]
    if commission > 0:
        journal.append(("TEST_COMMISSION_PAYABLE", commission, ZERO, f"{label}: commission and rewards paid"))
    if eos > 0:
        journal.append(("TEST_EXP_END_OF_SERVICE", eos, ZERO, f"{label}: end of service"))
    if leave_pay > 0:
        journal.append(("TEST_EXP_SALARIES", leave_pay, ZERO, f"{label}: unused leave and other amounts in final settlements"))
    elif leave_pay < 0:
        settled_off -= leave_pay
    if settled_off > 0:
        journal.append(("TEST_EXP_SALARIES", ZERO, settled_off, f"{label}: deductions in final settlements"))
    if deductions > 0:
        journal.append(("TEST_EXP_SALARIES", ZERO, deductions, f"{label}: absence and lateness deductions"))
    if instalments + settled_loans > 0:
        journal.append(("TEST_STAFF_LOANS", ZERO, instalments + settled_loans, f"{label}: staff loans and advances taken back"))
    journal.append(("TEST_SALARIES_PAYABLE", ZERO, net, f"{label}: to pay on {run.pay_date:%d %b %Y}"))
    operation, _ = _begin_idempotent_operation(f"payroll-{run.pk}", "payroll", {"run": run.pk})
    run.journal_entry = _post_journal(
        legal_entity=run.business_unit.legal_entity, business_unit=run.business_unit, idempotency=operation,
        kind=JournalEntry.Kind.PAYROLL, description=f"TEST ONLY: {label} for {run.business_unit.name}, {len(lines)} people"[:240],
        lines=journal, document_date=_end(run.month))
    _complete(operation, {"run": run.pk, "journal_entry_id": run.journal_entry.pk})
    from .staff_loans import record_repayments

    record_repayments(run, repayments)
    mark_paid(run, paid_runs)
    run.status, run.approved_by, run.approved_at = S.POSTED, actor, timezone.now()
    run.save(update_fields=["status", "approved_by", "approved_at", "journal_entry"])
    return _run_row(run, actor)


@transaction.atomic
def set_wps_settings(*, actor, business_unit, data):
    _require_demo_user(actor)
    if business_unit is None or not can_manage_pay(actor, business_unit):
        raise WorkflowPermissionDenied("Only people who prepare payroll in this unit can set the WPS details.")
    from .access_levels import require_master_admin
    require_master_admin(actor, "change the WPS bank file details")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the WPS details.")
    eid = str(data.get("employer_eid") or "").strip()
    if not eid.isdigit() or len(eid) not in (7, 8):
        raise InvalidTransaction("Give the employer establishment ID: the 7 or 8 digits on the company registration card.")
    bank = str(data.get("payer_bank") or "").strip().upper()
    if bank not in {code for code, _ in WPS_BANKS.values()}:
        raise InvalidTransaction("Choose the bank that pays the salaries.")
    iban = _iban(data.get("payer_iban"))
    if not iban:
        raise InvalidTransaction("Give the IBAN of the account that pays the salaries.")
    if WPS_BANKS.get(iban[4:8], ("",))[0] != bank:
        raise InvalidTransaction("That IBAN does not belong to the chosen bank.")
    settings, _ = WpsSettings.objects.update_or_create(business_unit=business_unit, defaults={
        "employer_eid": eid, "payer_bank": bank, "payer_iban": iban, "set_by": actor, "set_at": timezone.now()})
    return _wps_row(settings)


def wps_file(*, actor, business_unit, run_id):
    """The Salary Information File (SIF) for the bank: header, then one record per person."""
    _require_demo_user(actor)
    run = PayrollRun.objects.filter(pk=run_id if type(run_id) is int else None, business_unit=business_unit).first()
    if run is None or not can_view_pay(actor, business_unit):
        raise InvalidTransaction("That payroll was not found.")
    if run.status != S.POSTED:
        raise InvalidTransaction("The WPS file is made once the payroll is approved.")
    settings = WpsSettings.objects.filter(business_unit=business_unit).first()
    if settings is None:
        raise InvalidTransaction("Set the employer ID and paying account under WPS details first.")
    lines = list(run.lines.select_related("employee").order_by("pk"))
    problems, records = [], []
    for sequence, line in enumerate(lines, start=1):
        person, detail = line.employee, line.detail
        qid, iban = _qid(person), detail.get("iban", "")
        bank = WPS_BANKS.get(iban[4:8], ("",))[0] if iban else ""
        if not qid:
            problems.append(f"{person.full_name} has no Qatar ID on the staff record")
        if not bank:
            problems.append(f"{person.full_name} has no IBAN from a WPS bank")
        absent = len(detail.get("absent_days", [])) + int(detail.get("unpaid_days", 0))
        extra = line.allowances + line.overtime + line.end_of_service
        notes = "; ".join(filter(None, [
            _count(len(detail.get("absent_days", [])), "day") + " absent" if detail.get("absent_days") else "",
            _count(int(detail.get("unpaid_days", 0)), "day") + " unpaid leave" if detail.get("unpaid_days") else "",
            "late arrivals" if Decimal(detail.get("lateness_total", "0")) > 0 else "",
            "end of service" if line.end_of_service else ""]))
        pay = detail.get("pay", {})
        records.append([f"{sequence:06d}", qid, "", person.full_name.replace("TEST ONLY ", "")[:70], bank, iban, "M",
                        max(0, line.days_paid - absent), _text_money(line.net), _text_money(line.basic),
                        format(line.overtime_hours.normalize(), "f"), _text_money(extra), _text_money(line.deductions),
                        "Final Settlement" if line.end_of_service else "Normal Payment", notes[:300],
                        _text_money(Decimal(pay.get("housing", "0")) * line.days_paid / 30), "0.00",
                        _text_money(Decimal(pay.get("transport", "0")) * line.days_paid / 30), _text_money(line.overtime),
                        "01" if line.deductions else "", "", ""])
    if problems:
        raise InvalidTransaction("The WPS file cannot be made yet: " + "; ".join(problems[:5]) + ("; and others" if len(problems) > 5 else "") + ".")
    now = timezone.localtime()
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["Employer EID", "File Creation Date", "File Creation Time", "Payer EID", "Payer QID", "Payer Bank Short Name",
                     "Payer IBAN", "Salary Year and Month", "Total Salaries", "Total Records", "SIF Version"])
    writer.writerow([settings.employer_eid, now.strftime("%Y%m%d"), now.strftime("%H%M"), settings.employer_eid, "",
                     settings.payer_bank, settings.payer_iban, run.month.strftime("%Y%m"),
                     _text_money(sum((line.net for line in lines), ZERO)), len(lines), ""])
    writer.writerow(["Record Sequence", "Employee QID", "Employee Visa ID", "Employee Name", "Employee Bank Short Name",
                     "Employee Account", "Salary Frequency", "Number of Working days", "Net Salary", "Basic Salary", "Extra hours",
                     "Extra income", "Deductions", "Payment Type", "Notes / Comments", "Housing Allowance", "Food Allowance",
                     "Transportation Allowance", "Over Time Allowance", "Deduction Reason Code", "Extra Field 1", "Extra Field 2"])
    writer.writerows(records)
    name = f"SIF_{settings.employer_eid}_{settings.payer_bank}_{now:%Y%m%d}_{now:%H%M}.csv"
    return name, out.getvalue()
