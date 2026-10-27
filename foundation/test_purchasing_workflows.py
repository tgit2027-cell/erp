from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from decimal import Decimal
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connections
from django.db.models import Sum
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    ApprovedSellingPrice,
    BusinessUnit,
    BusinessUnitRoleAssignment,
    BusinessUnitWorkCalendar,
    DemoUserMarker,
    IdempotencyRecord,
    JournalEntry,
    JournalLine,
    Location,
    PurchaseReceipt,
    PurchaseCommitment,
    PurchaseCommitmentLine,
    PurchaseReceiptLine,
    ProductBrand,
    QuoteApproval,
    QuotePayment,
    QuoteReservation,
    SalesQuote,
    SalesQuoteLine,
    SalesReportLine,
    SpecialOrderValueBand,
    StockBalance,
    StockMovement,
    StockReservation,
    StorageCharge,
    SyntheticCounterparty,
    UserActionGrant,
    Variant,
    WorkflowEvent,
    WorkflowPolicyConfig,
)
from .purchasing_services import (
    WorkflowConfigurationMissing,
    WorkflowPermissionDenied,
    accrue_storage_charges,
    approve_post_handover_balance,
    approve_quote_discount,
    approve_special_order_value_band,
    approve_storage_charge_exception,
    assign_approval_role,
    configure_selling_price,
    configure_storage_exception_roles,
    create_sales_quote,
    create_purchase_commitment,
    create_special_order_value_band,
    create_temporary_quote_hold,
    deliver_sales_quote,
    notify_quote_ready,
    receive_imported_purchase,
    record_simulated_payment,
)
from .transaction_services import IdempotencyConflict, InsufficientStock, InvalidTransaction


class PurchasingFixtureMixin:
    @classmethod
    def create_fixture(cls):
        with patch.dict("os.environ", {"APP_ENV": "demo"}):
            call_command("seed_demo", stdout=StringIO())
        cls.unit = BusinessUnit.objects.get(code="TVL")
        cls.roche = BusinessUnit.objects.get(code="RBB")
        cls.admin = get_user_model().objects.get(username="demo_admin")
        cls.sales = get_user_model().objects.get(username="demo_multi")
        cls.roche_user = get_user_model().objects.get(username="demo_roche")
        cls.sales_grant, _ = UserActionGrant.objects.get_or_create(
            user=cls.sales,
            business_unit=cls.unit,
            action=UserActionGrant.Action.CREATE_ORDER,
        )
        cls.sales_role, _ = BusinessUnitRoleAssignment.objects.get_or_create(
            user=cls.sales,
            business_unit=cls.unit,
            role=BusinessUnitRoleAssignment.Role.SALESPERSON,
            revoked_at__isnull=True,
            defaults={"assigned_by": cls.admin},
        )
        cls.variant = Variant.objects.get(sku="TVF-002-LIN")
        cls.alternate_variant = Variant.objects.get(sku="TVF-002-LIN-DEMO2")
        cls.location = Location.objects.get(code="STIV-G")
        cls.other_location = Location.objects.get(code="W41-01")
        cls.customer, _ = SyntheticCounterparty.objects.get_or_create(
            party_type=SyntheticCounterparty.Type.CUSTOMER,
            code="TEST-CUST-RETAIL-01",
            defaults={"name": "TEST ONLY synthetic retail customer"},
        )
        cls.supplier, _ = SyntheticCounterparty.objects.get_or_create(
            party_type=SyntheticCounterparty.Type.SUPPLIER,
            code="TEST-SUP-IMPORT-01",
            defaults={"name": "TEST ONLY synthetic import supplier"},
        )
        configure_selling_price(
            actor=cls.admin,
            business_unit=cls.unit,
            variant=cls.variant,
            amount=Decimal("2000.00"),
        )
        configure_selling_price(
            actor=cls.admin,
            business_unit=cls.unit,
            variant=cls.alternate_variant,
            amount=Decimal("100.00"),
        )

    def make_receipt(
        self,
        *,
        key="receipt-test",
        variant=None,
        location=None,
        quantity="10.00",
        unit_cost="1000.00",
        charges=None,
        quote_line=None,
    ):
        return receive_imported_purchase(
            actor=self.sales,
            idempotency_key=key,
            business_unit=self.unit,
            supplier=self.supplier,
            lines=[
                {
                    "variant": variant or self.variant,
                    "location": location or self.location,
                    "quantity": Decimal(quantity),
                    "unit_cost": Decimal(unit_cost),
                    "quote_line": quote_line,
                }
            ],
            charges=charges if charges is not None else [
                {"category": "shipping", "amount": Decimal("2000.00"), "eligible": True}
            ],
        )

    def make_quote(
        self,
        *,
        key="quote-test",
        kind=SalesQuote.Kind.STOCK,
        quantity="2.00",
        variant=None,
        location=None,
        discount="0.00",
        payment_plan="full",
        downpayment_percent=None,
        payment_terms_reason="",
        discount_reason="TEST ONLY synthetic price decision",
    ):
        return create_sales_quote(
            actor=self.sales,
            idempotency_key=key,
            business_unit=self.unit,
            customer=self.customer,
            kind=kind,
            lines=[
                {
                    "variant": variant or self.variant,
                    "location": location or (self.location if kind == SalesQuote.Kind.STOCK else None),
                    "quantity": Decimal(quantity),
                    "specifications": {"finish": "synthetic linen"},
                }
            ],
            discount_percent=Decimal(discount),
            discount_reason=discount_reason,
            payment_plan=payment_plan,
            downpayment_percent=(
                Decimal(downpayment_percent) if downpayment_percent is not None else None
            ),
            payment_terms_reason=payment_terms_reason,
        )

    def pay(
        self,
        quote,
        *,
        key,
        amount,
        purpose="quote",
        method=QuotePayment.Method.SIMULATED_CASH,
        actor=None,
    ):
        return record_simulated_payment(
            actor=actor or self.sales,
            quote=quote,
            idempotency_key=key,
            amount=Decimal(amount),
            method=method,
            purpose=purpose,
        )


