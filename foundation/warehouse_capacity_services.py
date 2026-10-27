"""Synthetic, location-scoped warehouse-capacity authorization and audit helpers."""

import os
import re
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    DemoUserMarker,
    Location,
    LocationCapacity,
    LocationCapacityAudit,
    LocationCapacityGrant,
)

CAPACITY_FIELDS = (
    "total_floor_area_m2",
    "allocated_storage_floor_area_m2",
    "received_goods_volume_m3",
)
DECIMAL_RE = re.compile(r"^\d+(?:\.\d{1,6})?$")
SIX_PLACES = Decimal("0.000001")


class CapacityPermissionDenied(Exception):
    pass


class CapacityInputError(Exception):
    def __init__(self, field, message):
        self.field = field
        super().__init__(message)


def _require_demo_environment():
    if os.environ.get("APP_ENV") != "demo":
        raise CapacityPermissionDenied("Warehouse capacity is available only in the synthetic demo environment.")


def active_capacity_grants(user, *, location_id=None):
    grants = LocationCapacityGrant.objects.filter(
        user=user,
        revoked_at__isnull=True,
        is_synthetic=True,
        location__is_synthetic=True,
        location__location__is_synthetic=True,
        location__location__legal_entity__is_synthetic=True,
    )
    if location_id is not None:
        grants = grants.filter(location__location_id=location_id)
    return grants.select_related("location", "location__location")


def has_capacity_workspace_access(user):
    if not user.is_authenticated:
        return False
    return active_capacity_grants(user).exists()


def _require_synthetic_manager(user):
    _require_demo_environment()
    if not user.is_authenticated or not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        raise CapacityPermissionDenied("An explicitly assigned synthetic warehouse manager account is required.")


def _require_capacity_admin(user):
    _require_demo_environment()
    if not user.is_authenticated or not user.is_superuser:
        raise CapacityPermissionDenied("Only an ERP/system administrator may manage capacity assignments.")


def _profile_or_denied(location_id):
    try:
        return LocationCapacity.objects.select_related("location", "location__legal_entity").get(
            location_id=location_id,
            is_synthetic=True,
            location__is_synthetic=True,
            location__legal_entity__is_synthetic=True,
        )
    except LocationCapacity.DoesNotExist as error:
        raise CapacityPermissionDenied("This location has no explicitly configured physical-capacity profile.") from error


def can_view_location_capacity(user, location_id):
    if not user.is_authenticated or not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        return False
    return active_capacity_grants(user, location_id=location_id).filter(
        capability__in=[
            LocationCapacityGrant.Capability.VIEW,
            LocationCapacityGrant.Capability.EDIT,
        ]
    ).exists()


def can_edit_location_capacity(user, location_id):
    if not user.is_authenticated or not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
        return False
    return active_capacity_grants(user, location_id=location_id).filter(
        capability=LocationCapacityGrant.Capability.EDIT,
    ).exists()


def capacity_values(profile):
    if profile.total_floor_area_m2 is None:
        return {
            "configured": False,
            **{field: None for field in CAPACITY_FIELDS},
            "available_floor_area_m2": None,
            "updated_at": None,
        }
    total = profile.total_floor_area_m2
    allocated = profile.allocated_storage_floor_area_m2
    volume = profile.received_goods_volume_m3
    available = (total - allocated).quantize(SIX_PLACES)
    return {
        "configured": True,
        "total_floor_area_m2": format(total, "f"),
        "allocated_storage_floor_area_m2": format(allocated, "f"),
        "received_goods_volume_m3": format(volume, "f"),
        "available_floor_area_m2": format(available, "f"),
        "updated_at": profile.updated_at.isoformat(),
    }


def location_capacity_data(profile, capability):
    location = profile.location
    return {
        "location": {
            "id": location.pk,
            "code": location.code,
            "name": location.name,
        },
        "capability": capability,
        **capacity_values(profile),
    }


def _parse_capacity_decimal(field, value):
    if not isinstance(value, str) or not DECIMAL_RE.fullmatch(value.strip()):
        raise CapacityInputError(field, "Enter a non-negative decimal with no more than 6 decimal places.")
    text = value.strip()
    try:
        amount = Decimal(text)
    except InvalidOperation as error:
        raise CapacityInputError(field, "Enter a valid non-negative decimal.") from error
    if not amount.is_finite() or amount < 0 or amount.adjusted() > 11:
        raise CapacityInputError(field, "Value must be non-negative and fit the supported decimal range.")
    return amount.quantize(SIX_PLACES)


def validate_capacity_values(payload):
    if not isinstance(payload, dict):
        raise CapacityInputError("detail", "Send all three capacity measurements.")
    values = {field: _parse_capacity_decimal(field, payload.get(field)) for field in CAPACITY_FIELDS}
    if values["allocated_storage_floor_area_m2"] > values["total_floor_area_m2"]:
        raise CapacityInputError(
            "allocated_storage_floor_area_m2",
            "Allocated floor area cannot exceed total floor area.",
        )
    return values


def set_location_capacity(*, actor, location_id, payload):
    _require_synthetic_manager(actor)
    if not can_edit_location_capacity(actor, location_id):
        raise CapacityPermissionDenied("An explicit edit-capacity assignment for this location is required.")
    values = validate_capacity_values(payload)
    with transaction.atomic():
        profile = _profile_or_denied(location_id)
        profile = LocationCapacity.objects.select_for_update().get(pk=profile.pk)
        before = capacity_values(profile)
        for field, value in values.items():
            setattr(profile, field, value)
        profile.updated_at = timezone.now()
        profile.updated_by = actor
        profile.save(update_fields=[*CAPACITY_FIELDS, "updated_at", "updated_by"])
        after = capacity_values(profile)
        LocationCapacityAudit.objects.create(
            kind=LocationCapacityAudit.Kind.CAPACITY_EDITED,
            location=profile.location,
            actor=actor,
            before_values=before,
            after_values=after,
        )
    return profile


