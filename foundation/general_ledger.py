"""Read-only general ledger: trial balance, account ledgers and journal entries.

Everything here reads posted journal entries that the operational workflows
(receipts, deliveries, customer and supplier payments) already wrote; nothing
is created or changed. Every entry carries a legal entity and a business unit,
and its accounting date is the source document's date (document_date), not the
posting time. The scope is either the selected business unit or the whole
company (legal entity). Viewing a unit needs the view_ledger permission there;
the company view needs it in every active unit of the company.
"""

import datetime
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, F, Q, Sum, Value, Window
from django.db.models.functions import Coalesce
from django.utils import timezone

from .access import active_grants
from .models import BusinessUnit, DemoUserMarker, JournalEntry, JournalLine, LedgerAccount, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction, _require_demo

ZERO = Decimal("0.00")
DEBIT_NORMAL = {LedgerAccount.AccountClass.ASSET, LedgerAccount.AccountClass.EXPENSE}
CLASS_ORDER = [
    LedgerAccount.AccountClass.ASSET,
    LedgerAccount.AccountClass.LIABILITY,
    LedgerAccount.AccountClass.REVENUE,
    LedgerAccount.AccountClass.EXPENSE,
]
LINES_PAGE = 100
ENTRIES_PAGE = 50
EXPORT_LIMIT = 20000
SCOPES = ("unit", "company")


def can_view_ledger(user, business_unit):
    return active_grants(user, UserActionGrant.Action.VIEW_LEDGER).filter(business_unit=business_unit).exists()


def can_view_company_ledger(user, legal_entity):
    units = set(BusinessUnit.objects.filter(legal_entity=legal_entity, is_active=True).values_list("pk", flat=True))
    granted = set(
        active_grants(user, UserActionGrant.Action.VIEW_LEDGER)
        .filter(business_unit__legal_entity=legal_entity)
        .values_list("business_unit_id", flat=True)
    )
    return bool(units) and units <= granted


def _require_viewer(user, business_unit, scope="unit"):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("The general ledger runs only in the synthetic demo environment.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can view the general ledger.")
    _require_demo(business_unit, business_unit.legal_entity)
    scope = scope or "unit"
    if scope not in SCOPES:
        raise InvalidTransaction("Choose the business unit or the whole company.")
    if not can_view_ledger(user, business_unit):
        raise WorkflowPermissionDenied("You do not have permission to view the general ledger for this business unit.")
    if scope == "company" and not can_view_company_ledger(user, business_unit.legal_entity):
        raise WorkflowPermissionDenied(
            "The company ledger needs the 'view general ledger' permission in every business unit of the company."
        )
    return scope


def _money(value):
    return format((value or ZERO).quantize(Decimal("0.01")), "f")


def _day(value, fallback, field):
    if value in (None, ""):
        return fallback
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise InvalidTransaction(f"Give the {field} date as YYYY-MM-DD.") from None


def _period(start, end):
    today = timezone.localdate()
    end = _day(end, today, "end")
    start = _day(start, end.replace(day=1), "start")
    if start > end:
        raise InvalidTransaction("The start date must be on or before the end date.")
    return start, end


def _page(value):
    try:
        page = int(value or 1)
    except (TypeError, ValueError):
        raise InvalidTransaction("Page must be a whole number.") from None
    return max(page, 1)


def _entry_filter(business_unit, scope, prefix=""):
    if scope == "company":
        return Q(**{f"{prefix}legal_entity": business_unit.legal_entity})
    return Q(**{f"{prefix}business_unit": business_unit})


def _posted_entries(business_unit, scope):
    return JournalEntry.objects.filter(
        _entry_filter(business_unit, scope), status=JournalEntry.Status.POSTED, is_synthetic=True,
    )


def _posted_lines(business_unit, scope):
    return JournalLine.objects.filter(
        _entry_filter(business_unit, scope, "entry__"),
        entry__status=JournalEntry.Status.POSTED,
        entry__is_synthetic=True,
    )