class PurchasingWorkflowTests(PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()

    def test_session_handover_acknowledgement_and_replay_exclude_internal_costs(self):
        self.make_receipt(key="public-handover-opening")
        quote = SalesQuote.objects.get(pk=self.make_quote(key="public-handover-quote")["quote_id"])
        self.pay(quote, key="public-handover-full-payment", amount="4000.00")
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key="public-handover-ready")
        self.assertFalse(self.sales.is_staff)
        client, csrf = self.authenticated_api_client(self.sales)
        path = f"/api/purchasing/quotes/{quote.pk}/deliver/"
        key = "public-handover-ack"
        first = self.post_api(client, csrf, path, {}, key)
        self.assertEqual(first.status_code, 200)
        public_line_fields = {
            "journal_entry_id", "movement_id", "reservation_id",
            "quantity_delivered", "revenue",
        }
        self.assertEqual(set(first.data["delivery_results"][0]), public_line_fields)
        self.assertEqual(set(first.data), {
            "quote_id", "status", "delivered_at", "delivery_results",
            "amount_paid", "remaining_balance", "currency",
            "invoice_id", "invoice_number", "invoice_total",
        })
        stored = IdempotencyRecord.objects.get(key=key).result
        self.assertEqual(stored["delivery_results"][0]["cost_of_goods_sold"], "2400.00")
        self.assertEqual(stored["delivery_results"][0]["remaining_inventory_value"], "9600.00")
        def recorded():
            quote.refresh_from_db()
            return (
                StockMovement.objects.count(), JournalEntry.objects.count(),
                QuotePayment.objects.count(), QuoteReservation.objects.count(),
                SalesReportLine.objects.count(), IdempotencyRecord.objects.count(),
                quote.status, quote.ready_at, quote.delivered_at,
                list(StockBalance.objects.order_by("pk").values_list(
                    "on_hand_quantity", "reserved_quantity", "on_hand_value")),
            )
        committed = recorded()
        replay = self.post_api(client, csrf, path, {}, key)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data, first.data)
        self.assertEqual(recorded(), committed)
        self.assertEqual(IdempotencyRecord.objects.get(key=key).result, stored)
        # Staff visibility is not a sensitive-financial capability. The same
        # public action response must stay non-cost on an authorized retry.
        self.sales.is_staff = True
        self.sales.save(update_fields=["is_staff"])
        staff_replay = self.post_api(client, csrf, path, {}, key)
        self.assertEqual(staff_replay.data, first.data)
        self.assertEqual(recorded(), committed)

    def test_import_landed_cost_deposit_delivery_and_balanced_qar_journals(self):
        receipt = self.make_receipt(key="end-to-end-receipt")
        self.assertEqual(receipt["purchase_value"], "10000.00")
        self.assertEqual(receipt["eligible_landed_charges"], "2000.00")
        self.assertEqual(receipt["landed_cost_rate_percent"], "20.0000")
        self.assertEqual(receipt["landed_cost_allocations"], ["2000.00"])

        balance = StockBalance.objects.get(
            business_unit=self.unit,
            variant=self.variant,
            location=self.location,
        )
        self.assertEqual(balance.on_hand_quantity, Decimal("10.00"))
        self.assertEqual(balance.on_hand_value, Decimal("12000.00"))

        quote_data = self.make_quote(key="end-to-end-quote")
        quote = SalesQuote.objects.get(pk=quote_data["quote_id"])
        self.assertEqual(quote.total_quote_value, Decimal("4000.00"))
        hold = create_temporary_quote_hold(
            actor=self.sales,
            quote=quote,
            idempotency_key="end-to-end-hold",
            working_days=3,
        )
        self.assertEqual(hold["working_days"], 3)

        deposit = self.pay(quote, key="end-to-end-deposit", amount="1960.00")
        self.assertEqual(deposit["non_returnable_amount"], "1960.00")
        self.assertEqual(deposit["reservation_ids"], [])
        self.assertFalse(QuoteReservation.objects.filter(quote_line__quote=quote).exists())
        threshold = self.pay(quote, key="end-to-end-deposit-threshold", amount="40.00")
        self.assertEqual(threshold["non_returnable_amount"], "40.00")
        self.assertEqual(len(threshold["reservation_ids"]), 1)
        self.assertEqual(balance.reservations.get(status=StockReservation.Status.ACTIVE).quantity, Decimal("2.00"))

        notify_quote_ready(
            actor=self.sales,
            quote=quote,
            idempotency_key="end-to-end-ready",
            message="TEST ONLY: ready for synthetic handover",
        )
        with self.assertRaises(InvalidTransaction):
            deliver_sales_quote(
                actor=self.sales,
                quote=quote,
                idempotency_key="end-to-end-delivery-before-full-payment",
            )
        self.pay(quote, key="end-to-end-balance", amount="2000.00")
        delivered = deliver_sales_quote(
            actor=self.sales,
            quote=quote,
            idempotency_key="end-to-end-delivery",
        )
        self.assertEqual(delivered["status"], SalesQuote.Status.DELIVERED)
        self.assertEqual(delivered["remaining_balance"], "0.00")

        balance.refresh_from_db()
        self.assertEqual(balance.on_hand_quantity, Decimal("8.00"))
        self.assertEqual(balance.on_hand_value, Decimal("9600.00"))
        self.assertEqual(balance.reserved_quantity, Decimal("0.00"))
        quote_line = quote.lines.get()
        self.assertEqual(quote_line.delivered_quantity, Decimal("2.00"))
        self.assertEqual(quote_line.net_unit_price, Decimal("2000.00"))

        self.assertTrue(
            JournalEntry.objects.filter(
                legal_entity=self.unit.legal_entity,
                currency="QAR",
                status=JournalEntry.Status.POSTED,
                policy_label="TEST_ONLY_NOT_TIVOLI_APPROVED",
            ).exists()
        )
        for entry in JournalEntry.objects.filter(
            legal_entity=self.unit.legal_entity,
            status=JournalEntry.Status.POSTED,
        ):
            lines = JournalLine.objects.filter(entry=entry)
            debit = sum(lines.values_list("debit", flat=True), Decimal("0.00"))
            credit = sum(lines.values_list("credit", flat=True), Decimal("0.00"))
            self.assertEqual(debit, credit)
            self.assertEqual(entry.currency, "QAR")

    def test_purchase_value_rounding_replays_and_conflicting_idempotency(self):
        with self.assertRaises(WorkflowConfigurationMissing):
            receive_imported_purchase(
                actor=self.sales,
                idempotency_key="noneligible-landed-charge",
                business_unit=self.unit,
                supplier=self.supplier,
                lines=[
                    {
                        "variant": self.variant,
                        "location": self.location,
                        "quantity": Decimal("1.00"),
                        "unit_cost": Decimal("1.00"),
                    }
                ],
                charges=[{"category": "other", "amount": Decimal("1.00"), "eligible": False}],
            )
        self.assertFalse(IdempotencyRecord.objects.filter(key="noneligible-landed-charge").exists())

        result = receive_imported_purchase(
            actor=self.sales,
            idempotency_key="rounded-import",
            business_unit=self.unit,
            supplier=self.supplier,
            lines=[
                {
                    "variant": self.variant,
                    "location": self.location,
                    "quantity": Decimal("1.00"),
                    "unit_cost": Decimal("0.01"),
                },
                {
                    "variant": self.alternate_variant,
                    "location": self.location,
                    "quantity": Decimal("2.00"),
                    "unit_cost": Decimal("0.01"),
                },
            ],
            charges=[{"category": "clearance", "amount": Decimal("0.01"), "eligible": True}],
        )
        self.assertEqual(result["purchase_value"], "0.03")
        self.assertEqual(result["landed_cost_allocations"], ["0.00", "0.01"])
        self.assertEqual(sum(Decimal(value) for value in result["landed_cost_allocations"]), Decimal("0.01"))

        replay = receive_imported_purchase(
            actor=self.sales,
            idempotency_key="rounded-import",
            business_unit=self.unit,
            supplier=self.supplier,
            lines=[
                {
                    "variant": self.variant,
                    "location": self.location,
                    "quantity": Decimal("1.00"),
                    "unit_cost": Decimal("0.01"),
                },
                {
                    "variant": self.alternate_variant,
                    "location": self.location,
                    "quantity": Decimal("2.00"),
                    "unit_cost": Decimal("0.01"),
                },
            ],
            charges=[{"category": "clearance", "amount": Decimal("0.01"), "eligible": True}],
        )
        self.assertEqual(replay, result)
        self.assertEqual(PurchaseReceipt.objects.filter(idempotency__key="rounded-import").count(), 1)
        self.assertEqual(PurchaseReceipt.objects.count(), 1)

        with self.assertRaises(IdempotencyConflict):
            receive_imported_purchase(
                actor=self.sales,
                idempotency_key="rounded-import",
                business_unit=self.unit,
                supplier=self.supplier,
                lines=[
                    {
                        "variant": self.variant,
                        "location": self.location,
                        "quantity": Decimal("1.00"),
                        "unit_cost": Decimal("0.02"),
                    }
                ],
                charges=[{"category": "clearance", "amount": Decimal("0.01"), "eligible": True}],
            )

    def test_quote_price_snapshot_discount_basis_and_role_ceiling_approval(self):
        result = self.make_quote(key="discount-quote", discount="10.00")
        quote = SalesQuote.objects.get(pk=result["quote_id"])
        line = quote.lines.get()
        self.assertEqual(line.selling_price_snapshot, Decimal("2000.00"))
        self.assertEqual(line.discount_amount, Decimal("400.00"))
        self.assertEqual(line.net_total, Decimal("3600.00"))
        self.assertEqual(line.net_unit_price, Decimal("1800.00"))
        self.assertEqual(quote.approvals.filter(kind=QuoteApproval.Kind.DISCOUNT).count(), 1)

        configure_selling_price(
            actor=self.admin,
            business_unit=self.unit,
            variant=self.variant,
            amount=Decimal("2500.00"),
        )
        line.refresh_from_db()
        self.assertEqual(line.selling_price_snapshot, Decimal("2000.00"))
        self.assertEqual(ApprovedSellingPrice.objects.get(
            business_unit=self.unit,
            variant=self.variant,
            is_current=True,
        ).amount, Decimal("2500.00"))

        # Ordinary sales now apply discounts only within their grade. Keep
        # exercising the existing staff-mediated request/manager-review path.
        self.sales.is_staff = True
        self.sales.save(update_fields=["is_staff"])
        pending_data = self.make_quote(
            key="manager-approval-quote",
            discount="15.00",
            discount_reason="TEST ONLY requested fifteen percent",
        )
        pending = SalesQuote.objects.get(pk=pending_data["quote_id"])
        self.assertEqual(pending.status, SalesQuote.Status.PENDING_APPROVAL)
        manager = self.make_role_user("synthetic_manager", BusinessUnitRoleAssignment.Role.MANAGER)
        approval = approve_quote_discount(
            actor=manager,
            quote=pending,
            idempotency_key="manager-approval-key",
            reason="TEST ONLY within manager ceiling",
        )
        self.assertEqual(approval["status"], SalesQuote.Status.APPROVED)
        self.assertEqual(pending.approvals.get(kind=QuoteApproval.Kind.DISCOUNT).role,
                         BusinessUnitRoleAssignment.Role.MANAGER)

        too_high = self.make_quote(
            key="above-manager-quote",
            discount="25.00",
            discount_reason="TEST ONLY requires higher role",
        )
        too_high_quote = SalesQuote.objects.get(pk=too_high["quote_id"])
        self.assertEqual(too_high_quote.status, SalesQuote.Status.PENDING_APPROVAL)
        with self.assertRaises(WorkflowPermissionDenied):
            approve_quote_discount(
                actor=manager,
                quote=too_high_quote,
                idempotency_key="manager-cannot-approve-25",
                reason="TEST ONLY attempted over-ceiling approval",
            )

    def test_all_previously_approved_discount_ceiling_roles(self):
        expected = [
            (BusinessUnitRoleAssignment.Role.SALESPERSON, "10.00"),
            (BusinessUnitRoleAssignment.Role.MANAGER, "20.00"),
            (BusinessUnitRoleAssignment.Role.COMMERCIAL_GM, "30.00"),
            (BusinessUnitRoleAssignment.Role.CEO, "50.00"),
        ]
        for index, (role, discount) in enumerate(expected, start=1):
            actor = self.make_role_user(f"synthetic_discount_role_{index}", role)
            result = create_sales_quote(
                actor=actor,
                idempotency_key=f"discount-ceiling-{index}",
                business_unit=self.unit,
                customer=self.customer,
                kind=SalesQuote.Kind.STOCK,
                lines=[
                    {
                        "variant": self.variant,
                        "location": self.location,
                        "quantity": Decimal("1.00"),
                        "specifications": {},
                    }
                ],
                discount_percent=Decimal(discount),
                discount_reason="TEST ONLY assigned role price decision",
            )
            quote = SalesQuote.objects.get(pk=result["quote_id"])
            self.assertEqual(quote.status, SalesQuote.Status.APPROVED)
            self.assertEqual(quote.discount_percent, Decimal(discount))

        with self.assertRaises(InvalidTransaction):
            self.make_quote(key="discount-over-limit", discount="50.01")

    def test_quote_replay_uses_original_snapshot_and_conflicting_payload_is_rejected(self):
        first = self.make_quote(key="quote-idempotency", discount="5.00")
        configure_selling_price(
            actor=self.admin,
            business_unit=self.unit,
            variant=self.variant,
            amount=Decimal("2300.00"),
        )
        replay = self.make_quote(key="quote-idempotency", discount="5.00")
        self.assertEqual(replay, first)
        self.assertEqual(SalesQuote.objects.count(), 1)
        with self.assertRaises(IdempotencyConflict):
            self.make_quote(key="quote-idempotency", discount="6.00")

    def test_simulated_payment_replays_original_result_and_rejects_key_reuse(self):
        quote = SalesQuote.objects.get(pk=self.make_quote(key="payment-idempotency-quote")["quote_id"])
        first = self.pay(quote, key="payment-idempotency-key", amount="100.00")
        replay = self.pay(quote, key="payment-idempotency-key", amount="100.00")
        self.assertEqual(replay, first)
        self.assertEqual(quote.payments.count(), 1)
        with self.assertRaises(IdempotencyConflict):
            self.pay(quote, key="payment-idempotency-key", amount="101.00")

    def test_special_order_40_percent_does_not_reserve_stock_until_50_percent(self):
        quote_data = self.make_quote(
            key="special-order-quote",
            kind=SalesQuote.Kind.SPECIAL_ORDER,
            quantity="1.00",
            payment_plan="downpayment",
            downpayment_percent="40.00",
            payment_terms_reason="TEST ONLY explicit synthetic schedule approval",
        )
        quote = SalesQuote.objects.get(pk=quote_data["quote_id"])
        self.assertEqual(
            quote.approvals.filter(kind=QuoteApproval.Kind.PAYMENT_TERMS).count(),
            1,
        )
        payment = self.pay(quote, key="special-order-40-payment", amount="800.00")
        self.assertEqual(payment["reservation_ids"], [])
        self.assertEqual(QuoteReservation.objects.filter(quote_line__quote=quote).count(), 0)

        self.make_receipt(
            key="special-order-receipt",
            quantity="1.00",
            unit_cost="1000.00",
            charges=[],
            quote_line=quote.lines.get(),
        )
        self.assertEqual(QuoteReservation.objects.filter(quote_line__quote=quote).count(), 0)
        additional = self.pay(quote, key="special-order-50-payment", amount="200.00")
        self.assertEqual(len(additional["reservation_ids"]), 1)
        self.assertEqual(quote.lines.get().reserved_quantity, Decimal("1.00"))

    def test_special_order_payment_plan_boundaries_and_explicit_full_payment_approval(self):
        low = self.make_quote(
            key="special-downpayment-low-boundary",
            kind=SalesQuote.Kind.SPECIAL_ORDER,
            quantity="1.00",
            payment_plan="downpayment",
            downpayment_percent="40.00",
            payment_terms_reason="TEST ONLY minimum schedule choice",
        )
        high = self.make_quote(
            key="special-downpayment-high-boundary",
            kind=SalesQuote.Kind.SPECIAL_ORDER,
            quantity="1.00",
            payment_plan="downpayment",
            downpayment_percent="70.00",
            payment_terms_reason="TEST ONLY maximum schedule choice",
        )
        full = self.make_quote(
            key="special-full-payment-plan",
            kind=SalesQuote.Kind.SPECIAL_ORDER,
            quantity="1.00",
            payment_plan="full",
            payment_terms_reason="TEST ONLY full-payment schedule choice",
        )
        self.assertEqual(SalesQuote.objects.get(pk=low["quote_id"]).downpayment_percent, Decimal("40.00"))
        self.assertEqual(SalesQuote.objects.get(pk=high["quote_id"]).downpayment_percent, Decimal("70.00"))
        full_quote = SalesQuote.objects.get(pk=full["quote_id"])
        self.assertIsNone(full_quote.downpayment_percent)
        full_terms = full_quote.approvals.get(kind=QuoteApproval.Kind.PAYMENT_TERMS)
        self.assertEqual(full_terms.details["downpayment_percent"], "100.00")
        with self.assertRaises(InvalidTransaction):
            self.make_quote(
                key="special-downpayment-below-minimum",
                kind=SalesQuote.Kind.SPECIAL_ORDER,
                quantity="1.00",
                payment_plan="downpayment",
                downpayment_percent="39.99",
                payment_terms_reason="TEST ONLY invalid schedule",
            )

    def test_holds_use_seeded_friday_closed_calendar_and_enforce_three_days(self):
        calendar = BusinessUnitWorkCalendar.objects.get(business_unit=self.unit)
        self.assertTrue(calendar.is_configured)
        self.assertEqual(calendar.closed_weekdays, [4])

        self.make_receipt(key="hold-stock-receipt", charges=[])
        quote = SalesQuote.objects.get(pk=self.make_quote(key="calendar-hold-quote")["quote_id"])
        thursday = timezone.make_aware(datetime(2026, 10, 22, 10, 30))
        result = create_temporary_quote_hold(
            actor=self.sales,
            quote=quote,
            idempotency_key="calendar-hold-key",
            working_days=3,
            now=thursday,
        )
        expiry = timezone.localtime(datetime.fromisoformat(result["expires_at"]))
        self.assertEqual(expiry.date().isoformat(), "2026-10-26")
        self.assertEqual(expiry.time(), time(10, 30))
        self.assertEqual(quote.holds.get().working_days, 3)
        replay = create_temporary_quote_hold(
            actor=self.sales,
            quote=quote,
            idempotency_key="calendar-hold-key",
            working_days=3,
            now=thursday + timedelta(days=10),
        )
        self.assertEqual(replay, result)
        with self.assertRaises(InvalidTransaction):
            create_temporary_quote_hold(
                actor=self.sales,
                quote=quote,
                idempotency_key="calendar-hold-four-days",
                working_days=4,
                now=thursday,
            )

    def test_stock_reservation_failure_rolls_back_payment_and_idempotency(self):
        quote = SalesQuote.objects.get(
            pk=self.make_quote(key="rollback-overstock-quote", quantity="11.00")["quote_id"]
        )
        with self.assertRaises(InsufficientStock):
            self.pay(quote, key="rollback-overstock-payment", amount="11000.00")
        self.assertFalse(quote.payments.exists())
        self.assertFalse(IdempotencyRecord.objects.filter(key="rollback-overstock-payment").exists())
        self.assertFalse(StockReservation.objects.filter(balance__business_unit=self.unit).exists())

    def test_storage_free_month_rate_idempotency_auditing_and_fail_closed_exception(self):
        self.make_receipt(key="storage-stock-receipt", charges=[])
        quote = SalesQuote.objects.get(pk=self.make_quote(key="storage-quote")["quote_id"])
        self.pay(quote, key="storage-deposit", amount="2000.00")
        self.pay(quote, key="storage-balance", amount="2000.00")
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key="storage-ready")
        quote.refresh_from_db()
        ready_date = timezone.localtime(quote.ready_at).date()
        free_end = self.add_month(ready_date)
        before = timezone.make_aware(datetime.combine(free_end - timedelta(days=1), time(23, 0)))
        at_boundary = timezone.make_aware(datetime.combine(free_end, time.min))

        early = accrue_storage_charges(
            actor=self.sales,
            quote=quote,
            idempotency_prefix="storage-charge-run",
            as_of=before,
        )
        self.assertEqual(early["charges"], [])
        first = accrue_storage_charges(
            actor=self.sales,
            quote=quote,
            idempotency_prefix="storage-charge-run",
            as_of=at_boundary,
        )
        self.assertEqual(len(first["charges"]), 1)
        self.assertEqual(first["charges"][0]["amount"], "40.00")
        self.assertEqual(WorkflowEvent.objects.filter(
            quote=quote,
            kind=WorkflowEvent.Kind.STORAGE_CHARGE_NOTIFICATION,
        ).count(), 1)
        repeated = accrue_storage_charges(
            actor=self.sales,
            quote=quote,
            idempotency_prefix="storage-charge-run",
            as_of=at_boundary,
        )
        self.assertEqual(repeated, first)
        self.assertEqual(StorageCharge.objects.filter(quote=quote).count(), 1)
        client = APIClient()
        client.force_login(self.sales)
        storage_rows = client.get(
            "/api/purchasing/storage-charges/",
            {"business_unit_id": self.unit.pk},
        )
        self.assertEqual(storage_rows.status_code, 200, storage_rows.data)
        self.assertEqual(storage_rows.data["pagination"]["total_count"], 1)
        self.assertEqual(storage_rows.data["rows"][0]["amount"], "40.00")
        charge_id = StorageCharge.objects.get(quote=quote).pk
        storage_detail = client.get(
            f"/api/purchasing/storage-charges/{charge_id}/",
            {"business_unit_id": self.unit.pk},
        )
        self.assertEqual(storage_detail.status_code, 200, storage_detail.data)
        self.assertEqual(storage_detail.data["charge"]["quote_id"], quote.pk)
        client.force_login(self.roche_user)
        self.assertEqual(
            client.get(
                "/api/purchasing/storage-charges/",
                {"business_unit_id": self.unit.pk},
            ).status_code,
            404,
        )
        self.assertEqual(
            client.get(
                f"/api/purchasing/storage-charges/{charge_id}/",
                {"business_unit_id": self.unit.pk},
            ).status_code,
            404,
        )
        second_boundary = self.add_month(free_end)
        second_period = accrue_storage_charges(
            actor=self.sales,
            quote=quote,
            idempotency_prefix="storage-charge-run",
            as_of=timezone.make_aware(datetime.combine(second_boundary, time.min)),
        )
        self.assertEqual(len(second_period["charges"]), 2)
        self.assertEqual([row["amount"] for row in second_period["charges"]], ["40.00", "40.00"])
        self.assertEqual(StorageCharge.objects.filter(quote=quote).count(), 2)

        charge = StorageCharge.objects.filter(quote=quote).order_by("period_start").first()
        with self.assertRaises(WorkflowConfigurationMissing):
            approve_storage_charge_exception(
                actor=self.sales,
                charge=charge,
                idempotency_key="storage-exception-disabled",
                amount_waived=Decimal("10.00"),
                reason="TEST ONLY not configured",
            )
        configure_storage_exception_roles(
            actor=self.admin,
            business_unit=self.unit,
            roles=[BusinessUnitRoleAssignment.Role.BU_GM],
        )
        bu_gm = self.make_role_user("synthetic_bu_gm", BusinessUnitRoleAssignment.Role.BU_GM)
        exception = approve_storage_charge_exception(
            actor=bu_gm,
            charge=charge,
            idempotency_key="storage-exception-approved",
            amount_waived=Decimal("10.00"),
            reason="TEST ONLY synthetic exception with explicit authority",
        )
        self.assertEqual(exception["amount_waived"], "10.00")
        self.assertEqual(charge.exception.approver_role, BusinessUnitRoleAssignment.Role.BU_GM)

    def test_post_handover_balance_needs_explicit_bu_gm_or_ceo_approval(self):
        self.make_receipt(key="post-handover-receipt", charges=[])
        quote = SalesQuote.objects.get(pk=self.make_quote(key="post-handover-quote")["quote_id"])
        self.pay(quote, key="post-handover-deposit", amount="3600.00")
        with self.assertRaises(WorkflowPermissionDenied):
            approve_post_handover_balance(
                actor=self.sales,
                quote=quote,
                idempotency_key="post-handover-salesperson-denied",
                deferred_balance_percent=Decimal("10.00"),
                reason="TEST ONLY unauthorized attempt",
            )
        bu_gm = self.make_role_user("synthetic_bu_gm_post", BusinessUnitRoleAssignment.Role.BU_GM)
        with self.assertRaises(InvalidTransaction):
            approve_post_handover_balance(
                actor=bu_gm,
                quote=quote,
                idempotency_key="post-handover-over-limit",
                deferred_balance_percent=Decimal("10.01"),
                reason="TEST ONLY outside exceptional range",
            )
        approve_post_handover_balance(
            actor=bu_gm,
            quote=quote,
            idempotency_key="post-handover-approval",
            deferred_balance_percent=Decimal("10.00"),
            reason="TEST ONLY rare synthetic delivery exception",
        )
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key="post-handover-ready")
        result = deliver_sales_quote(
            actor=self.sales,
            quote=quote,
            idempotency_key="post-handover-delivery",
        )
        self.assertEqual(result["remaining_balance"], "400.00")
        self.assertEqual(result["status"], SalesQuote.Status.DELIVERED)
        paid = self.pay(quote, key="post-handover-final-payment", amount="400.00")
        self.assertEqual(paid["total_paid"], "4000.00")

    def test_session_api_post_delivery_balance_preserves_state_and_replays_idempotently(self):
        self.make_receipt(key="post-delivery-api-receipt", charges=[])
        quote = SalesQuote.objects.get(
            pk=self.make_quote(key="post-delivery-api-quote")["quote_id"]
        )
        self.pay(quote, key="post-delivery-api-deposit", amount="3600.00")
        bu_gm = self.make_api_approver(
            "test_api_post_delivery_bu_gm",
            BusinessUnitRoleAssignment.Role.BU_GM,
        )
        approve_post_handover_balance(
            actor=bu_gm,
            quote=quote,
            idempotency_key="post-delivery-api-approval",
            deferred_balance_percent=Decimal("10.00"),
            reason="TEST ONLY authorized synthetic handover balance",
        )
        notify_quote_ready(
            actor=self.sales,
            quote=quote,
            idempotency_key="post-delivery-api-ready",
        )
        delivery = deliver_sales_quote(
            actor=self.sales,
            quote=quote,
            idempotency_key="post-delivery-api-delivery",
        )
        self.assertEqual(delivery["status"], SalesQuote.Status.DELIVERED)
        quote.refresh_from_db()
        self.assertEqual(quote.status, SalesQuote.Status.DELIVERED)

        delivered_state = {
            "status": quote.status,
            "ready_at": quote.ready_at,
            "delivered_at": quote.delivered_at,
            "updated_at": quote.updated_at,
        }
        payments_before = QuotePayment.objects.filter(quote=quote).count()
        reservations_before = QuoteReservation.objects.filter(quote_line__quote=quote).count()
        movements_before = StockMovement.objects.count()
        workflow_events_before = WorkflowEvent.objects.filter(quote=quote).count()

        client, csrf_token = self.authenticated_api_client(self.sales)
        payment_payload = {
            "amount": "400.00",
            "method": QuotePayment.Method.SIMULATED_BANK,
            "purpose": "quote",
        }
        over_limit = self.post_api(
            client,
            csrf_token,
            f"/api/purchasing/quotes/{quote.pk}/payments/",
            payment_payload | {"amount": "401.00"},
            "post-delivery-api-over-approved-balance",
        )
        self.assertEqual(over_limit.status_code, 400, over_limit.data)
        self.assertEqual(QuotePayment.objects.filter(quote=quote).count(), payments_before)
        self.assertFalse(
            IdempotencyRecord.objects.filter(
                key="post-delivery-api-over-approved-balance"
            ).exists()
        )
        quote.refresh_from_db()
        self.assertEqual(quote.status, SalesQuote.Status.DELIVERED)
        self.assertEqual(quote.delivered_at, delivered_state["delivered_at"])

        payment_key = "post-delivery-api-final-balance"
        first = self.post_api(
            client,
            csrf_token,
            f"/api/purchasing/quotes/{quote.pk}/payments/",
            payment_payload,
            payment_key,
        )
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data["amount"], "400.00")
        self.assertEqual(first.data["total_paid"], "4000.00")

        quote.refresh_from_db()
        self.assertEqual(
            {
                "status": quote.status,
                "ready_at": quote.ready_at,
                "delivered_at": quote.delivered_at,
                "updated_at": quote.updated_at,
            },
            delivered_state,
        )
        self.assertEqual(quote.status, SalesQuote.Status.DELIVERED)
        self.assertEqual(QuotePayment.objects.filter(quote=quote).count(), payments_before + 1)
        payment = QuotePayment.objects.get(pk=first.data["payment_id"])
        self.assertEqual(payment.kind, QuotePayment.Kind.BALANCE)
        self.assertEqual(payment.method, QuotePayment.Method.SIMULATED_BANK)
        self.assertEqual(payment.received_by, self.sales)

        operation = IdempotencyRecord.objects.get(key=payment_key)
        self.assertEqual(operation.operation, "quote_payment")
        self.assertEqual(operation.status, IdempotencyRecord.Status.COMPLETE)
        self.assertEqual(operation.result, first.data)
        self.assertEqual(QuoteReservation.objects.filter(quote_line__quote=quote).count(), reservations_before)
        self.assertEqual(StockMovement.objects.count(), movements_before)
        self.assertEqual(WorkflowEvent.objects.filter(quote=quote).count(), workflow_events_before)

        replay = self.post_api(
            client,
            csrf_token,
            f"/api/purchasing/quotes/{quote.pk}/payments/",
            payment_payload,
            payment_key,
        )
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay.data, first.data)
        self.assertEqual(QuotePayment.objects.filter(quote=quote).count(), payments_before + 1)
        self.assertEqual(IdempotencyRecord.objects.filter(key=payment_key).count(), 1)
        quote.refresh_from_db()
        self.assertEqual(quote.status, SalesQuote.Status.DELIVERED)
        self.assertEqual(quote.ready_at, delivered_state["ready_at"])
        self.assertEqual(quote.delivered_at, delivered_state["delivered_at"])

    def test_unauthorized_cross_unit_and_governance_actions_are_denied(self):
        with self.assertRaises(WorkflowPermissionDenied):
            create_sales_quote(
                actor=self.roche_user,
                idempotency_key="roche-cannot-quote-tivoli",
                business_unit=self.unit,
                customer=self.customer,
                kind=SalesQuote.Kind.STOCK,
                lines=[
                    {
                        "variant": self.variant,
                        "location": self.location,
                        "quantity": Decimal("1.00"),
                        "specifications": {},
                    }
                ],
                discount_percent=Decimal("0.00"),
                discount_reason="TEST ONLY unauthorized attempt",
            )

        with self.assertRaises(WorkflowPermissionDenied):
            assign_approval_role(
                actor=self.admin,
                user=self.roche_user,
                business_unit=self.unit,
                role=BusinessUnitRoleAssignment.Role.SALESPERSON,
            )

        target = get_user_model().objects.create_user(username="synthetic_role_assignment_target")
        target.set_unusable_password()
        target.save(update_fields=["password"])
        DemoUserMarker.objects.create(user=target)
        target_grant = UserActionGrant.objects.create(
            user=target,
            business_unit=self.unit,
            action=UserActionGrant.Action.CREATE_ORDER,
        )
        grants_before = list(UserActionGrant.objects.filter(user=target).values_list("id", "action"))
        assigned = assign_approval_role(
            actor=self.admin,
            user=target,
            business_unit=self.unit,
            role=BusinessUnitRoleAssignment.Role.SALESPERSON,
        )
        self.assertTrue(assigned["created"])
        self.assertEqual(
            list(UserActionGrant.objects.filter(user=target).values_list("id", "action")),
            grants_before,
        )
        self.assertTrue(BusinessUnitRoleAssignment.objects.filter(
            user=target,
            business_unit=self.unit,
            role=BusinessUnitRoleAssignment.Role.SALESPERSON,
            revoked_at__isnull=True,
        ).exists())
        self.assertEqual(target_grant.action, UserActionGrant.Action.CREATE_ORDER)

        client = APIClient()
        client.force_login(self.roche_user)
        denied = client.put(
            f"/api/governance/purchasing/{self.unit.pk}/work-calendar/",
            {"closed_weekdays": [4], "closed_dates": []},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)

    def test_session_authenticated_quote_api_requires_csrf_and_returns_snapshot(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.sales)
        csrf_token = client.get("/api/auth/csrf/").data["csrfToken"]
        payload = {
            "business_unit_id": self.unit.pk,
            "customer_id": self.customer.pk,
            "kind": SalesQuote.Kind.STOCK,
            "discount_percent": "0.00",
            "discount_reason": "TEST ONLY API price decision",
            "lines": [
                {
                    "variant_id": self.variant.pk,
                    "location_id": self.location.pk,
                    "quantity": "1.00",
                    "specifications": {"fabric": "synthetic demo"},
                }
            ],
        }
        denied = client.post(
            "/api/purchasing/quotes/",
            payload | {"idempotency_key": "api-quote-no-csrf"},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)
        response = client.post(
            "/api/purchasing/quotes/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="api-quote-session",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(response.status_code, 201, response.data)
        quote_id = response.data["quote_id"]
        self.assertEqual(response.data["lines"][0]["selling_price_snapshot"], "2000.00")
        detail = client.get(f"/api/purchasing/quotes/{quote_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["quote_id"], quote_id)

    def make_api_approver(self, username, role):
        user = get_user_model().objects.create_user(username=username)
        user.set_unusable_password()
        user.save(update_fields=["password"])
        DemoUserMarker.objects.create(user=user)
        UserActionGrant.objects.create(
            user=user,
            business_unit=self.unit,
            action=UserActionGrant.Action.CREATE_ORDER,
        )
        assign_approval_role(
            actor=self.admin,
            user=user,
            business_unit=self.unit,
            role=role,
        )
        return user

    @staticmethod
    def authenticated_api_client(user):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(user)
        csrf_token = client.get("/api/auth/csrf/").data["csrfToken"]
        return client, csrf_token

    @staticmethod
    def post_api(client, csrf_token, path, payload, key):
        return client.post(
            path,
            payload,
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
            HTTP_IDEMPOTENCY_KEY=key,
        )

    def test_reviewed_commitment_preparation_get_is_safe_and_explicit_links_remain_atomic(self):
        """Local file review uses existing GETs; only explicit receipt POSTs write."""
        client, csrf = self.authenticated_api_client(self.sales)
        def create(key, variants):
            result = self.post_api(client, csrf, "/api/purchasing/commitments/", {
                "business_unit_id": self.unit.pk, "supplier_id": self.supplier.pk,
                "lines": [{"variant_id": variant.pk, "ordered_quantity": "5.00",
                           "unit_cost": "1000.00"} for variant in variants],
            }, key)
            self.assertEqual(result.status_code, 201, result.data)
            return result.data

        selected = create("review-selected", [self.variant, self.alternate_variant])
        unrelated = create("review-other", [self.variant])
        selected_id = selected["purchase_commitment_id"]
        line_ids = [line["commitment_line_id"] for line in selected["lines"]]
        def snapshot():
            return (
                PurchaseReceipt.objects.count(), PurchaseReceiptLine.objects.count(),
                StockMovement.objects.count(), JournalEntry.objects.count(),
                WorkflowEvent.objects.count(), IdempotencyRecord.objects.count(),
                list(StockBalance.objects.order_by("pk").values_list(
                    "pk", "on_hand_quantity", "reserved_quantity", "on_hand_value")),
            )

        before = snapshot()
        response = client.get(f"/api/purchasing/commitments/?business_unit_id={self.unit.pk}")
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data["rows"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(snapshot(), before, "Preparation/progress GET must not write facts.")
        for row in rows:
            self.assertEqual(set(row), {
                "commitment_id", "commitment_line_id", "supplier_id", "variant_id",
                "variant_sku", "item_description", "ordered_quantity",
                "received_to_date", "remaining_quantity", "currency",
            })
        payload = {
            "business_unit_id": self.unit.pk, "supplier_id": self.supplier.pk,
            "purchase_commitment_id": selected_id, "charges": [],
            "lines": [
                {"commitment_line_id": line_id, "variant_id": variant.pk,
                 "location_id": self.location.pk, "quantity": quantity}
                for line_id, variant, quantity in zip(
                    line_ids, [self.variant, self.alternate_variant], ["2.00", "3.00"])
            ],
        }
        # No cost or uploaded source fields: committed values remain server-owned.
        result = self.post_api(client, csrf, "/api/purchasing/receipts/", payload, "review-post")
        self.assertEqual(result.status_code, 201, result.data)
        after = snapshot()
        replay = self.post_api(client, csrf, "/api/purchasing/receipts/", payload, "review-post")
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay.data, result.data)
        self.assertEqual(snapshot(), after)
        progress = client.get(f"/api/purchasing/commitments/?business_unit_id={self.unit.pk}").data["rows"]
        self.assertEqual(
            [(row["received_to_date"], row["remaining_quantity"]) for row in progress
             if row["commitment_id"] == selected_id],
            [("2.00", "3.00"), ("3.00", "2.00")],
        )
        from copy import deepcopy
        for invalid_kind in ["other-commitment", "over-remaining", "wrong-location"]:
            invalid = deepcopy(payload)
            if invalid_kind == "other-commitment":
                invalid["lines"][1]["commitment_line_id"] = unrelated["lines"][0]["commitment_line_id"]
            elif invalid_kind == "over-remaining":
                invalid["lines"][1]["quantity"] = "3.00"
            else:
                invalid["lines"][1]["location_id"] = Location.objects.filter(
                    business_units=self.roche, is_synthetic=True
                ).exclude(business_units=self.unit).first().pk
            rejected = self.post_api(client, csrf, "/api/purchasing/receipts/", invalid, f"review-{invalid_kind}")
            self.assertEqual(
                rejected.status_code, 404 if invalid_kind == "other-commitment" else 400,
                rejected.data,
            )
            self.assertEqual(snapshot(), after, invalid_kind)
        self.assertEqual(client.get(
            f"/api/purchasing/commitments/?business_unit_id={self.roche.pk}"
        ).status_code, 404)
        reader, _ = self.authenticated_api_client(self.roche_user)
        self.assertEqual(reader.get(
            f"/api/purchasing/commitments/?business_unit_id={self.roche.pk}"
        ).status_code, 404)
        self.assertEqual(snapshot(), after)

    def test_commitment_multi_line_partial_receipts_replay_progress_and_atomic_overreceipt(self):
        client, csrf = self.authenticated_api_client(self.sales)
        commitment_payload = {
            "business_unit_id": self.unit.pk,
            "supplier_id": self.supplier.pk,
            "lines": [
                {
                    "variant_id": self.variant.pk,
                    "ordered_quantity": "10.00",
                    "unit_cost": "1000.00",
                },
                {
                    "variant_id": self.alternate_variant.pk,
                    "ordered_quantity": "5.00",
                    "unit_cost": "200.00",
                },
            ],
        }
        commitment_response = self.post_api(
            client, csrf, "/api/purchasing/commitments/", commitment_payload, "commitment-partials"
        )
        self.assertEqual(commitment_response.status_code, 201, commitment_response.data)
        commitment_id = commitment_response.data["purchase_commitment_id"]
        line_by_variant = {
            row["variant_id"]: row["commitment_line_id"]
            for row in commitment_response.data["lines"]
        }
        replayed_commitment = self.post_api(
            client, csrf, "/api/purchasing/commitments/", commitment_payload, "commitment-partials"
        )
        self.assertEqual(replayed_commitment.status_code, 201)
        self.assertEqual(replayed_commitment.data, commitment_response.data)
        self.assertEqual(PurchaseCommitment.objects.filter(pk=commitment_id).count(), 1)
        self.assertEqual(PurchaseCommitmentLine.objects.filter(commitment_id=commitment_id).count(), 2)

        def receipt_payload(lines, charge):
            return {
                "business_unit_id": self.unit.pk,
                "supplier_id": self.supplier.pk,
                "purchase_commitment_id": commitment_id,
                "lines": lines,
                "charges": [{"category": "shipping", "amount": charge, "eligible": True}],
            }

        first_payload = receipt_payload(
            [
                {
                    "commitment_line_id": line_by_variant[self.variant.pk],
                    "variant_id": self.variant.pk,
                    "location_id": self.location.pk,
                    "quantity": "4.00",
                },
                {
                    "commitment_line_id": line_by_variant[self.alternate_variant.pk],
                    "variant_id": self.alternate_variant.pk,
                    "location_id": self.location.pk,
                    "quantity": "2.00",
                },
            ],
            "880.00",
        )
        first_receipt = self.post_api(
            client, csrf, "/api/purchasing/receipts/", first_payload, "commitment-receipt-first"
        )
        self.assertEqual(first_receipt.status_code, 201, first_receipt.data)
        for field in ("purchase_value", "eligible_landed_charges",
                      "landed_cost_rate_percent", "landed_cost_allocations", "inventory_value"):
            self.assertNotIn(field, first_receipt.data)
        private_receipt = IdempotencyRecord.objects.get(key="commitment-receipt-first").result
        self.assertEqual(private_receipt["purchase_value"], "4400.00")
        self.assertEqual(private_receipt["eligible_landed_charges"], "880.00")
        self.assertEqual(private_receipt["landed_cost_rate_percent"], "20.0000")
        self.assertEqual(private_receipt["landed_cost_allocations"], ["800.00", "80.00"])
        self.assertEqual(first_receipt.data["purchase_commitment_id"], commitment_id)
        first_receipt_lines = {
            line.commitment_line_id: line.quantity
            for line in PurchaseReceiptLine.objects.filter(
                receipt_id=first_receipt.data["purchase_receipt_id"]
            )
        }
        self.assertEqual(
            first_receipt_lines,
            {
                line_by_variant[self.variant.pk]: Decimal("4.00"),
                line_by_variant[self.alternate_variant.pk]: Decimal("2.00"),
            },
            "One receipt request must persist the selected quantities independently by commitment line.",
        )

        replayed_receipt = self.post_api(
            client, csrf, "/api/purchasing/receipts/", first_payload, "commitment-receipt-first"
        )
        self.assertEqual(replayed_receipt.status_code, 201)
        self.assertEqual(replayed_receipt.data, first_receipt.data)
        self.assertEqual(PurchaseReceipt.objects.filter(purchase_commitment_id=commitment_id).count(), 1)
        self.assertEqual(PurchaseReceiptLine.objects.filter(
            receipt__purchase_commitment_id=commitment_id
        ).count(), 2, "Replay must not duplicate either line from the multi-line receipt.")
        changed_payload = {
            **first_payload,
            "lines": [
                {**first_payload["lines"][0], "quantity": "3.00"},
                first_payload["lines"][1],
            ],
        }
        conflicting_replay = self.post_api(
            client, csrf, "/api/purchasing/receipts/", changed_payload, "commitment-receipt-first"
        )
        self.assertEqual(conflicting_replay.status_code, 409)
        self.assertEqual(PurchaseReceipt.objects.filter(purchase_commitment_id=commitment_id).count(), 1)
        self.assertEqual(PurchaseReceiptLine.objects.filter(
            receipt__purchase_commitment_id=commitment_id
        ).count(), 2)

        progress_url = f"/api/purchasing/commitments/?business_unit_id={self.unit.pk}&page_size=100"
        progress = client.get(progress_url)
        self.assertEqual(progress.status_code, 200, progress.data)
        progress_by_variant = {row["variant_id"]: row for row in progress.data["rows"]}
        self.assertEqual(progress_by_variant[self.variant.pk]["ordered_quantity"], "10.00")
        self.assertEqual(progress_by_variant[self.variant.pk]["received_to_date"], "4.00")
        self.assertEqual(progress_by_variant[self.variant.pk]["remaining_quantity"], "6.00")
        self.assertEqual(progress_by_variant[self.alternate_variant.pk]["ordered_quantity"], "5.00")
        self.assertEqual(progress_by_variant[self.alternate_variant.pk]["received_to_date"], "2.00")
        self.assertEqual(progress_by_variant[self.alternate_variant.pk]["remaining_quantity"], "3.00")
        self.assertEqual(
            set(progress_by_variant[self.variant.pk]),
            {
                "commitment_id",
                "commitment_line_id",
                "supplier_id",
                "variant_id",
                "variant_sku",
                "item_description",
                "ordered_quantity",
                "received_to_date",
                "remaining_quantity",
                "currency",
            },
        )
        self.assertNotIn("unit_cost", progress_by_variant[self.variant.pk])
        self.assertNotIn("purchase_value", progress_by_variant[self.variant.pk])

        before_rejection = (
            IdempotencyRecord.objects.count(),
            PurchaseReceipt.objects.count(),
            PurchaseReceiptLine.objects.count(),
            StockMovement.objects.count(),
            JournalEntry.objects.count(),
            StockBalance.objects.count(),
        )
        over_receipt_payload = receipt_payload(
            [
                {
                    "commitment_line_id": line_by_variant[self.alternate_variant.pk],
                    "variant_id": self.alternate_variant.pk,
                    "location_id": self.location.pk,
                    "quantity": "1.00",
                },
                {
                    "commitment_line_id": line_by_variant[self.variant.pk],
                    "variant_id": self.variant.pk,
                    "location_id": self.location.pk,
                    "quantity": "7.00",
                },
            ],
            "100.00",
        )
        over_receipt = self.post_api(
            client, csrf, "/api/purchasing/receipts/", over_receipt_payload, "commitment-over-receipt"
        )
        self.assertEqual(over_receipt.status_code, 400, over_receipt.data)
        self.assertEqual(
            (
                IdempotencyRecord.objects.count(),
                PurchaseReceipt.objects.count(),
                PurchaseReceiptLine.objects.count(),
                StockMovement.objects.count(),
                JournalEntry.objects.count(),
                StockBalance.objects.count(),
            ),
            before_rejection,
            "Rejected over-receipt must leave no idempotency, receipt, ledger, journal, or balance writes.",
        )

        second_payload = receipt_payload(
            [
                {
                    "commitment_line_id": line_by_variant[self.variant.pk],
                    "variant_id": self.variant.pk,
                    "location_id": self.location.pk,
                    "quantity": "6.00",
                },
                {
                    "commitment_line_id": line_by_variant[self.alternate_variant.pk],
                    "variant_id": self.alternate_variant.pk,
                    "location_id": self.location.pk,
                    "quantity": "3.00",
                },
            ],
            "1320.00",
        )
        second_receipt = self.post_api(
            client, csrf, "/api/purchasing/receipts/", second_payload, "commitment-receipt-second"
        )
        self.assertEqual(second_receipt.status_code, 201, second_receipt.data)
        for field in ("purchase_value", "eligible_landed_charges",
                      "landed_cost_rate_percent", "landed_cost_allocations", "inventory_value"):
            self.assertNotIn(field, second_receipt.data)
        private_receipt = IdempotencyRecord.objects.get(key="commitment-receipt-second").result
        self.assertEqual(private_receipt["purchase_value"], "6600.00")
        self.assertEqual(private_receipt["eligible_landed_charges"], "1320.00")
        self.assertEqual(private_receipt["landed_cost_rate_percent"], "20.0000")
        self.assertEqual(private_receipt["landed_cost_allocations"], ["1200.00", "120.00"])

        receipts = PurchaseReceipt.objects.filter(purchase_commitment_id=commitment_id)
        reconciled = receipts.aggregate(
            purchase=Sum("purchase_value"),
            landed=Sum("eligible_landed_charges"),
            inventory=Sum("inventory_value"),
        )
        self.assertEqual(reconciled["purchase"], Decimal("11000.00"))
        self.assertEqual(reconciled["landed"], Decimal("2200.00"))
        self.assertEqual(reconciled["inventory"], Decimal("13200.00"))
        chair_balance = StockBalance.objects.get(
            business_unit=self.unit, variant=self.variant, location=self.location
        )
        alternate_balance = StockBalance.objects.get(
            business_unit=self.unit, variant=self.alternate_variant, location=self.location
        )
        self.assertEqual(chair_balance.on_hand_quantity, Decimal("10.00"))
        self.assertEqual(chair_balance.on_hand_value, Decimal("12000.00"))
        self.assertEqual(alternate_balance.on_hand_quantity, Decimal("5.00"))
        self.assertEqual(alternate_balance.on_hand_value, Decimal("1200.00"))

        final_progress = client.get(progress_url)
        self.assertEqual(final_progress.status_code, 200)
        final_by_variant = {row["variant_id"]: row for row in final_progress.data["rows"]}
        self.assertEqual(final_by_variant[self.variant.pk]["received_to_date"], "10.00")
        self.assertEqual(final_by_variant[self.variant.pk]["remaining_quantity"], "0.00")
        self.assertEqual(final_by_variant[self.alternate_variant.pk]["received_to_date"], "5.00")
        self.assertEqual(final_by_variant[self.alternate_variant.pk]["remaining_quantity"], "0.00")

        self.assertEqual(
            client.get(f"/api/purchasing/commitments/?business_unit_id={self.roche.pk}").status_code,
            404,
        )
        admin_client, admin_csrf = self.authenticated_api_client(self.admin)
        self.assertEqual(admin_client.get(progress_url).status_code, 404)
        denied_commitment = self.post_api(
            admin_client,
            admin_csrf,
            "/api/purchasing/commitments/",
            commitment_payload,
            "commitment-denied-no-order-grant",
        )
        self.assertEqual(denied_commitment.status_code, 403)

    def test_session_api_transaction_workbench_full_stock_lifecycle_and_denials(self):
        # This scenario exercises an existing staff-mediated request for a
        # manager decision, not ordinary sales applying above-grade discounts.
        self.sales.is_staff = True
        self.sales.save(update_fields=["is_staff"])
        manager = self.make_api_approver(
            "test_api_manager",
            BusinessUnitRoleAssignment.Role.MANAGER,
        )
        ceo = self.make_api_approver(
            "test_api_ceo",
            BusinessUnitRoleAssignment.Role.CEO,
        )
        sales_client, sales_csrf = self.authenticated_api_client(self.sales)

        receipt_payload = {
            "business_unit_id": self.unit.pk,
            "supplier_id": self.supplier.pk,
            "lines": [{
                "variant_id": self.variant.pk,
                "location_id": self.location.pk,
                "quantity": "10.00",
                "unit_cost": "1000.00",
            }],
            "charges": [{"category": "shipping", "amount": "2000.00", "eligible": True}],
        }
        receipt = self.post_api(
            sales_client,
            sales_csrf,
            "/api/purchasing/receipts/",
            receipt_payload,
            "api-workbench-receipt",
        )
        self.assertEqual(receipt.status_code, 201, receipt.data)
        self.assertNotIn("purchase_value", receipt.data)
        self.assertNotIn("eligible_landed_charges", receipt.data)
        private_receipt = IdempotencyRecord.objects.get(key="api-workbench-receipt").result
        self.assertEqual(private_receipt["purchase_value"], "10000.00")
        self.assertEqual(private_receipt["eligible_landed_charges"], "2000.00")
        replay = self.post_api(
            sales_client,
            sales_csrf,
            "/api/purchasing/receipts/",
            receipt_payload,
            "api-workbench-receipt",
        )
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay.data, receipt.data)
        self.assertEqual(PurchaseReceipt.objects.filter(business_unit=self.unit).count(), 1)
        received_balance = StockBalance.objects.get(
            business_unit=self.unit,
            variant=self.variant,
            location=self.location,
        )
        self.assertEqual(received_balance.on_hand_quantity, Decimal("10.00"))
        self.assertEqual(received_balance.on_hand_value, Decimal("12000.00"))

        quote_payload = {
            "business_unit_id": self.unit.pk,
            "customer_id": self.customer.pk,
            "kind": SalesQuote.Kind.STOCK,
            "discount_percent": "20.00",
            "discount_reason": "TEST ONLY API price approval scenario",
            "payment_plan": "full",
            "lines": [{
                "variant_id": self.variant.pk,
                "location_id": self.location.pk,
                "quantity": "2.00",
                "specifications": {"finish": "synthetic review fabric"},
            }],
        }
        created = self.post_api(
            sales_client,
            sales_csrf,
            "/api/purchasing/quotes/",
            quote_payload,
            "api-workbench-quote",
        )
        self.assertEqual(created.status_code, 201, created.data)
        quote_id = created.data["quote_id"]
        quote = SalesQuote.objects.get(pk=quote_id)
        self.assertEqual(quote.status, SalesQuote.Status.PENDING_APPROVAL)
        self.assertEqual(quote.lines.get().selling_price_snapshot, Decimal("2000.00"))
        self.assertEqual(quote.total_quote_value, Decimal("3200.00"))

        denied_discount = self.post_api(
            sales_client,
            sales_csrf,
            f"/api/purchasing/quotes/{quote_id}/discount-approval/",
            {"reason": "TEST ONLY salesperson cannot approve 20 percent"},
            "api-workbench-discount-denied",
        )
        self.assertEqual(denied_discount.status_code, 403, denied_discount.data)
        manager_client, manager_csrf = self.authenticated_api_client(manager)
        approved = self.post_api(
            manager_client,
            manager_csrf,
            f"/api/purchasing/quotes/{quote_id}/discount-approval/",
            {"reason": "TEST ONLY assigned manager approves within the synthetic ceiling"},
            "api-workbench-discount-approved",
        )
        self.assertEqual(approved.status_code, 200, approved.data)
        quote.refresh_from_db()
        self.assertEqual(quote.status, SalesQuote.Status.APPROVED)

        hold = self.post_api(
            manager_client,
            manager_csrf,
            f"/api/purchasing/quotes/{quote_id}/holds/",
            {"working_days": 3},
            "api-workbench-hold",
        )
        self.assertEqual(hold.status_code, 201, hold.data)
        invalid_hold = self.post_api(
            manager_client,
            manager_csrf,
            f"/api/purchasing/quotes/{quote_id}/holds/",
            {"working_days": 4},
            "api-workbench-hold-too-long",
        )
        self.assertEqual(invalid_hold.status_code, 400, invalid_hold.data)

        deposit = self.post_api(
            sales_client,
            sales_csrf,
            f"/api/purchasing/quotes/{quote_id}/payments/",
            {"amount": "1600.00", "method": QuotePayment.Method.SIMULATED_CASH, "purpose": "quote"},
            "api-workbench-deposit",
        )
        self.assertEqual(deposit.status_code, 201, deposit.data)
        quote.refresh_from_db()
        self.assertEqual(quote.status, SalesQuote.Status.CONFIRMED)
        self.assertEqual(
            QuoteReservation.objects.filter(quote_line__quote=quote).count(),
            1,
            "The API reserves stock only after the 50% non-returnable deposit threshold is met.",
        )
        self.assertEqual(
            quote.payments.get().non_returnable_amount,
            Decimal("1600.00"),
        )

        denied_post_handover = self.post_api(
            manager_client,
            manager_csrf,
            f"/api/purchasing/quotes/{quote_id}/post-handover-approval/",
            {"deferred_balance_percent": "10.00", "reason": "TEST ONLY unauthorized role"},
            "api-workbench-post-handover-denied",
        )
        self.assertEqual(denied_post_handover.status_code, 403, denied_post_handover.data)
        ceo_client, ceo_csrf = self.authenticated_api_client(ceo)
        post_handover = self.post_api(
            ceo_client,
            ceo_csrf,
            f"/api/purchasing/quotes/{quote_id}/post-handover-approval/",
            {"deferred_balance_percent": "10.00", "reason": "TEST ONLY CEO exception for this synthetic quote"},
            "api-workbench-post-handover-approved",
        )
        self.assertEqual(post_handover.status_code, 200, post_handover.data)

        ready = self.post_api(
            sales_client,
            sales_csrf,
            f"/api/purchasing/quotes/{quote_id}/ready/",
            {"message": "TEST ONLY synthetic readiness notification"},
            "api-workbench-ready",
        )
        self.assertEqual(ready.status_code, 200, ready.data)
        self.assertEqual(SalesQuote.objects.get(pk=quote_id).status, SalesQuote.Status.READY)
        free_period = self.post_api(
            sales_client,
            sales_csrf,
            f"/api/purchasing/quotes/{quote_id}/storage-accrual/",
            {},
            "api-workbench-storage-free-period",
        )
        self.assertEqual(free_period.status_code, 200, free_period.data)
        self.assertEqual(free_period.data["charges"], [])

        SalesQuote.objects.filter(pk=quote_id).update(
            ready_at=timezone.now() - timedelta(days=40),
        )
        accrued = self.post_api(
            sales_client,
            sales_csrf,
            f"/api/purchasing/quotes/{quote_id}/storage-accrual/",
            {},
            "api-workbench-storage-due",
        )
        self.assertEqual(accrued.status_code, 200, accrued.data)
        charge = StorageCharge.objects.get(quote_id=quote_id)
        self.assertEqual(charge.amount, Decimal("32.00"))
        storage_list = sales_client.get(
            "/api/purchasing/storage-charges/",
            {"business_unit_id": self.unit.pk, "quote_id": quote_id},
        )
        self.assertEqual(storage_list.status_code, 200, storage_list.data)
        self.assertEqual(storage_list.data["pagination"]["total_count"], 1)

        insufficient = self.post_api(
            sales_client,
            sales_csrf,
            f"/api/purchasing/quotes/{quote_id}/deliver/",
            {},
            "api-workbench-delivery-insufficient",
        )
        self.assertEqual(insufficient.status_code, 400, insufficient.data)
        additional = self.post_api(
            sales_client,
            sales_csrf,
            f"/api/purchasing/quotes/{quote_id}/payments/",
            {"amount": "1280.00", "method": QuotePayment.Method.SIMULATED_BANK, "purpose": "quote"},
            "api-workbench-payment-to-90",
        )
        self.assertEqual(additional.status_code, 201, additional.data)
        delivered = self.post_api(
            sales_client,
            sales_csrf,
            f"/api/purchasing/quotes/{quote_id}/deliver/",
            {},
            "api-workbench-delivery",
        )
        self.assertEqual(delivered.status_code, 200, delivered.data)
        self.assertEqual(SalesQuote.objects.get(pk=quote_id).status, SalesQuote.Status.DELIVERED)
        storage_payment = self.post_api(
            sales_client,
            sales_csrf,
            f"/api/purchasing/quotes/{quote_id}/payments/",
            {"amount": "32.00", "method": QuotePayment.Method.SIMULATED_CASH, "purpose": "storage"},
            "api-workbench-storage-payment",
        )
        self.assertEqual(storage_payment.status_code, 201, storage_payment.data)

        detail = sales_client.get(f"/api/purchasing/quotes/{quote_id}/")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["status"], SalesQuote.Status.DELIVERED)
        self.assertEqual(
            set(
                QuotePayment.objects.filter(quote_id=quote_id)
                .values_list("method", flat=True)
            ),
            {
                QuotePayment.Method.SIMULATED_CASH,
                QuotePayment.Method.SIMULATED_BANK,
            },
        )

        no_order_admin, admin_csrf = self.authenticated_api_client(self.admin)
        forbidden_receipt = self.post_api(
            no_order_admin,
            admin_csrf,
            "/api/purchasing/receipts/",
            receipt_payload,
            "api-workbench-no-grant",
        )
        self.assertEqual(forbidden_receipt.status_code, 403, forbidden_receipt.data)
        cross_unit_list = sales_client.get(
            "/api/purchasing/quotes/",
            {"business_unit_id": self.roche.pk},
        )
        self.assertEqual(cross_unit_list.status_code, 404, cross_unit_list.data)
        cross_unit_quote = self.post_api(
            sales_client,
            sales_csrf,
            "/api/purchasing/quotes/",
            quote_payload | {"business_unit_id": self.roche.pk},
            "api-workbench-cross-unit",
        )
        self.assertEqual(cross_unit_quote.status_code, 403, cross_unit_quote.data)
        roche_client, roche_csrf = self.authenticated_api_client(self.roche_user)
        blocked_quote = self.post_api(
            roche_client,
            roche_csrf,
            f"/api/purchasing/quotes/{quote_id}/payments/",
            {"amount": "1.00", "method": QuotePayment.Method.SIMULATED_CASH, "purpose": "quote"},
            "api-workbench-other-brand",
        )
        self.assertEqual(blocked_quote.status_code, 404, blocked_quote.data)

    def test_transaction_api_rejects_invalid_receipt_and_quote_payloads_without_writes(self):
        client, csrf_token = self.authenticated_api_client(self.sales)
        receipt_count = PurchaseReceipt.objects.count()
        invalid_receipt = self.post_api(
            client,
            csrf_token,
            "/api/purchasing/receipts/",
            {
                "business_unit_id": self.unit.pk,
                "supplier_id": self.supplier.pk,
                "lines": [{
                    "variant_id": self.variant.pk,
                    "location_id": self.location.pk,
                    "quantity": "1.00",
                    "unit_cost": "100.00",
                }],
                "charges": [{"category": "other", "amount": "25.00", "eligible": False}],
            },
            "api-workbench-ineligible-charge",
        )
        self.assertEqual(invalid_receipt.status_code, 409, invalid_receipt.data)
        self.assertEqual(PurchaseReceipt.objects.count(), receipt_count)

        quote_count = SalesQuote.objects.count()
        invalid_quote = self.post_api(
            client,
            csrf_token,
            "/api/purchasing/quotes/",
            {
                "business_unit_id": self.unit.pk,
                "customer_id": self.customer.pk,
                "kind": SalesQuote.Kind.STOCK,
                "discount_percent": "51.00",
                "discount_reason": "TEST ONLY invalid discount",
                "payment_plan": "full",
                "lines": [{
                    "variant_id": self.variant.pk,
                    "location_id": self.location.pk,
                    "quantity": "1.00",
                    "specifications": {},
                }],
            },
            "api-workbench-invalid-discount",
        )
        self.assertEqual(invalid_quote.status_code, 400, invalid_quote.data)
        self.assertEqual(SalesQuote.objects.count(), quote_count)

        special_order = self.post_api(
            client,
            csrf_token,
            "/api/purchasing/quotes/",
            {
                "business_unit_id": self.unit.pk,
                "customer_id": self.customer.pk,
                "kind": SalesQuote.Kind.SPECIAL_ORDER,
                "discount_percent": "0.00",
                "discount_reason": "TEST ONLY special-order price decision",
                "payment_plan": "downpayment",
                "downpayment_percent": "40.00",
                "payment_terms_reason": "TEST ONLY per-order downpayment choice",
                "lines": [{
                    "variant_id": self.variant.pk,
                    "quantity": "1.00",
                    "specifications": {"fabric": "synthetic special order"},
                }],
            },
            "api-workbench-special-order",
        )
        self.assertEqual(special_order.status_code, 201, special_order.data)
        self.assertEqual(special_order.data["payment_plan"], "downpayment")
        self.assertEqual(special_order.data["downpayment_percent"], "40.00")
        self.assertFalse(special_order.data["special_order_value_bands_configured"])
        self.assertEqual(SalesQuote.objects.count(), quote_count + 1)

        invalid_special_order = self.post_api(
            client,
            csrf_token,
            "/api/purchasing/quotes/",
            {
                "business_unit_id": self.unit.pk,
                "customer_id": self.customer.pk,
                "kind": SalesQuote.Kind.SPECIAL_ORDER,
                "discount_percent": "0.00",
                "discount_reason": "TEST ONLY invalid special-order terms",
                "payment_plan": "downpayment",
                "downpayment_percent": "39.99",
                "payment_terms_reason": "TEST ONLY outside allowed range",
                "lines": [{
                    "variant_id": self.variant.pk,
                    "quantity": "1.00",
                    "specifications": {},
                }],
            },
            "api-workbench-invalid-special-downpayment",
        )
        self.assertEqual(invalid_special_order.status_code, 400, invalid_special_order.data)
        self.assertEqual(SalesQuote.objects.count(), quote_count + 1)

    def test_purchasing_workbench_read_endpoints_keep_existing_scope_and_limits(self):
        quote_id = self.make_quote(key="workbench-read-scope")["quote_id"]
        client = APIClient()

        client.force_login(self.sales)
        quote = client.get(f"/api/purchasing/quotes/{quote_id}/")
        self.assertEqual(quote.status_code, 200, quote.data)
        self.assertEqual(quote.data["business_unit_id"], self.unit.pk)
        selected_unit_quote = client.get(
            f"/api/purchasing/quotes/{quote_id}/",
            {"business_unit_id": self.unit.pk},
        )
        self.assertEqual(selected_unit_quote.status_code, 200, selected_unit_quote.data)
        self.assertEqual(
            client.get(
                f"/api/purchasing/quotes/{quote_id}/",
                {"business_unit_id": self.roche.pk},
            ).status_code,
            404,
        )
        self.assertEqual(
            {key for key in ("payments", "approvals", "holds", "events") if key in quote.data},
            {"payments", "approvals", "holds", "events"},
        )
        quote_list = client.get(
            "/api/purchasing/quotes/",
            {"business_unit_id": self.unit.pk},
        )
        self.assertEqual(quote_list.status_code, 200, quote_list.data)
        self.assertEqual(quote_list.data["rows"][0]["quote_id"], quote_id)
        self.assertEqual(quote_list.data["pagination"]["total_count"], 1)
        self.assertEqual(
            client.get(
                "/api/purchasing/quotes/",
                {"business_unit_id": self.roche.pk},
            ).status_code,
            404,
        )
        self.assertEqual(client.get("/api/purchasing/quotes/").status_code, 400)
        self.assertEqual(client.get("/api/purchasing/receipts/").status_code, 405)
        self.assertEqual(
            client.get(f"/api/governance/purchasing/{self.unit.pk}/").status_code,
            403,
        )
        client.force_login(self.roche_user)
        cross_unit_quote = client.get(f"/api/purchasing/quotes/{quote_id}/")
        self.assertEqual(cross_unit_quote.status_code, 404)
        self.assertEqual(
            client.get(f"/api/governance/purchasing/{self.unit.pk}/").status_code,
            403,
        )

        client.force_login(self.admin)
        self.assertEqual(
            client.get(
                "/api/purchasing/quotes/",
                {"business_unit_id": self.unit.pk},
            ).status_code,
            404,
        )
        configuration = client.get(f"/api/governance/purchasing/{self.unit.pk}/")
        self.assertEqual(configuration.status_code, 200, configuration.data)
        self.assertEqual(configuration.data["business_unit"]["code"], self.unit.code)
        counterparties = client.get("/api/governance/purchasing/counterparties/")
        self.assertEqual(counterparties.status_code, 200, counterparties.data)
        self.assertTrue(counterparties.data["synthetic_only"])
        self.assertIn(self.customer.code, {row["code"] for row in counterparties.data["rows"]})
        counterparty = client.get(
            f"/api/governance/purchasing/counterparties/{self.customer.pk}/"
        )
        self.assertEqual(counterparty.status_code, 200, counterparty.data)
        self.assertEqual(counterparty.data["code"], self.customer.code)

        client.force_login(self.sales)
        self.assertEqual(
            client.get("/api/governance/purchasing/counterparties/").status_code,
            403,
        )
        self.assertEqual(
            client.get(
                f"/api/governance/purchasing/counterparties/{self.customer.pk}/"
            ).status_code,
            403,
        )

    def assert_two_paginated_pages(self, client, path, query, expected_ids, id_key):
        self.assertEqual(len(expected_ids), 3, "Fixture should fill two pages of two and one rows.")
        rows = []
        for page in (1, 2):
            response = client.get(path, {**query, "page": page, "page_size": 2})
            self.assertEqual(response.status_code, 200, response.data)
            metadata = response.data["pagination"]
            self.assertEqual(metadata["page"], page)
            self.assertEqual(metadata["page_size"], 2)
            self.assertEqual(metadata["total_count"], 3)
            self.assertEqual(metadata["total_pages"], 2)
            self.assertEqual(metadata["previous_page"], 1 if page == 2 else None)
            self.assertEqual(metadata["next_page"], 2 if page == 1 else None)
            self.assertEqual(len(response.data["rows"]), 2 if page == 1 else 1)
            rows.extend(response.data["rows"])
        observed_ids = [row[id_key] for row in rows]
        self.assertEqual(observed_ids, expected_ids)
        self.assertEqual(len(set(observed_ids)), 3)
        return rows

    def test_quote_storage_and_counterparty_registers_paginate_without_gaps(self):
        quotes = []
        as_of = timezone.now() + timedelta(days=40)
        for index in range(1, 4):
            key = f"readside-page-{index}"
            self.make_receipt(key=f"{key}-receipt", quantity="10.00")
            quote_data = self.make_quote(
                key=f"{key}-quote",
                quantity="1.00",
            )
            quote = SalesQuote.objects.get(pk=quote_data["quote_id"])
            self.pay(
                quote,
                key=f"{key}-payment",
                amount=str(quote.total_quote_value),
            )
            notify_quote_ready(
                actor=self.sales,
                quote=quote,
                idempotency_key=f"{key}-ready",
                message="TEST ONLY synthetic pagination readiness",
            )
            storage_result = accrue_storage_charges(
                actor=self.sales,
                quote=quote,
                idempotency_prefix=f"{key}-storage",
                as_of=as_of,
            )
            self.assertEqual(len(storage_result["charges"]), 1)
            quotes.append(quote)

        client = APIClient()
        client.force_login(self.sales)
        quote_query = {"business_unit_id": self.unit.pk}
        expected_quote_ids = list(
            SalesQuote.objects.filter(
                business_unit=self.unit,
                is_synthetic=True,
            ).order_by("-created_at", "-pk").values_list("pk", flat=True)
        )
        quote_rows = self.assert_two_paginated_pages(
            client,
            "/api/purchasing/quotes/",
            quote_query,
            expected_quote_ids,
            "quote_id",
        )
        self.assertEqual({row["quote_id"] for row in quote_rows}, {quote.pk for quote in quotes})
        self.assertTrue(all(row["currency"] == "QAR" for row in quote_rows))

        expected_storage_ids = list(
            StorageCharge.objects.filter(
                quote__business_unit=self.unit,
                is_synthetic=True,
            ).order_by("period_start", "pk").values_list("pk", flat=True)
        )
        storage_rows = self.assert_two_paginated_pages(
            client,
            "/api/purchasing/storage-charges/",
            quote_query,
            expected_storage_ids,
            "charge_id",
        )
        self.assertEqual({row["quote_id"] for row in storage_rows}, {quote.pk for quote in quotes})
        self.assertTrue(all(row["amount"] == "20.00" for row in storage_rows))

        self.assertEqual(
            client.get(
                "/api/purchasing/quotes/",
                {"business_unit_id": self.roche.pk, "page_size": 2},
            ).status_code,
            404,
        )
        self.assertEqual(
            client.get(
                "/api/purchasing/storage-charges/",
                {"business_unit_id": self.roche.pk, "page_size": 2},
            ).status_code,
            404,
        )

        SyntheticCounterparty.objects.create(
            party_type=SyntheticCounterparty.Type.CUSTOMER,
            code="TEST-CUST-PAGE-A",
            name="TEST ONLY synthetic pagination customer A",
        )
        SyntheticCounterparty.objects.create(
            party_type=SyntheticCounterparty.Type.CUSTOMER,
            code="TEST-CUST-PAGE-B",
            name="TEST ONLY synthetic pagination customer B",
        )
        client.force_login(self.admin)
        counterparty_query = {"party_type": "customer"}
        expected_counterparty_ids = list(
            SyntheticCounterparty.objects.filter(
                party_type=SyntheticCounterparty.Type.CUSTOMER,
                is_synthetic=True,
            ).order_by("party_type", "code", "pk").values_list("pk", flat=True)
        )
        counterparties = self.assert_two_paginated_pages(
            client,
            "/api/governance/purchasing/counterparties/",
            counterparty_query,
            expected_counterparty_ids,
            "counterparty_id",
        )
        self.assertTrue(all(row["party_type"] == "customer" for row in counterparties))
        self.assertTrue(all(row["name"].startswith("TEST ONLY") for row in counterparties))

        client.force_login(self.sales)
        self.assertEqual(
            client.get(
                "/api/governance/purchasing/counterparties/",
                counterparty_query,
            ).status_code,
            403,
        )
        client.force_login(self.roche_user)
        self.assertEqual(
            client.get(
                "/api/purchasing/quotes/",
                quote_query,
            ).status_code,
            404,
        )
        self.assertEqual(
            client.get(
                "/api/purchasing/storage-charges/",
                quote_query,
            ).status_code,
            404,
        )

    def test_synthetic_staff_governance_routes_persist_workflow_configuration(self):
        client = APIClient()
        client.force_login(self.admin)
        config = client.get(f"/api/governance/purchasing/{self.unit.pk}/")
        self.assertEqual(config.status_code, 200, config.data)
        self.assertEqual(config.data["work_calendar"]["closed_weekdays"], [4])
        self.assertEqual(len(config.data["current_prices"]), 2)
        self.assertEqual(config.data["special_order_value_bands"], [])

        calendar_response = client.put(
            f"/api/governance/purchasing/{self.unit.pk}/work-calendar/",
            {"closed_weekdays": [4], "closed_dates": ["2026-12-25"]},
            format="json",
        )
        self.assertEqual(calendar_response.status_code, 200, calendar_response.data)
        self.assertEqual(calendar_response.data["closed_dates"], ["2026-12-25"])

        price_response = client.post(
            f"/api/governance/purchasing/{self.unit.pk}/selling-prices/",
            {"variant_id": self.alternate_variant.pk, "amount": "125.50"},
            format="json",
        )
        self.assertEqual(price_response.status_code, 201, price_response.data)
        self.assertEqual(price_response.data["approved_selling_price"], "125.50")

        target = get_user_model().objects.create_user(username="synthetic_api_role_target")
        target.set_unusable_password()
        target.save(update_fields=["password"])
        DemoUserMarker.objects.create(user=target)
        UserActionGrant.objects.create(
            user=target,
            business_unit=self.unit,
            action=UserActionGrant.Action.CREATE_ORDER,
        )
        role_response = client.post(
            "/api/governance/purchasing/roles/",
            {
                "business_unit_id": self.unit.pk,
                "user_id": target.pk,
                "role": BusinessUnitRoleAssignment.Role.MANAGER,
            },
            format="json",
        )
        self.assertEqual(role_response.status_code, 201, role_response.data)
        self.assertTrue(
            BusinessUnitRoleAssignment.objects.filter(
                user=target,
                business_unit=self.unit,
                role=BusinessUnitRoleAssignment.Role.MANAGER,
                revoked_at__isnull=True,
            ).exists()
        )

    def test_special_order_bands_start_unconfigured_and_need_approval(self):
        self.assertFalse(SpecialOrderValueBand.objects.filter(business_unit=self.unit, is_active=True).exists())
        draft = create_special_order_value_band(
            actor=self.admin,
            business_unit=self.unit,
            minimum_value=Decimal("1000.00"),
            maximum_value=Decimal("5000.00"),
        )
        self.assertFalse(draft.is_active)
        ceo = self.make_role_user("synthetic_ceo", BusinessUnitRoleAssignment.Role.CEO)
        approved = approve_special_order_value_band(
            actor=ceo,
            band=draft,
            reason="TEST ONLY explicit synthetic band approval",
        )
        self.assertTrue(approved["is_active"])
        self.assertEqual(SpecialOrderValueBand.objects.filter(business_unit=self.unit, is_active=True).count(), 1)

    def test_posted_records_and_audit_records_are_immutable(self):
        self.make_receipt(key="immutable-receipt", charges=[])
        quote = SalesQuote.objects.get(pk=self.make_quote(key="immutable-quote")["quote_id"])
        payment = self.pay(quote, key="immutable-payment", amount="100.00")
        payment_record = QuotePayment.objects.get(pk=payment["payment_id"])
        payment_record.amount = Decimal("1.00")
        with self.assertRaises(ValidationError):
            payment_record.save()

        receipt = PurchaseReceipt.objects.get(idempotency__key="immutable-receipt")
        receipt.purchase_value = Decimal("1.00")
        with self.assertRaises(ValidationError):
            receipt.save()

        event = WorkflowEvent.objects.create(
            quote=quote,
            kind=WorkflowEvent.Kind.READINESS_NOTIFICATION,
            actor=self.sales,
            message="TEST ONLY immutability fixture",
        )
        event.message = "mutated"
        with self.assertRaises(ValidationError):
            event.save()

    def make_role_user(self, username, role, *, unit=None):
        unit = unit or self.unit
        user, created = get_user_model().objects.get_or_create(
            username=username,
            defaults={"is_active": True},
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        DemoUserMarker.objects.get_or_create(user=user)
        UserActionGrant.objects.get_or_create(
            user=user,
            business_unit=unit,
            action=UserActionGrant.Action.CREATE_ORDER,
        )
        BusinessUnitRoleAssignment.objects.get_or_create(
            user=user,
            business_unit=unit,
            role=role,
            revoked_at__isnull=True,
            defaults={"assigned_by": self.admin},
        )
        return user

    @staticmethod
    def add_month(day):
        year = day.year + (day.month == 12)
        month = 1 if day.month == 12 else day.month + 1
        import calendar

        return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


class ConcurrentWorkflowReservationTests(PurchasingFixtureMixin, TransactionTestCase):
    def setUp(self):
        self.create_fixture()
        self.make_receipt(key="concurrent-receipt", charges=[])
        self.quote_one = SalesQuote.objects.get(
            pk=self.make_quote(key="concurrent-quote-1", quantity="7.00")["quote_id"]
        )
        self.quote_two = SalesQuote.objects.get(
            pk=self.make_quote(key="concurrent-quote-2", quantity="7.00")["quote_id"]
        )
        self.user_id = self.sales.pk

    @staticmethod
    def _pay_on_separate_connection(barrier, quote_id, user_id, key):
        close_old_connections()
        try:
            connection = connections["default"]
            connection.ensure_connection()
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                backend_pid = cursor.fetchone()[0]
            barrier.wait(timeout=10)
            user = get_user_model().objects.get(pk=user_id)
            quote = SalesQuote.objects.get(pk=quote_id)
            try:
                result = record_simulated_payment(
                    actor=user,
                    quote=quote,
                    idempotency_key=key,
                    amount=Decimal("7000.00"),
                    method=QuotePayment.Method.SIMULATED_CASH,
                )
                return ("success", result, backend_pid)
            except InsufficientStock as error:
                return ("unavailable", str(error), backend_pid)
        finally:
            connections.close_all()

    def test_two_postgres_connections_cannot_reserve_stock_for_both_quotes(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    self._pay_on_separate_connection,
                    barrier,
                    self.quote_one.pk,
                    self.user_id,
                    "concurrent-payment-one",
                ),
                pool.submit(
                    self._pay_on_separate_connection,
                    barrier,
                    self.quote_two.pk,
                    self.user_id,
                    "concurrent-payment-two",
                ),
            ]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual({result[0] for result in results}, {"success", "unavailable"})
        self.assertEqual(len({result[2] for result in results}), 2)

        balance = StockBalance.objects.get(
            business_unit=self.unit,
            variant=self.variant,
            location=self.location,
        )
        balance.refresh_from_db()
        self.assertEqual(balance.reserved_quantity, Decimal("7.00"))
        self.assertEqual(
            QuoteReservation.objects.filter(
                quote_line__quote__in=[self.quote_one, self.quote_two]
            ).aggregate(total=Sum("quantity"))["total"],
            Decimal("7.00"),
        )
        self.assertEqual(
            QuotePayment.objects.filter(
                quote__in=[self.quote_one, self.quote_two]
            ).count(),
            1,
        )


