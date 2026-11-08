"""Cheques: post-dated cheques received from customers and written to suppliers (synthetic only).

Provisional rules, waiting for Finance's approval:

- A cheque received is for one customer order, with the day we received it and the date written
  on it. The bank account it goes into is chosen when the bank clears it, not when it is received
  (from Bilal's review, 3 Nov 2026). It is kept off the books while it is held
  and while it is at the bank. It cannot be deposited before the date on it.
- When the bank clears it, it is recorded as the customer's payment on that order (the same
  way as any other payment, so deposits, balances and the receivables follow) and the money
  lands in the bank account it was deposited to.
- A bounced cheque changes nothing in the books: the customer still owes the money. It stays
  on the list as bounced until a replacement cheque is recorded for it. A cheque can also be
  handed back to the customer, with a reason.
- The cheques held for an order cannot add up to more than what is still unpaid on it.
- A cheque written to a supplier or other payee is recorded against one of our bank accounts,
  with its number (unique per account) and date. The payment itself is booked through
  supplier payments, so marking a written cheque as cashed or cancelled does not post anything.
- Seeing cheques needs the same permission as the Bank and cash screen; recording and
  updating them needs "Record bank and cash".
- QAR only.
"""

import datetime
import re
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .cash_accounts import _account, _require_manager, _require_user, _text, can_manage
from .general_ledger import _day
from .models import (
    CashAccount, CashLineAssignment, Cheque, JournalEntry, QuotePayment, SalesQuote, SupplierPayment,
)
from .purchasing_services import take_quote_payment
from .transaction_services import InvalidTransaction, _money

ZERO = Decimal("0.00")
S = Cheque.Status
D = Cheque.Direction
LIVE_RECEIVED = (S.HELD, S.DEPOSITED)
PAID_KINDS = (QuotePayment.Kind.DEPOSIT, QuotePayment.Kind.BALANCE)
NUMBER = re.compile(r"^[0-9]{4,12}$")
WEEKS_AHEAD = 8
POLICY_LABEL = ("TEST ONLY provisional rules: a cheque received stays off the books until the bank clears it, then it is "
                "the customer's payment on the order; a bounced cheque leaves the customer owing; cheques written are "
                "booked through supplier payments. QAR only.")


def _fmt(value):
    return format((value or ZERO).quantize(Decimal("0.01")), "f")


def _name(user):
    return (user.get_full_name() or user.username) if user else ""


def _unpaid(quote):
    paid = sum((payment.amount for payment in quote.payments.all() if payment.kind in PAID_KINDS), ZERO)
    return quote.total_quote_value - paid


def _held(quote, exclude=None):
    rows = quote.cheques.filter(status__in=LIVE_RECEIVED)
    if exclude is not None:
        rows = rows.exclude(pk=exclude.pk)
    return sum((row.amount for row in rows), ZERO)


def _order_label(quote):
    invoice = getattr(quote, "invoice", None)
    return f"Order {quote.pk}" + (f" · invoice {invoice.number}" if invoice is not None else "")


def _row(cheque, today, manager):
    received = cheque.direction == D.RECEIVED
    actions = []
    if manager:
        if cheque.status == S.HELD:
            actions = (["deposit"] if cheque.cheque_date <= today else []) + ["return"]
        elif cheque.status == S.DEPOSITED:
            actions = ["clear", "bounce"]
        elif cheque.status in (S.BOUNCED, S.RETURNED) and not hasattr(cheque, "replaced_by"):
            actions = ["replace"]
        elif cheque.status == S.ISSUED:
            actions = ["clear", "cancel"]
    replaced_by = getattr(cheque, "replaced_by", None)
    return {
        "id": cheque.pk, "direction": cheque.direction, "number": cheque.number, "drawn_on": cheque.drawn_on,
        "account": {"id": cheque.account_id, "name": cheque.account.name} if cheque.account_id else None,
        "received_on": cheque.received_on.isoformat() if cheque.received_on else None,
        "party": cheque.customer.name if received else cheque.payee,
        "customer_id": cheque.customer_id, "order": ({"id": cheque.quote_id, "label": _order_label(cheque.quote)} if received else None),
        "supplier_payment_id": cheque.supplier_payment_id,
        "amount": _fmt(cheque.amount), "cheque_date": cheque.cheque_date.isoformat(),
        "days": (cheque.cheque_date - today).days, "status": cheque.status, "status_label": cheque.get_status_display(),
        "note": cheque.note, "reason": cheque.reason,
        "deposited_on": cheque.deposited_on.isoformat() if cheque.deposited_on else None,
        "finished_on": cheque.finished_on.isoformat() if cheque.finished_on else None,
        "replaces": cheque.replaces.number if cheque.replaces_id else None,
        "replaced_by": replaced_by.number if replaced_by else None,
        "journal_entry_id": _journal_id(cheque.payment) if cheque.payment_id else None,
        "recorded_by": _name(cheque.recorded_by), "updated_by": _name(cheque.updated_by), "actions": actions,
    }