def _natural(account_class, net):
    """A debit-minus-credit amount shown in the account's normal direction (credits for liabilities and revenue)."""
    return net if account_class in DEBIT_NORMAL else -net


def _totals(lines):
    rows = lines.values("account_id").annotate(
        debit=Coalesce(Sum("debit"), Value(ZERO)), credit=Coalesce(Sum("credit"), Value(ZERO)),
    )
    return {row["account_id"]: (row["debit"], row["credit"]) for row in rows}


def _unit(business_unit):
    return {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name}


def _scope(business_unit, scope):
    entity = business_unit.legal_entity
    return {
        "scope": scope,
        "unit": _unit(business_unit),
        "legal_entity": {"id": entity.pk, "code": entity.code, "name": entity.name},
        "scope_label": f"{entity.name}, all business units" if scope == "company" else f"{business_unit.name} ({entity.name})",
    }


def _kind_label(kind):
    return JournalEntry.Kind(kind).label.replace("Synthetic ", "").capitalize()


def _entry_dates(entry):
    return {
        "document_date": entry.document_date.isoformat(),
        "posted_on": timezone.localtime(entry.posted_at).date().isoformat(),
    }


def _entry_owner(entry):
    return {"legal_entity_code": entry.legal_entity.code, "unit_code": entry.business_unit.code}


def trial_balance(*, actor, business_unit, start=None, end=None, scope="unit"):
    scope = _require_viewer(actor, business_unit, scope)
    start, end = _period(start, end)
    lines = _posted_lines(business_unit, scope)
    opening = _totals(lines.filter(entry__document_date__lt=start))
    period = _totals(lines.filter(entry__document_date__gte=start, entry__document_date__lte=end))
    accounts = LedgerAccount.objects.filter(pk__in=set(opening) | set(period)).order_by("code")
    rows = []
    totals = dict.fromkeys(("opening_debit", "opening_credit", "debit", "credit", "closing_debit", "closing_credit"), ZERO)
    for account in sorted(accounts, key=lambda a: (CLASS_ORDER.index(a.account_class), a.code)):
        opening_debit, opening_credit = opening.get(account.pk, (ZERO, ZERO))
        debit, credit = period.get(account.pk, (ZERO, ZERO))
        opening_net = opening_debit - opening_credit
        closing_net = opening_net + debit - credit
        values = {
            "opening_debit": max(opening_net, ZERO), "opening_credit": max(-opening_net, ZERO),
            "debit": debit, "credit": credit,
            "closing_debit": max(closing_net, ZERO), "closing_credit": max(-closing_net, ZERO),
        }
        for key, value in values.items():
            totals[key] += value
        rows.append({
            "account_id": account.pk,
            "code": account.code,
            "name": account.name,
            "account_class": account.account_class,
            "class_label": LedgerAccount.AccountClass(account.account_class).label,
            **{key: _money(value) for key, value in values.items()},
            "closing_balance": _money(_natural(account.account_class, closing_net)),
        })
    unbalanced = (
        _posted_entries(business_unit, scope)
        .annotate(debit=Coalesce(Sum("lines__debit"), Value(ZERO)), credit=Coalesce(Sum("lines__credit"), Value(ZERO)))
        .exclude(debit=F("credit"))
        .count()
    )
    entry_count = _posted_entries(business_unit, scope).filter(document_date__gte=start, document_date__lte=end).count()
    return {
        **_scope(business_unit, scope),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "currency": "QAR",
        "rows": rows,
        "totals": {key: _money(value) for key, value in totals.items()},
        "checks": {
            "period_balanced": totals["debit"] == totals["credit"],
            "closing_balanced": totals["closing_debit"] == totals["closing_credit"],
            "unbalanced_entries": unbalanced,
        },
        "entry_count": entry_count,
        "kinds": [{"kind": kind, "label": _kind_label(kind)} for kind in JournalEntry.Kind.values],
        "can_view_company": can_view_company_ledger(actor, business_unit.legal_entity),
        "policy_label": "TEST ONLY synthetic ledger, not Tivoli-approved accounts",
    }


