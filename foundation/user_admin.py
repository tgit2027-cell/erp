"""Users and permissions in one place (synthetic demo only, system administrators only).

One screen shows everything a person may do and lets an administrator change it:

- the account: active or switched off, a new temporary password, and whether
  the person is a system administrator (shown, not changed here);
- per business unit: every action permission and the approval roles
  (salesperson up to CEO);
- company-wide permissions (opening and approving suppliers);
- reporting roles and the chat review roles;
- warehouse capacity access, shown for reference (changed on its own screen).

Every change is recorded: action permissions in the permission audit table,
everything else as an 'account access changed' audit event naming who did it.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEvent
from audit.recording import record

from .access import active_grants
from .access_levels import became_custom, can_manage_users, level_detail, level_label, level_of, may_edit, set_level
from .areas import person_areas, set_area
from .models import (
    AccessLevelAssignment,
    BusinessUnit,
    BusinessUnitRoleAssignment,
    DemoUserMarker,
    LocationCapacityGrant,
    ReportingRole,
    ReportingRoleMembership,
    ReportPanelGrant,
    TeamEvent,
    TransferPermissionAudit,
    UserActionGrant,
    UserArea,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction
from .transfer_permissions import COMPANY_ACTIONS as COMPANY_WIDE, set_transfer_permission

Action = UserActionGrant.Action
Role = BusinessUnitRoleAssignment.Role
UNIT_GROUPS = [
    ("Sales and stock", [Action.READ_STOCK, Action.CREATE_ORDER]),
    ("Warehouse", [Action.REQUEST_TRANSFER, Action.APPROVE_TRANSFER, Action.EXECUTE_TRANSFER, Action.COUNT_STOCK,
                   Action.APPROVE_COUNT, Action.APPROVE_WRITE_OFF, Action.MANAGE_REORDER]),
    ("Items and buying", [Action.MANAGE_ITEMS, Action.VIEW_COSTS]),
    ("Finance", [Action.PAY_SUPPLIER, Action.APPROVE_PAYMENT, Action.VIEW_LEDGER, Action.APPROVE_REFUND,
                 Action.CLOSE_MONTH, Action.MANAGE_CASH, Action.APPROVE_BANK_REC, Action.RECORD_EXPENSE,
                 Action.APPROVE_EXPENSE, Action.PREPARE_BUDGET, Action.APPROVE_BUDGET,
                 Action.MANAGE_ASSETS, Action.APPROVE_ASSET, Action.PREPARE_JOURNAL, Action.APPROVE_JOURNAL,
                 Action.PREPARE_NOTE, Action.APPROVE_NOTE]),
    ("HR", [Action.VIEW_STAFF, Action.MANAGE_STAFF, Action.POST_PORTAL]),
    ("Payroll", [Action.VIEW_PAY, Action.MANAGE_PAY, Action.APPROVE_PAY]),
    ("Factory", [Action.RUN_FACTORY, Action.APPROVE_FACTORY, Action.APPROVE_FACTORY_COST, Action.BUY_FACTORY]),
    ("Projects & Fit-Out", [Action.RUN_PROJECTS, Action.APPROVE_PROJECTS, Action.INVOICE_PROJECTS]),
]
COMPANY_ACTIONS = list(COMPANY_WIDE)
CHAT_ROLES = {"requester": ("request_chat_access", "Ask to review a chat (HR)"),
              "approver": ("approve_chat_access", "Approve chat reviews (owner)")}
HINTS = {
    Action.READ_STOCK: "Lets the person open this unit and see its stock.",
    Action.CREATE_ORDER: "Lets the person make quotations and orders in this unit.",
}


def _require_admin(actor):
    """Master Admin only: one-by-one permissions (custom users), copying access, teams."""
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Users and permissions are managed here only in the synthetic demo environment.")
    if not getattr(actor, "is_authenticated", False) or not actor.is_superuser:
        raise WorkflowPermissionDenied("Only a Master Admin may change permissions one by one, copy access or manage teams.")


def _require_manager(actor):
    """Master Admins and General Admins may open the screen; what they may change is checked per change."""
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Users and permissions are managed here only in the synthetic demo environment.")
    if not can_manage_users(actor):
        raise WorkflowPermissionDenied("Only a Master Admin or General Admin may see and change users and permissions.")


def _require_may_edit(actor, person):
    _require_manager(actor)
    if not may_edit(actor, person):
        raise WorkflowPermissionDenied("Only a Master Admin may change admins and custom users.")


def _people():
    return get_user_model().objects.filter(demo_marker__is_synthetic=True)


def _person(user_id):
    person = _people().filter(pk=user_id).first() if isinstance(user_id, int) else None
    if person is None:
        raise InvalidTransaction("Choose a person from the list.")
    return person


def _units():
    return BusinessUnit.objects.filter(is_active=True, is_synthetic=True).select_related("legal_entity").order_by("code")


def _unit(unit_id):
    unit = _units().filter(pk=unit_id).first() if isinstance(unit_id, int) else None
    if unit is None:
        raise InvalidTransaction("Choose an active business unit.")
    return unit


def _live_grants(person):
    now = timezone.now()
    return UserActionGrant.objects.filter(user=person, revoked_at__isnull=True, starts_at__lte=now) \
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))


def _chat_roles(person):
    codenames = set(person.user_permissions.filter(content_type__app_label="chat").values_list("codename", flat=True))
    return {key: codename in codenames for key, (codename, _) in CHAT_ROLES.items()}


def _summary(person, grants, roles, level=None):
    units = sorted({code for code, _ in grants} | {code for code, _ in roles})
    if level is None:
        level = level_of(person)
    return {
        "id": person.pk, "username": person.username, "name": person.get_full_name() or person.username,
        "email": person.email, "is_active": person.is_active, "is_superuser": person.is_superuser,
        "is_staff": person.is_staff, "last_login": person.last_login.isoformat() if person.last_login else "",
        "units": units, "permission_count": len(grants), "role_count": len(roles),
        "level": level, "level_label": level_label(level),
    }


def people_overview(*, actor, search=""):
    _require_manager(actor)
    people = _people().order_by("username")
    term = (search or "").strip()
    if term:
        people = people.filter(Q(username__icontains=term) | Q(first_name__icontains=term)
                               | Q(last_name__icontains=term) | Q(email__icontains=term))
    people = list(people[:500])
    ids = [person.pk for person in people]
    now = timezone.now()
    grants, roles = {}, {}
    for user_id, code, action in UserActionGrant.objects.filter(
            user_id__in=ids, revoked_at__isnull=True, starts_at__lte=now, business_unit__is_active=True,
    ).filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now)).values_list("user_id", "business_unit__code", "action"):
        grants.setdefault(user_id, set()).add((code, action))
    personal = set(grants)
    for user_id, code, action in UserActionGrant.objects.filter(
            team__members__user_id__in=ids, team__is_active=True, revoked_at__isnull=True, starts_at__lte=now,
            business_unit__is_active=True,
    ).filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now)).values_list("team__members__user_id", "business_unit__code", "action"):
        grants.setdefault(user_id, set()).add((code, action))
    for user_id, code, role in BusinessUnitRoleAssignment.objects.filter(
            user_id__in=ids, revoked_at__isnull=True, business_unit__is_active=True,
    ).values_list("user_id", "business_unit__code", "role"):
        roles.setdefault(user_id, set()).add((code, role))
    assigned = dict(AccessLevelAssignment.objects.filter(user_id__in=ids).values_list("user_id", "level"))

    def level(person):
        if person.is_superuser:
            return AccessLevelAssignment.Level.MASTER_ADMIN.value
        return assigned.get(person.pk) or ("custom" if person.pk in personal or person.pk in roles else "")

    return {
        "people": [_summary(person, grants.get(person.pk, set()), roles.get(person.pk, set()), level(person))
                   for person in people],
        "units": [{"id": unit.pk, "code": unit.code, "name": unit.name} for unit in _units()],
        "me": actor.pk,
        "is_master_admin": bool(actor.is_superuser),
    }


def person_detail(*, actor, user_id):
    _require_manager(actor)
    person = _person(user_id)
    grants = list(_live_grants(person).filter(business_unit__is_active=True).values_list("business_unit_id", "action"))
    granted = {}
    for unit_id, action in grants:
        granted.setdefault(unit_id, set()).add(action)
    company = {action for _, action in grants if action in COMPANY_ACTIONS}
    role_rows = BusinessUnitRoleAssignment.objects.filter(user=person, revoked_at__isnull=True)
    roles = {}
    for unit_id, role in role_rows.values_list("business_unit_id", "role"):
        roles.setdefault(unit_id, set()).add(role)
    memberships = set(ReportingRoleMembership.objects.filter(user=person, revoked_at__isnull=True).values_list("role_id", flat=True))
    now = timezone.now()
    panels = ReportPanelGrant.objects.filter(user=person, revoked_at__isnull=True, starts_at__lte=now) \
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
    capacity = LocationCapacityGrant.objects.filter(user=person, revoked_at__isnull=True).select_related("location__location")
    unit_codes = {unit.pk: unit.code for unit in _units()}
    from .teams import person_teams
    teams, inherited = person_teams(person)
    everything = set(grants) | {(unit_id, action) for unit_id, action in inherited if unit_id is not None}
    history = [
        {"at": event.created_at.isoformat(), "by": event.actor.username,
         "what": f"{Action(event.action).label} (whole company)" if event.action in COMPANY_ACTIONS
         else f"{Action(event.action).label} in {event.business_unit.code}", "change": event.change}
        for event in TransferPermissionAudit.objects.filter(target_user=person).select_related("actor", "business_unit")[:40]
    ] + [
        {"at": event.occurred_at.isoformat(), "by": event.detail.get("by", "system"),
         "what": "the account" if event.kind == AuditEvent.Kind.ACCOUNT_CREATED
         else event.detail.get("what") or _account_fields(event.detail.get("changed", [])),
         "change": "created" if event.kind == AuditEvent.Kind.ACCOUNT_CREATED else event.detail.get("change", "changed")}
        for event in AuditEvent.objects.filter(user=person, kind__in=[AuditEvent.Kind.ACCOUNT_CHANGED, AuditEvent.Kind.ACCOUNT_CREATED])[:40]
    ] + [
        {"at": event.at.isoformat(), "by": event.actor.username, "what": f"team {event.team.name}",
         "change": "added to" if event.change == "added" else "removed from"}
        for event in TeamEvent.objects.filter(person=person).select_related("actor", "team")[:40]
    ]
    history.sort(key=lambda row: row["at"], reverse=True)
    return {
        "person": _summary(person, {(unit_codes.get(u, ""), a) for u, a in everything if u in unit_codes},
                           {(unit_codes.get(u, ""), r) for u, rs in roles.items() for r in rs if u in unit_codes}),
        "is_me": person.pk == actor.pk,
        "access_level": level_detail(actor, person),
        "groups": [{"name": name, "actions": [{"action": action.value, "label": action.label, "hint": HINTS.get(action, "")}
                                              for action in actions]} for name, actions in UNIT_GROUPS],
        "roles": [{"role": role.value, "label": role.label} for role in Role],
        "units": [{"id": unit.pk, "code": unit.code, "name": unit.name,
                   "actions": sorted(granted.get(unit.pk, set()) - set(COMPANY_ACTIONS)),
                   "from_teams": {action: sorted(names) for (unit_id, action), names in inherited.items()
                                  if unit_id == unit.pk and action not in COMPANY_ACTIONS},
                   "roles": sorted(roles.get(unit.pk, set()))} for unit in _units()],
        "company": [{"action": action.value, "label": action.label, "on": action.value in company,
                     "from_teams": sorted(inherited.get((None, action.value), set()))} for action in COMPANY_ACTIONS],
        "teams": teams,
        "reporting_roles": [{"id": role.pk, "name": role.name, "on": role.pk in memberships,
                             "manages_panels": role.can_manage_report_panels}
                            for role in ReportingRole.objects.filter(is_synthetic=True).order_by("name")],
        "report_panels": sorted({grant.get_panel_display() for grant in panels}),
        "chat_roles": [{"role": key, "label": label, "on": on}
                       for key, on in _chat_roles(person).items() for label in [CHAT_ROLES[key][1]]],
        "capacity": [{"location": grant.location.location.name, "access": grant.get_capability_display()}
                     for grant in capacity],
        "history": history[:40],
        "areas": person_areas(person),
    }


def _account_fields(fields):
    names = {"is_active": "account on/off", "is_staff": "staff access", "is_superuser": "system administrator", "password": "password"}
    return ", ".join(names.get(field, field) for field in fields) or "the account"


def _note(actor, person, what, change):
    record(AuditEvent.Kind.ACCOUNT_CHANGED, user=person, detail={"by": actor.get_username(), "what": what, "change": change})


@transaction.atomic
def change_person(*, actor, user_id, change):
    """Apply one change (switch on or off, or a level) and return the person's updated details.

    Master Admins may make any change. General Admins may only set a level they are allowed to
    hand out and switch accounts on or off, for people who are not admins or custom users.
    """
    _require_manager(actor)
    person = _person(user_id)
    if not isinstance(change, dict):
        raise InvalidTransaction("Send the change to make.")
    kind, enabled = change.get("kind"), change.get("enabled")
    if kind == "level":
        _require_may_edit(actor, person)
        set_level(actor=actor, person=person, level=change.get("level"), unit_ids=change.get("unit_ids"))
        return person_detail(actor=actor, user_id=person.pk)
    if kind in ("active", "area"):
        _require_may_edit(actor, person)
    else:
        _require_admin(actor)
    if type(enabled) is not bool:
        raise InvalidTransaction("Say whether to switch it on or off.")
    if kind == "action":
        action = change.get("action")
        if action in COMPANY_ACTIONS:
            unit = _unit(change.get("unit_id")) if change.get("unit_id") else _units().first()
        else:
            unit = _unit(change.get("unit_id"))
        if set_transfer_permission(actor=actor, business_unit=unit, user_id=person.pk, action=action,
                                   enabled=enabled).get("changed"):
            became_custom(actor, person)
    elif kind == "role":
        unit = _unit(change.get("unit_id"))
        role = change.get("role")
        if role not in Role.values:
            raise InvalidTransaction("Choose an approval role.")
        current = BusinessUnitRoleAssignment.objects.select_for_update().filter(
            user=person, business_unit=unit, role=role, revoked_at__isnull=True).first()
        if enabled and current is None:
            BusinessUnitRoleAssignment.objects.create(user=person, business_unit=unit, role=role, assigned_by=actor)
            _note(actor, person, f"{Role(role).label} in {unit.code}", "granted")
            became_custom(actor, person)
        elif not enabled and current is not None:
            current.revoked_at = timezone.now()
            current.save(update_fields=["revoked_at"])
            _note(actor, person, f"{Role(role).label} in {unit.code}", "revoked")
            became_custom(actor, person)
    elif kind == "reporting_role":
        role = ReportingRole.objects.filter(pk=change.get("role_id"), is_synthetic=True).first() \
            if isinstance(change.get("role_id"), int) else None
        if role is None:
            raise InvalidTransaction("Choose a reporting role.")
        current = ReportingRoleMembership.objects.select_for_update().filter(user=person, role=role, revoked_at__isnull=True).first()
        if enabled and current is None:
            ReportingRoleMembership.objects.create(role=role, user=person, assigned_by=actor)
            _note(actor, person, f"Reporting role {role.name}", "granted")
        elif not enabled and current is not None:
            current.revoked_at = timezone.now()
            current.save(update_fields=["revoked_at"])
            _note(actor, person, f"Reporting role {role.name}", "revoked")
    elif kind == "chat_role":
        key = change.get("role")
        if key not in CHAT_ROLES:
            raise InvalidTransaction("Choose a chat review role.")
        permission = Permission.objects.get(content_type__app_label="chat", codename=CHAT_ROLES[key][0])
        if enabled != _chat_roles(person)[key]:
            (person.user_permissions.add if enabled else person.user_permissions.remove)(permission)
            _note(actor, person, CHAT_ROLES[key][1], "granted" if enabled else "revoked")
            became_custom(actor, person)
    elif kind == "team":
        from .teams import set_person_team
        set_person_team(actor=actor, person=person, team_id=change.get("team_id"), enabled=enabled)
    elif kind == "area":
        if set_area(actor=actor, person=person, area=change.get("area"), enabled=enabled):
            _note(actor, person, f"Area {UserArea.Area(change.get('area')).label}", "granted" if enabled else "revoked")
    elif kind == "active":
        if person.pk == actor.pk:
            raise InvalidTransaction("You cannot switch off your own account.")
        if person.is_active != enabled:
            person.is_active = enabled
            person.save(update_fields=["is_active"])
    else:
        raise InvalidTransaction("Choose what to change.")
    return person_detail(actor=actor, user_id=person.pk)


@transaction.atomic
def copy_unit_access(*, actor, user_id, from_user_id, unit_id):
    """Give the person the same permissions and approval roles in a unit as another person (adds, never removes)."""
    _require_admin(actor)
    person, source, unit = _person(user_id), _person(from_user_id), _unit(unit_id)
    if person.pk == source.pk:
        raise InvalidTransaction("Choose someone else to copy from.")
    actions = set(_live_grants(source).filter(business_unit=unit).values_list("action", flat=True)) - set(COMPANY_ACTIONS)
    for action in sorted(actions):
        set_transfer_permission(actor=actor, business_unit=unit, user_id=person.pk, action=action, enabled=True)
    for role in BusinessUnitRoleAssignment.objects.filter(user=source, business_unit=unit, revoked_at__isnull=True) \
            .values_list("role", flat=True):
        change_person(actor=actor, user_id=person.pk, change={"kind": "role", "unit_id": unit.pk, "role": role, "enabled": True})
    return person_detail(actor=actor, user_id=person.pk)


def _set_password(person, password):
    password = password if isinstance(password, str) else ""
    try:
        validate_password(password, person)
    except DjangoValidationError as error:
        raise InvalidTransaction(" ".join(error.messages)) from None
    person.set_password(password)


@transaction.atomic
def add_person(*, actor, username, first_name="", last_name="", email="", password="", level="", unit_ids=None):
    """Create a sign-in for someone new, optionally straight onto a level; otherwise they start with no permissions."""
    _require_manager(actor)
    username = (username or "").strip()
    if not username or len(username) > 150 or not all(ch.isalnum() or ch in "@.+-_" for ch in username):
        raise InvalidTransaction("Give a username of letters, digits and @ . + - _ only.")
    if get_user_model().objects.filter(username__iexact=username).exists():
        raise InvalidTransaction(f"The username {username} is already taken.")
    person = get_user_model()(username=username, first_name=(first_name or "").strip()[:150],
                              last_name=(last_name or "").strip()[:150], email=(email or "").strip()[:254])
    _set_password(person, password)
    person.save()
    DemoUserMarker.objects.create(user=person)
    if level:
        set_level(actor=actor, person=person, level=level, unit_ids=unit_ids)
    return person_detail(actor=actor, user_id=person.pk)


@transaction.atomic
def reset_password(*, actor, user_id, password):
    """Set a new temporary password the administrator passes on to the person."""
    _require_manager(actor)
    person = _person(user_id)
    _require_may_edit(actor, person)
    _set_password(person, password)
    person.save(update_fields=["password"])
    return {"done": True}
