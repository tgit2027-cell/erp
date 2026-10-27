from datetime import datetime, time, timedelta
from decimal import Decimal
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from foundation.models import (
    BusinessUnit,
    DemoReportSalesFact,
    DemoUserMarker,
    Item,
    Location,
    ReportBudget,
    ReportDataCapability,
    ReportDataGrant,
    ReportPanelGrant,
    ReportingPanel,
    ReportingRole,
    ReportingRoleMembership,
)


FIXTURE_PREFIX = "report-review-v1:"

UNIT_FIXTURES = {
    "TVL": {
        "user": "demo_tivoli",
        "item": "TVF-002",
        "location": "STIV-G",
        "price": Decimal("3200.00"),
        "category": "TEST ONLY Dining",
    },
    "RBB": {
        "user": "demo_roche",
        "item": "RBB-001",
        "location": "SRBB",
        "price": Decimal("8400.00"),
        "category": "TEST ONLY Seating",
    },
    "NAT": {
        "user": "demo_natuzzi",
        "item": "NAT-001",
        "location": "SNAT",
        "price": Decimal("5600.00"),
        "category": "TEST ONLY Upholstery",
    },
}


class Command(BaseCommand):
    help = (
        "Refresh or reset only the owner-controlled synthetic reporting review fixture. "
        "It never changes passwords or posted transactions."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Remove only records tagged by this reporting fixture; retain users and passwords.",
        )

    def handle(self, *args, **options):
        self._require_local_demo_database()
        with transaction.atomic():
            counts = self._clear_fixture(delete_unused_roles=options["reset"])
            if options["reset"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        "Synthetic reporting review fixture reset. Demo users, passwords, manual grants, "
                        "and export audit records were retained."
                    )
                )
                return
            loaded = self._load_fixture()
        self.stdout.write(
            self.style.SUCCESS(
                "Synthetic reporting review fixture refreshed: "
                f"{loaded['facts']} report-only sales facts, {loaded['budgets']} sales budgets, "
                f"{loaded['roles']} roles and {loaded['grants']} panel/data grants. "
                "No passwords or posted transactions were changed."
            )
        )

    @staticmethod
    def _require_local_demo_database():
        if os.environ.get("APP_ENV") != "demo":
            raise CommandError("Refusing to run unless APP_ENV=demo is explicitly set.")
        if connection.vendor != "postgresql":
            raise CommandError("The reporting review fixture requires the local PostgreSQL demo database.")
        db = connection.settings_dict
        if db.get("NAME") not in {"tivoli_erp_demo", "test_tivoli_erp_demo"}:
            raise CommandError("Refusing to seed an unrecognized database; expected tivoli_erp_demo.")
        if db.get("HOST", "") not in {"", "localhost", "127.0.0.1"}:
            raise CommandError("Refusing to seed a non-local database.")

    def _clear_fixture(self, *, delete_unused_roles):
        models = (
            DemoReportSalesFact,
            ReportBudget,
            ReportPanelGrant,
            ReportDataGrant,
            ReportingRoleMembership,
        )
        counts = {}
        for model in models:
            deleted, _details = model.objects.filter(
                demo_fixture_key__startswith=FIXTURE_PREFIX
            ).delete()
            counts[model.__name__] = deleted
        if delete_unused_roles:
            roles = ReportingRole.objects.filter(demo_fixture_key__startswith=FIXTURE_PREFIX)
            for role in roles:
                if role.panel_grants.exists() or role.data_grants.exists() or role.memberships.exists():
                    continue
                role.delete()
        return counts

    def _load_fixture(self):
        User = get_user_model()
        admin = User.objects.filter(username="demo_admin").first()
        if (
            admin is None
            or not DemoUserMarker.objects.filter(user=admin, is_synthetic=True).exists()
            or not admin.is_staff
            or admin.is_superuser
        ):
            raise CommandError("Run the existing synthetic seed first; a valid demo_admin is required.")

        units = {
            unit.code: unit
            for unit in BusinessUnit.objects.filter(
                code__in=UNIT_FIXTURES,
                is_active=True,
                is_synthetic=True,
            ).select_related("legal_entity")
        }
        if set(units) != set(UNIT_FIXTURES):
            raise CommandError("The three synthetic business units are required; run seed_demo first.")

        users = {}
        items = {}
        locations = {}
        for unit_code, spec in UNIT_FIXTURES.items():
            user = User.objects.filter(username=spec["user"]).first()
            if (
                user is None
                or not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists()
                or user.is_staff
                or user.is_superuser
            ):
                raise CommandError(f"The synthetic sales account for {unit_code} is unavailable.")
            item = Item.objects.filter(
                item_code=spec["item"],
                brand__code={"TVL": "TVF", "RBB": "RBB", "NAT": "NAT"}[unit_code],
                is_synthetic=True,
            ).select_related("brand").first()
            location = Location.objects.filter(
                code=spec["location"],
                is_synthetic=True,
                legal_entity_id=units[unit_code].legal_entity_id,
                business_units=units[unit_code],
            ).first()
            if item is None or location is None:
                raise CommandError(f"Synthetic catalog/location fixture is incomplete for {unit_code}.")
            users[unit_code] = user
            items[unit_code] = item
            locations[unit_code] = location

        manager_role = self._ensure_role(
            admin,
            code="test-report-managing-partner",
            name="TEST ONLY Synthetic Managing Partner — Report Panel Manager",
            can_manage=True,
            fixture_key=self._key("role", "managing-partner"),
        )
        self._ensure_membership(
            admin,
            manager_role,
            admin,
            self._key("membership", "managing-partner"),
        )
        sales_roles = {}
        for unit_code, spec in UNIT_FIXTURES.items():
            sales_roles[unit_code] = self._ensure_role(
                admin,
                code=f"test-report-sales-{unit_code.lower()}",
                name=f"TEST ONLY {unit_code} Business-Unit Sales",
                can_manage=False,
                fixture_key=self._key("role", "sales", unit_code),
            )
            self._ensure_membership(
                admin,
                sales_roles[unit_code],
                users[unit_code],
                self._key("membership", "sales", unit_code),
            )

        panel_grants = 0
        for panel in (
            ReportingPanel.SALES,
            ReportingPanel.BEST_SELLERS,
            ReportingPanel.BUDGETS_ACTUALS,
        ):
            if self._ensure_panel_grant(
                admin,
                role=manager_role,
                user=None,
                panel=panel,
                fixture_key=self._key("panel", "manager", panel),
            ):
                panel_grants += 1
        for unit_code, role in sales_roles.items():
            for panel in (ReportingPanel.SALES, ReportingPanel.BEST_SELLERS):
                if self._ensure_panel_grant(
                    admin,
                    role=role,
                    user=None,
                    panel=panel,
                    fixture_key=self._key("panel", "sales", unit_code, panel),
                ):
                    panel_grants += 1

        data_grants = 0
        for unit_code, unit in units.items():
            for capability in (ReportDataCapability.SALES, ReportDataCapability.BUDGETS):
                if self._ensure_data_grant(
                    admin,
                    role=manager_role,
                    capability=capability,
                    unit=unit,
                    fixture_key=self._key("data", "manager", capability, unit_code),
                ):
                    data_grants += 1
            if self._ensure_data_grant(
                admin,
                role=sales_roles[unit_code],
                capability=ReportDataCapability.SALES,
                unit=unit,
                fixture_key=self._key("data", "sales", unit_code, ReportDataCapability.SALES),
            ):
                data_grants += 1

        self._create_sales_facts(units, items, locations)
        budget_count = self._create_sales_budgets(admin, units)
        return {
            "facts": DemoReportSalesFact.objects.filter(
                demo_fixture_key__startswith=FIXTURE_PREFIX
            ).count(),
            "budgets": budget_count,
            "roles": len(sales_roles) + 1,
            "grants": panel_grants + data_grants,
        }

    @staticmethod
    def _key(*parts):
        return FIXTURE_PREFIX + ":".join(str(part) for part in parts)

    @staticmethod
    def _ensure_role(actor, *, code, name, can_manage, fixture_key):
        role = ReportingRole.objects.filter(code=code).first()
        if role is None:
            return ReportingRole.objects.create(
                code=code,
                name=name,
                can_manage_report_panels=can_manage,
                demo_fixture_key=fixture_key,
                created_by=actor,
                is_synthetic=True,
            )
        if (
            not role.is_synthetic
            or role.name != name
            or role.can_manage_report_panels != can_manage
            or role.demo_fixture_key != fixture_key
        ):
            raise CommandError(f"Report-role code {code} exists outside the expected fixture; nothing was changed.")
        return role

    @staticmethod
    def _ensure_membership(actor, role, user, fixture_key):
        membership = ReportingRoleMembership.objects.filter(demo_fixture_key=fixture_key).first()
        if membership:
            if membership.role_id != role.pk or membership.user_id != user.pk:
                raise CommandError("A tagged reporting membership has unexpected ownership.")
            if membership.revoked_at is not None:
                membership.revoked_at = None
                membership.save(update_fields=["revoked_at"])
            return False
        if ReportingRoleMembership.objects.filter(
            role=role,
            user=user,
            revoked_at__isnull=True,
        ).exists():
            return False
        ReportingRoleMembership.objects.create(
            role=role,
            user=user,
            assigned_by=actor,
            demo_fixture_key=fixture_key,
            is_synthetic=True,
        )
        return True

    @staticmethod
    def _ensure_panel_grant(actor, *, role, user, panel, fixture_key):
        grant = ReportPanelGrant.objects.filter(demo_fixture_key=fixture_key).first()
        if grant:
            if grant.role_id != getattr(role, "pk", None) or grant.user_id != getattr(user, "pk", None) or grant.panel != panel:
                raise CommandError("A tagged report-panel grant has unexpected ownership.")
            if grant.revoked_at is not None:
                grant.revoked_at = None
                grant.save(update_fields=["revoked_at"])
            return False
        existing = ReportPanelGrant.objects.filter(
            role=role,
            user=user,
            panel=panel,
            revoked_at__isnull=True,
        ).exists()
        if existing:
            return False
        ReportPanelGrant.objects.create(
            role=role,
            user=user,
            panel=panel,
            granted_by=actor,
            demo_fixture_key=fixture_key,
            is_synthetic=True,
        )
        return True

    @staticmethod
    def _ensure_data_grant(actor, *, role, capability, unit, fixture_key):
        grant = ReportDataGrant.objects.filter(demo_fixture_key=fixture_key).first()
        if grant:
            if (
                grant.role_id != role.pk
                or grant.capability != capability
                or grant.business_unit_id != unit.pk
            ):
                raise CommandError("A tagged report-data grant has unexpected ownership.")
            if grant.revoked_at is not None:
                grant.revoked_at = None
                grant.save(update_fields=["revoked_at"])
            return False
        if ReportDataGrant.objects.filter(
            role=role,
            capability=capability,
            business_unit=unit,
            revoked_at__isnull=True,
        ).exists():
            return False
        ReportDataGrant.objects.create(
            role=role,
            capability=capability,
            business_unit=unit,
            granted_by=actor,
            demo_fixture_key=fixture_key,
            is_synthetic=True,
        )
        return True

    def _create_sales_facts(self, units, items, locations):
        today = timezone.localdate()
        month_start = today.replace(day=1)
        quarter_start = datetime(today.year, ((today.month - 1) // 3) * 3 + 1, 1).date()
        event_dates = (
            ("today", today),
            ("recent", today - timedelta(days=5)),
            ("month-edge", month_start - timedelta(days=1)),
            ("quarter-edge", quarter_start - timedelta(days=1)),
            ("older-quarter", quarter_start - timedelta(days=45)),
        )
        for unit_code, spec in UNIT_FIXTURES.items():
            for index, (event, event_date) in enumerate(event_dates):
                quantity = Decimal(1 + (index % 3))
                selling_price = spec["price"] + Decimal(index * 125)
                DemoReportSalesFact.objects.create(
                    demo_fixture_key=self._key("fact", unit_code, event),
                    business_unit=units[unit_code],
                    location=locations[unit_code],
                    item=items[unit_code],
                    category_label=spec["category"],
                    quantity=quantity,
                    selling_price=selling_price,
                    revenue=(quantity * selling_price).quantize(Decimal("0.01")),
                    created_at=timezone.make_aware(
                        datetime.combine(event_date, time(hour=9 + index)),
                        timezone.get_current_timezone(),
                    ),
                    is_synthetic=True,
                )

    def _create_sales_budgets(self, actor, units):
        today = timezone.localdate()
        month_start = today.replace(day=1)
        if month_start.month == 12:
            next_month_start = month_start.replace(year=month_start.year + 1, month=1)
        else:
            next_month_start = month_start.replace(month=month_start.month + 1)
        quarter_month = ((today.month - 1) // 3) * 3 + 1
        quarter_start = today.replace(month=quarter_month, day=1)
        if quarter_month == 10:
            next_quarter_start = quarter_start.replace(year=quarter_start.year + 1, month=1)
        else:
            next_quarter_start = quarter_start.replace(month=quarter_month + 3)

        daily_period = (today, today + timedelta(days=1), ReportBudget.Granularity.DAILY)
        monthly_period = (month_start, next_month_start, ReportBudget.Granularity.MONTHLY)
        quarterly_period = (quarter_start, next_quarter_start, ReportBudget.Granularity.QUARTERLY)
        budget_multipliers = {
            "TVL": Decimal("5"),
            "RBB": Decimal("6"),
            "NAT": Decimal("4"),
        }
        for unit_code, unit in units.items():
            base = UNIT_FIXTURES[unit_code]["price"]
            for granularity_index, (period_start, period_end, granularity) in enumerate(
                (daily_period, monthly_period, quarterly_period),
                start=1,
            ):
                fixture_key = self._key("budget", unit_code, granularity)
                existing = ReportBudget.objects.filter(demo_fixture_key=fixture_key).first()
                if existing:
                    if (
                        existing.business_unit_id != unit.pk
                        or existing.metric != ReportBudget.Metric.SALES
                        or existing.granularity != granularity
                        or existing.period_start != period_start
                        or existing.period_end != period_end
                    ):
                        raise CommandError("A tagged synthetic budget has unexpected ownership.")
                    continue
                collision = ReportBudget.objects.filter(
                    business_unit=unit,
                    location__isnull=True,
                    metric=ReportBudget.Metric.SALES,
                    granularity=granularity,
                    period_start=period_start,
                    period_end=period_end,
                ).exists()
                if collision:
                    raise CommandError(
                        f"A manually configured sales budget already exists for {unit_code} {granularity}; "
                        "the fixture will not overwrite it."
                    )
                amount = (base * budget_multipliers[unit_code] * Decimal(granularity_index)).quantize(
                    Decimal("0.01")
                )
                ReportBudget.objects.create(
                    business_unit=unit,
                    metric=ReportBudget.Metric.SALES,
                    granularity=granularity,
                    period_start=period_start,
                    period_end=period_end,
                    amount=amount,
                    created_by=actor,
                    updated_by=actor,
                    demo_fixture_key=fixture_key,
                    is_synthetic=True,
                )
        return ReportBudget.objects.filter(demo_fixture_key__startswith=FIXTURE_PREFIX).count()