def account_ledger(*, actor, business_unit, account_id, start=None, end=None, page=1, scope="unit", everything=False):
    scope = _require_viewer(actor, business_unit, scope)
    start, end = _period(start, end)
    page = _page(page)
    account = LedgerAccount.objects.filter(pk=account_id, legal_entity=business_unit.legal_entity).first()
    if account is None:
        raise InvalidTransaction("Choose an account of this company.")
    lines = _posted_lines(business_unit, scope).filter(account=account)
    before = lines.filter(entry__document_date__lt=start).aggregate(
        debit=Coalesce(Sum("debit"), Value(ZERO)), credit=Coalesce(Sum("credit"), Value(ZERO)),
    )
    opening = before["debit"] - before["credit"]
    in_period = lines.filter(entry__document_date__gte=start, entry__document_date__lte=end)
    summary = in_period.aggregate(
        debit=Coalesce(Sum("debit"), Value(ZERO)), credit=Coalesce(Sum("credit"), Value(ZERO)), count=Count("pk"),
    )
    order = [F("entry__document_date").asc(), F("entry__posted_at").asc(), F("pk").asc()]
    ordered = in_period.select_related("entry", "entry__legal_entity", "entry__business_unit").annotate(
        running=Window(Sum(F("debit") - F("credit")), order_by=order),
    ).order_by(*order)
    if everything:
        shown = list(ordered[:EXPORT_LIMIT])
    else:
        offset = (page - 1) * LINES_PAGE
        shown = list(ordered[offset:offset + LINES_PAGE])
    closing = opening + summary["debit"] - summary["credit"]

    def natural(net):
        return _money(_natural(account.account_class, net))

    return {
        **_scope(business_unit, scope),
        "account": {
            "id": account.pk, "code": account.code, "name": account.name,
            "account_class": account.account_class,
            "class_label": LedgerAccount.AccountClass(account.account_class).label,
            "normal_side": "debit" if account.account_class in DEBIT_NORMAL else "credit",
        },
        "start": start.isoformat(),
        "end": end.isoformat(),
        "opening_balance": natural(opening),
        "closing_balance": natural(closing),
        "debit": _money(summary["debit"]),
        "credit": _money(summary["credit"]),
        "lines": [
            {
                "id": line.pk,
                "entry_id": line.entry_id,
                **_entry_dates(line.entry),
                **_entry_owner(line.entry),
                "kind": line.entry.kind,
                "description": line.entry.description,
                "memo": line.memo,
                "debit": _money(line.debit),
                "credit": _money(line.credit),
                "balance": natural(opening + line.running),
            }
            for line in shown
        ],
        "pagination": {"page": page, "page_size": LINES_PAGE, "total_count": summary["count"]},
    }


def journal_entries(*, actor, business_unit, start=None, end=None, kind=None, search=None, page=1, scope="unit",
                    everything=False):
    scope = _require_viewer(actor, business_unit, scope)
    start, end = _period(start, end)
    page = _page(page)
    entries = _posted_entries(business_unit, scope).filter(document_date__gte=start, document_date__lte=end)
    if kind:
        if kind not in JournalEntry.Kind.values:
            raise InvalidTransaction("Choose a known entry type.")
        entries = entries.filter(kind=kind)
    search = " ".join((search or "").split())[:80]
    if search:
        condition = Q(description__icontains=search) | Q(lines__memo__icontains=search)
        if search.isdigit():
            condition |= Q(pk=int(search))
        entries = entries.filter(condition).distinct()
    total = entries.count()
    ordered = entries.select_related("legal_entity", "business_unit").annotate(
        total=Coalesce(Sum("lines__debit"), Value(ZERO)), line_count=Count("lines"),
    ).order_by("-document_date", "-posted_at", "-pk")
    offset = 0 if everything else (page - 1) * ENTRIES_PAGE
    shown = ordered[offset:offset + (EXPORT_LIMIT if everything else ENTRIES_PAGE)]
    return {
        **_scope(business_unit, scope),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "entries": [
            {
                "id": entry.pk,
                **_entry_dates(entry),
                **_entry_owner(entry),
                "kind": entry.kind,
                "kind_label": _kind_label(entry.kind),
                "description": entry.description,
                "total": _money(entry.total),
                "line_count": entry.line_count,
            }
            for entry in shown
        ],
        "pagination": {"page": page, "page_size": ENTRIES_PAGE, "total_count": total},
    }


