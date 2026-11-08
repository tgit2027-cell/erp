"""Preset access levels: Master Admin, General Admin, Finance Admin, HR Admin, Head, Manager, Sales, and Custom.

A level is a ready-made bundle of the permissions that already exist (action
permissions per business unit, company-wide supplier permissions, approval roles
and chat review roles). Choosing a level for a person sets their own permissions
to exactly that bundle; permissions they get from a team are left alone.

- The admin levels apply in every business unit; Head, Manager and Sales apply in
  the business units chosen for the person.
- Master Admin can do everything, including this screen, and is the only level that
  may make admins, change permissions one by one (a custom user), copy access or
  manage teams. A Master Admin is a system administrator (superuser).
- General Admin may use this screen to add people, put them on the Head, Manager or
  Sales level, switch accounts on or off and set temporary passwords, but only for
  people who are not admins or custom users.
- Custom keeps whatever the person has now; a Master Admin then switches things on
  or off one by one. Changing a preset person one by one turns them into Custom.

The exact contents of each preset are provisional business defaults until Sam
confirms them; they are listed in ACCESS_LEVELS.md.
"""

from django.conf import settings
from django.contrib.auth.models import Permission
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEvent
from audit.recording import record

from .models import (
    AccessLevelAssignment,
    BusinessUnit,
    BusinessUnitRoleAssignment,
    TransferPermissionAudit,
    UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction

A = UserActionGrant.Action
Level = AccessLevelAssignment.Level
Role = BusinessUnitRoleAssignment.Role

INCENTIVES = (A.PREPARE_INCENTIVES, A.CHECK_INCENTIVES, A.APPROVE_INCENTIVES)
COMPANY = (A.OPEN_SUPPLIER, A.APPROVE_SUPPLIER, A.IMPORT_DATA, A.APPROVE_IMPORT) + INCENTIVES
FINANCE = (A.PAY_SUPPLIER, A.APPROVE_PAYMENT, A.VIEW_LEDGER, A.APPROVE_REFUND, A.CLOSE_MONTH, A.MANAGE_CASH,
           A.APPROVE_BANK_REC, A.RECORD_EXPENSE, A.APPROVE_EXPENSE, A.PREPARE_BUDGET, A.APPROVE_BUDGET,
           A.MANAGE_ASSETS, A.APPROVE_ASSET, A.PREPARE_JOURNAL, A.APPROVE_JOURNAL, A.PREPARE_NOTE, A.APPROVE_NOTE)
PAYROLL = (A.VIEW_PAY, A.MANAGE_PAY, A.APPROVE_PAY)
ALL_UNIT_ACTIONS = tuple(action for action in A if action not in COMPANY)

ADMIN_LEVELS = (Level.MASTER_ADMIN, Level.GENERAL_ADMIN, Level.FINANCE_ADMIN, Level.HR_ADMIN)
UNIT_LEVELS = (Level.HEAD, Level.MANAGER, Level.SALES)
# What a General Admin may hand out.
GENERAL_ADMIN_MAY_ASSIGN = UNIT_LEVELS

# chat review roles: requester = HR asks to review a chat, approver = the owner approves it.
CHAT_PERMISSIONS = {"requester": "request_chat_access", "approver": "approve_chat_access"}

PRESETS = {
    Level.MASTER_ADMIN: {
        "summary": "Everything, in every business unit, including users and permissions.",
        "unit_actions": ALL_UNIT_ACTIONS,
        "company": COMPANY,
        "roles": tuple(Role),
        "chat": ("requester", "approver"),
    },
    Level.GENERAL_ADMIN: {
        "summary": "Everything except finance, payroll and item costs, in every business unit. "
                   "Can add people and put them on the Head, Manager or Sales level.",
        "unit_actions": (A.READ_STOCK, A.CREATE_ORDER, A.REQUEST_TRANSFER, A.APPROVE_TRANSFER, A.EXECUTE_TRANSFER,
                         A.COUNT_STOCK, A.APPROVE_COUNT, A.MANAGE_REORDER, A.MANAGE_ITEMS,
                         A.VIEW_STAFF, A.MANAGE_STAFF),
        "company": (A.OPEN_SUPPLIER, A.IMPORT_DATA),
        "roles": (),
        "chat": (),
    },
    Level.FINANCE_ADMIN: {
        "summary": "All finance in every business unit: payments, bank and cash, ledger, month close, expenses, "
                   "budgets, assets, journals, notes, refunds, item costs, write-offs, approving payroll and suppliers, and preparing commission and rewards.",
        "unit_actions": (A.READ_STOCK, A.VIEW_COSTS, A.APPROVE_WRITE_OFF) + FINANCE + (A.VIEW_PAY, A.APPROVE_PAY),
        "company": (A.APPROVE_SUPPLIER, A.APPROVE_IMPORT, A.PREPARE_INCENTIVES),
        "roles": (),
        "chat": (),
    },
    Level.HR_ADMIN: {
        "summary": "Staff records, salaries and preparing payroll in every business unit, posting on the HR Portal, checking commission and rewards, and asking to review a chat.",
        "unit_actions": (A.VIEW_STAFF, A.MANAGE_STAFF, A.VIEW_PAY, A.MANAGE_PAY, A.POST_PORTAL),
        "company": (A.CHECK_INCENTIVES,),
        "roles": (),
        "chat": ("requester",),
    },
    Level.HEAD: {
        "summary": "Runs the chosen business units: sales, stock, transfers, count approval, item costs, the ledger, "
                   "expenses and budgets, refunds and staff records. Approves as business-unit GM.",
        "unit_actions": (A.READ_STOCK, A.CREATE_ORDER, A.REQUEST_TRANSFER, A.APPROVE_TRANSFER, A.APPROVE_COUNT,
                         A.MANAGE_REORDER, A.VIEW_COSTS, A.VIEW_LEDGER, A.RECORD_EXPENSE, A.APPROVE_EXPENSE,
                         A.PREPARE_BUDGET, A.APPROVE_REFUND, A.VIEW_STAFF),
        "company": (),
        "roles": (Role.BU_GM,),
        "chat": (),
    },
    Level.MANAGER: {
        "summary": "Manages a showroom or team in the chosen business units: sales, stock, transfers, counting, "
                   "low-stock levels and entering expenses. Approves as manager.",
        "unit_actions": (A.READ_STOCK, A.CREATE_ORDER, A.REQUEST_TRANSFER, A.APPROVE_TRANSFER, A.COUNT_STOCK,
                         A.MANAGE_REORDER, A.RECORD_EXPENSE),
        "company": (),
        "roles": (Role.MANAGER,),
        "chat": (),
    },
    Level.SALES: {
        "summary": "Sells in the chosen business units: sees stock, makes quotations and orders, asks for transfers.",
        "unit_actions": (A.READ_STOCK, A.CREATE_ORDER, A.REQUEST_TRANSFER),
        "company": (),
        "roles": (Role.SALESPERSON,),
        "chat": (),
    },
}


def _units():
    return BusinessUnit.objects.filter(is_active=True, is_synthetic=True).order_by("code")


def _assignment(person):
    return AccessLevelAssignment.objects.filter(user=person).first()


def _live_personal(person):
    now = timezone.now()
    return UserActionGrant.objects.filter(user=person, revoked_at__isnull=True, starts_at__lte=now) \
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))


