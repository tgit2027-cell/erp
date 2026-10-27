"""Chart-ready summaries of the report panels for the visual dashboard.

Each visual is built from the same report the standard view returns
(build_report with the same filters and permission checks), then grouped on
the server so the browser never has to download every row. A field the user
may not see (for example supplier cost) is absent from the report, so the
chart that needs it is simply not produced.
"""

from collections import defaultdict
from decimal import Decimal

from .models import ReportingPanel
from .reporting_services import build_report

ZERO = Decimal("0")
TOP = 10


def _number(value):
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - report values are strings; blanks count as zero
        return ZERO


def _text(value):
    return format(value.quantize(Decimal("0.01")), "f")


def _series(key, label, values):
    return {"key": key, "label": label, "values": [_text(value) for value in values]}


def _chart(chart_id, title, kind, unit, categories, series, *, stacked=False, note=""):
    return {
        "id": chart_id, "title": title, "kind": kind, "unit": unit, "categories": categories,
        "series": series, "stacked": stacked, "note": note,
    }


def _kpi(label, value, unit):
    return {"label": label, "value": _text(value), "unit": unit}


def _by_period(rows, fields):
    periods = sorted({str(row["period"]) for row in rows})
    totals = {field: defaultdict(lambda: ZERO) for field in fields}
    for row in rows:
        for field in fields:
            totals[field][str(row["period"])] += _number(row.get(field))
    return periods, {field: [totals[field][period] for period in periods] for field in fields}


def _top(rows, label_of, value_field, *, limit=TOP):
    totals = defaultdict(lambda: ZERO)
    for row in rows:
        totals[label_of(row)] += _number(row.get(value_field))
    ranked = sorted(totals.items(), key=lambda pair: (-pair[1], pair[0]))
    shown = ranked[:limit]
    note = f"Top {limit} of {len(ranked)}." if len(ranked) > limit else ""
    return [label for label, _ in shown], [value for _, value in shown], note


def _sales(report):
    trend = report.get("trend") or []
    periods = [row["period"] for row in trend]
    amounts = [_number(row["sales_amount"]) for row in trend]
    quantities = [_number(row["quantity"]) for row in trend]
    summary = report.get("summary") or {}
    kpis = [
        _kpi("Sales", _number(summary.get("sales_amount")), "QAR"),
        _kpi("Quantity sold", _number(summary.get("quantity")), "units"),
    ]
    if periods:
        kpis.append(_kpi("Average per period", sum(amounts, ZERO) / len(periods), "QAR"))
    charts = [
        _chart("sales-by-period", "Sales by period", "column", "QAR", periods, [_series("sales_amount", "Sales", amounts)]),
        _chart("quantity-by-period", "Quantity sold by period", "line", "units", periods,
               [_series("quantity", "Quantity", quantities)]),
    ]
    if report["panel"] == ReportingPanel.BEST_SELLERS:
        items = report.get("best_selling_items") or []
        labels, values, note = _top(items, lambda row: f"{row['item_id']} · {row['description']}", "sales_amount")
        charts.append(_chart("top-items", "Best-selling items by sales", "bar", "QAR", labels,
                             [_series("sales_amount", "Sales", values)], note=note))
        categories = report.get("best_selling_categories")
        if categories:
            labels, values, note = _top(categories, lambda row: str(row["category"]), "sales_amount")
            charts.append(_chart("top-categories", "Sales by category", "bar", "QAR", labels,
                                 [_series("sales_amount", "Sales", values)], note=note))
    return kpis, charts


def _gross_margin(report):
    rows = report["rows"]
    periods, values = _by_period(rows, ["revenue", "cost_of_goods_sold", "gross_margin"])
    revenue = sum(values["revenue"], ZERO)
    cogs = sum(values["cost_of_goods_sold"], ZERO)
    margin = revenue - cogs
    kpis = [_kpi("Revenue", revenue, "QAR"), _kpi("Cost of goods sold", cogs, "QAR"), _kpi("Gross margin", margin, "QAR")]
    if revenue:
        kpis.append(_kpi("Gross margin %", margin * 100 / revenue, "%"))
    percents = [
        (value * 100 / revenue_value) if revenue_value else ZERO
        for value, revenue_value in zip(values["gross_margin"], values["revenue"])
    ]
    charts = [
        _chart("revenue-vs-cost", "Revenue and cost of goods sold", "column", "QAR", periods, [
            _series("revenue", "Revenue", values["revenue"]),
            _series("cost_of_goods_sold", "Cost of goods sold", values["cost_of_goods_sold"]),
        ]),
        _chart("margin-percent", "Gross margin % by period", "line", "%", periods,
               [_series("gross_margin_percent", "Gross margin %", percents)]),
    ]
    return kpis, charts


COST_LABELS = {
    "supplier_purchase_value": "Supplier purchases",
    "landed_charges": "Landed charges",
    "posted_expense_accounts": "Posted expenses",
}


def _costs(report):
    fields = [field for field in COST_LABELS if field in report["fields"]]
    periods, values = _by_period(report["rows"], fields)
    kpis = [_kpi(COST_LABELS[field], sum(values[field], ZERO), "QAR") for field in fields]
    charts = [
        _chart("costs-by-period", "Costs and expenses by period", "column", "QAR", periods,
               [_series(field, COST_LABELS[field], values[field]) for field in fields], stacked=True),
    ] if fields else []
    return kpis, charts


