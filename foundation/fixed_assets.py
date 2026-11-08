"""Fixed assets: register, approve, depreciate monthly, sell or write off (synthetic only).

A fixed asset is something the business keeps and uses for more than a year:
showroom furniture, a fit-out, a delivery van, computers. It is not stock for
sale. Buying one is not a running cost of that month; its cost is spread over
its useful life instead.

    approved purchase:   Dr fixed assets at cost          Cr bank and cash (the account it was paid from)
    finished building:   Dr fixed assets at cost          Cr assets under construction
    taken from stock:    Dr fixed assets at cost          Cr stock, at the stock's own value
    each month:          Dr depreciation                  Cr accumulated depreciation
    sold / written off:  Dr bank and cash (any proceeds), Dr accumulated depreciation,
                         Cr fixed assets at cost, and the difference to gain or loss on disposal

Costs of something being built for the company (a showroom, say) are collected
on the "assets under construction" account with manual journals. When it is
finished, it is added here from that account and starts to depreciate.

Depreciation is straight line: (cost less the value expected at the end) divided
by the useful life in months. The month the asset starts being used is charged
only for the days left in it (bought on the 21st of a 30-day month: 10/30 of a
month); the last month takes whatever is left so the total is exact. A run covers one month for the
whole business unit, in order, once that month has ended and every delivery
received up to its end has its supplier bill, and catches up any months an asset missed (for
example one approved after the run for its purchase month). Selling or writing
off needs depreciation run up to the month before; the month of disposal is not
depreciated. Provisional: the categories and their usual lives, straight line
only, QAR only, no tax.
"""