def _synthetic_physical_location(location_id):
    location = Location.objects.filter(
        pk=location_id,
        is_synthetic=True,
        legal_entity__is_synthetic=True,
        business_units__is_active=True,
        business_units__is_synthetic=True,
        business_units__legal_entity=F("legal_entity"),
    ).distinct().first()
    if location is None:
        raise CapacityInputError("location_id", "Choose an existing synthetic location associated with an active business unit.")
    return location


def designate_capacity_location(*, actor, location_id):
    _require_capacity_admin(actor)
    location = _synthetic_physical_location(location_id)
    with transaction.atomic():
        profile, created = LocationCapacity.objects.get_or_create(location=location)
        if created:
            LocationCapacityAudit.objects.create(
                kind=LocationCapacityAudit.Kind.LOCATION_CONFIGURED,
                location=location,
                actor=actor,
                after_values={"physical_capacity_profile_created": True},
            )
    return profile, created


def assign_capacity(*, actor, user_id, location_id, capability):
    _require_capacity_admin(actor)
    profile = _profile_or_denied(location_id)
    try:
        capability = LocationCapacityGrant.Capability(capability)
    except (ValueError, TypeError) as error:
        raise CapacityInputError("capability", "Choose view_capacity or edit_capacity.") from error
    User = get_user_model()
    target = User.objects.filter(
        pk=user_id,
        is_active=True,
        demo_marker__is_synthetic=True,
    ).first()
    if target is None:
        raise CapacityInputError("user_id", "Choose an active synthetic demo account.")
    try:
        with transaction.atomic():
            grant = LocationCapacityGrant.objects.create(
                user=target,
                location=profile,
                capability=capability,
                assigned_by=actor,
            )
            LocationCapacityAudit.objects.create(
                kind=LocationCapacityAudit.Kind.ASSIGNED,
                location=profile.location,
                actor=actor,
                target_user=target,
                capability=capability,
                grant=grant,
                after_values={"active": True},
            )
    except IntegrityError as error:
        raise CapacityInputError("capability", "This active capacity assignment already exists.") from error
    return grant


def revoke_capacity_assignment(*, actor, grant_id):
    _require_capacity_admin(actor)
    with transaction.atomic():
        grant = LocationCapacityGrant.objects.select_for_update().filter(
            pk=grant_id,
            revoked_at__isnull=True,
            is_synthetic=True,
        ).select_related("user", "location__location").first()
        if grant is None:
            raise CapacityInputError("assignment", "The active capacity assignment was not found.")
        grant.revoked_at = timezone.now()
        grant.save(update_fields=["revoked_at"])
        LocationCapacityAudit.objects.create(
            kind=LocationCapacityAudit.Kind.REVOKED,
            location=grant.location.location,
            actor=actor,
            target_user=grant.user,
            capability=grant.capability,
            grant=grant,
            before_values={"active": True},
            after_values={"active": False, "revoked_at": grant.revoked_at.isoformat()},
        )
    return grant


def admin_capacity_configuration():
    locations = Location.objects.filter(
        is_synthetic=True,
        legal_entity__is_synthetic=True,
        business_units__is_active=True,
        business_units__is_synthetic=True,
        business_units__legal_entity=F("legal_entity"),
    ).distinct().prefetch_related("business_units", "capacity_profile__grants__user")
    result = []
    for location in locations:
        try:
            profile = location.capacity_profile
        except LocationCapacity.DoesNotExist:
            profile = None
        grants = []
        if profile:
            grants = [
                {
                    "id": grant.id,
                    "user_id": grant.user_id,
                    "username": grant.user.get_username(),
                    "capability": grant.capability,
                    "created_at": grant.created_at.isoformat(),
                }
                for grant in profile.grants.filter(revoked_at__isnull=True, is_synthetic=True).order_by(
                    "user__username", "capability",
                )
            ]
        result.append({
            "id": location.id,
            "code": location.code,
            "name": location.name,
            "business_units": [
                {"id": unit.id, "code": unit.code, "name": unit.name}
                for unit in location.business_units.filter(is_active=True, is_synthetic=True).order_by("code")
            ],
            "physical_capacity_configured": profile is not None,
            "assignments": grants,
        })
    User = get_user_model()
    users = [
        {"id": user.id, "username": user.get_username()}
        for user in User.objects.filter(is_active=True, demo_marker__is_synthetic=True).order_by("username")
    ]
    return {"locations": result, "synthetic_users": users}


def capacity_audit_queryset():
    """Synthetic audit events only; callers must authorize before evaluation."""
    return (
        LocationCapacityAudit.objects.filter(
            is_synthetic=True,
            location__is_synthetic=True,
            location__legal_entity__is_synthetic=True,
            location__capacity_profile__is_synthetic=True,
        )
        .select_related("location", "actor", "target_user")
        .order_by("-created_at", "-pk")
    )


def capacity_audit_event_data(event, synthetic_user_ids):
    labels = dict(LocationCapacityAudit.Kind.choices)
    return {
        "id": event.pk,
        "kind": event.kind,
        "label": labels.get(event.kind, "Warehouse capacity event"),
        "location": {
            "id": event.location_id,
            "code": event.location.code,
            "name": event.location.name,
        },
        "actor": (
            event.actor.get_username()
            if event.actor_id in synthetic_user_ids
            else "System administrator"
        ),
        "target_user": (
            event.target_user.get_username()
            if event.target_user_id in synthetic_user_ids
            else None
        ),
        "capability": event.capability or None,
        "created_at": event.created_at.isoformat(),
    }
