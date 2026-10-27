from django.conf import settings
from django.db.models import F, Q, Subquery
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied

from .models import (
    BusinessUnit,
    Item,
    Location,
    LocationCapacityGrant,
    UserActionGrant,
    Variant,
)


def active_grants(user, action=None):
    now = timezone.now()
    grants = UserActionGrant.objects.filter(
        user=user,
        revoked_at__isnull=True,
        starts_at__lte=now,
    ).filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
    if action:
        grants = grants.filter(action=action)
    return grants.select_related("business_unit", "business_unit__legal_entity")


def permitted_units(user):
    return BusinessUnit.objects.filter(
        pk__in=active_grants(user).values("business_unit_id"),
        is_active=True,
    ).distinct().order_by("code")


def context_units(user):
    """Units usable as UI context; this does not grant any protected action."""
    if settings.APP_ENV != "demo":
        return permitted_units(user)
    action_unit_ids = active_grants(user).order_by().values("business_unit_id")
    capacity_unit_ids = LocationCapacityGrant.objects.filter(
        user=user,
        revoked_at__isnull=True,
        is_synthetic=True,
        location__is_synthetic=True,
        location__location__is_synthetic=True,
        location__location__legal_entity__is_synthetic=True,
        location__location__business_units__is_active=True,
        location__location__business_units__is_synthetic=True,
        location__location__business_units__legal_entity=F("location__location__legal_entity"),
    ).order_by().values("location__location__business_units__id")
    unit_ids = action_unit_ids.union(capacity_unit_ids)
    if user.is_superuser:
        return BusinessUnit.objects.filter(
            is_active=True,
            is_synthetic=True,
            legal_entity__is_synthetic=True,
        ).order_by("code")
    return BusinessUnit.objects.filter(pk__in=Subquery(unit_ids), is_active=True).distinct().order_by("code")


def resolve_unit(request, action=None):
    units = context_units(request.user)
    requested = request.query_params.get("unit") or request.headers.get("X-Business-Unit")
    if requested is None:
        session_selection = request.session.get("selected_business_unit")
        if session_selection and units.filter(pk=session_selection).exists():
            requested = session_selection
    if requested is not None:
        try:
            unit_id = int(requested)
        except (TypeError, ValueError):
            raise PermissionDenied("The requested unit is not authorized.")
        unit = units.filter(pk=unit_id).first()
        if unit is None:
            raise PermissionDenied("The requested unit is not authorized.")
    else:
        unit = units.first()
    if unit is None:
        raise PermissionDenied("No active business-unit access is granted.")
    if action and not active_grants(request.user, action).filter(business_unit=unit).exists():
        raise PermissionDenied("This action is not granted for the selected unit.")
    return unit


def scoped_stock_queryset(unit, user, action=UserActionGrant.Action.READ_STOCK):
    if not active_grants(user, action).filter(business_unit=unit).exists():
        raise PermissionDenied("This action is not granted for the selected unit.")
    # The auto-created Location.business_units through table has a unique
    # (location, business_unit) pair, so this authorized join cannot duplicate
    # a stock row. Avoiding DISTINCT also avoids sorting every selected column.
    return (
        unit.stock_examples.select_related(
            "item",
            "item__brand",
            "variant",
            "location",
            "responsible_business_unit",
        )
        .filter(location__business_units=unit, is_synthetic=True)
    )


def apply_stock_search(queryset, search):
    term = search.strip()
    if not term:
        return queryset
    matching_item_ids = Item.objects.filter(
        Q(item_code__icontains=term)
        | Q(description__icontains=term)
        | Q(brand__name__icontains=term)
    ).values("pk")
    matching_variant_ids = Variant.objects.filter(sku__icontains=term).values("pk")
    matching_location_ids = Location.objects.filter(name__icontains=term).values("pk")
    return queryset.filter(
        Q(item_id__in=Subquery(matching_item_ids))
        | Q(variant_id__in=Subquery(matching_variant_ids))
        | Q(location_id__in=Subquery(matching_location_ids))
    )


def get_scoped_stock_or_404(queryset, stock_id):
    record = queryset.filter(pk=stock_id).first()
    if record is None:
        raise NotFound("Stock example not found.")
    return record


def stock_page_queryset(records, *, page, page_size, total_count):
    offset = (page - 1) * page_size
    if total_count <= page_size:
        return records[offset:offset + page_size]
    # Order/filter a narrow ID query first; hydrate related display columns for
    # only this page. Keep the original scoped filters and deterministic order
    # on both queries; no authorization decision is cached or bypassed.
    page_ids = records.values("pk")[offset:offset + page_size]
    return records.filter(pk__in=Subquery(page_ids))
