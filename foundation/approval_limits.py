"""Approval limits in one place: how much discount each role may give, and how much each approver may approve.

Provisional rules (synthetic demo only, until Tivoli confirms them):

- Discounts: each business unit can set the largest discount a salesperson, manager, commercial GM
  and CEO may give on their own. Until a unit sets one, the built-in defaults apply (10%, 20%, 30%,
  50%). A quote above the person's ceiling waits for someone whose ceiling covers it.
- Discount per person: for selling stock items, a person can be given their own discount, from 0%
  to 50%. It replaces their role's ceiling for stock sales only; special orders keep the role ceiling.
- Approval amounts: for each approval permission that moves money (supplier payments, expenses,
  fixed assets, manual journals, credit and debit notes, customer refunds and stock write-offs),
  a person can be given the most they may approve on their own, in QAR. With no limit set they may
  approve any amount, as today. Above their limit, someone with a higher limit (or none) approves.
- A Master Admin or General Admin sees the limits; only the Master Admin changes them (Sam, 5 Nov 2026).
  Every change is recorded.
- Payroll approvals are not limited here: HR approvals stay with the owner.
"""

from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.recording import record

from .access import active_grants, holders_filter
from .access_levels import can_manage_users, is_master_admin, require_master_admin
from .models import ApprovalLimit, BusinessUnitRoleAssignment, DiscountCeiling, PersonDiscountLimit, UserActionGrant
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action
Role = BusinessUnitRoleAssignment.Role
DEFAULT_CEILINGS = {
    Role.SALESPERSON: Decimal("10.00"),
    Role.MANAGER: Decimal("20.00"),
    Role.COMMERCIAL_GM: Decimal("30.00"),
    Role.CEO: Decimal("50.00"),
}
# No quote may go above 50%, so no person's own discount may either.
PERSON_DISCOUNT_TOP = Decimal("50.00")
# The approvals that move money, and what each one is, in plain words.
LIMITED = {
    A.APPROVE_PAYMENT: "Supplier payments, advances and payments on old supplier bills",
    A.APPROVE_EXPENSE: "Expenses and petty cash",
    A.APPROVE_ASSET: "Fixed asset purchases",
    A.APPROVE_JOURNAL: "Manual journal entries and receipts on old customer invoices",
    A.APPROVE_NOTE: "Credit and debit notes",
    A.APPROVE_REFUND: "Customer refunds",
    A.APPROVE_WRITE_OFF: "Stock write-offs and count adjustments",
}
POLICY_LABEL = ("TEST ONLY provisional rules: until a unit sets its own, the discount ceilings are 10% (salesperson), 20% (manager), "
                "30% (commercial GM) and 50% (CEO); a person's own discount, where set, replaces their role's for stock sales only; with no approval limit a person may approve any amount; above their limit "
                "someone with a higher limit approves; payroll is not limited here.")


def discount_ceilings(business_unit):
    """{role: percent} for the unit: its own where set, the default otherwise."""
    ceilings = dict(DEFAULT_CEILINGS)
    for row in DiscountCeiling.objects.filter(business_unit=business_unit):
        ceilings[row.role] = row.percent
    return ceilings


def person_discount(user, business_unit):
    """The person's own discount for selling stock items in the unit, or None to use their role's."""
    row = PersonDiscountLimit.objects.filter(business_unit=business_unit, user=user).first()
    return row.percent if row else None


def limit_for(user, business_unit, action):
    """The most the person may approve, or None for no limit."""
    row = ApprovalLimit.objects.filter(business_unit=business_unit, action=action, user=user).first()
    return row.max_amount if row else None


def require_within_limit(user, business_unit, action, amount, what="this"):
    limit = limit_for(user, business_unit, action)
    if limit is not None and amount > limit:
        raise WorkflowPermissionDenied(
            f"Your approval limit here is QAR {limit:,.2f} and {what} is QAR {amount:,.2f}. "
            "Someone with a higher limit must approve it.")


def _require_viewer(actor):
    if not can_manage_users(actor):
        raise WorkflowPermissionDenied("Only a Master Admin or General Admin may see approval limits.")


def _require_changer(actor):
    require_master_admin(actor, "change approval limits and discounts")


def _name(user):
    return (user.get_full_name() or user.username) if user else ""