class ConcurrentPurchaseCommitmentReceiptTests(PurchasingFixtureMixin, TransactionTestCase):
    def setUp(self):
        self.create_fixture()
        commitment = create_purchase_commitment(
            actor=self.sales,
            idempotency_key="concurrent-purchase-commitment",
            business_unit=self.unit,
            supplier=self.supplier,
            lines=[{
                "variant": self.variant,
                "ordered_quantity": Decimal("10.00"),
                "unit_cost": Decimal("1000.00"),
            }],
        )
        self.commitment_id = commitment["purchase_commitment_id"]
        self.commitment_line_id = commitment["lines"][0]["commitment_line_id"]
        self.user_id = self.sales.pk
        self.business_unit_id = self.unit.pk
        self.supplier_id = self.supplier.pk
        self.variant_id = self.variant.pk
        self.location_id = self.location.pk

    @staticmethod
    def _receive_six_on_separate_connection(barrier, ids, key):
        close_old_connections()
        try:
            connection = connections["default"]
            connection.ensure_connection()
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                backend_pid = cursor.fetchone()[0]
            barrier.wait(timeout=10)
            user = get_user_model().objects.get(pk=ids["user_id"])
            unit = BusinessUnit.objects.select_related("legal_entity").get(pk=ids["business_unit_id"])
            supplier = SyntheticCounterparty.objects.get(pk=ids["supplier_id"])
            variant = Variant.objects.get(pk=ids["variant_id"])
            location = Location.objects.get(pk=ids["location_id"])
            commitment = PurchaseCommitment.objects.get(pk=ids["commitment_id"])
            commitment_line = PurchaseCommitmentLine.objects.get(pk=ids["commitment_line_id"])
            try:
                result = receive_imported_purchase(
                    actor=user,
                    idempotency_key=key,
                    business_unit=unit,
                    supplier=supplier,
                    purchase_commitment=commitment,
                    lines=[{
                        "variant": variant,
                        "location": location,
                        "quantity": Decimal("6.00"),
                        "commitment_line": commitment_line,
                    }],
                    charges=[],
                )
                return ("success", result, backend_pid)
            except InvalidTransaction as error:
                return ("rejected", str(error), backend_pid)
        finally:
            connections.close_all()

    def test_separate_postgres_connections_cannot_overreceive_one_commitment_line(self):
        barrier = Barrier(2)
        ids = {
            "user_id": self.user_id,
            "business_unit_id": self.business_unit_id,
            "supplier_id": self.supplier_id,
            "variant_id": self.variant_id,
            "location_id": self.location_id,
            "commitment_id": self.commitment_id,
            "commitment_line_id": self.commitment_line_id,
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    self._receive_six_on_separate_connection,
                    barrier,
                    ids,
                    "concurrent-commitment-receipt-one",
                ),
                pool.submit(
                    self._receive_six_on_separate_connection,
                    barrier,
                    ids,
                    "concurrent-commitment-receipt-two",
                ),
            ]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual({result[0] for result in results}, {"success", "rejected"})
        self.assertEqual(len({result[2] for result in results}), 2)
        self.assertEqual(
            PurchaseReceiptLine.objects.filter(commitment_line_id=self.commitment_line_id)
            .aggregate(total=Sum("quantity"))["total"],
            Decimal("6.00"),
        )
        balance = StockBalance.objects.get(
            business_unit_id=self.business_unit_id,
            variant_id=self.variant_id,
            location_id=self.location_id,
        )
        self.assertEqual(balance.on_hand_quantity, Decimal("6.00"))