def _journal_id(payment):
    entry = JournalEntry.objects.filter(idempotency=payment.idempotency).only("pk").first()
    return entry.pk if entry else None


def _cheques(business_unit):
    return (Cheque.objects.filter(business_unit=business_unit, is_synthetic=True)
            .select_related("account", "customer", "quote__invoice", "replaces", "replaced_by", "recorded_by", "updated_by",
                            "payment__idempotency"))


def _open_orders(business_unit):
    quotes = (SalesQuote.objects.filter(business_unit=business_unit)
              .exclude(status__in=(SalesQuote.Status.PENDING_APPROVAL, SalesQuote.Status.CANCELLED))
              .select_related("customer", "invoice").prefetch_related("payments", "cheques").order_by("customer__name", "pk"))
    rows = []
    for quote in quotes:
        unpaid = _unpaid(quote)
        if unpaid <= 0:
            continue
        rows.append({"id": quote.pk, "customer_id": quote.customer_id, "customer": quote.customer.name, "label": _order_label(quote),
                     "unpaid": _fmt(unpaid), "held": _fmt(_held(quote)), "open": _fmt(unpaid - _held(quote))})
    return rows


def cheques_overview(*, actor, business_unit):
    _require_user(actor, business_unit)
    today = timezone.localdate()
    manager = can_manage(actor, business_unit)
    cheques = list(_cheques(business_unit))
    received = [cheque for cheque in cheques if cheque.direction == D.RECEIVED]
    issued = [cheque for cheque in cheques if cheque.direction == D.ISSUED]
    week_end = today + datetime.timedelta(days=6)

    def total(rows):
        return _fmt(sum((row.amount for row in rows), ZERO))

    held = [row for row in received if row.status == S.HELD]
    deposited = [row for row in received if row.status == S.DEPOSITED]
    bounced = [row for row in received if row.status == S.BOUNCED and not hasattr(row, "replaced_by")]
    to_cash = [row for row in issued if row.status == S.ISSUED]
    weeks = []
    for index in range(WEEKS_AHEAD):
        start = today + datetime.timedelta(weeks=index)
        stop = start + datetime.timedelta(days=6)
        coming = [row for row in held + deposited if start <= max(row.cheque_date, today) <= stop]
        going = [row for row in to_cash if start <= max(row.cheque_date, today) <= stop]
        weeks.append({"start": start.isoformat(), "end": stop.isoformat(), "in": total(coming), "out": total(going),
                      "in_count": len(coming), "out_count": len(going)})
    finished = sorted((row for row in cheques if row.status not in (S.HELD, S.DEPOSITED, S.ISSUED) and row not in bounced),
                      key=lambda row: (row.finished_on or row.cheque_date, row.pk), reverse=True)[:50]
    accounts = CashAccount.objects.filter(business_unit=business_unit, kind=CashAccount.Kind.BANK, is_synthetic=True).order_by("name")
    return {
        "unit": {"id": business_unit.pk, "name": business_unit.name}, "today": today.isoformat(),
        "tiles": {
            "held": {"count": len(held), "total": total(held)},
            "due_this_week": {"count": sum(1 for row in held if row.cheque_date <= week_end),
                              "total": total([row for row in held if row.cheque_date <= week_end])},
            "deposited": {"count": len(deposited), "total": total(deposited)},
            "bounced": {"count": len(bounced), "total": total(bounced)},
            "to_cash": {"count": len(to_cash), "total": total(to_cash)},
        },
        "received": [_row(row, today, manager) for row in held + deposited + bounced],
        "issued": [_row(row, today, manager) for row in to_cash],
        "finished": [_row(row, today, manager) for row in finished],
        "weeks": weeks,
        "accounts": [{"id": account.pk, "name": account.name, "bank_name": account.bank_name} for account in accounts],
        "orders": _open_orders(business_unit) if manager else [],
        "supplier_payments": ([{"id": payment.pk, "label": f"Payment {payment.pk} to {payment.payee_name or payment.supplier.name}",
                                "amount": _fmt(payment.paid_value), "paid_on": payment.paid_on.isoformat(),
                                "status_label": payment.get_status_display()}
                               for payment in SupplierPayment.objects.filter(business_unit=business_unit)
                               .exclude(status=SupplierPayment.Status.REJECTED).select_related("supplier").order_by("-pk")[:50]]
                              if manager else []),
        "can_manage": manager, "policy": POLICY_LABEL,
    }


