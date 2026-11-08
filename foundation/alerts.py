"""Email alerts: what is waiting for someone's approval, and documents about to expire.

Provisional rules (synthetic demo only, until Tivoli confirms them):

- Approvals: everyone who can approve something waiting (and whose approval limit covers it)
  gets one email listing what is new since their last alert, with a link to each screen. The
  person who prepared it is never asked. Each thing is emailed to each person once.
- Expiring documents: supplier CRs expiring within 30 days and supplier KYC documents due
  within 14 days go to whoever opens or approves suppliers; staff documents (QID, passport,
  residence and work permits, health cards, driving licences) expiring within 60 days go to
  HR (whoever adds and changes staff records) in that business unit. Each document is emailed
  once for each expiry date.
- Approvals are checked every 15 minutes and expiring documents once a day (send_alerts command).
- Payroll and leave approvals are not emailed: HR approvals stay with the owner.
- A Master Admin or General Admin assigns, on the Alerts screen, who receives alerts, the email
  address they go to (the account's address if left empty) and which types each person receives.
  Nobody receives alerts until they are assigned. A type is only offered to someone who can act on
  it, and an alert only goes to someone who can act on that thing.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from audit.models import AuditEvent
from audit.recording import record

from .access_levels import can_manage_users
from .approval_limits import limit_for
from .models import (
    AdjustmentNote, AlertEmail, AlertPreference, AlertSent, Budget, EmployeeDocument, Expense, FixedAsset, ImportBatch,
    ManualJournal, OldBalanceSettlement, StockCount, StockTransferRequest, SupplierAdvance, SupplierKyc, SupplierPayment,
    TeamMember, UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action
CR_DAYS, KYC_DAYS, STAFF_DAYS = 30, 14, 60
POLICY_LABEL = ("TEST ONLY provisional rules: only the people assigned here receive alerts, and only the types ticked for "
                "them; approvals are checked every 15 minutes and each one is emailed once to everyone "
                "who can approve it, except the person who prepared it; supplier CRs are flagged 30 days before they expire, "
                "supplier KYC documents 14 days before they are due, staff documents 60 days before they expire; payroll and "
                "leave approvals are not emailed.")

# The alert types an admin can assign: (label, email kind, the permissions that let someone act on it).
TYPES = {
    "payments": ("Supplier payments and advances", AlertEmail.Kind.APPROVALS, (A.APPROVE_PAYMENT,)),
    "expenses": ("Expenses and petty cash", AlertEmail.Kind.APPROVALS, (A.APPROVE_EXPENSE,)),
    "assets": ("Fixed assets", AlertEmail.Kind.APPROVALS, (A.APPROVE_ASSET,)),
    "journals": ("Journals and receipts on old invoices", AlertEmail.Kind.APPROVALS, (A.APPROVE_JOURNAL,)),
    "notes": ("Credit and debit notes", AlertEmail.Kind.APPROVALS, (A.APPROVE_NOTE,)),
    "budgets": ("Budgets", AlertEmail.Kind.APPROVALS, (A.APPROVE_BUDGET,)),
    "stock": ("Stock transfers and counts", AlertEmail.Kind.APPROVALS, (A.APPROVE_TRANSFER, A.APPROVE_COUNT)),
    "suppliers": ("New suppliers", AlertEmail.Kind.APPROVALS, (A.APPROVE_SUPPLIER,)),
    "imports": ("Go-live imports", AlertEmail.Kind.APPROVALS, (A.APPROVE_IMPORT,)),
    "supplier_documents": ("Supplier CR and KYC documents expiring", AlertEmail.Kind.EXPIRING, (A.OPEN_SUPPLIER, A.APPROVE_SUPPLIER)),
    "staff_documents": ("Staff documents expiring", AlertEmail.Kind.EXPIRING, (A.MANAGE_STAFF,)),
}


def type_of(kind, action):
    """The alert type an item of this email kind and permission belongs to."""
    return next(key for key, (_, type_kind, actions) in TYPES.items() if type_kind == kind and action in actions)


@dataclass
class Item:
    key: str
    action: str
    business_unit: object  # None: any business unit
    title: str
    link: str
    prepared_by_id: int | None = None
    amount: object = None


def _show(text):
    return str(text).replace("TEST ONLY ", "").replace("TEST ONLY", "")


def _qar(value):
    return f"QAR {value:,.2f}"


# What is waiting for approval.
def waiting_items():
    items = []
    for row in SupplierPayment.objects.filter(status=SupplierPayment.Status.REQUESTED).select_related("supplier", "business_unit"):
        items.append(Item(f"payment:{row.pk}", A.APPROVE_PAYMENT, row.business_unit,
                          f"Supplier payment to {_show(row.supplier.name)}, {_qar(row.paid_value)}", "#/finance/supplier-payments",
                          row.paid_by_id, row.paid_value))
    for row in SupplierAdvance.objects.filter(status=SupplierAdvance.Status.REQUESTED).select_related("supplier", "business_unit"):
        items.append(Item(f"advance:{row.pk}", A.APPROVE_PAYMENT, row.business_unit,
                          f"Advance {row.number} to {_show(row.supplier.name)}, {_qar(row.value)}", "#/finance/advances",
                          row.prepared_by_id, row.value))
    for row in OldBalanceSettlement.objects.filter(status=OldBalanceSettlement.Status.REQUESTED).select_related(
            "balance__party", "balance__business_unit"):
        customer = row.balance.side == "customer"
        items.append(Item(f"old-balance:{row.pk}", A.APPROVE_JOURNAL if customer else A.APPROVE_PAYMENT, row.balance.business_unit,
                          f"{'Money received from' if customer else 'Payment to'} {_show(row.balance.party.name)} on old "
                          f"{'invoice' if customer else 'bill'} {row.balance.reference}, {_qar(row.paid_value)}",
                          "#/finance/old-balances", row.prepared_by_id, row.paid_value))
    for row in Expense.objects.filter(status=Expense.Status.SUBMITTED).select_related("business_unit"):
        items.append(Item(f"expense:{row.pk}", A.APPROVE_EXPENSE, row.business_unit,
                          f"Expense {row.number}, {_qar(row.amount)}", "#/finance/expenses", row.submitted_by_id, row.amount))
    for row in FixedAsset.objects.filter(status=FixedAsset.Status.SUBMITTED).select_related("business_unit"):
        items.append(Item(f"asset:{row.pk}", A.APPROVE_ASSET, row.business_unit,
                          f"Fixed asset {row.number}, {_qar(row.cost)}", "#/finance/assets", row.submitted_by_id, row.cost))
    journals = ManualJournal.objects.filter(status=ManualJournal.Status.SUBMITTED).select_related("business_unit") \
        .annotate(total=Sum("lines__debit"))
    for row in journals:
        items.append(Item(f"journal:{row.pk}", A.APPROVE_JOURNAL, row.business_unit,
                          f"Journal {row.number}, {_qar(row.total or 0)}", "#/finance/journals", row.prepared_by_id, row.total or 0))
    for row in AdjustmentNote.objects.filter(status=AdjustmentNote.Status.SUBMITTED).select_related("business_unit", "counterparty"):
        items.append(Item(f"note:{row.pk}", A.APPROVE_NOTE, row.business_unit,
                          f"{row.get_kind_display()} for {_show(row.counterparty.name)}, {_qar(row.value)}", "#/finance/notes",
                          row.prepared_by_id, row.value))
    for row in Budget.objects.filter(status=Budget.Status.SUBMITTED).select_related("business_unit"):
        items.append(Item(f"budget:{row.pk}", A.APPROVE_BUDGET, row.business_unit, f"{row.business_unit.name} budget for {row.year}",
                          "#/finance/budgets", row.prepared_by_id))
    for row in StockTransferRequest.objects.filter(status=StockTransferRequest.Status.SUBMITTED).select_related("business_unit"):
        items.append(Item(f"transfer:{row.pk}", A.APPROVE_TRANSFER, row.business_unit, f"Stock transfer request {row.pk}",
                          "#/inventory/transfers", row.requested_by_id))
    for row in StockCount.objects.filter(status=StockCount.Status.SUBMITTED).select_related("business_unit"):
        items.append(Item(f"count:{row.pk}", A.APPROVE_COUNT, row.business_unit, f"Stock count {row.pk}", "#/inventory/counts",
                          row.submitted_by_id))
    for row in SupplierKyc.objects.filter(status=SupplierKyc.Status.SUBMITTED).select_related("supplier"):
        items.append(Item(f"kyc:{row.pk}", A.APPROVE_SUPPLIER, None, f"New supplier {_show(row.supplier.name)}",
                          "#/procurement/kyc", row.opened_by_id))
    for row in ImportBatch.objects.filter(status=ImportBatch.Status.WAITING):
        items.append(Item(f"import:{row.pk}", A.APPROVE_IMPORT, None,
                          f"Go-live import: {row.get_kind_display().lower()}, {row.row_count} rows ({_show(row.file_name)})",
                          "#/governance/imports", row.prepared_by_id))
    return items


# Documents about to expire.
def expiring_items(today=None):
    today = today or timezone.localdate()
    items = []
    soon = today + datetime.timedelta(days=CR_DAYS)
    for kyc in SupplierKyc.objects.filter(status=SupplierKyc.Status.APPROVED, cr_expiry__isnull=False, cr_expiry__lte=soon) \
            .select_related("supplier"):
        when = "expired on" if kyc.cr_expiry < today else "expires on"
        for action in (A.OPEN_SUPPLIER, A.APPROVE_SUPPLIER):
            items.append(Item(f"cr:{kyc.pk}:{kyc.cr_expiry.isoformat()}", action, None,
                              f"{_show(kyc.supplier.name)}: the CR {when} {kyc.cr_expiry:%d %b %Y}", "#/procurement/kyc"))
    due = today + datetime.timedelta(days=KYC_DAYS)
    for kyc in SupplierKyc.objects.filter(documents_due__isnull=False, documents_due__lte=due).select_related("supplier"):
        when = "were due by" if kyc.documents_due < today else "are due by"
        tail = ". The supplier is frozen until they are attached." if kyc.documents_due < today else ""
        for action in (A.OPEN_SUPPLIER, A.APPROVE_SUPPLIER):
            items.append(Item(f"kyc-due:{kyc.pk}:{kyc.documents_due.isoformat()}", action, None,
                              f"{_show(kyc.supplier.name)}: the KYC documents {when} {kyc.documents_due:%d %b %Y}{tail}",
                              "#/procurement/kyc"))
    staff = today + datetime.timedelta(days=STAFF_DAYS)
    documents = EmployeeDocument.objects.filter(removed_at__isnull=True, expires_on__isnull=False, expires_on__lte=staff) \
        .select_related("employee__business_unit")
    for document in documents:
        when = "expired on" if document.expires_on < today else "expires on"
        items.append(Item(f"staff-doc:{document.pk}:{document.expires_on.isoformat()}", A.MANAGE_STAFF, document.employee.business_unit,
                          f"{document.employee.full_name}: {document.get_kind_display()} {when} {document.expires_on:%d %b %Y}",
                          "#/hr/main"))
    return items


def _holders(action, business_unit):
    """Active synthetic users with a live grant of the action in the unit (any unit when None), personally or by team."""
    now = timezone.now()
    live = UserActionGrant.objects.filter(action=action, revoked_at__isnull=True, starts_at__lte=now) \
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
    if business_unit is not None:
        live = live.filter(business_unit=business_unit)
    else:
        live = live.filter(business_unit__is_active=True)
    people = set(live.filter(user__isnull=False).values_list("user_id", flat=True))
    teams = live.filter(team__isnull=False, team__is_active=True).values("team_id")
    people |= set(TeamMember.objects.filter(team_id__in=teams).values_list("user_id", flat=True))
    return people


def _recipients(items):
    """{user_id: [items]} for everyone each item should go to."""
    found = defaultdict(list)
    cache = {}
    for item in items:
        key = (item.action, getattr(item.business_unit, "pk", None))
        if key not in cache:
            cache[key] = _holders(item.action, item.business_unit)
        for user_id in cache[key]:
            if user_id == item.prepared_by_id:
                continue
            if item.amount is not None and item.business_unit is not None:
                limit = limit_for(user_id, item.business_unit, item.action)
                if limit is not None and item.amount > limit:
                    continue
            if item.key not in {other.key for other in found[user_id]}:
                found[user_id].append(item)
    return found


def _address(user, preference):
    return (preference.email if preference is not None and preference.email else user.email) or ""


def _compose(kind, user, items):
    name = user.get_full_name() or user.username
    if kind == AlertEmail.Kind.APPROVALS:
        subject = f"Tivoli ERP: {len(items)} {'thing is' if len(items) == 1 else 'things are'} waiting for your approval"
        intro = "These are waiting for your approval:"
    else:
        subject = f"Tivoli ERP: {len(items)} {'document needs' if len(items) == 1 else 'documents need'} attention"
        intro = "These documents expire soon or have expired:"
    lines = [f"Hello {name},", "", intro, ""]
    for item in items:
        unit = f" ({item.business_unit.name})" if item.business_unit is not None else ""
        lines.append(f"- {item.title}{unit}: {settings.ERP_BASE_URL}/{item.link}")
    lines += ["", "This is a synthetic test email from the Tivoli ERP demo. An administrator chooses who gets which alerts on the Alerts screen."]
    return subject[:200], "\n".join(lines)


def _send(kind, items_by_user):
    users = {user.pk: user for user in get_user_model().objects.filter(pk__in=items_by_user, is_active=True,
                                                                         demo_marker__is_synthetic=True)}
    preferences = {row.user_id: row for row in AlertPreference.objects.filter(user_id__in=items_by_user)}
    sent = []
    for user_id, items in items_by_user.items():
        user, preference = users.get(user_id), preferences.get(user_id)
        if user is None or preference is None:
            continue
        chosen = set(preference.types)
        items = [item for item in items if type_of(kind, item.action) in chosen]
        done = set(AlertSent.objects.filter(user=user, key__in=[item.key for item in items]).values_list("key", flat=True))
        new = [item for item in items if item.key not in done]
        if not new:
            continue
        subject, body = _compose(kind, user, new)
        status, error = AlertEmail.Status.SENT, ""
        address = _address(user, preference)
        if not address:
            status = AlertEmail.Status.NO_EMAIL
        else:
            try:
                send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [address])
            except Exception as failure:  # noqa: BLE001 - a mail server problem must not stop the other alerts
                status, error = AlertEmail.Status.FAILED, str(failure)[:300]
        with transaction.atomic():
            email = AlertEmail.objects.create(kind=kind, user=user, to_address=address, subject=subject, body=body,
                                              items=[{"key": item.key, "title": item.title, "link": item.link} for item in new],
                                              status=status, error=error)
            if status != AlertEmail.Status.FAILED:
                AlertSent.objects.bulk_create([AlertSent(user=user, key=item.key, email=email) for item in new], ignore_conflicts=True)
        sent.append(email)
    return sent


def send_alerts(*, approvals=True, expiring=True, today=None):
    """Send what is new. Returns the emails recorded."""
    emails = []
    if approvals:
        emails += _send(AlertEmail.Kind.APPROVALS, _recipients(waiting_items()))
    if expiring:
        emails += _send(AlertEmail.Kind.EXPIRING, _recipients(expiring_items(today)))
    return emails


# The screen.
def _require_admin(actor):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Email alerts run only in the synthetic demo environment.")
    if not can_manage_users(actor):
        raise WorkflowPermissionDenied("Only a Master Admin or General Admin may see and change email alerts.")


def _email_row(email):
    return {"id": email.pk, "kind": email.kind, "kind_label": email.get_kind_display(), "user": email.user.get_full_name() or email.user.username,
            "to": email.to_address, "subject": email.subject, "items": email.items, "status": email.status,
            "status_label": email.get_status_display(), "error": email.error, "at": timezone.localtime(email.created_at).isoformat()}


def _can_act(person_ids_by_action, user_id, key):
    return any(user_id in person_ids_by_action[action] for action in TYPES[key][2])


def alerts_overview(*, actor):
    _require_admin(actor)
    preferences = {row.user_id: row for row in AlertPreference.objects.all()}
    holders = {action: _holders(action, None) for _, _, actions in TYPES.values() for action in actions}
    people = get_user_model().objects.filter(is_active=True, demo_marker__is_synthetic=True).order_by("first_name", "username")
    rows = []
    for person in people:
        offered = [key for key in TYPES if _can_act(holders, person.pk, key)]
        if not offered:
            continue  # nothing they could act on, so nothing to alert them about
        preference = preferences.get(person.pk)
        rows.append({
            "id": person.pk, "name": person.get_full_name() or person.username,
            "account_email": person.email, "email": preference.email if preference else "",
            "sends_to": _address(person, preference),
            "offered": offered, "types": [key for key in (preference.types if preference else []) if key in offered],
        })
    return {
        "types": [{"key": key, "label": label, "kind": kind} for key, (label, kind, _) in TYPES.items()],
        "emails": [_email_row(email) for email in AlertEmail.objects.select_related("user")[:100]],
        "people": rows,
        "waiting": len(waiting_items()), "expiring": len({item.key for item in expiring_items()}),
        "mail_configured": settings.EMAIL_BACKEND != "django.core.mail.backends.console.EmailBackend",
        "policy": POLICY_LABEL,
    }


@transaction.atomic
def set_preference(*, actor, user_id, types=None, email=None):
    """Assign the alert types a person receives, and the address they go to. Only what is given changes."""
    _require_admin(actor)
    person = get_user_model().objects.filter(pk=user_id if type(user_id) is int else None, is_active=True,
                                             demo_marker__is_synthetic=True).first()
    if person is None:
        raise InvalidTransaction("That person was not found.")
    preference = AlertPreference.objects.filter(user=person).first() or AlertPreference(user=person, updated_by=actor)
    change = {}
    if types is not None:
        if not isinstance(types, list) or any(key not in TYPES for key in types):
            raise InvalidTransaction("Choose alert types from the list.")
        holders = {action: _holders(action, None) for key in types for action in TYPES[key][2]}
        refused = [TYPES[key][0] for key in types if not _can_act(holders, person.pk, key)]
        if refused:
            raise InvalidTransaction(f"{person.get_full_name() or person.username} cannot act on: {', '.join(refused)}. "
                                     "Give them the permission first.")
        preference.types = [key for key in TYPES if key in types]
        change["types"] = preference.types
    if email is not None:
        email = str(email).strip()
        if email:
            try:
                validate_email(email)
            except ValidationError:
                raise InvalidTransaction("That email address is not valid.") from None
        preference.email = email
        change["email"] = email or "account address"
    preference.updated_by, preference.updated_at = actor, timezone.now()
    preference.save()
    record(AuditEvent.Kind.ACCOUNT_CHANGED, user=person, detail={"by": actor.get_username(), "what": "alerts", "change": change})
    return alerts_overview(actor=actor)


def send_now(*, actor):
    _require_admin(actor)
    emails = send_alerts()
    return {**alerts_overview(actor=actor), "just_sent": len(emails)}