import calendar
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .approval_limits import require_within_limit
from .access import active_grants
from .cash_accounts import _account as _cash_account, _past_day, _reconciled, _text, account_row
from .general_ledger import can_view_ledger
from .models import (
    AssetDepreciation,
    CashAccount,
    CashLineAssignment,
    DemoUserMarker,
    DepreciationRun,
    FixedAsset,
    JournalEntry,
    JournalLine,
    Location,
    PurchaseReceipt,
    StockBalance,
    StockMovement,
    SupplierBill,
    UserActionGrant,
    Variant,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import (
    InvalidTransaction,
    _begin_idempotent_operation,
    _complete,
    _money,
    _post_journal,
    _quantity,
    _require_demo,
    held_quantity,
)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
MANAGE = UserActionGrant.Action.MANAGE_ASSETS
APPROVE = UserActionGrant.Action.APPROVE_ASSET
MAX_COST = Decimal("100000000.00")
USUAL_LIFE_MONTHS = {
    FixedAsset.Category.FURNITURE: 60, FixedAsset.Category.FITOUT: 60, FixedAsset.Category.VEHICLES: 60,
    FixedAsset.Category.COMPUTERS: 36, FixedAsset.Category.MACHINERY: 120, FixedAsset.Category.OTHER: 60,
}
POLICY_LABEL = (
    "TEST ONLY provisional rules: straight-line depreciation, the first month counted by its remaining days; run after the "
    "month ends and every delivery is billed; usual lives are furniture, "
    "fit-out, vehicles and other equipment 5 years, computers 3 years, machinery 10 years; a second person approves "
    "purchases; QAR only, no tax."
)


def _granted(user, business_unit, action):
    return active_grants(user, action).filter(business_unit=business_unit).exists()


def can_manage(user, business_unit):
    return _granted(user, business_unit, MANAGE)


def can_approve(user, business_unit):
    return _granted(user, business_unit, APPROVE)


def _require_user(user, business_unit):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Fixed assets run only in the synthetic demo environment.")
    if not getattr(user, "is_authenticated", False):
        raise WorkflowPermissionDenied("Sign-in is required.")
    if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise WorkflowPermissionDenied("Only synthetic demo users can use fixed assets.")
    _require_demo(business_unit, business_unit.legal_entity)
    if not (can_manage(user, business_unit) or can_approve(user, business_unit) or can_view_ledger(user, business_unit)):
        raise WorkflowPermissionDenied("You need permission to manage, approve or view fixed assets in this business unit.")


def _first(day):
    return day.replace(day=1)


def _next_month(month):
    return (month.replace(day=28) + timezone.timedelta(days=4)).replace(day=1)


def _months_between(start, end):
    """Whole months from start's month to end's month, counting both."""
    return (end.year - start.year) * 12 + end.month - start.month + 1


def _depreciated(asset):
    """Depreciation taken so far, including what the old system took before go-live."""
    return asset.opening_depreciation + (asset.depreciation.aggregate(total=Sum("amount"))["total"] or ZERO)


def _months_used(asset, month):
    """Months of use by the end of month; the first month counts only the days from the start date."""
    elapsed = _months_between(asset.purchase_date, month)
    if elapsed <= 0:
        return Decimal(0)
    days = calendar.monthrange(asset.purchase_date.year, asset.purchase_date.month)[1]
    first = Decimal(days - asset.purchase_date.day + 1) / Decimal(days)
    return first + (elapsed - 1)


def _target(asset, month):
    """How much of this asset should be depreciated in total by the end of month."""
    depreciable = asset.cost - asset.residual_value
    if asset.depreciation_from is not None:
        # Brought over at go-live: the old system's monthly amount carries on until nothing is left (Bilal, 5 Nov 2026).
        months = _months_between(asset.depreciation_from, month)
        if months <= 0:
            return asset.opening_depreciation
        return min(depreciable, asset.opening_depreciation + (asset.opening_monthly or ZERO) * months)
    used = _months_used(asset, month)
    if used <= 0:
        return ZERO
    if used >= asset.life_months:
        return depreciable
    return min(depreciable, (depreciable * used / asset.life_months).quantize(CENT, rounding=ROUND_HALF_UP))


def _charge(asset, month, done=None):
    if asset.status != FixedAsset.Status.IN_USE:
        return ZERO
    done = _depreciated(asset) if done is None else done
    return max(ZERO, _target(asset, month) - done)


def _last_run(business_unit):
    return DepreciationRun.objects.filter(business_unit=business_unit).order_by("-month").first()


def _next_run_month(business_unit):
    last = _last_run(business_unit)
    if last:
        return _next_month(last.month)
    starts = [_first(asset.depreciation_from or asset.purchase_date) for asset in FixedAsset.objects.filter(
        business_unit=business_unit, status__in=[FixedAsset.Status.IN_USE, FixedAsset.Status.DISPOSED])]
    return min(starts) if starts else None


def _f(value):
    return format(value, "f")


def _person(user):
    return (user.get_full_name() or user.username) if user else ""


def _row(asset, actor=None, done=None):
    done = _depreciated(asset) if done is None else done
    depreciable = asset.cost - asset.residual_value
    monthly = (depreciable / asset.life_months).quantize(CENT, rounding=ROUND_HALF_UP)
    in_books = asset.status in (FixedAsset.Status.IN_USE, FixedAsset.Status.DISPOSED)
    row = {
        "id": asset.pk, "number": asset.number, "name": asset.name, "category": asset.category,
        "category_label": asset.get_category_display(), "supplier": asset.supplier,
        "purchase_date": asset.purchase_date.isoformat(), "cost": _f(asset.cost), "residual_value": _f(asset.residual_value),
        "life_months": asset.life_months, "monthly": _f(monthly),
        "depreciated": _f(done), "book_value": _f(asset.cost - done) if in_books else _f(asset.cost),
        "fully_depreciated": in_books and done >= depreciable,
        "source": asset.source, "source_label": asset.get_source_display(),
        "paid_from": {"id": asset.paid_from_id, "name": asset.paid_from.name} if asset.paid_from_id else None,
        "from_stock": None if asset.source != FixedAsset.Source.STOCK else {
            "sku": asset.stock_variant.sku, "location": asset.stock_location.code, "quantity": _f(asset.stock_quantity)},
        "status": asset.status, "status_label": asset.get_status_display(),
        "submitted_by": _person(asset.submitted_by), "decided_by": _person(asset.decided_by),
        "rejection_reason": asset.rejection_reason, "journal_entry_id": asset.journal_entry_id,
        "disposal": None if asset.status != FixedAsset.Status.DISPOSED else {
            "date": asset.disposal_date.isoformat(), "proceeds": _f(asset.disposal_proceeds or ZERO),
            "account": asset.disposal_account.name if asset.disposal_account_id else "", "reason": asset.disposal_reason,
            "by": _person(asset.disposed_by), "journal_entry_id": asset.disposal_entry_id,
        },
    }
    if actor is not None:
        row["can_decide"] = (asset.status == FixedAsset.Status.SUBMITTED and can_approve(actor, asset.business_unit)
                             and actor.pk != asset.submitted_by_id)
        row["can_dispose"] = asset.status == FixedAsset.Status.IN_USE and can_approve(actor, asset.business_unit)
    return row


def _assets(business_unit):
    return FixedAsset.objects.filter(business_unit=business_unit).select_related(
        "paid_from", "stock_variant", "stock_location", "submitted_by", "decided_by", "disposed_by", "disposal_account", "business_unit")


def _done_by_asset(business_unit):
    rows = AssetDepreciation.objects.filter(asset__business_unit=business_unit).values("asset_id").annotate(total=Sum("amount"))
    done = {row["asset_id"]: row["total"] for row in rows}
    for pk, opening in FixedAsset.objects.filter(business_unit=business_unit, opening_depreciation__gt=0).values_list(
            "pk", "opening_depreciation"):
        done[pk] = done.get(pk, ZERO) + opening
    return done


def _preview(business_unit, month, done):
    charges = []
    for asset in _assets(business_unit).filter(status=FixedAsset.Status.IN_USE, purchase_date__lt=_next_month(month)):
        amount = _charge(asset, month, done.get(asset.pk, ZERO))
        if amount > 0:
            charges.append((asset, amount))
    return charges


def assets_overview(*, actor, business_unit, status=""):
    _require_user(actor, business_unit)
    done = _done_by_asset(business_unit)
    assets = list(_assets(business_unit))
    shown = [asset for asset in assets if not status or asset.status == status]
    in_use = [asset for asset in assets if asset.status == FixedAsset.Status.IN_USE]
    today = timezone.localdate()
    next_month = _next_run_month(business_unit)
    due = next_month is not None and next_month < _first(today)
    preview = _preview(business_unit, next_month, done) if due else []
    last = _last_run(business_unit)
    payload = {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "rows": [_row(asset, actor, done.get(asset.pk, ZERO)) for asset in shown],
        "totals": {
            "count": len(in_use),
            "cost": _f(sum((asset.cost for asset in in_use), ZERO)),
            "depreciated": _f(sum((done.get(asset.pk, ZERO) for asset in in_use), ZERO)),
            "book_value": _f(sum((asset.cost - done.get(asset.pk, ZERO) for asset in in_use), ZERO)),
            "monthly": _f(sum(((asset.cost - asset.residual_value) / asset.life_months for asset in in_use
                               if done.get(asset.pk, ZERO) < asset.cost - asset.residual_value), ZERO).quantize(CENT)),
            "waiting": sum(1 for asset in assets if asset.status == FixedAsset.Status.SUBMITTED),
        },
        "last_run": None if last is None else {"month": last.month.isoformat()[:7], "total": _f(last.total),
                                                 "by": _person(last.run_by), "journal_entry_id": last.journal_entry_id},
        "next_run": None if not due else {
            "month": next_month.isoformat()[:7], "total": _f(sum((amount for _, amount in preview), ZERO)),
            "assets": len(preview), "unbilled": _unbilled(business_unit, next_month)[:10],
        },
        "runs": [{"month": run.month.isoformat()[:7], "total": _f(run.total), "by": _person(run.run_by),
                  "journal_entry_id": run.journal_entry_id}
                 for run in DepreciationRun.objects.filter(business_unit=business_unit).select_related("run_by")[:12]],
        "categories": [{"value": value, "label": label, "usual_life_months": USUAL_LIFE_MONTHS[value]}
                       for value, label in FixedAsset.Category.choices],
        "can_manage": can_manage(actor, business_unit), "can_approve": can_approve(actor, business_unit),
        "policy_label": POLICY_LABEL,
    }
    if payload["can_manage"] or payload["can_approve"]:
        payload["accounts"] = [account_row(account) for account in CashAccount.objects.filter(business_unit=business_unit)]
        payload["under_construction"] = _f(_construction_available(business_unit))
        payload["stock_options"] = _stock_options(business_unit)
        payload["sources"] = [{"value": value, "label": label} for value, label in FixedAsset.Source.choices
                              if value != FixedAsset.Source.OPENING]
    return payload


def asset_detail(*, actor, business_unit, asset_id):
    """One asset with its month-by-month depreciation: posted so far, then what is still to come."""
    _require_user(actor, business_unit)
    asset = _load(business_unit, asset_id, lock=False)
    posted = list(asset.depreciation.select_related("run").order_by("run__month"))
    schedule = [{"month": charge.run.month.isoformat()[:7], "amount": _f(charge.amount), "posted": True} for charge in posted]
    if asset.status == FixedAsset.Status.IN_USE:
        done = asset.opening_depreciation + sum((charge.amount for charge in posted), ZERO)
        start = _first(asset.depreciation_from or asset.purchase_date)
        month = max(_next_run_month(business_unit) or start, start)
        while done < asset.cost - asset.residual_value and len(schedule) < 600:
            amount = _target(asset, month) - done
            if amount > 0:
                schedule.append({"month": month.isoformat()[:7], "amount": _f(amount), "posted": False})
                done += amount
            month = _next_month(month)
    return {**_row(asset, actor), "schedule": schedule}


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _next_number(business_unit):
    prefix = f"FA-{business_unit.code}-"
    last = FixedAsset.objects.select_for_update().filter(number__startswith=prefix).order_by("-number").first()
    following = int(last.number.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{following:05d}"


def _ledger_balance(business_unit, code):
    lines = JournalLine.objects.filter(entry__business_unit=business_unit, entry__status=JournalEntry.Status.POSTED,
                                       account__code=code).aggregate(debit=Sum("debit"), credit=Sum("credit"))
    return (lines["debit"] or ZERO) - (lines["credit"] or ZERO)


def _construction_available(business_unit, excluding=None):
    """What is on assets under construction and not already claimed by an asset waiting for approval."""
    claimed = FixedAsset.objects.filter(business_unit=business_unit, source=FixedAsset.Source.CONSTRUCTION,
                                        status=FixedAsset.Status.SUBMITTED)
    if excluding is not None:
        claimed = claimed.exclude(pk=excluding)
    return _ledger_balance(business_unit, "TEST_ASSETS_UNDER_CONSTRUCTION") - (claimed.aggregate(total=Sum("cost"))["total"] or ZERO)


def _free(balance):
    return balance.on_hand_quantity - balance.reserved_quantity - held_quantity(balance)


def _stock_choice(business_unit, data, *, lock=False):
    variant = Variant.objects.filter(pk=_int(data.get("variant_id"))).first()
    location = Location.objects.filter(pk=_int(data.get("location_id"))).first()
    if variant is None or location is None:
        raise InvalidTransaction("Choose the item and the location it is taken from.")
    quantity = _quantity(data.get("quantity"), "quantity")
    rows = StockBalance.objects.filter(business_unit=business_unit, variant=variant, location=location)
    balance = (rows.select_for_update() if lock else rows).select_related("variant", "location").first()
    if balance is None or _free(balance) < quantity:
        free = _free(balance) if balance else ZERO
        raise InvalidTransaction(f"Only {free} of {variant.sku} is free at {location.code}; the rest is reserved or held.")
    return balance, quantity


def _stock_value(balance, quantity):
    if balance.on_hand_quantity <= 0:
        raise InvalidTransaction("That stock has no value to move.")
    if quantity == balance.on_hand_quantity:
        return balance.on_hand_value
    return (balance.on_hand_value * quantity / balance.on_hand_quantity).quantize(CENT, rounding=ROUND_HALF_UP)


def _stock_options(business_unit):
    rows = (StockBalance.objects.filter(business_unit=business_unit, on_hand_quantity__gt=0)
            .select_related("variant", "location").order_by("variant__sku", "location__code")[:200])
    options = []
    for balance in rows:
        free = _free(balance)
        if free > 0:
            options.append({"variant_id": balance.variant_id, "location_id": balance.location_id, "sku": balance.variant.sku,
                            "location": balance.location.code, "free": _f(free),
                            "unit_value": _f((balance.on_hand_value / balance.on_hand_quantity).quantize(CENT))})
    return options


def _unbilled(business_unit, month):
    """Deliveries received up to the end of month whose goods still wait for the supplier's bill."""
    from .supplier_payments import _payable

    billed = SupplierBill.objects.filter(business_unit=business_unit, status__in=SupplierBill.ACTIVE).values_list("receipt_id", flat=True)
    end = timezone.make_aware(timezone.datetime.combine(_next_month(month), timezone.datetime.min.time()))
    receipts = (PurchaseReceipt.objects.filter(business_unit=business_unit, is_synthetic=True, created_at__lt=end)
                .exclude(pk__in=billed).select_related("supplier").order_by("created_at", "pk"))
    return [{"receipt_id": receipt.pk, "supplier": receipt.supplier.name,
             "received_on": timezone.localtime(receipt.created_at).date().isoformat()}
            for receipt in receipts if _payable(receipt).payable > 0]


@transaction.atomic
def submit_asset(*, actor, business_unit, data):
    """Register a purchase for approval. Nothing is booked yet."""
    _require_user(actor, business_unit)
    if not can_manage(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'register fixed assets' permission for this unit.")
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the asset's details.")
    name = _text(data.get("name"), "asset name", 120, required=True)
    category = data.get("category")
    if category not in FixedAsset.Category.values:
        raise InvalidTransaction("Choose a category.")
    day = _past_day(data.get("purchase_date"), "purchase date")
    source = data.get("source") or FixedAsset.Source.PURCHASE
    if source not in FixedAsset.Source.values or source == FixedAsset.Source.OPENING:
        raise InvalidTransaction("Choose how the asset was added: bought, finished construction or taken from stock.")
    details = {"source": source}
    if source == FixedAsset.Source.STOCK:
        balance, quantity = _stock_choice(business_unit, data)
        cost = _stock_value(balance, quantity)
    else:
        cost = _money(data.get("cost"), "cost")
    if cost <= 0 or cost > MAX_COST:
        raise InvalidTransaction("The cost must be more than zero and at most QAR 100,000,000.")
    residual = _money(data.get("residual_value") or "0", "value at the end")
    if residual < 0 or residual >= cost:
        raise InvalidTransaction("The value at the end must be zero or more and less than the cost.")
    life = _int(data.get("life_months")) if data.get("life_months") not in (None, "") else USUAL_LIFE_MONTHS[category]
    if life is None or not 1 <= life <= 600:
        raise InvalidTransaction("The useful life must be between 1 and 600 months.")
    if source == FixedAsset.Source.PURCHASE:
        account = _cash_account(business_unit, _int(data.get("account_id")), "account it was paid from")
        if day < account.opening_date:
            raise InvalidTransaction(f"{account.name} opened on {account.opening_date.isoformat()}, after this date.")
        details["paid_from"] = account
    elif source == FixedAsset.Source.CONSTRUCTION:
        available = _construction_available(business_unit)
        if cost > available:
            raise InvalidTransaction(f"Only {available:,.2f} QAR is waiting on assets under construction. "
                                     "Move the building costs there with a journal entry first.")
    else:
        details.update(stock_variant=balance.variant, stock_location=balance.location, stock_quantity=quantity)
    asset = FixedAsset.objects.create(
        business_unit=business_unit, number=_next_number(business_unit), name=name, category=category,
        supplier=_text(data.get("supplier"), "supplier", 120), purchase_date=day, cost=cost, residual_value=residual,
        life_months=life, submitted_by=actor, **details,
    )
    return _row(_load(business_unit, asset.pk, lock=False), actor)


def _load(business_unit, asset_id, *, lock=True):
    rows = _assets(business_unit).filter(pk=asset_id)
    if lock:
        rows = rows.select_for_update(of=("self",))
    asset = rows.first()
    if asset is None:
        raise InvalidTransaction("That asset was not found.")
    return asset


def _require_approver(actor, asset):
    _require_user(actor, asset.business_unit)
    if not can_approve(actor, asset.business_unit):
        raise WorkflowPermissionDenied("You need the 'approve fixed assets' permission for this unit.")


@transaction.atomic
def approve_asset(*, actor, business_unit, asset_id):
    asset = _load(business_unit, asset_id)
    _require_approver(actor, asset)
    if asset.status != FixedAsset.Status.SUBMITTED:
        raise InvalidTransaction(f"{asset.number} is already {asset.get_status_display().lower()}.")
    if actor.pk == asset.submitted_by_id:
        raise WorkflowPermissionDenied("Someone other than the person who registered the asset must approve it.")
    require_within_limit(actor, business_unit, APPROVE, asset.cost, "this asset")
    if asset.source == FixedAsset.Source.PURCHASE and _reconciled(asset.paid_from, asset.purchase_date):
        raise InvalidTransaction(f"{asset.purchase_date:%B %Y} is reconciled and approved for {asset.paid_from.name}.")
    operation, _ = _begin_idempotent_operation(f"fixed-asset-{asset.pk}-approve", "fixed_asset_purchase", {"asset": asset.pk})
    memo = f"{asset.number} {asset.name}"[:240]
    if asset.source == FixedAsset.Source.PURCHASE:
        credit, verb = "TEST_CASH", "bought"
    elif asset.source == FixedAsset.Source.CONSTRUCTION:
        credit, verb = "TEST_ASSETS_UNDER_CONSTRUCTION", "finished building"
        available = _construction_available(business_unit, excluding=asset.pk)
        if asset.cost > available:
            raise InvalidTransaction(f"Only {available:,.2f} QAR is on assets under construction now, less than {asset.number}'s cost.")
    else:
        credit, verb = "TEST_INVENTORY", "took from stock"
        balance, quantity = _stock_choice(business_unit, {"variant_id": asset.stock_variant_id,
                                                          "location_id": asset.stock_location_id,
                                                          "quantity": asset.stock_quantity}, lock=True)
        value = _stock_value(balance, quantity)
        if asset.residual_value >= value:
            raise InvalidTransaction("The stock is now worth less than the value at the end; register it again.")
        StockMovement.objects.create(balance=balance, idempotency=operation, kind=StockMovement.Kind.CAPITALIZED,
                                     quantity_delta=-quantity, value_delta=-value, memo=f"TEST ONLY: into fixed asset {asset.number}")
        balance.on_hand_quantity -= quantity
        balance.on_hand_value -= value
        balance.updated_at = timezone.now()
        balance.save(update_fields=["on_hand_quantity", "on_hand_value", "updated_at"])
        asset.cost = value
    entry = _post_journal(
        legal_entity=business_unit.legal_entity, business_unit=business_unit, idempotency=operation,
        kind=JournalEntry.Kind.FIXED_ASSET, description=f"TEST ONLY: {verb} {asset.number} {asset.name}"[:255],
        lines=[("TEST_FIXED_ASSETS", asset.cost, ZERO, memo), (credit, ZERO, asset.cost, memo)],
        document_date=asset.purchase_date,
    )
    if asset.source == FixedAsset.Source.PURCHASE:
        CashLineAssignment.objects.create(journal_line=entry.lines.get(credit__gt=0), account=asset.paid_from, assigned_by=actor)
    asset.status, asset.decided_by, asset.decided_at, asset.journal_entry = FixedAsset.Status.IN_USE, actor, timezone.now(), entry
    asset.save(update_fields=["status", "decided_by", "decided_at", "journal_entry", "cost"])
    _complete(operation, {"asset": asset.pk, "journal_entry_id": entry.pk})
    return _row(asset, actor)


@transaction.atomic
def reject_asset(*, actor, business_unit, asset_id, reason):
    asset = _load(business_unit, asset_id)
    _require_approver(actor, asset)
    if asset.status != FixedAsset.Status.SUBMITTED:
        raise InvalidTransaction(f"{asset.number} is already {asset.get_status_display().lower()}.")
    if actor.pk == asset.submitted_by_id:
        raise WorkflowPermissionDenied("Someone other than the person who registered the asset must decide on it.")
    asset.rejection_reason = _text(reason, "reason", 200, required=True)
    asset.status, asset.decided_by, asset.decided_at = FixedAsset.Status.REJECTED, actor, timezone.now()
    asset.save(update_fields=["status", "decided_by", "decided_at", "rejection_reason"])
    return _row(asset, actor)


@transaction.atomic
def run_depreciation(*, actor, business_unit, month):
    """Post one month's depreciation for every asset in use, in month order."""
    _require_user(actor, business_unit)
    if not can_manage(actor, business_unit):
        raise WorkflowPermissionDenied("You need the 'register fixed assets and run depreciation' permission for this unit.")
    # Serialise runs for the unit: lock its assets.
    list(FixedAsset.objects.select_for_update().filter(business_unit=business_unit).values_list("pk", flat=True))
    expected = _next_run_month(business_unit)
    if expected is None:
        raise InvalidTransaction("There are no approved fixed assets to depreciate.")
    try:
        chosen = _first(timezone.datetime.strptime(str(month or "")[:7], "%Y-%m").date())
    except ValueError:
        raise InvalidTransaction("Choose the month to depreciate.") from None
    if chosen != expected:
        raise InvalidTransaction(f"Depreciation runs one month at a time; the next month to run is {expected:%B %Y}.")
    if chosen >= _first(timezone.localdate()):
        raise InvalidTransaction(f"{chosen:%B %Y} has not ended yet. Depreciation is run once the month is over.")
    unbilled = _unbilled(business_unit, chosen)
    if unbilled:
        shown = ", ".join(f"receipt {row['receipt_id']} from {row['supplier']}" for row in unbilled[:5])
        raise InvalidTransaction(f"Record the supplier bills for every delivery up to the end of {chosen:%B %Y} first. "
                                 f"Still waiting: {shown}{' and more' if len(unbilled) > 5 else ''}.")
    charges = _preview(business_unit, chosen, _done_by_asset(business_unit))
    total = sum((amount for _, amount in charges), ZERO)
    last_day = _next_month(chosen) - timezone.timedelta(days=1)
    run = DepreciationRun(business_unit=business_unit, month=chosen, total=total, run_by=actor)
    if total > 0:
        operation, _ = _begin_idempotent_operation(f"depreciation-{business_unit.pk}-{chosen:%Y-%m}", "depreciation_run",
                                                   {"unit": business_unit.pk, "month": chosen.isoformat()})
        memo = f"Depreciation {chosen:%B %Y}"
        run.journal_entry = _post_journal(
            legal_entity=business_unit.legal_entity, business_unit=business_unit, idempotency=operation,
            kind=JournalEntry.Kind.DEPRECIATION,
            description=f"TEST ONLY: depreciation for {chosen:%B %Y}, {len(charges)} asset{'s' if len(charges) != 1 else ''}",
            lines=[("TEST_DEPRECIATION", total, ZERO, memo), ("TEST_ACCUM_DEPRECIATION", ZERO, total, memo)],
            document_date=last_day,
        )
        _complete(operation, {"month": chosen.isoformat(), "journal_entry_id": run.journal_entry.pk})
    run.save()
    AssetDepreciation.objects.bulk_create([AssetDepreciation(run=run, asset=asset, amount=amount) for asset, amount in charges])
    return assets_overview(actor=actor, business_unit=business_unit)


@transaction.atomic
def dispose_asset(*, actor, business_unit, asset_id, date, proceeds="0", account_id=None, reason=""):
    """Sell or write off an asset in use; any money received goes into a bank account or cash box."""
    asset = _load(business_unit, asset_id)
    _require_approver(actor, asset)
    if asset.status != FixedAsset.Status.IN_USE:
        raise InvalidTransaction(f"Only an asset in use can be sold or written off; {asset.number} is {asset.get_status_display().lower()}.")
    day = _past_day(date, "date")
    if day < asset.purchase_date:
        raise InvalidTransaction("The date cannot be before the asset was bought.")
    reason = _text(reason, "reason", 200, required=True)
    money = _money(proceeds or "0", "amount received")
    if money < 0 or money > MAX_COST:
        raise InvalidTransaction("The amount received must be zero or more.")
    account = None
    if money > 0:
        account = _cash_account(business_unit, _int(account_id), "account the money went into")
        if day < account.opening_date:
            raise InvalidTransaction(f"{account.name} opened on {account.opening_date.isoformat()}, after this date.")
        if _reconciled(account, day):
            raise InvalidTransaction(f"{day:%B %Y} is reconciled and approved for {account.name}. Use a later date.")
    month_before = _first(day) - timezone.timedelta(days=1)
    last = _last_run(business_unit)
    if _first(asset.purchase_date) < _first(day) and (last is None or last.month < _first(month_before)):
        raise InvalidTransaction(f"Run depreciation up to {month_before:%B %Y} first, so the value in the books is current.")
    done = _depreciated(asset)
    book_value = asset.cost - done
    difference = money - book_value
    memo = f"{asset.number} {asset.name}"[:240]
    lines = [("TEST_FIXED_ASSETS", ZERO, asset.cost, memo)]
    if done:
        lines.append(("TEST_ACCUM_DEPRECIATION", done, ZERO, memo))
    if money:
        lines.append(("TEST_CASH", money, ZERO, memo))
    if difference > 0:
        lines.append(("TEST_ASSET_DISPOSAL_GAIN", ZERO, difference, memo))
    elif difference < 0:
        lines.append(("TEST_ASSET_DISPOSAL_LOSS", -difference, ZERO, memo))
    operation, _ = _begin_idempotent_operation(f"fixed-asset-{asset.pk}-dispose", "fixed_asset_disposal", {"asset": asset.pk})
    entry = _post_journal(
        legal_entity=business_unit.legal_entity, business_unit=business_unit, idempotency=operation,
        kind=JournalEntry.Kind.FIXED_ASSET,
        description=f"TEST ONLY: {'sold' if money else 'wrote off'} {asset.number} {asset.name}"[:255],
        lines=lines, document_date=day,
    )
    if account:
        CashLineAssignment.objects.create(journal_line=entry.lines.get(account__code="TEST_CASH"), account=account,
                                          assigned_by=actor)
    asset.status, asset.disposal_date, asset.disposal_proceeds = FixedAsset.Status.DISPOSED, day, money
    asset.disposal_account, asset.disposal_reason, asset.disposed_by, asset.disposal_entry = account, reason, actor, entry
    asset.save(update_fields=["status", "disposal_date", "disposal_proceeds", "disposal_account", "disposal_reason",
                              "disposed_by", "disposal_entry"])
    _complete(operation, {"asset": asset.pk, "journal_entry_id": entry.pk})
    return _row(asset, actor)