def _number(value):
    number = "".join(str(value or "").split())
    if not NUMBER.match(number):
        raise InvalidTransaction("Give the cheque number as 4 to 12 digits.")
    return number


@transaction.atomic
def record_cheque(*, actor, business_unit, data):
    _require_manager(actor, business_unit)
    if not isinstance(data, dict):
        raise InvalidTransaction("Send the cheque details.")
    direction = data.get("direction")
    if direction not in D.values:
        raise InvalidTransaction("Say whether the cheque was received or written.")
    number = _number(data.get("number"))
    amount = _money(data.get("amount"), "amount")
    if amount <= 0:
        raise InvalidTransaction("The amount must be more than zero.")
    cheque_date = _day(data.get("cheque_date"), None, "cheque date") if data.get("cheque_date") else None
    if cheque_date is None:
        raise InvalidTransaction("Give the date written on the cheque.")
    today = timezone.localdate()
    if cheque_date > today + datetime.timedelta(days=366):
        raise InvalidTransaction("A cheque can be dated at most a year ahead.")
    values = {"business_unit": business_unit, "direction": direction, "number": number, "amount": amount,
              "cheque_date": cheque_date, "note": _text(data.get("note"), "note", 200), "recorded_by": actor}
    if direction == D.RECEIVED:
        received_on = _day(data.get("received_on"), today, "date received")
        if received_on > today:
            raise InvalidTransaction("The date received cannot be in the future.")
        if received_on < today - datetime.timedelta(days=366):
            raise InvalidTransaction("The date received can be at most a year ago.")
        values["received_on"] = received_on
        if cheque_date < today - datetime.timedelta(days=180):
            raise InvalidTransaction("That cheque is more than 6 months old. Banks do not accept it.")
        quote = (SalesQuote.objects.select_for_update().filter(pk=data.get("order_id") if type(data.get("order_id")) is int else None,
                                                             business_unit=business_unit).select_related("customer").first())
        if quote is None:
            raise InvalidTransaction("Choose the customer order the cheque pays for.")
        if quote.status in (SalesQuote.Status.PENDING_APPROVAL, SalesQuote.Status.CANCELLED):
            raise InvalidTransaction("That order is not approved or has been cancelled.")
        replaces = None
        if data.get("replaces_id") not in (None, ""):
            replaces = (Cheque.objects.select_for_update().filter(pk=data.get("replaces_id") if type(data.get("replaces_id")) is int else None,
                                                                  business_unit=business_unit, direction=D.RECEIVED).first())
            if replaces is None or replaces.status not in (S.BOUNCED, S.RETURNED) or hasattr(replaces, "replaced_by"):
                raise InvalidTransaction("Only a bounced or returned cheque that has not been replaced yet can be replaced.")
            if replaces.quote_id != quote.pk:
                raise InvalidTransaction("A replacement cheque must be for the same order.")
        drawn_on = _text(data.get("drawn_on"), "bank the cheque is drawn on", 80, required=True)
        room = _unpaid(quote) - _held(quote)
        if amount > room:
            raise InvalidTransaction(f"Only QAR {_fmt(max(room, ZERO))} of {_order_label(quote)} is not paid or covered by "
                                     "another cheque yet.")
        if Cheque.objects.filter(direction=D.RECEIVED, customer=quote.customer, drawn_on__iexact=drawn_on, number=number,
                                 status__in=LIVE_RECEIVED + (S.CLEARED,)).exists():
            raise InvalidTransaction(f"Cheque {number} from {drawn_on} is already recorded for this customer.")
        values.update(customer=quote.customer, quote=quote, drawn_on=drawn_on, status=S.HELD, replaces=replaces)
    else:
        account = _bank(business_unit, data.get("account_id"))
        values["account"] = account
        payee = _text(data.get("payee"), "payee", 120, required=True)
        payment = None
        if data.get("supplier_payment_id") not in (None, ""):
            payment = SupplierPayment.objects.filter(pk=data.get("supplier_payment_id") if type(data.get("supplier_payment_id")) is int else None,
                                                     business_unit=business_unit).first()
            if payment is None or payment.status == SupplierPayment.Status.REJECTED:
                raise InvalidTransaction("Choose a supplier payment of this unit that was not rejected.")
        if Cheque.objects.filter(direction=D.ISSUED, account=account, number=number).exists():
            raise InvalidTransaction(f"Cheque {number} on {account.name} is already recorded.")
        values.update(payee=payee, supplier_payment=payment, drawn_on=account.bank_name, status=S.ISSUED)
    cheque = Cheque.objects.create(**values)
    return _row(_cheques(business_unit).get(pk=cheque.pk), today, True)


