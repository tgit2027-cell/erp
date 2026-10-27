import importlib
import json
import os
import tempfile
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, TransactionTestCase, override_settings

from .location_catalog import LOCATIONS
from .models import BusinessUnit, LegalEntity, Location, StockBalance, StockExample, Variant
from .transaction_services import InvalidTransaction, ReceiptLine, receive_stock, transfer_stock

migration = importlib.import_module("foundation.migrations.0037_warehouse_location_catalog")


@override_settings(APP_ENV="demo")
class LocationCatalogTests(TestCase):
    def seed(self):
        with patch.dict(os.environ, {"APP_ENV": "demo"}):
            call_command("seed_demo", stdout=StringIO())

    def test_seed_creates_the_owner_list_with_its_details(self):
        self.seed()
        self.assertEqual(sorted(Location.objects.values_list("code", flat=True)), sorted(row[0] for row in LOCATIONS))
        mezzanine = Location.objects.get(code="STIV-M")
        self.assertEqual(
            (mezzanine.parent.code, mezzanine.kind, mezzanine.area, mezzanine.sub_location),
            ("STIV", "showroom", "Al Rayyan Building", "Tivoli Showroom - Mezzanine Floor"),
        )
        self.assertEqual(list(Location.objects.get(code="SRBB").business_units.values_list("code", flat=True)), ["RBB"])
        self.assertEqual(Location.objects.get(code="W41-03.2").business_units.count(), 3)
        self.assertEqual(
            sorted(Location.objects.filter(kind="virtual").values_list("code", flat=True)), ["TFRW", "VTIV", "XX"],
        )
        self.assertEqual(migration.LOCATIONS, LOCATIONS, "The migration snapshot matches the catalog")

    def test_codes_allow_dots_but_not_spaces(self):
        self.seed()
        company = LegalEntity.objects.get(code="tivoli")
        with self.assertRaises(ValidationError):
            Location.objects.create(legal_entity=company, code="W41 07", name="TEST ONLY bad code")
        Location.objects.create(legal_entity=company, code="W41-07.1", name="TEST ONLY good code")

    def test_migration_renames_old_locations_and_keeps_their_stock(self):
        self.seed()
        # Recreate the earlier layout: rename back, and add the two locations the new list drops.
        reverse = {new: old for old, new in migration.RENAMED.items()}
        for new, old in reverse.items():
            Location.objects.filter(code=new).update(code=old)
        stock = StockExample.objects.select_related("location").get(location__code="tivoli-ground-floor", variant__sku="TVF-002-LIN")
        company = LegalEntity.objects.get(code="tivoli")
        street = Location.objects.create(legal_entity=company, code="street-41", name="Street 41")
        Location.objects.filter(code__in=["street-41-building-1", "street-41-building-2"]).update(parent=street)
        right = Location.objects.create(legal_entity=company, code="tivoli-mezzanine-right", name="Mezzanine — right")
        right.business_units.add(BusinessUnit.objects.get(code="TVL"))
        held = StockExample.objects.create(
            item=stock.item, variant=stock.variant, location=right,
            responsible_business_unit=stock.responsible_business_unit, quantity="1.00",
        )

        migration.adopt_catalog(apps, None)

        stock.refresh_from_db()
        self.assertEqual(stock.location.code, "STIV-G", "Stock stays on the same location, now coded STIV-G")
        self.assertFalse(Location.objects.filter(code="street-41").exists(), "An unused grouping is removed")
        self.assertIsNone(Location.objects.get(code="W41-01").parent)
        held.refresh_from_db()
        self.assertEqual(held.location.code, "RETIRED-TIVOLI-MEZZANINE-RIGHT")
        self.assertEqual(held.location.business_units.count(), 0, "A retired location with no stock is offered nowhere")
        self.assertFalse(held.location.is_active)
        codes = set(Location.objects.exclude(code__startswith="RETIRED-").values_list("code", flat=True))
        self.assertEqual(codes, {row[0] for row in LOCATIONS})
        self.assertEqual(Location.objects.get(code="W41-03.1").name, "Industrial Area, Street 41 warehouse - Building 03 - Section 01")



@override_settings(APP_ENV="demo")
class LocationMigrationSafetyTests(TransactionTestCase):
    """The report reads a committed snapshot, so these run outside a test transaction."""

    seed = LocationCatalogTests.seed

    def receive(self, location, key, quantity="3.00"):
        unit = BusinessUnit.objects.get(code="TVL")
        variant = Variant.objects.get(sku="TVF-002-LIN")
        receive_stock(idempotency_key=key, business_unit=unit, eligible_landed_charges="0.00",
                      lines=[ReceiptLine(variant=variant, location=location, quantity=Decimal(quantity), unit_cost=Decimal("100.00"))])
        return unit, variant

    def test_virtual_locations_take_no_receipts_and_retired_stock_only_leaves_by_transfer(self):
        self.seed()
        with self.assertRaisesMessage(InvalidTransaction, "XX is a virtual location and cannot be used for receipts"):
            self.receive(Location.objects.get(code="XX"), "into-xx")
        company = LegalEntity.objects.get(code="tivoli")
        right = Location.objects.create(legal_entity=company, code="tivoli-mezzanine-right", name="Mezzanine — right")
        right.business_units.add(BusinessUnit.objects.get(code="TVL"))
        unit, variant = self.receive(right, "before-retiring")
        with tempfile.NamedTemporaryFile("w+", suffix=".json") as before:
            call_command("location_stock_report", output=before.name, stdout=StringIO())
            migration.adopt_catalog(apps, None)
            after = StringIO()
            call_command("location_stock_report", compare=before.name, stdout=after)
        report = json.loads(after.getvalue())
        self.assertEqual(report["differences"], [], "Stock, value and history are unchanged by the migration")
        self.assertEqual(report["retired_locations_holding_stock"], ["RETIRED-TIVOLI-MEZZANINE-RIGHT"])
        right.refresh_from_db()
        self.assertEqual((right.is_active, list(right.business_units.values_list("code", flat=True))), (False, ["TVL"]))
        with self.assertRaisesMessage(InvalidTransaction, "is retired and cannot be used for receipts"):
            self.receive(right, "after-retiring")
        ground = Location.objects.get(code="STIV-G")
        with self.assertRaisesMessage(InvalidTransaction, "is retired and cannot receive stock"):
            transfer_stock(idempotency_key="back-in", business_unit=unit, variant=variant, source_location=ground,
                           destination_location=right, quantity="1.00")
        transfer_stock(idempotency_key="out", business_unit=unit, variant=variant, source_location=right,
                       destination_location=ground, quantity="3.00")
        self.assertEqual(StockBalance.objects.get(location=right, variant=variant).on_hand_quantity, Decimal("0.00"))

    def test_report_fails_when_stock_changed(self):
        self.seed()
        ground = Location.objects.get(code="STIV-G")
        with tempfile.NamedTemporaryFile("w+", suffix=".json") as before:
            call_command("location_stock_report", output=before.name, stdout=StringIO())
            self.receive(ground, "changes-stock", quantity="1.00")
            with self.assertRaisesMessage(CommandError, "does not reconcile"):
                call_command("location_stock_report", compare=before.name, stdout=StringIO())
