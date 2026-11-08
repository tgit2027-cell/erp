"""Teams and groups (synthetic demo only, system administrators only).

A team (for example the sales team) has its own permissions per business unit
and for the whole company. Every member gets all of the team's permissions on
top of anything given to them personally, so adding someone to the team is
enough to give them what the team does. Taking them out of the team takes the
team's permissions away again; their personal ones stay.

Each team also has its own chat. Its members follow the team automatically, so a
message there reaches everyone in the team. Every change is recorded with who
made it.
"""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from chat.models import MAX_TITLE_LENGTH, Conversation, Participant

from .models import Team, TeamEvent, TeamMember, UserActionGrant
from .transaction_services import InvalidTransaction
from .purchasing_services import WorkflowPermissionDenied
from .access_levels import GENERAL_ADMIN_ACTIONS, is_master_admin, may_edit
from .user_admin import COMPANY_ACTIONS, HINTS, UNIT_GROUPS, _require_manager, _unit, _units

Action = UserActionGrant.Action
MAX_TEAM_MEMBERS = 200


def _team(team_id):
    team = Team.objects.filter(pk=team_id).first() if isinstance(team_id, int) else None
    if team is None:
        raise InvalidTransaction("Choose a team from the list.")
    return team


def _people():
    return get_user_model().objects.filter(demo_marker__is_synthetic=True)


def _name(person):
    return person.get_full_name() or person.username


def _clean_name(name):
    name = " ".join((name or "").split()) if isinstance(name, str) else ""
    if not name:
        raise InvalidTransaction("Give the team a name.")
    if len(name) > 80:
        raise InvalidTransaction("Keep the team name to 80 characters.")
    return name


def _live(queryset):
    now = timezone.now()
    return queryset.filter(revoked_at__isnull=True, starts_at__lte=now).filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))


def _event(team, actor, change, what, person=None):
    TeamEvent.objects.create(team=team, actor=actor, change=change, what=what, person=person)


def _chat(team):
    conversation = Conversation.objects.filter(team=team).first()
    if conversation is None:
        conversation = Conversation.objects.create(kind=Conversation.Kind.TEAM, team=team,
                                                   title=team.name[:MAX_TITLE_LENGTH], created_by=team.created_by)
    return conversation


def _join_chat(team, person):
    conversation = _chat(team)
    row, created = Participant.objects.get_or_create(conversation=conversation, user=person)
    if not created and row.left_at is not None:
        # Coming back: they see the team chat from now on.
        newest = conversation.messages.order_by("-id").values_list("id", flat=True).first() or 0
        row.left_at, row.joined_at, row.last_read_message_id = None, timezone.now(), newest
        row.save(update_fields=["left_at", "joined_at", "last_read_message_id"])


def _leave_chat(team, person=None):
    rows = Participant.objects.filter(conversation__team=team, left_at__isnull=True)
    if person is not None:
        rows = rows.filter(user=person)
    rows.update(left_at=timezone.now())


def _summary(team):
    return {
        "id": team.pk, "name": team.name, "description": team.description, "is_active": team.is_active,
        "members": team.member_count if hasattr(team, "member_count") else team.members.count(),
        "permissions": team.grant_count if hasattr(team, "grant_count") else _live(team.grants).count(),
    }


def teams_overview(*, actor):
    _require_manager(actor)
    now = timezone.now()
    live = Q(grants__revoked_at__isnull=True, grants__starts_at__lte=now) & (
        Q(grants__ends_at__isnull=True) | Q(grants__ends_at__gt=now))
    teams = Team.objects.filter(is_synthetic=True).annotate(
        member_count=Count("members", distinct=True), grant_count=Count("grants", filter=live, distinct=True),
    ).order_by("-is_active", "name")
    return {"teams": [_summary(team) for team in teams]}


