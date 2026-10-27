"""Disposable Retail acceptance data and probes; refuses the shared demo."""
import hashlib
import json
import os
import secrets
from decimal import Decimal
from pathlib import Path

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.utils import timezone
from foundation.models import (
    ApprovedSellingPrice, QuotePayment, QuoteReservation, SalesQuote,
    StockBalance, UserActionGrant, BusinessUnitRoleAssignment, QuoteApproval,
    SalesReportLine, StockMovement, JournalEntry, BusinessUnit, Variant,
)
from foundation.purchasing_services import configure_selling_price
from foundation.test_purchasing_workflows import PurchasingFixtureMixin, PurchasingWorkflowTests


def assert_isolated():
    expected = os.environ.get("EXPECTED_DISPOSABLE_DATA", "")
    assert expected.startswith("/tmp/tivoli-retail-acceptance.")
    assert os.environ["APP_ENV"] == "demo"
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('data_directory'), current_setting('server_version'), inet_server_port()")
        directory, version, port = cursor.fetchone()
    assert directory == expected and version.startswith("16.15")
    assert str(port) == os.environ["DB_PORT"] == "55165"


def probe():
    assert_isolated()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        facts = {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in apps.get_app_config("foundation").get_models()
        }
        return {
            "business_digest": hashlib.sha256(
                json.dumps(facts, sort_keys=True, default=str).encode()
            ).hexdigest(),
            "quotes": list(SalesQuote.objects.order_by("pk").values(
                "pk", "kind", "status", "total_quote_value", "ready_at", "delivered_at",
            )),
            "payments": QuotePayment.objects.count(),
            "reservations": QuoteReservation.objects.count(),
            "balances": list(StockBalance.objects.order_by("pk").values(
                "on_hand_quantity", "reserved_quantity",
            )),
            "prices": list(ApprovedSellingPrice.objects.order_by("pk").values_list("amount", flat=True)),
            "approvals": list(QuoteApproval.objects.order_by("pk").values("quote_id", "kind", "role")),
            "deliveries": list(SalesReportLine.objects.order_by("pk").values("quote_line__quote_id", "quantity")),
            "delivery_movements": list(StockMovement.objects.filter(kind="delivery").order_by("pk").values("quantity_delta")),
            "journal_count": JournalEntry.objects.count(),
            "stock_buckets": list(StockBalance.objects.order_by("pk").values(
                "business_unit_id", "variant_id", "location_id",
                "on_hand_quantity", "reserved_quantity",
            )),
            "reservation_lines": list(QuoteReservation.objects.order_by("pk").values(
                "quote_line__quote_id", "quote_line__variant_id",
                "stock_reservation__quantity", "stock_reservation__status",
            )),
            "delivery_lines": list(SalesReportLine.objects.order_by("pk").values(
                "quote_line__quote_id", "quote_line__variant_id", "quantity",
            )),
        }