def _bank(business_unit, account_id):
    account = _account(business_unit, account_id, "bank account")
    if account.kind != CashAccount.Kind.BANK:
        raise InvalidTransaction("Cheques go through a bank account, not a cash box.")
    return account


def _assign(payment, account, actor):
    """Put the cleared money on the bank account the cheque was deposited to."""
    entry = JournalEntry.objects.filter(idempotency=payment.idempotency).first()
    line = entry.lines.filter(account__code="TEST_CASH").first() if entry else None
    if line is not None and line.entry.document_date >= account.opening_date:
        CashLineAssignment.objects.update_or_create(journal_line=line, defaults={"account": account, "assigned_by": actor})


@transaction.atomic
def update_cheque(*, actor, business_unit, cheque_id, action, reason="", account_id=None):
    _require_manager(actor, business_unit)
    cheque = (Cheque.objects.select_for_update(of=("self",)).filter(pk=cheque_id if type(cheque_id) is int else None,
                                                                    business_unit=business_unit).select_related("account", "quote").first())
    if cheque is None:
        raise InvalidTransaction("Cheque not found in this business unit.")
    today = timezone.localdate()
    reason = _text(reason, "reason", 200)
    allowed = {
        (D.RECEIVED, S.HELD): ("deposit", "return"), (D.RECEIVED, S.DEPOSITED): ("clear", "bounce"),
        (D.ISSUED, S.ISSUED): ("clear", "cancel"),
    }.get((cheque.direction, cheque.status), ())
    if action not in allowed:
        raise InvalidTransaction(f"Cheque {cheque.number} is {cheque.get_status_display().lower()}; that cannot be done now.")
    if action in ("bounce", "return", "cancel") and not reason:
        raise InvalidTransaction("Give the reason.")
    if action == "deposit":
        if cheque.cheque_date > today:
            raise InvalidTransaction(f"Cheque {cheque.number} is dated {cheque.cheque_date:%d %b %Y}. It can be deposited from that day.")
        cheque.status, cheque.deposited_on = S.DEPOSITED, today
    elif action == "clear" and cheque.direction == D.RECEIVED:
        if account_id in (None, ""):
            raise InvalidTransaction("Choose the bank account the money went into.")
        cheque.account = _bank(business_unit, account_id)
        result = take_quote_payment(actor=actor, quote=cheque.quote, idempotency_key=f"cheque-{cheque.pk}-cleared",
                                    amount=cheque.amount, method=QuotePayment.Method.SIMULATED_CHEQUE)
        cheque.payment = QuotePayment.objects.get(pk=result["payment_id"])
        _assign(cheque.payment, cheque.account, actor)
        cheque.status, cheque.finished_on = S.CLEARED, today
    elif action == "clear":
        cheque.status, cheque.finished_on = S.CLEARED, today
    else:
        cheque.status = {"bounce": S.BOUNCED, "return": S.RETURNED, "cancel": S.CANCELLED}[action]
        cheque.finished_on, cheque.reason = today, reason
    cheque.updated_by = actor
    cheque.save()
    return _row(_cheques(business_unit).get(pk=cheque.pk), today, True)


def forecast_flows(business_unit, today):
    """For the cash-flow forecast: cheques received by order, and the money each cheque moves on its date.

    Returns (held_by_quote, flows, payments_with_cheques). Held amounts replace the guess from the invoice
    date; a cheque written for a prepared supplier payment replaces that payment's date.
    """
    held, flows, covered = {}, [], set()
    for cheque in _cheques(business_unit).filter(status__in=LIVE_RECEIVED + (S.ISSUED,)).select_related("supplier_payment"):
        day = max(cheque.cheque_date, today)
        if cheque.direction == D.RECEIVED:
            held[cheque.quote_id] = held.get(cheque.quote_id, ZERO) + cheque.amount
            flows.append((day, "in", "cheques", f"Cheque {cheque.number} · {cheque.customer.name}", cheque.amount, False))
        elif cheque.supplier_payment is None or cheque.supplier_payment.status == SupplierPayment.Status.REQUESTED:
            # An approved payment has already left the books, so its cheque is not counted again.
            if cheque.supplier_payment_id:
                covered.add(cheque.supplier_payment_id)
            flows.append((day, "out", "cheques", f"Cheque {cheque.number} to {cheque.payee}", cheque.amount, False))
    return held, flows, covered