def team_detail(*, actor, team_id):
    _require_manager(actor)
    team = _team(team_id)
    grants = list(_live(team.grants).filter(business_unit__is_active=True).values_list("business_unit_id", "action"))
    granted = {}
    for unit_id, action in grants:
        granted.setdefault(unit_id, set()).add(action)
    company = {action for _, action in grants if action in COMPANY_ACTIONS}
    members = list(team.members.select_related("user").order_by("user__first_name", "user__username"))
    member_ids = {row.user_id for row in members}
    conversation = Conversation.objects.filter(team=team).first()
    master = is_master_admin(actor)
    return {
        # A General Admin changes a team only while it holds nothing beyond what they may hand out.
        "can_change": master or not _live(team.grants).exclude(action__in=GENERAL_ADMIN_ACTIONS).exists(),
        "allowed_actions": None if master else sorted(GENERAL_ADMIN_ACTIONS),
        "team": _summary(team) | {"created_at": team.created_at.isoformat(), "created_by": team.created_by.username,
                                  "chat_id": conversation.pk if conversation else None},
        "members": [{"id": row.user.pk, "username": row.user.username, "name": _name(row.user),
                     "is_active": row.user.is_active, "added_at": row.added_at.isoformat()} for row in members],
        "people": [{"id": person.pk, "name": _name(person), "username": person.username}
                   for person in _people().filter(is_active=True).exclude(pk__in=member_ids)
                   .order_by("first_name", "username")],
        "groups": [{"name": name, "actions": [{"action": action.value, "label": action.label, "hint": HINTS.get(action, "")}
                                              for action in actions]} for name, actions in UNIT_GROUPS],
        "units": [{"id": unit.pk, "code": unit.code, "name": unit.name,
                   "actions": sorted(granted.get(unit.pk, set()) - set(COMPANY_ACTIONS))} for unit in _units()],
        "company": [{"action": action.value, "label": action.label, "on": action.value in company}
                    for action in COMPANY_ACTIONS],
        "history": [{"at": event.at.isoformat(), "by": event.actor.username, "change": event.change, "what": event.what}
                    for event in team.events.select_related("actor")[:40]],
    }


@transaction.atomic
def create_team(*, actor, name, description="", member_ids=None):
    _require_manager(actor)
    name = _clean_name(name)
    if Team.objects.filter(name__iexact=name).exists():
        raise InvalidTransaction(f"There is already a team called {name}.")
    try:
        with transaction.atomic():
            team = Team.objects.create(name=name, description=(description or "").strip()[:300], created_by=actor)
    except IntegrityError:
        raise InvalidTransaction(f"There is already a team called {name}.") from None
    _chat(team)
    _event(team, actor, "created", "the team")
    for user_id in member_ids or []:
        _set_member(actor, team, user_id, True)
    return team_detail(actor=actor, team_id=team.pk)


def _person(user_id):
    person = _people().filter(pk=user_id).first() if isinstance(user_id, int) else None
    if person is None:
        raise InvalidTransaction("Choose a person from the list.")
    return person


def _general_admin_limits(actor, team, person=None):
    """A General Admin manages teams only within what they may hand out (Sam, 5 Nov 2026)."""
    if is_master_admin(actor):
        return
    if person is not None and not may_edit(actor, person):
        raise WorkflowPermissionDenied(f"Only the Master Admin can change {_name(person)}'s teams.")
    beyond = _live(team.grants).exclude(action__in=GENERAL_ADMIN_ACTIONS).exists()
    if beyond:
        raise WorkflowPermissionDenied("This team gives finance, payroll or company-wide permissions, so only the Master Admin can change it.")


def _set_member(actor, team, user_id, enabled):
    person = _person(user_id)
    _general_admin_limits(actor, team, person)
    row = TeamMember.objects.select_for_update().filter(team=team, user=person).first()
    if enabled and row is None:
        if not person.is_active:
            raise InvalidTransaction(f"{_name(person)}'s account is switched off.")
        if team.members.count() >= MAX_TEAM_MEMBERS:
            raise InvalidTransaction(f"A team can have up to {MAX_TEAM_MEMBERS} people.")
        TeamMember.objects.create(team=team, user=person, added_by=actor)
        if team.is_active:
            _join_chat(team, person)
        _event(team, actor, "added", _name(person), person)
    elif not enabled and row is not None:
        row.delete()
        _leave_chat(team, person)
        _event(team, actor, "removed", _name(person), person)


def _set_action(actor, team, action, enabled, unit_id=None):
    if action not in Action.values:
        raise InvalidTransaction("Choose a permission.")
    _general_admin_limits(actor, team)
    if not is_master_admin(actor) and action not in GENERAL_ADMIN_ACTIONS:
        raise WorkflowPermissionDenied(f"Only the Master Admin can give {Action(action).label.lower()} through a team.")
    company = action in COMPANY_ACTIONS
    label = f"{Action(action).label} (whole company)" if company else None
    now = timezone.now()
    if company and not enabled:
        changed = _live(team.grants.select_for_update().filter(action=action)).update(revoked_at=now)
        if changed:
            _event(team, actor, "revoked", label)
        return
    if company:
        if _live(team.grants.filter(action=action, business_unit__is_active=True)).exists():
            return
        unit = _units().first()
    else:
        unit = _unit(unit_id)
        label = f"{Action(action).label} in {unit.code}"
    grant = team.grants.select_for_update().filter(business_unit=unit, action=action).first()
    is_live = grant is not None and _live(team.grants.filter(pk=grant.pk)).exists()
    if enabled and not is_live:
        if grant is None:
            UserActionGrant.objects.create(team=team, business_unit=unit, action=action, starts_at=now)
        else:
            grant.starts_at, grant.ends_at, grant.revoked_at = now, None, None
            grant.save(update_fields=["starts_at", "ends_at", "revoked_at"])
        _event(team, actor, "granted", label)
    elif not enabled and is_live:
        grant.revoked_at = now
        grant.save(update_fields=["revoked_at"])
        _event(team, actor, "revoked", label)