def journal_entry_detail(*, actor, business_unit, entry_id, scope="unit"):
    scope = _require_viewer(actor, business_unit, scope)
    entry = _posted_entries(business_unit, scope).select_related("legal_entity", "business_unit").filter(pk=entry_id).first()
    if entry is None:
        where = "company" if scope == "company" else "selected business unit"
        raise InvalidTransaction(f"This journal entry is not in the {where}.")
    lines = list(entry.lines.select_related("account").order_by("pk"))
    debit = sum((line.debit for line in lines), ZERO)
    credit = sum((line.credit for line in lines), ZERO)
    return {
        "id": entry.pk,
        **_scope(business_unit, scope),
        **_entry_dates(entry),
        **_entry_owner(entry),
        "posted_at": entry.posted_at.isoformat(),
        "kind": entry.kind,
        "kind_label": _kind_label(entry.kind),
        "description": entry.description,
        "currency": entry.currency,
        "policy_label": entry.policy_label,
        "lines": [
            {
                "account_id": line.account_id,
                "code": line.account.code,
                "name": line.account.name,
                "debit": _money(line.debit),
                "credit": _money(line.credit),
                "memo": line.memo,
            }
            for line in lines
        ],
        "debit": _money(debit),
        "credit": _money(credit),
        "balanced": debit == credit,
    }


XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXPORTS = ("trial-balance", "entries", "account", "income-statement", "balance-sheet")


def _columns(rows, columns):
    return [{header: row[key] for key, header in columns} for row in rows]


def _statement_rows(data, columns):
    """Flatten statement sections into labelled rows: each account, then the section total."""
    rows = []
    for section in data["sections"]:
        for row in section["rows"]:
            rows.append({"Section": section["label"], "Line": row["name"], **{h: row[k] for k, h in columns}})
        rows.append({"Section": section["label"], "Line": f"Total {section['label'].lower()}",
                     **{h: section["total"][k] for k, h in columns}})
    return rows


def _export_statement(*, actor, business_unit, view, start, end, scope):
    from .financial_statements import balance_sheet, income_statement
    from .reporting_exports import render_table_xlsx

    if view == "income-statement":
        data = income_statement(actor=actor, business_unit=business_unit, start=start, end=end, scope=scope)
        columns = [("period", "This period"), ("year_to_date", "Year to date")]
        rows = _statement_rows(data, columns)
        summary = {"Gross profit (period)": data["gross_profit"]["period"], "Net profit (period)": data["net_profit"]["period"],
                   "Net profit (year to date)": data["net_profit"]["year_to_date"]}
        title, note = "Profit and loss", f"{data['start']} to {data['end']} by document date; year to date from {data['year_start']}"
        stamp = f"{data['start']}-{data['end']}"
    else:
        data = balance_sheet(actor=actor, business_unit=business_unit, as_of=end, scope=scope)
        columns = [("balance", "Balance")]
        rows = _statement_rows(data, columns)
        summary = {"Total assets": data["total_assets"], "Total liabilities and equity": data["total_liabilities_and_equity"]}
        title, note, stamp = "Balance sheet", f"As of {data['as_of']} by document date", data["as_of"]
    headers = [header for _key, header in columns]
    content = render_table_xlsx(
        title=f"{title}: {data['scope_label']}", note=f"{note}, amounts in QAR", policy_label=data["policy_label"],
        fields=["Section", "Line", *headers], rows=rows, numeric=set(headers) | set(summary), summary=summary,
    )
    code = data["legal_entity"]["code"] if data["scope"] == "company" else data["unit"]["code"]
    return f"ledger-{view}-{code}-{stamp}.xlsx", content