def overview(*, actor, business_unit):
    _require_viewer(actor)
    ceilings = {row.role: row for row in DiscountCeiling.objects.filter(business_unit=business_unit).select_related("updated_by")}
    limits = {(row.action, row.user_id): row for row in
              ApprovalLimit.objects.filter(business_unit=business_unit).select_related("updated_by")}
    people = get_user_model().objects.filter(holders_filter(business_unit), demo_marker__is_synthetic=True).order_by("username")
    ceilings_now = discount_ceilings(business_unit)
    own = {row.user_id: row for row in PersonDiscountLimit.objects.filter(business_unit=business_unit).select_related("updated_by")}
    assignments = BusinessUnitRoleAssignment.objects.filter(
        business_unit=business_unit, revoked_at__isnull=True, user__demo_marker__is_synthetic=True).select_related("user")
    sellers = {}
    for row in assignments:
        sellers.setdefault(row.user, []).append(row.role)
    approvals = []
    for action, label in LIMITED.items():
        holders = [person for person in people
                   if active_grants(person, action).filter(business_unit=business_unit).exists()]
        approvals.append({
            "action": action, "label": A(action).label, "covers": label,
            "people": [{
                "id": person.pk, "name": _name(person),
                "limit": format(limits[(action, person.pk)].max_amount, "f") if (action, person.pk) in limits else None,
                "changed": ({"by": _name(limits[(action, person.pk)].updated_by),
                             "at": timezone.localtime(limits[(action, person.pk)].updated_at).isoformat()}
                            if (action, person.pk) in limits else None),
            } for person in holders],
        })
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "discounts": [{
            "role": role, "label": Role(role).label, "percent": format(value, "f"), "default": format(DEFAULT_CEILINGS[role], "f"),
            "changed": ({"by": _name(ceilings[role].updated_by), "at": timezone.localtime(ceilings[role].updated_at).isoformat()}
                        if role in ceilings else None),
        } for role, value in discount_ceilings(business_unit).items()],
        "people_discounts": [{
            "id": person.pk, "name": _name(person),
            "roles": [Role(role).label for role in sorted(roles, key=list(Role.values).index)],
            "role_percent": format(max((ceilings_now.get(role, Decimal("0.00")) for role in roles), default=Decimal("0.00")), "f"),
            "percent": format(own[person.pk].percent, "f") if person.pk in own else None,
            "changed": ({"by": _name(own[person.pk].updated_by), "at": timezone.localtime(own[person.pk].updated_at).isoformat()}
                        if person.pk in own else None),
        } for person, roles in sorted(sellers.items(), key=lambda item: item[0].username)],
        "approvals": approvals,
        "can_change": is_master_admin(actor),
        "policy": POLICY_LABEL,
    }


def _number(value, label, *, top=None):
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        raise InvalidTransaction(f"{label} must be a number.") from None
    if not number.is_finite() or number < 0 or (top is not None and number > top):
        raise InvalidTransaction(f"{label} must be between 0 and {top}." if top is not None else f"{label} cannot be negative.")
    if number >= Decimal("100000000000"):
        raise InvalidTransaction(f"{label} is too large.")
    return number.quantize(Decimal("0.01"))


@transaction.atomic
def set_discount(*, actor, business_unit, role, percent):
    _require_changer(actor)
    if role not in DEFAULT_CEILINGS:
        raise InvalidTransaction("Choose salesperson, manager, commercial GM or CEO.")
    if percent in (None, ""):
        DiscountCeiling.objects.filter(business_unit=business_unit, role=role).delete()
        value = DEFAULT_CEILINGS[role]
    else:
        value = _number(percent, "The discount", top=100)
        DiscountCeiling.objects.update_or_create(business_unit=business_unit, role=role,
                                                 defaults={"percent": value, "updated_by": actor, "updated_at": timezone.now()})
    ordered = [discount_ceilings(business_unit)[key] for key in DEFAULT_CEILINGS]
    if ordered != sorted(ordered):
        raise InvalidTransaction("A more senior role cannot have a lower discount ceiling than a junior one.")
    record(AuditEvent.Kind.ACCOUNT_CHANGED, user=actor, detail={
        "by": actor.get_username(), "what": "discount ceiling", "change": {"unit": business_unit.code, "role": role, "percent": format(value, "f")}})
    return overview(actor=actor, business_unit=business_unit)


@transaction.atomic
def set_limit(*, actor, business_unit, action, user_id, max_amount):
    _require_changer(actor)
    if action not in LIMITED:
        raise InvalidTransaction("Choose an approval that moves money.")
    person = get_user_model().objects.filter(pk=user_id if type(user_id) is int else None, demo_marker__is_synthetic=True).first()
    if person is None or not active_grants(person, action).filter(business_unit=business_unit).exists():
        raise InvalidTransaction(f"That person cannot {A(action).label.lower()} in {business_unit.name}.")
    if max_amount in (None, ""):
        ApprovalLimit.objects.filter(business_unit=business_unit, action=action, user=person).delete()
        shown = "no limit"
    else:
        value = _number(max_amount, "The limit")
        ApprovalLimit.objects.update_or_create(business_unit=business_unit, action=action, user=person,
                                               defaults={"max_amount": value, "updated_by": actor, "updated_at": timezone.now()})
        shown = format(value, "f")
    record(AuditEvent.Kind.ACCOUNT_CHANGED, user=person, detail={
        "by": actor.get_username(), "what": "approval limit", "change": {"unit": business_unit.code, "action": action, "limit": shown}})
    return overview(actor=actor, business_unit=business_unit)


@transaction.atomic
def set_person_discount(*, actor, business_unit, user_id, percent):
    """Give one person their own discount for selling stock items, or clear it so their role's applies."""
    _require_changer(actor)
    person = get_user_model().objects.filter(pk=user_id if type(user_id) is int else None, demo_marker__is_synthetic=True).first()
    if person is None or not BusinessUnitRoleAssignment.objects.filter(
            user=person, business_unit=business_unit, revoked_at__isnull=True).exists():
        raise InvalidTransaction(f"That person does not sell in {business_unit.name}. Give them a sales role first.")
    if percent in (None, ""):
        PersonDiscountLimit.objects.filter(business_unit=business_unit, user=person).delete()
        shown = "role discount"
    else:
        value = _number(percent, "The discount", top=PERSON_DISCOUNT_TOP)
        PersonDiscountLimit.objects.update_or_create(business_unit=business_unit, user=person,
                                                     defaults={"percent": value, "updated_by": actor, "updated_at": timezone.now()})
        shown = format(value, "f")
    record(AuditEvent.Kind.ACCOUNT_CHANGED, user=person, detail={
        "by": actor.get_username(), "what": "personal discount", "change": {"unit": business_unit.code, "percent": shown}})
    return overview(actor=actor, business_unit=business_unit)