@transaction.atomic
def change_team(*, actor, team_id, change):
    """Apply one change to a team and return its updated details."""
    _require_manager(actor)
    team = Team.objects.select_for_update().filter(pk=team_id).first() if isinstance(team_id, int) else None
    if team is None:
        raise InvalidTransaction("Choose a team from the list.")
    if not isinstance(change, dict):
        raise InvalidTransaction("Send the change to make.")
    kind = change.get("kind")
    if kind in ("details", "active"):
        _general_admin_limits(actor, team)
    if kind == "details":
        name = _clean_name(change.get("name"))
        if Team.objects.filter(name__iexact=name).exclude(pk=team.pk).exists():
            raise InvalidTransaction(f"There is already a team called {name}.")
        description = (change.get("description") or "").strip()[:300]
        if name != team.name:
            _event(team, actor, "renamed", f"{team.name} to {name}")
            Conversation.objects.filter(team=team).update(title=name[:MAX_TITLE_LENGTH])
        if description != team.description:
            _event(team, actor, "changed", "the description")
        team.name, team.description = name, description
        team.save(update_fields=["name", "description"])
        return team_detail(actor=actor, team_id=team.pk)
    if kind == "copy_person":
        # Start the team from what one person already has: their personal permissions, in every unit.
        person = _person(change.get("user_id"))
        rows = _live(UserActionGrant.objects.filter(user=person, business_unit__is_active=True)) \
            .values_list("business_unit_id", "action")
        for unit_id, action in sorted(set(rows)):
            _set_action(actor, team, action, True, unit_id)
        return team_detail(actor=actor, team_id=team.pk)
    enabled = change.get("enabled")
    if type(enabled) is not bool:
        raise InvalidTransaction("Say whether to switch it on or off.")
    if kind == "member":
        if not team.is_active and enabled:
            raise InvalidTransaction("This team is closed. Reopen it before adding people.")
        _set_member(actor, team, change.get("user_id"), enabled)
    elif kind == "action":
        _set_action(actor, team, change.get("action"), enabled, change.get("unit_id"))
    elif kind == "active":
        if team.is_active == enabled:
            return team_detail(actor=actor, team_id=team.pk)
        team.is_active, team.closed_at = enabled, None if enabled else timezone.now()
        team.save(update_fields=["is_active", "closed_at"])
        if enabled:
            for row in team.members.select_related("user"):
                _join_chat(team, row.user)
        else:
            _leave_chat(team)
        _event(team, actor, "reopened" if enabled else "closed",
               "the team" if enabled else "the team; its members no longer get its permissions")
    else:
        raise InvalidTransaction("Choose what to change.")
    return team_detail(actor=actor, team_id=team.pk)


def person_teams(person):
    """For the person screen: every open team, whether they are in it, and what each gives them."""
    member_of = set(TeamMember.objects.filter(user=person).values_list("team_id", flat=True))
    teams = list(Team.objects.filter(is_synthetic=True).filter(Q(is_active=True) | Q(pk__in=member_of)).order_by("name"))
    inherited = {}
    for team_name, unit_id, action in _live(UserActionGrant.objects.filter(
            team_id__in=member_of, team__is_active=True, business_unit__is_active=True,
    )).values_list("team__name", "business_unit_id", "action"):
        inherited.setdefault((unit_id, action), set()).add(team_name)
        inherited.setdefault((None, action), set()).add(team_name)
    return [{"id": team.pk, "name": team.name, "on": team.pk in member_of, "is_active": team.is_active} for team in teams], inherited


def set_person_team(*, actor, person, team_id, enabled):
    team = _team(team_id)
    return change_team(actor=actor, team_id=team.pk, change={"kind": "member", "user_id": person.pk, "enabled": enabled})
