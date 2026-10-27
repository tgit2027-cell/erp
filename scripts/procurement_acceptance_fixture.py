"""Private disposable-fixture setup/probe; never usable against the shared demo."""
import hashlib
import json
import os
import secrets
from decimal import Decimal
from pathlib import Path

from django.apps import apps
from django.db import connection, transaction
from django.db.models import Sum
from foundation.models import (
    Location, PurchaseCommitmentLine, PurchaseReceipt, ReportDataCapability,
    ReportingPanel, StockBalance, StockMovement, UserActionGrant,
)
from foundation.purchasing_services import create_purchase_commitment
from foundation.test_reporting import ReportingFixtureMixin


def assert_isolated():
    expected = os.environ.get("EXPECTED_DISPOSABLE_DATA", "")
    assert expected.startswith("/tmp/tivoli-procurement-acceptance.")
    assert os.environ["APP_ENV"] == "demo"
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('data_directory'), current_setting('server_version'), inet_server_port()")
        directory, version, port = cursor.fetchone()
    assert directory == expected and version.startswith("16.15")
    assert str(port) == os.environ["DB_PORT"] and port != 55432


def probe():
    assert_isolated()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        # Includes audit/idempotency, journals, reservation and payment tables;
        # session bookkeeping is deliberately outside this business snapshot.
        facts = {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in apps.get_app_config("foundation").get_models()
        }
        digest = hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()
        progress = []
        for line in PurchaseCommitmentLine.objects.order_by("pk"):
            received = line.receipt_lines.aggregate(total=Sum("quantity"))["total"] or Decimal("0.00")
            progress.append([str(line.ordered_quantity), str(received), str(line.ordered_quantity - received)])
        return {
            "business_digest": digest,
            "receipts": PurchaseReceipt.objects.count(),
            "receipt_ids": list(PurchaseReceipt.objects.order_by("pk").values_list("pk", flat=True)),
            "movements": StockMovement.objects.count(),
            "progress": progress,
            "balances": [
                [row.variant_id, str(row.on_hand_quantity), str(row.reserved_quantity)]
                for row in StockBalance.objects.order_by("variant_id")
            ],
        }


if os.environ.get("PROCUREMENT_ACCEPTANCE_PHASE") == "prepare":
    assert_isolated()
    fixture = ReportingFixtureMixin()
    fixture.create_fixture()
    # Existing test role combination only, in the disposable cluster:
    # staff directory visibility + explicit create_order + scoped read_stock.
    fixture.sales.is_staff = True
    fixture.sales.save(update_fields=["is_staff"])
    fixture.grant_report_access(
        fixture.sales, ReportingPanel.PURCHASE_HISTORY,
        [ReportDataCapability.PURCHASE_HISTORY], unit=fixture.unit,
    )
    commitment = create_purchase_commitment(
        actor=fixture.sales, idempotency_key="TEST-ONLY-browser-commitment",
        business_unit=fixture.unit, supplier=fixture.supplier,
        lines=[
            {"variant": fixture.variant, "ordered_quantity": Decimal("10"), "unit_cost": Decimal("1000")},
            {"variant": fixture.alternate_variant, "ordered_quantity": Decimal("5"), "unit_cost": Decimal("200")},
        ],
    )
    credentials = {}
    for user in (fixture.sales, fixture.roche_user):
        password = secrets.token_urlsafe(32)
        user.set_password(password)
        user.save(update_fields=["password"])
        credentials[user.username] = password
    root = Path(os.environ["ACCEPTANCE_ROOT"])
    private = root / "browser-private.json"
    private.write_text(json.dumps({
        "credentials": credentials, "unit": fixture.unit.pk, "roche": fixture.roche.pk,
        "location": fixture.location.pk, "forbidden_location": Location.objects.get(code="SNAT").pk,
        "supplier": fixture.supplier.pk, "commitment": commitment["purchase_commitment_id"],
        "lines": [line["commitment_line_id"] for line in commitment["lines"]],
        "variants": [fixture.variant.pk, fixture.alternate_variant.pk],
    }))
    private.chmod(0o600)
    print("Verified disposable PG16.15 fixture: existing synthetic test roles, two commitment lines; no shared grants/accounts touched.")