def control(phase):
    """Authority changes are fixture operations, never API/new shared powers."""
    assert_isolated()
    user = get_user_model().objects.get(username="demo_multi")
    if phase.startswith("registers"):
        from foundation.models import Location, ReportPanelGrant, ReportingPanel, ReportDataCapability
        from foundation.test_reporting import ReportingFixtureMixin
        unit = BusinessUnit.objects.get(code="TVL")
        a = fixture_location()
        b = Location.objects.get(code="W41-01")
        if phase == "registers":
            a.business_units.add(unit)
            fixture = ReportingFixtureMixin()
            # Reuse prepared fixture objects; never reseed a running test session.
            for name, value in dict(
                unit=unit, sales=user, admin=get_user_model().objects.get(username="demo_admin"),
                location=a, other_location=b,
                variant=Variant.objects.get(sku="TVF-002-LIN"),
            ).items():
                setattr(fixture, name, value)
            from foundation.transaction_services import transfer_stock
            transfer_stock(
                idempotency_key="TEST-register-browser-transfer", business_unit=unit,
                variant=fixture.variant, source_location=a, destination_location=b,
                quantity="2.25",
            )
            for panel, capability in (
                (ReportingPanel.INVENTORY, ReportDataCapability.INVENTORY),
                (ReportingPanel.TRANSFER_HISTORY, ReportDataCapability.TRANSFER_HISTORY),
            ):
                fixture.grant_report_access(user, panel, [capability], unit=unit)
            user.is_staff = False
            user.save(update_fields=["is_staff"])
            private = Path(os.environ["ACCEPTANCE_ROOT"]) / "browser-private.json"
            values = json.loads(private.read_text())
            values["register_locations"] = [a.pk, b.pk]
            private.write_text(json.dumps(values))
        elif phase == "registers-remove-location":
            a.business_units.remove(unit)
        elif phase == "registers-revoke-panel":
            ReportPanelGrant.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())
        else:
            raise ValueError("Unknown isolated register phase")
        return
    if phase.startswith("capacity"):
        from foundation.models import LocationCapacity, LocationCapacityGrant
        from foundation.warehouse_capacity_services import assign_capacity, revoke_capacity_assignment, set_location_capacity
        if phase == "capacity":
            from foundation.test_warehouse_capacity import WarehouseCapacityAuthorizationTests
            if not get_user_model().objects.filter(username="capacity_system_admin_test").exists():
                WarehouseCapacityAuthorizationTests.setUpTestData()
            admin = get_user_model().objects.get(username="capacity_system_admin_test")
            user.is_staff = False
            user.save(update_fields=["is_staff"])
            for code, total in (("W41-01", "125.250000"), ("W41-02", "222.500000")):
                profile = LocationCapacity.objects.get(location__code=code)
                for capability in ("view_capacity", "edit_capacity"):
                    grant = LocationCapacityGrant.objects.filter(
                        user=user, location=profile, capability=capability, revoked_at__isnull=True,
                    ).first() or assign_capacity(actor=admin, user_id=user.pk, location_id=profile.location_id, capability=capability)
                set_location_capacity(actor=user, location_id=profile.location_id, payload={
                    "total_floor_area_m2": total, "allocated_storage_floor_area_m2": "80.125000",
                    "received_goods_volume_m3": "14.750000",
                })
                if code.endswith("2"):
                    revoke_capacity_assignment(actor=admin, grant_id=grant.pk)
            private = Path(os.environ["ACCEPTANCE_ROOT"]) / "browser-private.json"
            data = json.loads(private.read_text())
            data["capacity_locations"] = list(LocationCapacity.objects.filter(
                location__code__in=["W41-01", "W41-02"],
            ).order_by("location__code").values_list("location_id", flat=True))
            private.write_text(json.dumps(data))
        else:
            grants = LocationCapacityGrant.objects.filter(
                user=user, location__location__code="W41-01", revoked_at__isnull=True,
            )
            if phase == "capacity-edit-revoke":
                grants = grants.filter(capability="edit_capacity")
            elif phase != "capacity-view-revoke":
                raise ValueError("Unknown isolated capacity control")
            admin = get_user_model().objects.get(username="capacity_system_admin_test")
            for grant in grants:
                revoke_capacity_assignment(actor=admin, grant_id=grant.pk)
    elif phase in ("multiline", "quotation", "journey"):
        # Restore this disposable persona's original authority after other
        # acceptance cases revoke it; do not add grants or touch shared data.
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        unit = BusinessUnit.objects.get(code="TVL")
        UserActionGrant.objects.filter(user=user, business_unit=unit).update(revoked_at=None)
        admin = get_user_model().objects.get(username="demo_admin")
        for sku, amount in (("TVF-002-LIN", "19.99"), ("TVF-002-LIN-DEMO2", "10.05")):
            configure_selling_price(
                actor=admin, business_unit=unit, variant=Variant.objects.get(sku=sku),
                amount=Decimal(amount),
            )
        if phase in ("quotation", "journey"):
            # This fixture is disposable and already has these fictional
            # identities/authority. No shared account or assignment is changed.
            user.is_staff = False
            user.save(update_fields=["is_staff"])
            variant = Variant.objects.select_related("item").get(sku="TVF-002-LIN")
            variant.item.name_en = "TEST ONLY bilingual chair"
            variant.item.name_ar = "كرسي تجريبي"
            variant.item.description_ar = "وصف تجريبي"
            variant.item.category_ar = "فئة تجريبية"
            # A generated pixel served only by the isolated browser build;
            # this proves image decoding without copying real product photos.
            variant.item.image_path = "/assets/test-only-chair.png"
            variant.item.save()
            from foundation.models import SyntheticCounterparty
            SyntheticCounterparty.objects.filter(code="TEST-CUST-RETAIL-01").update(
                name_ar="عميل تجريبي", contact_en="TEST ONLY contact", contact_ar="جهة اتصال تجريبية",
            )
        if phase == "journey":
            # Existing receipt service, once in this disposable cluster only.
            # The second quote line needs real stock, not illustrative examples.
            from foundation.purchasing_services import receive_imported_purchase
            receive_imported_purchase(
                actor=user, idempotency_key="TEST-ONLY-multiline-journey-second-stock",
                business_unit=unit,
                supplier=SyntheticCounterparty.objects.get(code="TEST-SUP-IMPORT-01"),
                lines=[{
                    "variant": Variant.objects.get(sku="TVF-002-LIN-DEMO2"),
                    "location": fixture_location(),
                    "quantity": Decimal("10.00"), "unit_cost": Decimal("5.00"),
                }], charges=[],
            )
    elif phase in ("ordinary", "staff"):
        user.is_staff = phase == "staff"
        user.save(update_fields=["is_staff"])
    elif phase == "revoke":
        UserActionGrant.objects.filter(
            user=user, business_unit__code="TVL", revoked_at__isnull=True,
        ).update(revoked_at=timezone.now())
    elif phase == "downgrade-manager-role":
        # Lower this existing fictional assignment to the existing salesperson
        # role. Do not remove the final read prerequisite or add any assignment.
        BusinessUnitRoleAssignment.objects.filter(
            user__username="synthetic_manager", business_unit__code="TVL",
            role=BusinessUnitRoleAssignment.Role.MANAGER, revoked_at__isnull=True,
        ).update(role=BusinessUnitRoleAssignment.Role.SALESPERSON)
    else:
        raise ValueError("Unknown isolated fixture control")