def level_of(person):
    """The level a person is on now: a system administrator is always a Master Admin."""
    if person.is_superuser:
        return Level.MASTER_ADMIN.value
    assignment = _assignment(person)
    if assignment is not None:
        return assignment.level
    has_any = _live_personal(person).exists() or BusinessUnitRoleAssignment.objects.filter(
        user=person, revoked_at__isnull=True).exists()
    return Level.CUSTOM.value if has_any else ""


def level_label(level):
    return Level(level).label if level else "No access yet"


def is_master_admin(user):
    return bool(getattr(user, "is_authenticated", False) and user.is_active and user.is_superuser)


def is_general_admin(user):
    if not getattr(user, "is_authenticated", False) or not user.is_active or user.is_superuser:
        return False
    return AccessLevelAssignment.objects.filter(user=user, level=Level.GENERAL_ADMIN).exists()


def require_master_admin(user, what="change the ERP's setup"):
    """The setup lock (Sam, 5 Nov 2026): only the Master Admin changes setup, permissions and rules."""
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Setup is changed here only in the synthetic demo environment.")
    if not is_master_admin(user):
        raise WorkflowPermissionDenied(f"Only the Master Admin can {what}.")


# What a General Admin may give through teams or transfer permissions: only what the Head, Manager
# and Sales levels hold, never finance, payroll or company-wide permissions.
GENERAL_ADMIN_ACTIONS = frozenset(action for level in UNIT_LEVELS for action in PRESETS[level]["unit_actions"])


