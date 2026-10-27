"""System-administrator assignment of stock transfer and supplier onboarding permissions.

Transfer permissions are per business unit. Supplier onboarding permissions
(open and approve suppliers) apply company-wide: the screen shows them as on
when they are granted in any unit, and turning one off revokes it everywhere.
Every change is written to an append-only audit table; the services re-check
the live grant on every step.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .access import active_grants
from .models import (
    BusinessUnit,
    TransferPermissionAudit,
    UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied
from .transaction_services import InvalidTransaction, _require_demo


TRANSFER_ACTIONS = (
    UserActionGrant.Action.REQUEST_TRANSFER,
    UserActionGrant.Action.APPROVE_TRANSFER,
    UserActionGrant.Action.EXECUTE_TRANSFER,
)
SUPPLIER_ACTIONS = (
    UserActionGrant.Action.OPEN_SUPPLIER,
    UserActionGrant.Action.APPROVE_SUPPLIER,
)
PERMISSION_ACTIONS = TRANSFER_ACTIONS + SUPPLIER_ACTIONS


def _require_permission_admin(actor, business_unit):
    if getattr(settings, "APP_ENV", None) != "demo":
        raise WorkflowPermissionDenied("Transfer permissions are managed only in the synthetic demo environment.")
    if not getattr(actor, "is_authenticated", False) or not actor.is_superuser:
        raise WorkflowPermissionDenied("Only an ERP/system administrator may change transfer permissions.")
    _require_demo(business_unit, business_unit.legal_entity)


def _target(user_id):
    target = get_user_model().objects.filter(
        pk=user_id, is_active=True, demo_marker__is_synthetic=True,
    ).first()
    if target is None:
        raise InvalidTransaction("Choose an active synthetic demo account.")
    return target


def _action(value):
    if value not in PERMISSION_ACTIONS:
        raise InvalidTransaction("Choose a transfer or supplier permission.")
    return value


def transfer_permission_overview(*, actor, business_unit):
    _require_permission_admin(actor, business_unit)
    users = get_user_model().objects.filter(
        is_active=True, demo_marker__is_synthetic=True,
    ).order_by("username")
    now = timezone.now()
    granted = {}
    live = UserActionGrant.objects.filter(
        revoked_at__isnull=True,
        starts_at__lte=now,
    ).filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
    unit_grants = live.filter(business_unit=business_unit, action__in=TRANSFER_ACTIONS)
    company_grants = live.filter(business_unit__is_active=True, action__in=SUPPLIER_ACTIONS)
    for user_id, action in list(unit_grants.values_list("user_id", "action")) + list(
        company_grants.values_list("user_id", "action")
    ):
        granted.setdefault(user_id, set()).add(action)
    history = TransferPermissionAudit.objects.filter(
        Q(business_unit=business_unit) | Q(action__in=SUPPLIER_ACTIONS)
    ).select_related(
        "actor", "target_user",
    )[:50]
    return {
        "unit": {"id": business_unit.pk, "code": business_unit.code, "name": business_unit.name},
        "actions": [
            {
                "action": action,
                "label": UserActionGrant.Action(action).label,
                "scope": "company" if action in SUPPLIER_ACTIONS else "unit",
            }
            for action in PERMISSION_ACTIONS
        ],
        "users": [
            {
                "id": user.pk,
                "username": user.username,
                "name": user.get_full_name() or user.username,
                "actions": sorted(granted.get(user.pk, set())),
            }
            for user in users
        ],
        "history": [
            {
                "actor": event.actor.username,
                "target": event.target_user.username,
                "action": event.action,
                "change": event.change,
                "at": event.created_at.isoformat(),
            }
            for event in history
        ],
    }


@transaction.atomic
def set_transfer_permission(*, actor, business_unit, user_id, action, enabled):
    _require_permission_admin(actor, business_unit)
    target = _target(user_id)
    action = _action(action)
    if type(enabled) is not bool:
        raise InvalidTransaction("Say whether the permission should be on or off.")
    if action in SUPPLIER_ACTIONS and not enabled:
        return _revoke_everywhere(actor, target, action)
    if action in SUPPLIER_ACTIONS and active_grants(target, action).filter(business_unit__is_active=True).exists():
        return {"user_id": target.pk, "action": action, "enabled": True, "changed": False}
    grant = UserActionGrant.objects.select_for_update().filter(
        user=target, business_unit=business_unit, action=action,
    ).first()
    now = timezone.now()
    is_active = grant is not None and active_grants(target, action).filter(pk=grant.pk).exists()
    if enabled and not is_active:
        if grant is None:
            UserActionGrant.objects.create(user=target, business_unit=business_unit, action=action, starts_at=now)
        else:
            # The grant row is unique per user, unit and action, so re-granting reopens it.
            grant.starts_at, grant.ends_at, grant.revoked_at = now, None, None
            grant.save(update_fields=["starts_at", "ends_at", "revoked_at"])
        change = TransferPermissionAudit.Change.GRANTED
    elif not enabled and is_active:
        grant.revoked_at = now
        grant.save(update_fields=["revoked_at"])
        change = TransferPermissionAudit.Change.REVOKED
    else:
        return {"user_id": target.pk, "action": action, "enabled": enabled, "changed": False}
    TransferPermissionAudit.objects.create(
        actor=actor, target_user=target, business_unit=business_unit, action=action, change=change,
    )
    return {"user_id": target.pk, "action": action, "enabled": enabled, "changed": True}


def _revoke_everywhere(actor, target, action):
    now = timezone.now()
    grants = list(active_grants(target, action).select_for_update(of=("self",)))
    for grant in grants:
        grant.revoked_at = now
        grant.save(update_fields=["revoked_at"])
        TransferPermissionAudit.objects.create(
            actor=actor, target_user=target, business_unit=grant.business_unit, action=action,
            change=TransferPermissionAudit.Change.REVOKED,
        )
    return {"user_id": target.pk, "action": action, "enabled": False, "changed": bool(grants)}


def lookup_unit(unit_id):
    unit = BusinessUnit.objects.select_related("legal_entity").filter(pk=unit_id, is_active=True).first()
    if unit is None:
        raise InvalidTransaction("Choose an active business unit.")
    return unit