def export_ledger(*, actor, business_unit, view, start=None, end=None, scope="unit", account_id=None, kind=None,
                  search=None):
    """The ledger view as an Excel workbook: returns (file name, bytes). Same permissions and filters as the screen."""
    from .reporting_exports import render_table_xlsx

    if view not in EXPORTS:
        raise InvalidTransaction("Choose the trial balance, journal entries, an account ledger or a statement to export.")
    if view in ("income-statement", "balance-sheet"):
        return _export_statement(actor=actor, business_unit=business_unit, view=view, start=start, end=end, scope=scope)
    if view == "trial-balance":
        data = trial_balance(actor=actor, business_unit=business_unit, start=start, end=end, scope=scope)
        columns = [
            ("code", "Account code"), ("name", "Account"), ("class_label", "Class"),
            ("opening_debit", "Opening debit"), ("opening_credit", "Opening credit"),
            ("debit", "Period debit"), ("credit", "Period credit"),
            ("closing_debit", "Closing debit"), ("closing_credit", "Closing credit"),
        ]
        rows = _columns(data["rows"], columns)
        totals = {header: data["totals"][key] for key, header in columns[3:]}
        title, summary = "Trial balance", {"Entries in period": data["entry_count"], **totals}
    elif view == "entries":
        data = journal_entries(actor=actor, business_unit=business_unit, start=start, end=end, scope=scope, kind=kind,
                               search=search, everything=True)
        columns = [
            ("id", "Entry"), ("document_date", "Document date"), ("posted_on", "Posted on"),
            ("legal_entity_code", "Legal entity"), ("unit_code", "Business unit"), ("kind_label", "Type"),
            ("description", "Description"), ("line_count", "Lines"), ("total", "Total QAR"),
        ]
        rows = _columns(data["entries"], columns)
        title, summary = "Journal entries", None
    else:
        data = account_ledger(actor=actor, business_unit=business_unit, account_id=account_id, start=start, end=end,
                              scope=scope, everything=True)
        columns = [
            ("document_date", "Document date"), ("posted_on", "Posted on"), ("entry_id", "Entry"),
            ("legal_entity_code", "Legal entity"), ("unit_code", "Business unit"), ("description", "Description"),
            ("memo", "Memo"), ("debit", "Debit"), ("credit", "Credit"), ("balance", "Balance"),
        ]
        rows = _columns(data["lines"], columns)
        account = data["account"]
        title = f"Account ledger {account['code']} {account['name']}"
        summary = {"Opening balance": data["opening_balance"], "Debit": data["debit"], "Credit": data["credit"],
                   "Closing balance": data["closing_balance"]}
    numeric = {header for key, header in columns if key in {
        "opening_debit", "opening_credit", "debit", "credit", "closing_debit", "closing_credit", "total", "balance",
        "line_count", "id", "entry_id",
    }} | (set(summary) if summary else set())
    content = render_table_xlsx(
        title=f"{title}: {data['scope_label']}",
        note=f"{data['start']} to {data['end']} by document date, amounts in QAR",
        policy_label="TEST ONLY synthetic ledger, not Tivoli-approved accounts",
        fields=[header for _key, header in columns], rows=rows, numeric=numeric, summary=summary,
    )
    code = data["legal_entity"]["code"] if data["scope"] == "company" else data["unit"]["code"]
    return f"ledger-{view}-{code}-{data['start']}-{data['end']}.xlsx", content