def can_manage_users(user):
    """Who may open Users and permissions: Master Admins and General Admins (synthetic demo only)."""
    return getattr(settings, "APP_ENV", None) == "demo" and (is_master_admin(user) or is_general_admin(user))


def may_edit(actor, person):
    """Whether the actor may change this person's level, account and password."""
    if is_master_admin(actor):
        return True
    if is_general_admin(actor):
        return person.pk != actor.pk and level_of(person) in ("",) + tuple(GENERAL_ADMIN_MAY_ASSIGN)
    return False


def assignable_levels(actor, person):
    if is_master_admin(actor):
        # Master Admin is offered only to someone who already is one: IT adds a new one at the owner's request.
        return [level.value for level in Level if level != Level.MASTER_ADMIN or person.is_superuser]
    if may_edit(actor, person):
        return [level.value for level in GENERAL_ADMIN_MAY_ASSIGN]
    return []


def level_choices():
    return [{"level": level.value, "label": level.label, "summary": PRESETS[level]["summary"] if level in PRESETS
             else "Keeps what the person has now; a Master Admin then switches permissions on or off one by one.",
             "per_unit": level in UNIT_LEVELS} for level in Level]


def _note(actor, person, what, change):
    record(AuditEvent.Kind.ACCOUNT_CHANGED, user=person, detail={"by": actor.get_username(), "what": what, "change": change})


def _audit_grant(actor, person, unit, action, change):
    TransferPermissionAudit.objects.create(actor=actor, target_user=person, business_unit=unit, action=action, change=change)


def _sync_grants(actor, person, units, unit_actions, company_actions, all_units):
    now = timezone.now()
    wanted = {(unit.pk, action) for unit in units for action in unit_actions}
    unit_by_id = {unit.pk: unit for unit in all_units}
    live = list(_live_personal(person).select_for_update().select_related("business_unit"))
    have = set()
    for grant in live:
        key = (grant.business_unit_id, grant.action)
        keep = grant.action in company_actions if grant.action in COMPANY else key in wanted
        if keep:
            have.add(key if grant.action not in COMPANY else (None, grant.action))
            continue
        grant.revoked_at = now
        grant.save(update_fields=["revoked_at"])
        _audit_grant(actor, person, grant.business_unit, grant.action, TransferPermissionAudit.Change.REVOKED)
    targets = [(unit_id, action) for unit_id, action in sorted(wanted) if (unit_id, action) not in have]
    # Company-wide permissions are stored against one unit, as the permissions screen does.
    home = units[0] if units else (all_units[0] if all_units else None)
    targets += [(home.pk, action) for action in company_actions if (None, action) not in have and home is not None]
    for unit_id, action in targets:
        unit = unit_by_id[unit_id]
        grant = UserActionGrant.objects.select_for_update().filter(user=person, business_unit=unit, action=action).first()
        if grant is None:
            UserActionGrant.objects.create(user=person, business_unit=unit, action=action, starts_at=now)
        else:
            grant.starts_at, grant.ends_at, grant.revoked_at = now, None, None
            grant.save(update_fields=["starts_at", "ends_at", "revoked_at"])
        _audit_grant(actor, person, unit, action, TransferPermissionAudit.Change.GRANTED)


def _sync_roles(actor, person, units, roles):
    wanted = {(unit.pk, role) for unit in units for role in roles}
    current = list(BusinessUnitRoleAssignment.objects.select_for_update().filter(user=person, revoked_at__isnull=True)
                   .select_related("business_unit"))
    have = set()
    for row in current:
        key = (row.business_unit_id, row.role)
        if key in wanted:
            have.add(key)
            continue
        row.revoked_at = timezone.now()
        row.save(update_fields=["revoked_at"])
        _note(actor, person, f"{Role(row.role).label} in {row.business_unit.code}", "revoked")
    for unit in units:
        for role in roles:
            if (unit.pk, role) not in have:
                BusinessUnitRoleAssignment.objects.create(user=person, business_unit=unit, role=role, assigned_by=actor)
                _note(actor, person, f"{Role(role).label} in {unit.code}", "granted")