def fixture_location():
    from foundation.models import Location
    return Location.objects.get(code="STIV-G")


if os.environ.get("RETAIL_ACCEPTANCE_PHASE") == "prepare":
    assert_isolated()
    fixture = PurchasingFixtureMixin()
    fixture.create_fixture()
    fixture.sales.is_staff = True
    fixture.sales.save(update_fields=["is_staff"])
    fixture.make_receipt(key="TEST-ONLY-retail-opening-stock")
    manager = PurchasingWorkflowTests.make_role_user(
        fixture, "synthetic_manager", BusinessUnitRoleAssignment.Role.MANAGER,
    )
    # Existing staff configuration gate, isolated persona only; no shared assignment.
    manager.is_staff = True
    manager.save(update_fields=["is_staff"])
    credentials = {}
    for user in (fixture.sales, fixture.roche_user, manager):
        password = secrets.token_urlsafe(32)
        user.set_password(password)
        user.save(update_fields=["password"])
        credentials[user.username] = password
    private = Path(os.environ["ACCEPTANCE_ROOT"]) / "browser-private.json"
    private.write_text(json.dumps({
        "credentials": credentials, "unit": fixture.unit.pk, "roche": fixture.roche.pk,
        "customer": fixture.customer.pk, "variant": fixture.variant.pk,
        "alternate_variant": fixture.alternate_variant.pk,
        "location": fixture.location.pk, "supplier": fixture.supplier.pk,
        "manager": manager.username,
    }))
    private.chmod(0o600)
    print("Verified disposable Retail PG16.15 fixture; temporary test authority only, no shared changes.")
