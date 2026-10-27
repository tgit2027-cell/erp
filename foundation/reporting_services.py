"""Server-authorized synthetic reporting and export data services."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Prefetch, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth, TruncQuarter
from django.utils import timezone

from .models import (
    BusinessUnit,
    DemoUserMarker,
    DemoReportSalesFact,
    Item,
    JournalEntry,
    JournalLine,
    LegalEntity,
    Location,
    PurchaseReceipt,
    PurchaseReceiptCharge,
    PurchaseReceiptLine,
    QuoteReservation,
    ReportBudget,
    ReportDataCapability,
    ReportDataGrant,
    ReportExportAudit,
    ReportPanelGrant,
    ReportingPanel,
    ReportingRole,
    ReportingRoleMembership,
    SalesReportLine,
    StockBalance,
    StockReservation,
    StockTransfer,
)
from .load_diagnostics import measure_stage
from .pagination import page_metadata, parse_page_parameters


class ReportingError(Exception):
    pass


class ReportingPermissionDenied(ReportingError):
    pass


class ReportingValidationError(ReportingError):
    pass


class ReportingNotConfigured(ReportingError):
    pass


PANEL_LABELS = dict(ReportingPanel.choices)
CAPABILITY_LABELS = dict(ReportDataCapability.choices)
PERIODS = {"daily", "monthly", "quarterly"}


def _require_synthetic_user(user):
    if (
        not getattr(settings, "APP_ENV", None) == "demo"
        or not getattr(user, "is_authenticated", False)
        or not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists()
    ):
        raise ReportingPermissionDenied("Reporting is available only to authenticated synthetic demo users.")


def require_reporting_admin(user):
    _require_synthetic_user(user)
    if not user.is_staff:
        raise ReportingPermissionDenied("Only synthetic staff may configure reporting access.")


def can_manage_report_panels(user):
    if (
        not getattr(settings, "APP_ENV", None) == "demo"
        or not getattr(user, "is_authenticated", False)
        or not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists()
    ):
        return False
    if user.is_staff:
        return True
    return ReportingRoleMembership.objects.filter(
        user=user,
        role__is_synthetic=True,
        role__can_manage_report_panels=True,
        is_synthetic=True,
        revoked_at__isnull=True,
        created_at__lte=timezone.now(),
    ).exists()


def require_reporting_panel_manager(user):
    _require_synthetic_user(user)
    if not can_manage_report_panels(user):
        raise ReportingPermissionDenied("A synthetic report-panel manager role is required.")


def _active_role_ids(user):
    now = timezone.now()
    return ReportingRoleMembership.objects.filter(
        user=user,
        role__is_synthetic=True,
        is_synthetic=True,
        revoked_at__isnull=True,
        created_at__lte=now,
    ).values("role_id")


def _principal_query(user):
    return Q(user=user, role__isnull=True) | Q(user__isnull=True, role_id__in=_active_role_ids(user))


def _active_grants(model, user):
    now = timezone.now()
    return model.objects.filter(
        _principal_query(user),
        is_synthetic=True,
        revoked_at__isnull=True,
        starts_at__lte=now,
    ).filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))


def _has_capability(user, capability):
    return _active_grants(ReportDataGrant, user).filter(capability=capability).exists()


def _require_capability(user, capability):
    if not _has_capability(user, capability):
        raise ReportingPermissionDenied(f"Missing explicit report-data permission: {capability}.")


def _require_panel(user, panel):
    if panel not in ReportingPanel.values:
        raise ReportingValidationError("Unknown report panel.")
    if not _active_grants(ReportPanelGrant, user).filter(panel=panel).exists():
        raise ReportingPermissionDenied("This report panel has not been assigned to the user.")


def _now():
    return timezone.now()


def _principal_target(*, target_user=None, target_role=None):
    if (target_user is None) == (target_role is None):
        raise ReportingValidationError("Choose exactly one synthetic user or reporting role.")
    if target_user is not None:
        if not DemoUserMarker.objects.filter(user=target_user, is_synthetic=True).exists():
            raise ReportingValidationError("Report access may be assigned only to a synthetic demo user.")
        return {"user": target_user, "role": None}
    if not target_role.is_synthetic:
        raise ReportingValidationError("Report access may be assigned only to a synthetic reporting role.")
    return {"user": None, "role": target_role}


@transaction.atomic
def create_reporting_role(*, actor, code, name):
    require_reporting_admin(actor)
    code = code.strip().lower() if isinstance(code, str) else ""
    name = name.strip() if isinstance(name, str) else ""
    if not code.startswith("test-") or len(code) > 40:
        raise ReportingValidationError("Synthetic report-role codes must start with test- and be at most 40 characters.")
    if not name or len(name) > 100:
        raise ReportingValidationError("Enter a report-role name of at most 100 characters.")
    role, created = ReportingRole.objects.get_or_create(
        code=code,
        defaults={"name": name, "created_by": actor, "is_synthetic": True},
    )
    if not created and role.name != name:
        raise ReportingValidationError("That synthetic report-role code is already in use.")
    return role


@transaction.atomic
def add_reporting_role_member(*, actor, role, target_user):
    require_reporting_admin(actor)
    if not role.is_synthetic:
        raise ReportingValidationError("Only synthetic reporting roles can have members.")
    if not DemoUserMarker.objects.filter(user=target_user, is_synthetic=True).exists():
        raise ReportingValidationError("Report roles may include only synthetic demo users.")
    membership, created = ReportingRoleMembership.objects.get_or_create(
        role=role,
        user=target_user,
        revoked_at__isnull=True,
        defaults={"assigned_by": actor, "is_synthetic": True},
    )
    return membership, created


@transaction.atomic
def assign_report_panel(*, actor, panel, target_user=None, target_role=None):
    require_reporting_panel_manager(actor)
    if panel not in ReportingPanel.values:
        raise ReportingValidationError("Unknown report panel.")
    principal = _principal_target(target_user=target_user, target_role=target_role)
    grant, created = ReportPanelGrant.objects.get_or_create(
        **principal,
        panel=panel,
        revoked_at__isnull=True,
        defaults={"granted_by": actor, "is_synthetic": True},
    )
    return grant, created


@transaction.atomic
def revoke_report_panel(*, actor, grant):
    require_reporting_panel_manager(actor)
    if not grant.is_synthetic:
        raise ReportingValidationError("Only synthetic report-panel grants may be revoked here.")
    if grant.revoked_at is None:
        grant.revoked_at = _now()
        grant.save(update_fields=["revoked_at"])
    return grant


@transaction.atomic
def grant_report_data(
    *,
    actor,
    capability,
    target_user=None,
    target_role=None,
    business_unit=None,
    legal_entity=None,
):
    require_reporting_admin(actor)
    if capability not in ReportDataCapability.values:
        raise ReportingValidationError("Unknown report-data capability.")
    if (business_unit is None) == (legal_entity is None):
        raise ReportingValidationError("Choose exactly one business-unit or company-wide data scope.")
    principal = _principal_target(target_user=target_user, target_role=target_role)
    if business_unit is not None:
        if not business_unit.is_synthetic or not business_unit.is_active:
            raise ReportingValidationError("Only active synthetic business units can be granted.")
        legal_entity = None
    elif not legal_entity.is_synthetic:
        raise ReportingValidationError("Only synthetic legal entities can be granted.")
    grant, created = ReportDataGrant.objects.get_or_create(
        **principal,
        capability=capability,
        business_unit=business_unit,
        legal_entity=legal_entity,
        revoked_at__isnull=True,
        defaults={"granted_by": actor, "is_synthetic": True},
    )
    return grant, created


@transaction.atomic
def revoke_reporting_access(*, actor, grant):
    require_reporting_admin(actor)
    if grant.revoked_at is None:
        grant.revoked_at = _now()
        grant.save(update_fields=["revoked_at"])
    return grant


def list_reporting_configuration(*, actor):
    require_reporting_admin(actor)
    user_model = get_user_model()
    users = user_model.objects.filter(demo_marker__is_synthetic=True).order_by("username")
    roles = ReportingRole.objects.filter(is_synthetic=True).prefetch_related("memberships__user")
    panels = [
        {"code": key, "label": label}
        for key, label in ReportingPanel.choices
    ]
    capabilities = [
        {"code": key, "label": label}
        for key, label in ReportDataCapability.choices
    ]
    panel_grants = ReportPanelGrant.objects.filter(is_synthetic=True).select_related("user", "role", "granted_by")
    data_grants = ReportDataGrant.objects.filter(is_synthetic=True).select_related(
        "user", "role", "business_unit", "legal_entity", "granted_by"
    )
    return {
        "users": [{"id": user.pk, "username": user.get_username()} for user in users],
        "roles": [
            {
                "id": role.pk,
                "code": role.code,
                "name": role.name,
                "members": [
                    membership.user.get_username()
                    for membership in role.memberships.filter(revoked_at__isnull=True)
                ],
            }
            for role in roles
        ],
        "memberships": [
            {
                "id": membership.pk,
                "role_id": membership.role_id,
                "role_name": membership.role.name,
                "user_id": membership.user_id,
                "username": membership.user.get_username(),
                "revoked": membership.revoked_at is not None,
            }
            for membership in ReportingRoleMembership.objects.filter(is_synthetic=True)
            .select_related("role", "user")
            .order_by("role__name", "user__username")
        ],
        "units": [
            {"id": unit.pk, "code": unit.code, "name": unit.name}
            for unit in BusinessUnit.objects.filter(is_synthetic=True, is_active=True).select_related("legal_entity")
        ],
        "companies": [
            {"id": entity.pk, "code": entity.code, "name": entity.name}
            for entity in LegalEntity.objects.filter(is_synthetic=True)
        ],
        "panels": panels,
        "capabilities": capabilities,
        "panel_grants": [_serialize_panel_grant(grant) for grant in panel_grants],
        "data_grants": [_serialize_data_grant(grant) for grant in data_grants],
        "budgets": [
            {
                "id": budget.pk,
                "business_unit_id": budget.business_unit_id,
                "location_id": budget.location_id,
                "metric": budget.metric,
                "granularity": budget.granularity,
                "period_start": budget.period_start.isoformat(),
                "period_end": budget.period_end.isoformat(),
                "amount": format(budget.amount, "f"),
            }
            for budget in ReportBudget.objects.filter(is_synthetic=True).select_related("business_unit")
        ],
    }


def list_report_panel_management(*, actor):
    require_reporting_panel_manager(actor)
    users = get_user_model().objects.filter(demo_marker__is_synthetic=True).order_by("username")
    roles = ReportingRole.objects.filter(is_synthetic=True).order_by("name", "code")
    grants = ReportPanelGrant.objects.filter(is_synthetic=True).select_related("user", "role", "granted_by")
    return {
        "users": [{"id": user.pk, "username": user.get_username()} for user in users],
        "roles": [{"id": role.pk, "code": role.code, "name": role.name} for role in roles],
        "panels": [{"code": code, "label": label} for code, label in ReportingPanel.choices],
        "panel_grants": [_serialize_panel_grant(grant) for grant in grants],
    }


def _serialize_panel_grant(grant):
    return {
        "id": grant.pk,
        "user_id": grant.user_id,
        "username": grant.user.get_username() if grant.user_id else None,
        "role_id": grant.role_id,
        "role_name": grant.role.name if grant.role_id else None,
        "panel": grant.panel,
        "revoked": grant.revoked_at is not None,
    }


def _serialize_data_grant(grant):
    return {
        "id": grant.pk,
        "user_id": grant.user_id,
        "username": grant.user.get_username() if grant.user_id else None,
        "role_id": grant.role_id,
        "role_name": grant.role.name if grant.role_id else None,
        "capability": grant.capability,
        "business_unit_id": grant.business_unit_id,
        "business_unit": grant.business_unit.code if grant.business_unit_id else None,
        "legal_entity_id": grant.legal_entity_id,
        "company": grant.legal_entity.code if grant.legal_entity_id else None,
        "revoked": grant.revoked_at is not None,
    }


def _parse_date(value, fallback):
    if value in (None, ""):
        return fallback
    if not isinstance(value, str):
        raise ReportingValidationError("Date filters must use YYYY-MM-DD.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ReportingValidationError("Date filters must use YYYY-MM-DD.") from None


def _parse_filters(filters):
    granularity = filters.get("period", "monthly")
    if granularity not in PERIODS:
        raise ReportingValidationError("Period must be daily, monthly, or quarterly.")
    today = timezone.localdate()
    start = _parse_date(filters.get("start"), today - timedelta(days=29))
    end = _parse_date(filters.get("end"), today)
    if end < start:
        raise ReportingValidationError("The report end date must be on or after its start date.")
    raw_unit = filters.get("unit")
    raw_company = filters.get("company")
    if raw_unit and raw_company:
        raise ReportingValidationError("Select a business unit or a consolidated company view, not both.")
    try:
        unit_id = int(raw_unit) if raw_unit not in (None, "") else None
        company_id = int(raw_company) if raw_company not in (None, "") else None
        location_id = int(filters.get("location")) if filters.get("location") not in (None, "") else None
    except (TypeError, ValueError):
        raise ReportingValidationError("Business-unit, company, and location filters must be numeric IDs.") from None
    return {
        "period": granularity,
        "start": start,
        "end": end,
        "unit_id": unit_id,
        "company_id": company_id,
        "location_id": location_id,
    }


def _parse_money(value):
    if isinstance(value, float):
        raise ReportingValidationError("Budget values must be supplied as decimal-safe strings.")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ReportingValidationError("Budget amount must be a decimal value.") from None
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        raise ReportingValidationError("Budget amount must be finite, nonnegative, and have at most two decimals.")
    return amount.quantize(Decimal("0.01"))


@transaction.atomic
def configure_report_budget(
    *,
    actor,
    business_unit,
    metric,
    granularity,
    period_start,
    period_end,
    amount,
    location=None,
):
    require_reporting_admin(actor)
    if not business_unit.is_synthetic or not business_unit.is_active:
        raise ReportingValidationError("Only active synthetic business units can have test budgets.")
    if metric not in ReportBudget.Metric.values or granularity not in ReportBudget.Granularity.values:
        raise ReportingValidationError("Choose a supported budget metric and daily, monthly, or quarterly period.")
    start = _parse_date(period_start, None)
    end = _parse_date(period_end, None)
    if start is None or end is None:
        raise ReportingValidationError("Budget period start and end dates are required.")
    if end <= start:
        raise ReportingValidationError("Budget period end must be after its start date.")
    if location is not None:
        if not location.is_synthetic:
            raise ReportingValidationError("Only synthetic locations can be used for report budgets.")
        if location.legal_entity_id != business_unit.legal_entity_id:
            raise ReportingValidationError("Budget location must belong to the business unit's company.")
        if not location.business_units.filter(pk=business_unit.pk).exists():
            raise ReportingValidationError("Budget location is not assigned to the selected business unit.")
        if metric == ReportBudget.Metric.EXPENSES:
            raise ReportingNotConfigured(
                "Expense budgets cannot be showroom-specific until expense entries carry location attribution."
            )
    budget, created = ReportBudget.objects.get_or_create(
        business_unit=business_unit,
        location=location,
        metric=metric,
        granularity=granularity,
        period_start=start,
        period_end=end,
        defaults={
            "amount": _parse_money(amount),
            "created_by": actor,
            "updated_by": actor,
            "is_synthetic": True,
        },
    )
    if not created:
        budget.amount = _parse_money(amount)
        budget.updated_by = actor
        budget.updated_at = _now()
        budget.demo_fixture_key = ""
        budget.save(update_fields=["amount", "updated_by", "updated_at", "demo_fixture_key"])
    return budget


@transaction.atomic
def set_item_category(*, actor, item, category_label):
    require_reporting_admin(actor)
    if not item.is_synthetic:
        raise ReportingValidationError("Only synthetic catalog items can be categorized here.")
    if not isinstance(category_label, str) or len(category_label.strip()) > 80:
        raise ReportingValidationError("Category label must be 80 characters or fewer.")
    item.category_label = category_label.strip()
    item.save(update_fields=["category_label"])
    return item


def _effective_data_grants(user, capability):
    return _active_grants(ReportDataGrant, user).filter(capability=capability)


def _resolve_scope(user, capability, filters):
    _require_capability(user, capability)
    grants = _effective_data_grants(user, capability)
    unit_id = filters["unit_id"]
    company_id = filters["company_id"]
    if unit_id is not None:
        unit = BusinessUnit.objects.filter(pk=unit_id, is_active=True, is_synthetic=True).select_related(
            "legal_entity"
        ).first()
        if unit is None:
            raise ReportingPermissionDenied("The requested business unit is not available.")
        allowed = grants.filter(business_unit=unit).exists() or grants.filter(
            legal_entity=unit.legal_entity, business_unit__isnull=True
        ).exists()
        if not allowed:
            raise ReportingPermissionDenied("Report access is not granted for the requested business unit.")
        units = [unit]
        entity = unit.legal_entity
    elif company_id is not None:
        entity = LegalEntity.objects.filter(pk=company_id, is_synthetic=True).first()
        if entity is None or not grants.filter(legal_entity=entity, business_unit__isnull=True).exists():
            raise ReportingPermissionDenied("Consolidated reporting requires an explicit company-wide data grant.")
        units = list(BusinessUnit.objects.filter(legal_entity=entity, is_active=True, is_synthetic=True))
    else:
        unit_ids = set(grants.filter(business_unit__isnull=False).values_list("business_unit_id", flat=True))
        company_ids = set(grants.filter(legal_entity__isnull=False).values_list("legal_entity_id", flat=True))
        if len(company_ids) > 1:
            raise ReportingPermissionDenied("Select one business unit or an explicitly granted company scope.")
        if company_ids:
            company_id = next(iter(company_ids))
            company_unit_ids = set(
                BusinessUnit.objects.filter(
                    legal_entity_id=company_id,
                    is_active=True,
                    is_synthetic=True,
                ).values_list("pk", flat=True)
            )
            if unit_ids - company_unit_ids:
                raise ReportingPermissionDenied("Select one business unit or an explicitly granted company scope.")
            units = list(
                BusinessUnit.objects.filter(pk__in=company_unit_ids)
                .select_related("legal_entity")
                .order_by("code")
            )
            entity = LegalEntity.objects.filter(pk=company_id, is_synthetic=True).first()
        else:
            units = list(
                BusinessUnit.objects.filter(
                    pk__in=unit_ids,
                    is_active=True,
                    is_synthetic=True,
                ).select_related("legal_entity").order_by("code")
            )
            if len(units) > 1:
                raise ReportingPermissionDenied("Select one business unit; cross-unit consolidation requires an explicit company-wide grant.")
            entity = None
        if not units:
            raise ReportingPermissionDenied("No business-unit rows are authorized for this report.")
    if filters["location_id"] is not None:
        location = Location.objects.filter(
            pk=filters["location_id"],
            is_synthetic=True,
            legal_entity_id__in={unit.legal_entity_id for unit in units},
            business_units__in=units,
        ).first()
        if location is None:
            raise ReportingPermissionDenied("The requested location is outside the report's authorized row scope.")
        units = [unit for unit in units if location.business_units.filter(pk=unit.pk).exists()]
        if not units:
            raise ReportingPermissionDenied("The requested location is outside the report's authorized row scope.")
    return units, entity


def _date_for(timestamp):
    return timezone.localtime(timestamp).date()


def _period_key(value, granularity):
    if granularity == "daily":
        return value.isoformat()
    if granularity == "monthly":
        return value.strftime("%Y-%m")
    quarter = ((value.month - 1) // 3) + 1
    return f"{value.year}-Q{quarter}"


def _money(value):
    return format((value or Decimal("0.00")).quantize(Decimal("0.01")), "f")


def _scope_fields(units, entity, filters):
    if len(units) == 1:
        unit = units[0]
        return {
            "scope_type": "company" if filters["company_id"] is not None else "business_unit",
            "business_unit": {"id": unit.pk, "code": unit.code, "name": unit.name},
            "business_unit_ids": [unit.pk],
            "company": {"id": unit.legal_entity_id, "code": unit.legal_entity.code, "name": unit.legal_entity.name},
        }
    return {
        "scope_type": "company" if entity is not None else "authorized_units",
        "business_unit": None,
        "business_unit_ids": [unit.pk for unit in units],
        "company": (
            {"id": entity.pk, "code": entity.code, "name": entity.name}
            if entity
            else None
        ),
    }


def _location_filter(queryset, filters, relation="location"):
    if filters["location_id"] is None:
        return queryset
    return queryset.filter(**{f"{relation}_id": filters["location_id"]})


def _timestamp_bounds(start, end_exclusive):
    timezone_info = timezone.get_current_timezone()
    start_at = timezone.make_aware(datetime.combine(start, time.min), timezone_info)
    end_at = timezone.make_aware(datetime.combine(end_exclusive, time.min), timezone_info)
    return start_at, end_at


def _date_filter(queryset, field, start, end):
    start_at, end_at = _timestamp_bounds(start, end + timedelta(days=1))
    return queryset.filter(**{f"{field}__gte": start_at, f"{field}__lt": end_at})


def _sales_line_queryset(units, filters):
    queryset = _date_filter(SalesReportLine.objects.filter(
        business_unit__in=units,
        is_synthetic=True,
    ), "created_at", filters["start"], filters["end"])
    if filters["location_id"] is not None:
        queryset = queryset.filter(location_id=filters["location_id"])
    return queryset.order_by("created_at", "pk")


def _sales_facts(units, filters):
    queryset = _sales_line_queryset(units, filters)
    with measure_stage("database_fetch"):
        facts = list(queryset)
    return facts


def _base_report(panel, filters, units, entity, fields, rows, **extra):
    return {
        "panel": panel,
        "title": PANEL_LABELS[panel],
        "granularity": filters["period"],
        "currency": "QAR",
        "filters": {
            "start": filters["start"].isoformat(),
            "end": filters["end"].isoformat(),
            "location_id": filters["location_id"],
            "unit_id": filters["unit_id"],
            "company_id": filters["company_id"],
        },
        "scope": _scope_fields(units, entity, filters),
        "fields": fields,
        "rows": rows,
        "policy_label": (
            "TEST ONLY — fictional synthetic report data; the prototype's QAR display is not an approved currency "
            "rule. Calendar-day/month/quarter buckets use configurable date filters, not an approved fiscal calendar "
            "or accounting policy."
        ),
        **extra,
    }


def _sales_page_queryset(sales_lines, *, row_limit=None):
    row_fields = (
        "created_at",
        "location_name",
        "selling_price",
        "quantity",
        "description",
        "row_item_id",
        "brand_name",
        "source_pk",
    )
    rows = (
        sales_lines.order_by()
        .annotate(
            location_name=F("location__name"),
            row_item_id=F("item_code"),
            source_pk=F("pk"),
        )
        .values(*row_fields)
    )
    rows = rows.order_by("created_at", "source_pk")
    return rows[:row_limit] if row_limit is not None else rows


def _period_aggregation(queryset, granularity):
    expression = {
        "daily": TruncDate,
        "monthly": TruncMonth,
        "quarterly": TruncQuarter,
    }[granularity]("created_at", tzinfo=timezone.get_current_timezone())
    return (
        queryset.order_by()
        .annotate(report_period=expression)
        .values("report_period")
        .annotate(revenue=Sum("revenue"), quantity=Sum("quantity"))
        .order_by("report_period")
    )


def _item_aggregation(sales_lines):
    return (
        sales_lines.order_by()
        .values("item_code", "description", "brand_name")
        .annotate(quantity=Sum("quantity"), sales_amount=Sum("revenue"))
    )


def _category_aggregation(sales_lines):
    return (
        sales_lines.order_by()
        .exclude(category_label="")
        .values("category_label")
        .annotate(quantity=Sum("quantity"), sales_amount=Sum("revenue"))
    )


def _period_date(value):
    if isinstance(value, datetime):
        return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    return value


def _combine_group_rows(group_sets, group_key):
    combined = {}
    for group_set in group_sets:
        for entry in group_set:
            key = group_key(entry)
            current = combined.setdefault(
                key,
                {"quantity": Decimal("0.00"), "sales_amount": Decimal("0.00")},
            )
            current["quantity"] += entry["quantity"] or Decimal("0.00")
            current["sales_amount"] += entry["sales_amount"] or Decimal("0.00")
    return combined


def _sales_report(user, panel, filters, *, page=None, page_size=None):
    _require_capability(user, ReportDataCapability.SALES)
    units, entity = _resolve_scope(user, ReportDataCapability.SALES, filters)
    sales_lines = _sales_line_queryset(units, filters)

    category_units = []
    if panel == ReportingPanel.BEST_SELLERS and _has_capability(
        user, ReportDataCapability.CATEGORY_SALES
    ):
        try:
            category_units, _ = _resolve_scope(user, ReportDataCapability.CATEGORY_SALES, filters)
        except ReportingPermissionDenied:
            category_units = []
    category_unit_ids = {unit.pk for unit in category_units}
    category_allowed = bool(category_unit_ids)
    category_lines = sales_lines.filter(business_unit_id__in=category_unit_ids)

    with measure_stage("database_fetch"):
        sales_count = sales_lines.count()
        trend_groups = list(_period_aggregation(sales_lines, filters["period"]))
        row_queryset = _sales_page_queryset(
            sales_lines,
            row_limit=page * page_size if page is not None else None,
        )
        page_records = list(
            row_queryset[
                (page - 1) * page_size:page * page_size
                if page is not None
                else None
            ]
            if page is not None
            else row_queryset
        )
        item_groups = _item_aggregation(sales_lines) if panel == ReportingPanel.BEST_SELLERS else None
        item_records = list(item_groups) if item_groups is not None else []
        category_groups = (
            _category_aggregation(category_lines)
            if panel == ReportingPanel.BEST_SELLERS and category_allowed
            else None
        )
        category_records = list(category_groups) if category_groups is not None else []

    pagination = None
    if page is not None:
        try:
            pagination = page_metadata(sales_count, page, page_size)
        except ValueError as error:
            raise ReportingValidationError(str(error)) from error

    with measure_stage("python_transformation_aggregation"):
        rows = [
            {
                "selling_price": _money(record["selling_price"]),
                "quantity": format(record["quantity"], "f"),
                "description": record["description"],
                "item_id": record["row_item_id"],
                "warehouse_location": record["location_name"],
                "brand_name": record["brand_name"],
            }
            for record in page_records
        ]
        trend = {}
        for entry in trend_groups:
            period = _period_key(_period_date(entry["report_period"]), filters["period"])
            bucket = trend.setdefault(
                period,
                {"revenue": Decimal("0.00"), "quantity": Decimal("0.00")},
            )
            bucket["revenue"] += entry["revenue"] or Decimal("0.00")
            bucket["quantity"] += entry["quantity"] or Decimal("0.00")
        total_revenue = sum((entry["revenue"] for entry in trend.values()), Decimal("0.00"))
        total_quantity = sum((entry["quantity"] for entry in trend.values()), Decimal("0.00"))
        trend_rows = [
            {
                "period": period,
                "sales_amount": _money(values["revenue"]),
                "quantity": format(values["quantity"], "f"),
            }
            for period, values in sorted(trend.items())
        ]

        item_totals = _combine_group_rows(
            (item_records,),
            lambda entry: (entry["item_code"], entry["description"], entry["brand_name"]),
        )
        sorted_items = [
            {
                "item_id": item_id,
                "description": description,
                "brand_name": brand_name,
                "quantity": format(values["quantity"], "f"),
                "sales_amount": _money(values["sales_amount"]),
            }
            for (item_id, description, brand_name), values in sorted(
                item_totals.items(),
                key=lambda pair: (-pair[1]["quantity"], pair[0][0]),
            )
        ]
        category_totals = _combine_group_rows(
            (category_records,),
            lambda entry: entry["category_label"],
        )
        sorted_categories = [
            {
                "category": category,
                "quantity": format(values["quantity"], "f"),
                "sales_amount": _money(values["sales_amount"]),
            }
            for category, values in sorted(
                category_totals.items(),
                key=lambda pair: (-pair[1]["quantity"], pair[0]),
            )
        ]

    fields = [
        "selling_price",
        "quantity",
        "description",
        "item_id",
        "warehouse_location",
        "brand_name",
    ]
    report = _base_report(
        panel,
        filters,
        units,
        entity,
        fields,
        rows,
        summary={"sales_amount": _money(total_revenue), "quantity": format(total_quantity, "f")},
        trend=trend_rows,
        best_selling_items=sorted_items if panel == ReportingPanel.BEST_SELLERS else [],
        best_selling_categories=sorted_categories if panel == ReportingPanel.BEST_SELLERS and category_allowed else None,
        categories_permission_required=panel == ReportingPanel.BEST_SELLERS and not category_allowed,
        note=(
            "Operational sales rows, totals, trends, and exports use transactional "
            "SalesReportLine records only. Report-only examples are excluded."
        ),
    )
    if pagination is not None:
        report["pagination"] = pagination
    return report


def build_sales_examples(*, actor, query_params):
    """Return a separately scoped summary of report-only synthetic sales examples."""
    _require_synthetic_user(actor)
    _require_panel(actor, ReportingPanel.SALES)
    filters = _parse_filters(query_params)
    units, entity = _resolve_scope(actor, ReportDataCapability.SALES, filters)
    examples = _date_filter(
        DemoReportSalesFact.objects.filter(
            business_unit__in=units,
            is_synthetic=True,
        ),
        "created_at",
        filters["start"],
        filters["end"],
    )
    if filters["location_id"] is not None:
        examples = examples.filter(location_id=filters["location_id"])

    with measure_stage("database_fetch"):
        aggregate = examples.aggregate(
            example_count=Count("pk"),
            revenue=Sum("revenue"),
            quantity=Sum("quantity"),
        )
    with measure_stage("python_transformation_aggregation"):
        return {
            "panel": "sales_examples",
            "title": "Synthetic examples — not operational sales",
            "source": "DemoReportSalesFact",
            "example_count": aggregate["example_count"] or 0,
            "summary": {
                "illustrative_revenue": _money(aggregate["revenue"]),
                "illustrative_quantity": format(
                    aggregate["quantity"] or Decimal("0.00"),
                    "f",
                ),
            },
            "currency": "QAR",
            "filters": {
                "start": filters["start"].isoformat(),
                "end": filters["end"].isoformat(),
                "location_id": filters["location_id"],
                "unit_id": filters["unit_id"],
                "company_id": filters["company_id"],
            },
            "scope": _scope_fields(units, entity, filters),
            "policy_label": (
                "TEST ONLY — fictional report examples, separate from operational sales; "
                "the QAR display is not an approved currency or accounting rule."
            ),
            "note": (
                "This separately filtered count and summary use DemoReportSalesFact only. "
                "They are not included in operational SalesReportLine rows, totals, trends, "
                "pagination, exports, or saved report results."
            ),
        }


def _cost_expense_report(user, filters):
    capabilities = [
        capability
        for capability in (
            ReportDataCapability.SUPPLIER_COST,
            ReportDataCapability.LANDED_COST,
            ReportDataCapability.EXPENSES,
        )
        if _has_capability(user, capability)
    ]
    if not capabilities:
        raise ReportingPermissionDenied("Costs and expenses require explicit cost or expense permissions.")
    scope_by_cap = {}
    for capability in capabilities:
        try:
            scope_by_cap[capability] = _resolve_scope(user, capability, filters)
        except ReportingPermissionDenied:
            # Cost panels can contain independently scoped measures. Ignore a
            # measure outside the selected scope; never widen it to another unit.
            continue
    if not scope_by_cap:
        raise ReportingPermissionDenied("No cost or expense capability covers the requested report scope.")
    units = sorted(
        {unit.pk: unit for scope_units, _entity in scope_by_cap.values() for unit in scope_units}.values(),
        key=lambda unit: unit.code,
    )
    scope_entries = list(scope_by_cap.values())
    scope_entities = [entity for _scope_units, entity in scope_entries]
    scope_unit_sets = [{unit.pk for unit in scoped_units} for scoped_units, _entity in scope_entries]
    first_entity = scope_entities[0] if scope_entities else None
    same_company_scope = (
        first_entity is not None
        and all(item is not None and item.pk == first_entity.pk for item in scope_entities)
        and all(unit_ids == scope_unit_sets[0] for unit_ids in scope_unit_sets)
    )
    entity = first_entity if same_company_scope else None
    series = {}
    fields = ["period"]
    if ReportDataCapability.SUPPLIER_COST in scope_by_cap:
        allowed_units, _ = scope_by_cap[ReportDataCapability.SUPPLIER_COST]
        receipts = _date_filter(PurchaseReceiptLine.objects.filter(
            receipt__business_unit__in=allowed_units,
            receipt__is_synthetic=True,
            is_synthetic=True,
        ), "receipt__created_at", filters["start"], filters["end"])
        if filters["location_id"] is not None:
            receipts = receipts.filter(location_id=filters["location_id"])
        for row in receipts.values("receipt__created_at", "purchase_value"):
            key = _period_key(_date_for(row["receipt__created_at"]), filters["period"])
            series.setdefault(key, {})["supplier_purchase_value"] = (
                Decimal(series.get(key, {}).get("supplier_purchase_value", "0.00")) + row["purchase_value"]
            )
        fields.append("supplier_purchase_value")
    if ReportDataCapability.LANDED_COST in scope_by_cap:
        allowed_units, _ = scope_by_cap[ReportDataCapability.LANDED_COST]
        allocations = _date_filter(PurchaseReceiptLine.objects.filter(
            receipt__business_unit__in=allowed_units,
            receipt__is_synthetic=True,
            is_synthetic=True,
        ), "receipt__created_at", filters["start"], filters["end"])
        if filters["location_id"] is not None:
            allocations = allocations.filter(location_id=filters["location_id"])
        for row in allocations.values("receipt__created_at", "landed_cost_allocation"):
            key = _period_key(_date_for(row["receipt__created_at"]), filters["period"])
            series.setdefault(key, {})["landed_charges"] = (
                Decimal(series.get(key, {}).get("landed_charges", "0.00")) + row["landed_cost_allocation"]
            )
        fields.append("landed_charges")
    expense_details = []
    if ReportDataCapability.EXPENSES in scope_by_cap:
        if filters["location_id"] is not None:
            raise ReportingNotConfigured(
                "Expense postings do not have showroom/location attribution in the synthetic ledger."
            )
        allowed_units, _ = scope_by_cap[ReportDataCapability.EXPENSES]
        entries = _date_filter(JournalLine.objects.filter(
            entry__business_unit__in=allowed_units,
            entry__status=JournalEntry.Status.POSTED,
            entry__is_synthetic=True,
            is_synthetic=True,
            account__account_class="expense",
        ), "entry__created_at", filters["start"], filters["end"]).select_related("entry", "account")
        expense_by_period = {}
        for line in entries:
            period = _period_key(_date_for(line.entry.created_at), filters["period"])
            amount = line.debit - line.credit
            expense_by_period[period] = expense_by_period.get(period, Decimal("0.00")) + amount
            expense_details.append(
                {
                    "period": period,
                    "account_code": line.account.code,
                    "account_name": line.account.name,
                    "amount": _money(amount),
                }
            )
        for period, amount in expense_by_period.items():
            series.setdefault(period, {})["posted_expense_accounts"] = amount
        fields.append("posted_expense_accounts")
    rows = [
        {"period": period, **{name: _money(values[name]) for name in fields if name != "period" and name in values}}
        for period, values in sorted(series.items())
    ]
    return _base_report(
        ReportingPanel.COSTS_EXPENSES,
        filters,
        units,
        entity,
        fields,
        rows,
        expense_account_detail=expense_details,
        note="Amounts are grouped from explicitly synthetic receipt records and posted account classes; no unapproved expense mapping is inferred.",
    )


def _gross_margin_report(user, filters):
    _require_capability(user, ReportDataCapability.SALES)
    _require_capability(user, ReportDataCapability.GROSS_MARGIN)
    _require_capability(user, ReportDataCapability.SUPPLIER_COST)
    scopes = [
        _resolve_scope(user, capability, filters)
        for capability in (
            ReportDataCapability.SALES,
            ReportDataCapability.GROSS_MARGIN,
            ReportDataCapability.SUPPLIER_COST,
        )
    ]
    unit_ids = set(unit.pk for unit in scopes[0][0])
    for scoped_units, _scoped_entity in scopes[1:]:
        unit_ids.intersection_update(unit.pk for unit in scoped_units)
    if not unit_ids:
        raise ReportingPermissionDenied("Gross-margin permissions do not overlap for any authorized business unit.")
    units = [unit for unit in scopes[0][0] if unit.pk in unit_ids]
    entity = scopes[0][1] if all(scope_entity and scope_entity.pk == scopes[0][1].pk for _, scope_entity in scopes) else None
    facts = _sales_facts(units, filters)
    totals = {}
    for fact in facts:
        period = _period_key(_date_for(fact.created_at), filters["period"])
        row = totals.setdefault(period, {"revenue": Decimal("0.00"), "cogs": Decimal("0.00")})
        row["revenue"] += fact.revenue
        row["cogs"] += fact.cost_of_goods_sold
    rows = []
    for period, values in sorted(totals.items()):
        margin = values["revenue"] - values["cogs"]
        margin_percent = (margin * Decimal("100.00") / values["revenue"]) if values["revenue"] else Decimal("0.00")
        rows.append(
            {
                "period": period,
                "revenue": _money(values["revenue"]),
                "cost_of_goods_sold": _money(values["cogs"]),
                "gross_margin": _money(margin),
                "gross_margin_percent": _money(margin_percent),
            }
        )
    return _base_report(
        ReportingPanel.GROSS_MARGIN,
        filters,
        units,
        entity,
        ["period", "revenue", "cost_of_goods_sold", "gross_margin", "gross_margin_percent"],
        rows,
        note="Test-only gross margin from synthetic delivery facts; not an approved accounting measure.",
    )


def _inventory_report(user, filters):
    units, entity = _resolve_scope(user, ReportDataCapability.INVENTORY, filters)
    balances = StockBalance.objects.filter(
        business_unit__in=units,
        is_synthetic=True,
    ).select_related("business_unit", "variant__item__brand", "location")
    if filters["location_id"] is not None:
        balances = balances.filter(location_id=filters["location_id"])
    value_unit_ids = set()
    if _has_capability(user, ReportDataCapability.SUPPLIER_COST) and _has_capability(
        user, ReportDataCapability.LANDED_COST
    ):
        try:
            cost_units, _ = _resolve_scope(user, ReportDataCapability.SUPPLIER_COST, filters)
            landed_units, _ = _resolve_scope(user, ReportDataCapability.LANDED_COST, filters)
            value_unit_ids = {unit.pk for unit in cost_units}.intersection(unit.pk for unit in landed_units)
            value_unit_ids.intersection_update(unit.pk for unit in units)
        except ReportingPermissionDenied:
            value_unit_ids = set()
    can_value_inventory = bool(value_unit_ids)
    fields = [
        "business_unit",
        "item_id",
        "description",
        "brand_name",
        "warehouse_location",
        "on_hand_quantity",
        "reserved_quantity",
    ]
    if can_value_inventory:
        fields.append("inventory_value")
    rows = []
    for balance in balances.order_by("business_unit__code", "location__name", "variant__sku"):
        row = {
            "business_unit": balance.business_unit.code,
            "item_id": balance.variant.item.item_code,
            "description": balance.variant.item.description,
            "brand_name": balance.variant.item.brand.name,
            "warehouse_location": balance.location.name,
            "on_hand_quantity": format(balance.on_hand_quantity, "f"),
            "reserved_quantity": format(balance.reserved_quantity, "f"),
        }
        if can_value_inventory:
            row["inventory_value"] = (
                _money(balance.on_hand_value) if balance.business_unit_id in value_unit_ids else None
            )
        rows.append(row)
    return _base_report(
        ReportingPanel.INVENTORY,
        filters,
        units,
        entity,
        fields,
        rows,
    )


def _purchase_history_report(user, filters):
    units, entity = _resolve_scope(user, ReportDataCapability.PURCHASE_HISTORY, filters)
    supplier_cost_units = set()
    landed_cost_units = set()
    for capability, target in (
        (ReportDataCapability.SUPPLIER_COST, supplier_cost_units),
        (ReportDataCapability.LANDED_COST, landed_cost_units),
    ):
        if _has_capability(user, capability):
            try:
                scoped_units, _ = _resolve_scope(user, capability, filters)
                target.update(unit.pk for unit in scoped_units)
            except ReportingPermissionDenied:
                pass
    authorized_purchase_units = {unit.pk for unit in units}
    supplier_cost_units.intersection_update(authorized_purchase_units)
    landed_cost_units.intersection_update(authorized_purchase_units)
    can_supplier_cost = bool(supplier_cost_units)
    can_landed_cost = bool(landed_cost_units)
    queryset = _date_filter(PurchaseReceiptLine.objects.filter(
        receipt__business_unit__in=units,
        receipt__is_synthetic=True,
        is_synthetic=True,
    ), "receipt__created_at", filters["start"], filters["end"]).select_related(
        "receipt__supplier",
        "receipt__business_unit",
        "variant__item__brand",
        "location",
    )
    if filters["location_id"] is not None:
        queryset = queryset.filter(location_id=filters["location_id"])
    fields = [
        "period",
        "business_unit",
        "supplier",
        "item_id",
        "description",
        "brand_name",
        "warehouse_location",
        "quantity",
    ]
    if can_supplier_cost:
        fields.extend(["supplier_unit_cost", "purchase_value"])
    if can_landed_cost:
        fields.append("landed_cost_allocation")
    rows = []
    for line in queryset.order_by("receipt__created_at", "pk"):
        row = {
            "period": _period_key(_date_for(line.receipt.created_at), filters["period"]),
            "business_unit": line.receipt.business_unit.code,
            "supplier": line.receipt.supplier.name,
            "item_id": line.variant.item.item_code,
            "description": line.variant.item.description,
            "brand_name": line.variant.item.brand.name,
            "warehouse_location": line.location.name,
            "quantity": format(line.quantity, "f"),
        }
        if can_supplier_cost:
            row["supplier_unit_cost"] = None
            row["purchase_value"] = None
        if can_landed_cost:
            row["landed_cost_allocation"] = None
        if line.receipt.business_unit_id in supplier_cost_units:
            row["supplier_unit_cost"] = _money(line.unit_cost)
            row["purchase_value"] = _money(line.purchase_value)
        if line.receipt.business_unit_id in landed_cost_units:
            row["landed_cost_allocation"] = _money(line.landed_cost_allocation)
        rows.append(row)
    return _base_report(ReportingPanel.PURCHASE_HISTORY, filters, units, entity, fields, rows)


def _optional_capability_units(user, capability, filters, allowed_unit_ids):
    if not _has_capability(user, capability):
        return set()
    try:
        units, _entity = _resolve_scope(user, capability, filters)
    except ReportingPermissionDenied:
        return set()
    return {unit.pk for unit in units} & allowed_unit_ids


def _resource_page(queryset, query_params, paginate):
    if not paginate:
        return list(queryset), None
    try:
        page, page_size = parse_page_parameters(query_params)
        total_count = queryset.count()
        metadata = page_metadata(total_count, page, page_size)
    except ValueError as error:
        raise ReportingValidationError(str(error)) from error
    start = (page - 1) * page_size
    return list(queryset[start:start + page_size]), metadata


def build_purchase_receipt_register(*, actor, query_params, receipt_id=None, paginate=True):
    """Receipt register whose row and cost scopes match the purchase-history report."""
    _require_synthetic_user(actor)
    _require_panel(actor, ReportingPanel.PURCHASE_HISTORY)
    filters = _parse_filters(query_params)
    units, entity = _resolve_scope(actor, ReportDataCapability.PURCHASE_HISTORY, filters)
    allowed_unit_ids = {unit.pk for unit in units}
    supplier_cost_units = _optional_capability_units(
        actor, ReportDataCapability.SUPPLIER_COST, filters, allowed_unit_ids
    )
    landed_cost_units = _optional_capability_units(
        actor, ReportDataCapability.LANDED_COST, filters, allowed_unit_ids
    )

    line_prefetch = PurchaseReceiptLine.objects.filter(is_synthetic=True).select_related(
        "variant__item__brand",
        "location",
    ).filter(
        location__business_units__pk=F("receipt__business_unit_id")
    ).order_by("pk")
    if filters["location_id"] is not None:
        line_prefetch = line_prefetch.filter(location_id=filters["location_id"])
    receipts = PurchaseReceipt.objects.filter(
        business_unit__in=units,
        is_synthetic=True,
    ).select_related("business_unit__legal_entity", "supplier")
    receipts = _date_filter(receipts, "created_at", filters["start"], filters["end"])
    if filters["location_id"] is not None:
        receipts = receipts.filter(
            lines__is_synthetic=True,
            lines__location_id=filters["location_id"],
        ).distinct()
    if receipt_id is not None:
        receipts = receipts.filter(pk=receipt_id)
    receipts = receipts.order_by("-created_at", "-pk").prefetch_related(
        Prefetch("lines", queryset=line_prefetch, to_attr="authorized_lines")
    )
    if landed_cost_units:
        receipts = receipts.prefetch_related(
            Prefetch(
                "charges",
                queryset=PurchaseReceiptCharge.objects.filter(is_synthetic=True).order_by("pk"),
                to_attr="authorized_charges",
            )
        )

    if receipt_id is not None:
        receipt = receipts.first()
        if receipt is None:
            raise ReportingPermissionDenied("The receipt is not available in this purchase-history scope.")
        selected_receipts = [receipt]
        pagination = None
    else:
        selected_receipts, pagination = _resource_page(receipts, query_params, paginate)

    fields = ["receipt_id", "created_at", "business_unit", "supplier", "currency", "lines"]
    if supplier_cost_units:
        fields.extend(["purchase_value", "lines.supplier_unit_cost", "lines.purchase_value"])
    if landed_cost_units:
        fields.extend([
            "eligible_landed_charges",
            "charges.category",
            "charges.amount",
            "charges.eligible",
            "lines.landed_cost_allocation",
        ])
    if supplier_cost_units & landed_cost_units:
        fields.extend(["landed_cost_rate_percent", "inventory_value"])

    def serialize_receipt(receipt):
        unit_id = receipt.business_unit_id
        row_fields = ["receipt_id", "created_at", "business_unit", "supplier", "currency", "lines"]
        record = {
            "receipt_id": receipt.pk,
            "created_at": receipt.created_at.isoformat(),
            "business_unit": {
                "id": receipt.business_unit_id,
                "code": receipt.business_unit.code,
                "name": receipt.business_unit.name,
            },
            "supplier": {
                "code": receipt.supplier.code,
                "name": receipt.supplier.name,
            },
            "currency": receipt.currency,
        }
        if receipt.currency != "QAR":
            # The rate itself is not a cost; the supplier total in its currency is.
            record.update({
                "exchange_rate": format(receipt.exchange_rate, "f"),
                "rate_source": receipt.rate_source,
                "rate_date": receipt.rate_date.isoformat(),
            })
            row_fields.extend(["exchange_rate", "rate_source", "rate_date"])
        lines = []
        for line in receipt.authorized_lines:
            line_data = {
                "item_id": line.variant.item.item_code,
                "description": line.variant.item.description,
                "brand_name": line.variant.item.brand.name,
                "warehouse_location": line.location.name,
                "quantity": format(line.quantity, "f"),
            }
            if unit_id in supplier_cost_units:
                line_data["supplier_unit_cost"] = _money(line.unit_cost)
                line_data["purchase_value"] = _money(line.purchase_value)
            if unit_id in landed_cost_units:
                line_data["landed_cost_allocation"] = _money(line.landed_cost_allocation)
            lines.append(line_data)
        record["lines"] = lines
        if unit_id in supplier_cost_units:
            record["purchase_value"] = _money(receipt.purchase_value)
            row_fields.extend(["purchase_value", "lines.supplier_unit_cost", "lines.purchase_value"])
            if receipt.currency != "QAR":
                record["foreign_purchase_value"] = _money(receipt.foreign_purchase_value)
                row_fields.append("foreign_purchase_value")
        if unit_id in landed_cost_units:
            record["eligible_landed_charges"] = _money(receipt.eligible_landed_charges)
            record["charges"] = [
                {
                    "category": charge.category,
                    "amount": _money(charge.amount),
                    "eligible": charge.is_eligible,
                    **(
                        {
                            "currency": charge.currency,
                            "foreign_amount": _money(charge.foreign_amount),
                            "exchange_rate": format(charge.exchange_rate, "f"),
                            "rate_source": charge.rate_source,
                            "rate_date": charge.rate_date.isoformat(),
                        }
                        if charge.currency != "QAR"
                        else {}
                    ),
                }
                for charge in getattr(receipt, "authorized_charges", [])
            ]
            row_fields.extend([
                "eligible_landed_charges",
                "charges.category",
                "charges.amount",
                "charges.eligible",
                "charges.currency",
                "charges.foreign_amount",
                "lines.landed_cost_allocation",
            ])
        if unit_id in supplier_cost_units and unit_id in landed_cost_units:
            record["landed_cost_rate_percent"] = format(receipt.landed_cost_rate_percent, "f")
            record["inventory_value"] = _money(receipt.inventory_value)
            row_fields.extend(["landed_cost_rate_percent", "inventory_value"])
        record["fields"] = row_fields
        return record

    rows = [serialize_receipt(receipt) for receipt in selected_receipts]
    result = _base_report(
        ReportingPanel.PURCHASE_HISTORY,
        filters,
        units,
        entity,
        fields,
        rows,
        resource="synthetic_purchase_receipts",
    )
    if receipt_id is not None:
        result["receipt"] = rows[0]
    elif pagination is not None:
        result["pagination"] = pagination
    return result


def build_quote_reservations(*, actor, query_params, reservation_id=None, paginate=True):
    """Reservation details are inventory data and require the inventory panel and data scope."""
    _require_synthetic_user(actor)
    _require_panel(actor, ReportingPanel.INVENTORY)
    filters = _parse_filters(query_params)
    units, entity = _resolve_scope(actor, ReportDataCapability.INVENTORY, filters)
    queryset = QuoteReservation.objects.filter(
        is_synthetic=True,
        quote_line__is_synthetic=True,
        quote_line__quote__is_synthetic=True,
        quote_line__quote__business_unit__in=units,
        stock_reservation__is_synthetic=True,
        stock_reservation__balance__is_synthetic=True,
        stock_reservation__balance__business_unit__in=units,
        stock_reservation__balance__business_unit_id=F("quote_line__quote__business_unit_id"),
    ).select_related(
        "quote_line__variant__item__brand",
        "stock_reservation__balance__location",
    )
    queryset = _date_filter(queryset, "stock_reservation__created_at", filters["start"], filters["end"])
    if filters["location_id"] is not None:
        queryset = queryset.filter(stock_reservation__balance__location_id=filters["location_id"])
    if reservation_id is not None:
        queryset = queryset.filter(pk=reservation_id)
    queryset = queryset.order_by("-stock_reservation__created_at", "-pk")
    if reservation_id is not None:
        reservation = queryset.first()
        if reservation is None:
            raise ReportingPermissionDenied("The reservation is not available in this inventory scope.")
        selected = [reservation]
        pagination = None
    else:
        selected, pagination = _resource_page(queryset, query_params, paginate)

    fields = [
        "reservation_id",
        "status",
        "created_at",
        "item_id",
        "description",
        "brand_name",
        "warehouse_location",
        "quantity",
    ]
    rows = [
        {
            "reservation_id": row.pk,
            "status": row.stock_reservation.status,
            "created_at": row.stock_reservation.created_at.isoformat(),
            "item_id": row.quote_line.variant.item.item_code,
            "description": row.quote_line.variant.item.description,
            "brand_name": row.quote_line.variant.item.brand.name,
            "warehouse_location": row.stock_reservation.balance.location.name,
            "quantity": format(row.quantity, "f"),
        }
        for row in selected
    ]
    result = _base_report(
        ReportingPanel.INVENTORY,
        filters,
        units,
        entity,
        fields,
        rows,
        resource="synthetic_quote_reservations",
    )
    if reservation_id is not None:
        result["reservation"] = rows[0]
    elif pagination is not None:
        result["pagination"] = pagination
    return result


def build_delivery_details(*, actor, query_params, delivery_line_id=None, paginate=True):
    """Delivered sale rows use the ordinary sales field allowlist and scoped sales grants."""
    _require_synthetic_user(actor)
    _require_panel(actor, ReportingPanel.SALES)
    filters = _parse_filters(query_params)
    units, entity = _resolve_scope(actor, ReportDataCapability.SALES, filters)
    queryset = SalesReportLine.objects.filter(
        business_unit__in=units,
        is_synthetic=True,
        location__business_units__pk=F("business_unit_id"),
    ).select_related("business_unit", "location")
    queryset = _date_filter(queryset, "created_at", filters["start"], filters["end"])
    if filters["location_id"] is not None:
        queryset = queryset.filter(location_id=filters["location_id"])
    if delivery_line_id is not None:
        queryset = queryset.filter(pk=delivery_line_id)
    queryset = queryset.order_by("-created_at", "-pk")
    if delivery_line_id is not None:
        delivery = queryset.first()
        if delivery is None:
            raise ReportingPermissionDenied("The delivery detail is not available in this sales scope.")
        selected = [delivery]
        pagination = None
    else:
        selected, pagination = _resource_page(queryset, query_params, paginate)

    fields = [
        "delivery_line_id",
        "delivered_at",
        "selling_price",
        "quantity",
        "description",
        "item_id",
        "warehouse_location",
        "brand_name",
    ]
    rows = [
        {
            "delivery_line_id": row.pk,
            "delivered_at": row.created_at.isoformat(),
            "selling_price": _money(row.selling_price),
            "quantity": format(row.quantity, "f"),
            "description": row.description,
            "item_id": row.item_code,
            "warehouse_location": row.location.name,
            "brand_name": row.brand_name,
        }
        for row in selected
    ]
    result = _base_report(
        ReportingPanel.SALES,
        filters,
        units,
        entity,
        fields,
        rows,
        resource="synthetic_delivery_details",
    )
    if delivery_line_id is not None:
        result["delivery"] = rows[0]
    elif pagination is not None:
        result["pagination"] = pagination
    return result


def _transfer_history_report(user, filters):
    units, entity = _resolve_scope(user, ReportDataCapability.TRANSFER_HISTORY, filters)
    value_unit_ids = set()
    if _has_capability(user, ReportDataCapability.SUPPLIER_COST) and _has_capability(
        user, ReportDataCapability.LANDED_COST
    ):
        try:
            cost_units, _ = _resolve_scope(user, ReportDataCapability.SUPPLIER_COST, filters)
            landed_units, _ = _resolve_scope(user, ReportDataCapability.LANDED_COST, filters)
            value_unit_ids = {unit.pk for unit in cost_units}.intersection(unit.pk for unit in landed_units)
            value_unit_ids.intersection_update(unit.pk for unit in units)
        except ReportingPermissionDenied:
            value_unit_ids = set()
    can_see_value = bool(value_unit_ids)
    transfers = _date_filter(StockTransfer.objects.filter(
        source_balance__business_unit__in=units,
        is_synthetic=True,
    ), "created_at", filters["start"], filters["end"]).select_related(
        "source_balance__location",
        "source_balance__variant__item__brand",
        "destination_balance__location",
    )
    if filters["location_id"] is not None:
        transfers = transfers.filter(
            Q(source_balance__location_id=filters["location_id"])
            | Q(destination_balance__location_id=filters["location_id"])
        )
    fields = [
        "period",
        "business_unit",
        "item_id",
        "description",
        "brand_name",
        "source_location",
        "destination_location",
        "quantity",
    ]
    if can_see_value:
        fields.append("transferred_value")
    rows = []
    for transfer in transfers.order_by("created_at", "pk"):
        variant = transfer.source_balance.variant
        row = {
            "period": _period_key(_date_for(transfer.created_at), filters["period"]),
            "business_unit": transfer.source_balance.business_unit.code,
            "item_id": variant.item.item_code,
            "description": variant.item.description,
            "brand_name": variant.item.brand.name,
            "source_location": transfer.source_balance.location.name,
            "destination_location": transfer.destination_balance.location.name,
            "quantity": format(transfer.quantity, "f"),
        }
        if can_see_value:
            row["transferred_value"] = None
        if can_see_value and transfer.source_balance.business_unit_id in value_unit_ids:
            row["transferred_value"] = _money(transfer.transferred_value)
        rows.append(row)
    return _base_report(ReportingPanel.TRANSFER_HISTORY, filters, units, entity, fields, rows)


def _actual_for_budget(budget, start, end):
    period_start = max(start, budget.period_start)
    period_end_exclusive = min(end + timedelta(days=1), budget.period_end)
    if period_end_exclusive <= period_start:
        return Decimal("0.00")
    start_at, end_at = _timestamp_bounds(period_start, period_end_exclusive)
    bounds = {"created_at__gte": start_at, "created_at__lt": end_at}
    if budget.metric == ReportBudget.Metric.SALES:
        qs = SalesReportLine.objects.filter(business_unit=budget.business_unit, is_synthetic=True, **bounds)
        if budget.location_id:
            qs = qs.filter(location_id=budget.location_id)
        return sum(qs.values_list("revenue", flat=True), Decimal("0.00"))
    if budget.metric == ReportBudget.Metric.GROSS_MARGIN:
        qs = SalesReportLine.objects.filter(business_unit=budget.business_unit, is_synthetic=True, **bounds)
        if budget.location_id:
            qs = qs.filter(location_id=budget.location_id)
        return sum((line.revenue - line.cost_of_goods_sold for line in qs), Decimal("0.00"))
    if budget.metric == ReportBudget.Metric.SUPPLIER_PURCHASE:
        qs = PurchaseReceiptLine.objects.filter(receipt__business_unit=budget.business_unit, **{
            f"receipt__{key}": value for key, value in bounds.items()
        })
        if budget.location_id:
            qs = qs.filter(location_id=budget.location_id)
        return sum(qs.values_list("purchase_value", flat=True), Decimal("0.00"))
    if budget.metric == ReportBudget.Metric.LANDED_COST:
        qs = PurchaseReceiptCharge.objects.filter(
            receipt__business_unit=budget.business_unit,
            is_eligible=True,
            **{f"receipt__{key}": value for key, value in bounds.items()},
        )
        if budget.location_id:
            qs = qs.filter(receipt__lines__location_id=budget.location_id).distinct()
        return sum(qs.values_list("amount", flat=True), Decimal("0.00"))
    qs = JournalLine.objects.filter(
        entry__business_unit=budget.business_unit,
        entry__status=JournalEntry.Status.POSTED,
        entry__is_synthetic=True,
        account__account_class="expense",
        **{f"entry__{key}": value for key, value in bounds.items()},
    )
    return sum((line.debit - line.credit for line in qs), Decimal("0.00"))


def _budgets_report(user, filters):
    _require_capability(user, ReportDataCapability.BUDGETS)
    units, entity = _resolve_scope(user, ReportDataCapability.BUDGETS, filters)
    budgets = ReportBudget.objects.filter(
        business_unit__in=units,
        granularity=filters["period"],
        period_start__gte=filters["start"],
        period_start__lte=filters["end"],
        is_synthetic=True,
    ).select_related("business_unit", "location")
    if filters["location_id"] is not None:
        budgets = budgets.filter(location_id=filters["location_id"])
    metric_caps = {
        ReportBudget.Metric.SALES: (ReportDataCapability.SALES,),
        ReportBudget.Metric.SUPPLIER_PURCHASE: (ReportDataCapability.SUPPLIER_COST,),
        ReportBudget.Metric.LANDED_COST: (ReportDataCapability.LANDED_COST,),
        ReportBudget.Metric.EXPENSES: (ReportDataCapability.EXPENSES,),
        ReportBudget.Metric.GROSS_MARGIN: (
            ReportDataCapability.SALES,
            ReportDataCapability.GROSS_MARGIN,
            ReportDataCapability.SUPPLIER_COST,
        ),
    }
    rows = []
    for budget in budgets.order_by("period_start", "business_unit__code", "metric"):
        required_caps = metric_caps[budget.metric]
        if not all(_has_capability(user, cap) for cap in required_caps):
            continue
        budget_filters = dict(filters)
        budget_filters["unit_id"] = budget.business_unit_id
        budget_filters["company_id"] = None
        if budget.location_id is not None:
            budget_filters["location_id"] = budget.location_id
        try:
            for cap in required_caps:
                _resolve_scope(user, cap, budget_filters)
        except ReportingPermissionDenied:
            continue
        if budget.metric == ReportBudget.Metric.EXPENSES and budget.location_id is not None:
            raise ReportingNotConfigured(
                "Expense budgets cannot be compared by showroom until ledger entries carry location attribution."
            )
        actual = _actual_for_budget(budget, filters["start"], filters["end"])
        rows.append(
            {
                "period_start": budget.period_start.isoformat(),
                "period_end": budget.period_end.isoformat(),
                "business_unit": budget.business_unit.code,
                "location": budget.location.name if budget.location_id else "",
                "metric": budget.metric,
                "budget": _money(budget.amount),
                "actual": _money(actual),
                "variance_actual_minus_budget": _money(actual - budget.amount),
            }
        )
    return _base_report(
        ReportingPanel.BUDGETS_ACTUALS,
        filters,
        units,
        entity,
        ["period_start", "period_end", "business_unit", "location", "metric", "budget", "actual", "variance_actual_minus_budget"],
        rows,
        budgets_configured=bool(rows),
    )


def _paginate_report(report, page, page_size):
    try:
        pagination = page_metadata(len(report["rows"]), page, page_size)
    except ValueError as error:
        raise ReportingValidationError(str(error)) from error
    start = (page - 1) * page_size
    report["rows"] = report["rows"][start:start + page_size]
    report["pagination"] = pagination
    return report


def build_report(*, actor, panel, query_params, paginate=True):
    _require_synthetic_user(actor)
    _require_panel(actor, panel)
    filters = _parse_filters(query_params)
    if paginate:
        try:
            page, page_size = parse_page_parameters(query_params)
        except ValueError as error:
            raise ReportingValidationError(str(error)) from error
    else:
        page = page_size = None
    if panel in (ReportingPanel.SALES, ReportingPanel.BEST_SELLERS):
        return _sales_report(actor, panel, filters, page=page, page_size=page_size)
    if panel == ReportingPanel.COSTS_EXPENSES:
        report = _cost_expense_report(actor, filters)
    elif panel == ReportingPanel.GROSS_MARGIN:
        report = _gross_margin_report(actor, filters)
    elif panel == ReportingPanel.INVENTORY:
        report = _inventory_report(actor, filters)
    elif panel == ReportingPanel.PURCHASE_HISTORY:
        report = _purchase_history_report(actor, filters)
    elif panel == ReportingPanel.TRANSFER_HISTORY:
        report = _transfer_history_report(actor, filters)
    elif panel == ReportingPanel.BUDGETS_ACTUALS:
        report = _budgets_report(actor, filters)
    else:
        raise ReportingValidationError("Unknown report panel.")
    return _paginate_report(report, page, page_size) if paginate else report


def available_report_panels(*, actor):
    _require_synthetic_user(actor)
    active = _active_grants(ReportPanelGrant, actor)
    result = []
    for panel, label in ReportingPanel.choices:
        if active.filter(panel=panel).exists():
            result.append({"code": panel, "label": label})
    return result


def has_report_destination_access(*, actor):
    try:
        _require_synthetic_user(actor)
    except ReportingPermissionDenied:
        return False
    active_panels = set(
        _active_grants(ReportPanelGrant, actor).values_list("panel", flat=True)
    )
    required_by_panel = {
        ReportingPanel.SALES: (ReportDataCapability.SALES,),
        ReportingPanel.BEST_SELLERS: (ReportDataCapability.SALES,),
        ReportingPanel.COSTS_EXPENSES: (),
        ReportingPanel.GROSS_MARGIN: (
            ReportDataCapability.SALES,
            ReportDataCapability.GROSS_MARGIN,
            ReportDataCapability.SUPPLIER_COST,
        ),
        ReportingPanel.INVENTORY: (ReportDataCapability.INVENTORY,),
        ReportingPanel.PURCHASE_HISTORY: (ReportDataCapability.PURCHASE_HISTORY,),
        ReportingPanel.TRANSFER_HISTORY: (ReportDataCapability.TRANSFER_HISTORY,),
        ReportingPanel.BUDGETS_ACTUALS: (ReportDataCapability.BUDGETS,),
    }
    candidate_units = BusinessUnit.objects.filter(
        is_synthetic=True,
        is_active=True,
    ).order_by("code")
    for panel in active_panels:
        if panel not in ReportingPanel.values:
            continue
        required = required_by_panel[panel]
        candidates = (
            (
                ReportDataCapability.SUPPLIER_COST,
                ReportDataCapability.LANDED_COST,
                ReportDataCapability.EXPENSES,
            )
            if panel == ReportingPanel.COSTS_EXPENSES
            else required
        )
        for unit in candidate_units:
            filters = {"unit_id": unit.pk, "company_id": None, "location_id": None}
            authorized = []
            for capability in candidates:
                try:
                    _resolve_scope(actor, capability, filters)
                    authorized.append(capability)
                except ReportingPermissionDenied:
                    continue
            if panel == ReportingPanel.COSTS_EXPENSES and authorized:
                return True
            if panel != ReportingPanel.COSTS_EXPENSES and len(authorized) == len(required):
                return True
    return False


def available_report_scopes(*, actor):
    _require_synthetic_user(actor)
    scopes = {}
    for capability, _label in ReportDataCapability.choices:
        grants = _effective_data_grants(actor, capability)
        unit_ids = set(grants.filter(business_unit__isnull=False).values_list("business_unit_id", flat=True))
        company_ids = set(grants.filter(legal_entity__isnull=False).values_list("legal_entity_id", flat=True))
        units = BusinessUnit.objects.filter(
            is_synthetic=True,
            is_active=True,
        ).filter(Q(pk__in=unit_ids) | Q(legal_entity_id__in=company_ids)).order_by("code")
        unit_rows = list(units)
        companies = list(
            LegalEntity.objects.filter(pk__in=company_ids, is_synthetic=True).order_by("code")
        )
        company_units = {
            entity.pk: list(
                BusinessUnit.objects.filter(
                    legal_entity=entity,
                    is_synthetic=True,
                    is_active=True,
                ).values_list("id", flat=True)
            )
            for entity in companies
        }
        locations = Location.objects.filter(
            business_units__in=unit_rows,
            is_synthetic=True,
        ).distinct().order_by("name", "pk")
        scopes[capability] = {
            "business_units": [
                {"id": unit.pk, "code": unit.code, "name": unit.name}
                for unit in unit_rows
            ],
            "companies": [
                {
                    "id": entity.pk,
                    "code": entity.code,
                    "name": entity.name,
                    "business_unit_ids": company_units[entity.pk],
                }
                for entity in companies
            ],
            "locations": [
                {
                    "id": location.pk,
                    "name": location.name,
                    "business_units": sorted(
                        set(location.business_units.filter(pk__in=[unit.pk for unit in unit_rows]).values_list("id", flat=True))
                    ),
                }
                for location in locations
            ],
        }
    return scopes


def record_report_export(*, user, panel, file_format, report, filters, fields=None, row_count=None):
    business_unit_ids = report["scope"].get("business_unit_ids", [])
    company = report["scope"].get("company") or {}
    export = ReportExportAudit.objects.create(
        user=user,
        panel=panel,
        file_format=file_format,
        business_unit_ids=business_unit_ids,
        legal_entity_id=company.get("id"),
        location_id=report["filters"].get("location_id"),
        filters={
            "period": report["granularity"],
            "start": report["filters"]["start"],
            "end": report["filters"]["end"],
        },
        fields=fields if fields is not None else report["fields"],
        row_count=row_count if row_count is not None else len(report["rows"]),
        is_synthetic=True,
    )
    return export