def _sync_chat(actor, person, chat_roles):
    current = set(person.user_permissions.filter(content_type__app_label="chat").values_list("codename", flat=True))
    for key, codename in CHAT_PERMISSIONS.items():
        enabled = key in chat_roles
        if enabled == (codename in current):
            continue
        permission = Permission.objects.filter(content_type__app_label="chat", codename=codename).first()
        if permission is None:
            continue
        (person.user_permissions.add if enabled else person.user_permissions.remove)(permission)
        _note(actor, person, "Chat review role " + key, "granted" if enabled else "revoked")


def _other_master_admins(person):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(is_superuser=True, is_active=True).exclude(pk=person.pk).exists()


@transaction.atomic
def set_level(*, actor, person, level, unit_ids=None):
    """Put a person on a level. Presets replace their own permissions; Custom keeps them as they are."""
    if level not in Level.values:
        raise InvalidTransaction("Choose an access level.")
    if level == Level.MASTER_ADMIN and not person.is_superuser:
        # There is one Master Admin, the owner. Adding another is done by IT, only at the owner's request.
        raise WorkflowPermissionDenied("A new Master Admin can only be added by IT, at the owner's request.")
    if not may_edit(actor, person) or level not in assignable_levels(actor, person):
        raise WorkflowPermissionDenied(
            "Only a Master Admin may make admins or custom users, or change admins and custom users.")
    current = level_of(person)
    if person.is_superuser and level != Level.MASTER_ADMIN:
        if person.pk == actor.pk:
            raise InvalidTransaction("You cannot take away your own Master Admin level.")
        if not _other_master_admins(person):
            raise InvalidTransaction("Keep at least one other active Master Admin before changing this one.")
    all_units = list(_units())
    if level in UNIT_LEVELS:
        ids = unit_ids if isinstance(unit_ids, list) else []
        units = [unit for unit in all_units if unit.pk in {i for i in ids if isinstance(i, int)}]
        if not units:
            raise InvalidTransaction("Choose at least one business unit for this level.")
    else:
        units = all_units if level in ADMIN_LEVELS else []
    if level != Level.CUSTOM:
        preset = PRESETS[level]
        _sync_grants(actor, person, units, preset["unit_actions"], preset["company"], all_units)
        _sync_roles(actor, person, units, preset["roles"])
        _sync_chat(actor, person, preset["chat"])
    make_master = level == Level.MASTER_ADMIN
    if person.is_superuser != make_master or (make_master and not person.is_staff):
        person.is_superuser = make_master
        if make_master:
            person.is_staff = True
        person.save(update_fields=["is_superuser", "is_staff"])
    assignment, _ = AccessLevelAssignment.objects.update_or_create(
        user=person,
        defaults={"level": level, "assigned_by": actor, "assigned_at": timezone.now(),
                  "based_on": current if level == Level.CUSTOM and current not in ("", Level.CUSTOM) else ""},
    )
    assignment.business_units.set(units if level in UNIT_LEVELS else [])
    where = f" in {', '.join(unit.code for unit in units)}" if level in UNIT_LEVELS else ""
    _note(actor, person, f"access level {Level(level).label}{where}", "set to")


def became_custom(actor, person):
    """A Master Admin changed one permission by hand: a preset person is now a custom user."""
    if person.is_superuser:
        return
    current = level_of(person)
    if current == Level.CUSTOM:
        return
    AccessLevelAssignment.objects.update_or_create(
        user=person, defaults={"level": Level.CUSTOM, "based_on": current, "assigned_by": actor,
                               "assigned_at": timezone.now()})
    _note(actor, person, "access level Custom" + (f" (was {Level(current).label})" if current else ""), "set to")


def level_detail(actor, person):
    assignment = _assignment(person)
    level = level_of(person)
    return {
        "level": level,
        "label": level_label(level),
        "based_on": assignment.based_on if assignment and level == Level.CUSTOM else "",
        "unit_ids": sorted(assignment.business_units.values_list("pk", flat=True)) if assignment and level in UNIT_LEVELS else [],
        "choices": level_choices(),
        "assignable": assignable_levels(actor, person),
        "can_edit": may_edit(actor, person),
        "can_customize": is_master_admin(actor),
    }