def _inventory(report):
    rows = report["rows"]
    measure = "inventory_value" if "inventory_value" in report["fields"] else "on_hand_quantity"
    unit = "QAR" if measure == "inventory_value" else "units"
    kpis = [
        _kpi("Units on hand", sum((_number(row["on_hand_quantity"]) for row in rows), ZERO), "units"),
        _kpi("Units reserved", sum((_number(row["reserved_quantity"]) for row in rows), ZERO), "units"),
    ]
    if measure == "inventory_value":
        kpis.append(_kpi("Inventory value", sum((_number(row["inventory_value"]) for row in rows), ZERO), "QAR"))
    title = "Inventory value" if unit == "QAR" else "Units on hand"
    labels, values, note = _top(rows, lambda row: str(row["warehouse_location"]), measure)
    charts = [_chart("by-location", f"{title} by location", "bar", unit, labels, [_series(measure, title, values)], note=note)]
    labels, values, note = _top(rows, lambda row: str(row["brand_name"]), measure)
    charts.append(_chart("by-brand", f"{title} by brand", "bar", unit, labels, [_series(measure, title, values)], note=note))
    return kpis, charts


def _purchases(report):
    rows = report["rows"]
    measure = "purchase_value" if "purchase_value" in report["fields"] else "quantity"
    unit = "QAR" if measure == "purchase_value" else "units"
    title = "Purchase value" if unit == "QAR" else "Quantity received"
    periods, values = _by_period(rows, [measure, "quantity"])
    kpis = [_kpi("Quantity received", sum(values["quantity"], ZERO), "units")]
    if measure == "purchase_value":
        kpis.insert(0, _kpi("Purchase value", sum(values[measure], ZERO), "QAR"))
    labels, top_values, note = _top(rows, lambda row: str(row["supplier"]), measure)
    charts = [
        _chart("purchases-by-period", f"{title} by period", "column", unit, periods, [_series(measure, title, values[measure])]),
        _chart("by-supplier", f"{title} by supplier", "bar", unit, labels, [_series(measure, title, top_values)], note=note),
    ]
    return kpis, charts


def _transfers(report):
    rows = report["rows"]
    periods, values = _by_period(rows, ["quantity"])
    kpis = [_kpi("Transfers", Decimal(len(rows)), "count"), _kpi("Units moved", sum(values["quantity"], ZERO), "units")]
    labels, top_values, note = _top(
        rows, lambda row: f"{row['source_location']} → {row['destination_location']}", "quantity",
    )
    charts = [
        _chart("transfers-by-period", "Units moved by period", "column", "units", periods,
               [_series("quantity", "Units moved", values["quantity"])]),
        _chart("by-route", "Units moved by route", "bar", "units", labels,
               [_series("quantity", "Units moved", top_values)], note=note),
    ]
    return kpis, charts


def _budgets(report):
    rows = report["rows"]
    budget = sum((_number(row["budget"]) for row in rows), ZERO)
    actual = sum((_number(row["actual"]) for row in rows), ZERO)
    kpis = [_kpi("Budget", budget, "QAR"), _kpi("Actual", actual, "QAR"), _kpi("Actual minus budget", actual - budget, "QAR")]
    shown = rows[:12]
    labels = [
        " · ".join(part for part in (row["business_unit"], row["location"], row["metric"], row["period_start"][:7]) if part)
        for row in shown
    ]
    charts = [_chart("budget-vs-actual", "Budget and actual", "bar", "QAR", labels, [
        _series("budget", "Budget", [_number(row["budget"]) for row in shown]),
        _series("actual", "Actual", [_number(row["actual"]) for row in shown]),
    ], note=f"First 12 of {len(rows)} budget lines." if len(rows) > 12 else "")]
    return kpis, charts


BUILDERS = {
    ReportingPanel.SALES: _sales,
    ReportingPanel.BEST_SELLERS: _sales,
    ReportingPanel.GROSS_MARGIN: _gross_margin,
    ReportingPanel.COSTS_EXPENSES: _costs,
    ReportingPanel.INVENTORY: _inventory,
    ReportingPanel.PURCHASE_HISTORY: _purchases,
    ReportingPanel.TRANSFER_HISTORY: _transfers,
    ReportingPanel.BUDGETS_ACTUALS: _budgets,
}


def build_report_visual(*, actor, panel, query_params):
    if panel in (ReportingPanel.SALES, ReportingPanel.BEST_SELLERS):
        # Sales totals, trend and best sellers are aggregated by the report itself;
        # one row page is enough, so the sales lines are not all loaded.
        params = {key: query_params.get(key) for key in query_params if key not in ("page", "page_size")}
        report = build_report(actor=actor, panel=panel, query_params={**params, "page": 1, "page_size": 1})
        row_count = report["pagination"]["total_count"]
    else:
        report = build_report(actor=actor, panel=panel, query_params=query_params, paginate=False)
        row_count = len(report["rows"])
    kpis, charts = BUILDERS[report["panel"]](report)
    return {
        "panel": report["panel"],
        "title": report["title"],
        "granularity": report["granularity"],
        "currency": report["currency"],
        "filters": report["filters"],
        "scope": report["scope"],
        "row_count": row_count,
        "kpis": kpis,
        "charts": charts,
        "policy_label": report["policy_label"],
    }
