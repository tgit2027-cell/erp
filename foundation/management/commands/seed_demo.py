from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from foundation.location_catalog import LOCATIONS
from foundation.models import (
    ensure_currencies,
    BusinessUnit,
    BusinessUnitWorkCalendar,
    DemoUserMarker,
    Item,
    LegalEntity,
    Location,
    ProductBrand,
    StockExample,
    UserActionGrant,
    Variant,
    WorkflowPolicyConfig,
)


class Command(BaseCommand):
    help = "Create repeatable synthetic Tivoli organization, catalog, users, and read-only stock examples."

    @transaction.atomic
    def handle(self, *args, **options):
        import os

        if os.environ.get("APP_ENV") != "demo":
            raise CommandError("Refusing to seed: set APP_ENV=demo explicitly for synthetic development data.")
        ensure_currencies()

        company, _ = LegalEntity.objects.get_or_create(
            code="tivoli",
            defaults={"name": "Tivoli"},
        )
        if company.name != "Tivoli":
            raise CommandError("The existing tivoli legal entity has unexpected data; nothing was deleted.")

        unit_specs = [
            ("TVL", "Tivoli", BusinessUnit.Palette.TIVOLI),
            ("RBB", "Roche Bobois", BusinessUnit.Palette.ROCHE),
            ("NAT", "Natuzzi", BusinessUnit.Palette.NATUZZI),
        ]
        units = {}
        for code, name, theme_palette in unit_specs:
            unit, _ = BusinessUnit.objects.get_or_create(
                code=code,
                defaults={
                    "legal_entity": company,
                    "name": name,
                    "theme_palette": theme_palette,
                },
            )
            if unit.legal_entity_id != company.id or unit.name != name:
                raise CommandError(f"Existing {code} business unit has unexpected data; nothing was deleted.")
            units[code] = unit
            BusinessUnitWorkCalendar.objects.get_or_create(
                business_unit=unit,
                defaults={
                    "closed_weekdays": [4],
                    "closed_dates": [],
                    "is_configured": True,
                },
            )
            WorkflowPolicyConfig.objects.get_or_create(
                business_unit=unit,
                defaults={"storage_exception_approver_roles": []},
            )
        if LegalEntity.objects.count() != 1 or BusinessUnit.objects.count() != 3:
            raise CommandError("Expected exactly one legal entity and three business units; nothing was deleted.")

        brand_specs = [
            ("TVF", "Tivoli"),
            ("RBB", "Roche Bobois"),
            ("NAT", "Natuzzi"),
        ]
        brands = {}
        for code, name in brand_specs:
            brand, _ = ProductBrand.objects.get_or_create(code=code, defaults={"name": name})
            if brand.name != name:
                raise CommandError(f"Existing {code} brand has unexpected data; nothing was deleted.")
            brands[code] = brand

        locations = {}
        for code, name, kind, area, street, sub_location, parent_code, unit_codes in LOCATIONS:
            parent = locations.get(parent_code) if parent_code else None
            location, _ = Location.objects.get_or_create(
                code=code,
                defaults={
                    "legal_entity": company,
                    "name": name,
                    "parent": parent,
                    "kind": kind,
                    "area": area,
                    "street": street,
                    "sub_location": sub_location,
                },
            )
            if (
                location.legal_entity_id != company.id
                or location.name != name
                or location.parent_id != (parent.id if parent else None)
            ):
                raise CommandError(f"Existing location {code} has unexpected data; nothing was deleted.")
            location.business_units.add(*(units[unit_code] for unit_code in unit_codes))
            locations[code] = location

        item_specs = [
            ("TVF", "TVF-001", "Synthetic Tivoli console table", "Console / walnut", "TVF-001-WAL"),
            ("TVF", "TVF-002", "Synthetic Tivoli dining chair", "Chair / linen", "TVF-002-LIN"),
            ("RBB", "RBB-001", "Synthetic Roche Bobois lounge chair", "Chair / blue", "RBB-001-BLU"),
            ("RBB", "RBB-002", "Synthetic Roche Bobois side table", "Table / oak", "RBB-002-OAK"),
            ("NAT", "NAT-001", "Synthetic Natuzzi sofa", "Sofa / sand", "NAT-001-SND"),
            ("NAT", "NAT-002", "Synthetic Natuzzi recliner", "Recliner / charcoal", "NAT-002-CHR"),
        ]
        items = {}
        for brand_code, item_code, description, variant_desc, sku in item_specs:
            item, _ = Item.objects.get_or_create(
                item_code=item_code,
                defaults={"brand": brands[brand_code], "description": description},
            )
            if item.brand_id != brands[brand_code].id or item.description != description:
                raise CommandError(f"Existing item {item_code} has unexpected data; nothing was deleted.")
            variant, _ = Variant.objects.get_or_create(
                sku=sku,
                defaults={"item": item, "description": variant_desc},
            )
            if variant.item_id != item.id:
                raise CommandError(f"Existing variant {sku} has unexpected parent; nothing was deleted.")
            second_sku = sku + "-DEMO2"
            second_variant, _ = Variant.objects.get_or_create(
                sku=second_sku,
                defaults={"item": item, "description": variant_desc + " — alternate finish"},
            )
            if second_variant.item_id != item.id:
                raise CommandError(f"Existing variant {second_sku} has unexpected parent; nothing was deleted.")
            items[item_code] = (item, variant, second_variant, brand_code)

        stock_specs = [
            ("TVF-001", "W41-01", "TVL", "3.00"),
            ("TVF-002", "STIV-G", "TVL", "6.00"),
            ("RBB-001", "SRBB", "RBB", "2.00"),
            ("RBB-002", "W41-02", "RBB", "4.00"),
            ("NAT-001", "SNAT", "NAT", "2.00"),
            ("NAT-002", "WBA-01", "NAT", "5.00"),
        ]
        for index, (item_code, location_code, unit_code, quantity) in enumerate(stock_specs):
            item, variant, alternate_variant, _brand_code = items[item_code]
            for row_variant, count in ((variant, quantity), (alternate_variant, "1.00")):
                row, _ = StockExample.objects.get_or_create(
                    item=item,
                    variant=row_variant,
                    location=locations[location_code],
                    responsible_business_unit=units[unit_code],
                    defaults={"quantity": count},
                )

        user_specs = [
            ("demo_tivoli", "Synthetic Tivoli user", ("TVL",)),
            ("demo_roche", "Synthetic Roche Bobois user", ("RBB",)),
            ("demo_natuzzi", "Synthetic Natuzzi user", ("NAT",)),
            ("demo_multi", "Synthetic multi-unit user", ("TVL", "RBB", "NAT")),
        ]
        User = get_user_model()
        for username, full_name, unit_codes in user_specs:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": full_name.split()[1],
                    "last_name": "Demo",
                    "email": "",
                },
            )
            if not DemoUserMarker.objects.filter(user=user).exists():
                if not created:
                    raise CommandError(
                        f"Username {username} already exists without a synthetic marker; nothing was changed."
                    )
                user.set_unusable_password()
                user.save(update_fields=["password"])
                DemoUserMarker.objects.create(user=user)
            for unit_code in unit_codes:
                UserActionGrant.objects.get_or_create(
                    user=user,
                    business_unit=units[unit_code],
                    action=UserActionGrant.Action.READ_STOCK,
                )

        admin, created = User.objects.get_or_create(
            username="demo_admin",
            defaults={
                "first_name": "Synthetic",
                "last_name": "Demo Admin",
                "email": "",
                "is_staff": True,
            },
        )
        if created:
            admin.set_unusable_password()
            admin.save(update_fields=["password"])
            DemoUserMarker.objects.create(user=admin)
        elif not DemoUserMarker.objects.filter(user=admin).exists():
            raise CommandError(
                "Username demo_admin already exists without a synthetic marker; nothing was changed."
            )
        elif not admin.is_staff or admin.is_superuser:
            raise CommandError(
                "Existing synthetic demo_admin has unexpected staff status; nothing was changed."
            )

        expected_admin_grants = {
            (unit_code, UserActionGrant.Action.READ_STOCK)
            for unit_code in ("TVL", "RBB", "NAT")
        }
        for unit_code, action in expected_admin_grants:
            UserActionGrant.objects.get_or_create(
                user=admin,
                business_unit=units[unit_code],
                action=action,
            )
        actual_admin_grants = set(
            UserActionGrant.objects.filter(user=admin).values_list(
                "business_unit__code",
                "action",
            )
        )
        if actual_admin_grants != expected_admin_grants:
            raise CommandError(
                "Existing synthetic demo_admin has unexpected grants; nothing was changed."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Synthetic seed ready: {LegalEntity.objects.count()} company, "
                f"{BusinessUnit.objects.count()} units, {ProductBrand.objects.count()} brands, "
                f"{Item.objects.count()} items, {Variant.objects.count()} variants, "
                f"{Location.objects.count()} locations, {StockExample.objects.count()} stock examples."
            )
        )
