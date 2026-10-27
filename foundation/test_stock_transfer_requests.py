from datetime import date, datetime, timedelta, timezone as dt_timezone
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    DemoUserMarker,
    IdempotencyRecord,
    QuoteHold,
    SalesQuote,
    StockBalance,
    StockMovement,
    StockReservation,
    StockTransfer,
    StockTransferRequest,
    StockTransferRequestEvent,
    UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied, create_temporary_quote_hold
from .stock_transfer_requests import (
    decide_transfer_request,
    dispatch_transfer_request,
    receive_transfer_request,
    schedule_transfer_request,
    submit_transfer_request,
)
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import IdempotencyConflict, InsufficientStock, InvalidTransaction, transfer_stock


Action = UserActionGrant.Action
Status = StockTransferRequest.Status


class TransferRequestFixtureMixin(PurchasingFixtureMixin):
    @classmethod
    def create_transfer_fixture(cls):
        cls.create_fixture()
        cls.destination = cls.other_location
        cls.requester = cls._person("test_showroom_admin", Action.REQUEST_TRANSFER)
        cls.manager = cls._person("test_showroom_manager", Action.APPROVE_TRANSFER)
        cls.warehouse = cls._person("test_warehouse_team", Action.EXECUTE_TRANSFER)

    @classmethod
    def _person(cls, username, *actions):
        user = get_user_model().objects.create_user(username=username, password="unused-test-password")
        DemoUserMarker.objects.create(user=user)
        for action in actions:
            UserActionGrant.objects.create(user=user, business_unit=cls.unit, action=action)
        return user

    def stock(self, location=None, variant=None):
        balance = StockBalance.objects.get(
            business_unit=self.unit, variant=variant or self.variant, location=location or self.location,
        )
        return balance.on_hand_quantity, balance.reserved_quantity

    def submit(self, key="transfer-request", quantity="3.00", lines=None):
        return submit_transfer_request(
            actor=self.requester,
            business_unit=self.unit,
            idempotency_key=key,
            source_location=self.location,
            destination_location=self.destination,
            lines=lines or [{"variant": self.variant, "quantity": Decimal(quantity)}],
            note="TEST ONLY showroom display refresh",
        )

    def request_obj(self, result):
        return StockTransferRequest.objects.get(pk=result["request_id"])

    def through_schedule(self, key="flow", quantity="3.00"):
        transfer_request = self.request_obj(self.submit(key=f"{key}-submit", quantity=quantity))
        decide_transfer_request(
            actor=self.manager, transfer_request=transfer_request, idempotency_key=f"{key}-approve", approve=True,
        )
        schedule_transfer_request(
            actor=self.warehouse, transfer_request=transfer_request, idempotency_key=f"{key}-schedule",
            transfer_date=timezone.localdate().isoformat(),
        )
        return transfer_request


@override_settings(APP_ENV="demo")
class StockTransferRequestServiceTests(TransferRequestFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_transfer_fixture()

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.make_receipt(key="transfer-opening-stock", charges=[])

    def test_full_flow_moves_stock_only_on_receipt_and_keeps_it_off_sale_in_transit(self):
        transfer_request = self.through_schedule()
        self.assertEqual(self.request_obj({"request_id": transfer_request.pk}).status, Status.SCHEDULED)
        self.assertEqual(self.stock(), (Decimal("10.00"), Decimal("0.00")))

        dispatch_transfer_request(actor=self.warehouse, transfer_request=transfer_request, idempotency_key="flow-go")
        transfer_request.refresh_from_db()
        self.assertEqual(transfer_request.status, Status.IN_TRANSIT)
        # In transit: still counted at the source, but reserved so it cannot be sold there,
        # and nothing has arrived at the destination yet.
        self.assertEqual(self.stock(), (Decimal("10.00"), Decimal("3.00")))
        self.assertFalse(StockBalance.objects.filter(location=self.destination, variant=self.variant).exists())
        with self.assertRaises(InsufficientStock):
            self.make_quote_and_hold(quantity="8.00", key="in-transit-oversell")

        receive_transfer_request(actor=self.warehouse, transfer_request=transfer_request, idempotency_key="flow-in")
        transfer_request.refresh_from_db()
        self.assertEqual(transfer_request.status, Status.RECEIVED)
        self.assertEqual(self.stock(), (Decimal("7.00"), Decimal("0.00")))
        self.assertEqual(self.stock(self.destination), (Decimal("3.00"), Decimal("0.00")))
        line = transfer_request.lines.get()
        self.assertEqual(line.reservation.status, StockReservation.Status.RELEASED)
        self.assertEqual(line.transfer.quantity, Decimal("3.00"))
        self.assertEqual(
            sorted(line.transfer.movements.values_list("kind", "quantity_delta")),
            [(StockMovement.Kind.TRANSFER_IN, Decimal("3.00")), (StockMovement.Kind.TRANSFER_OUT, Decimal("-3.00"))],
        )
        self.assertEqual(
            list(transfer_request.events.values_list("to_status", flat=True)),
            [Status.SUBMITTED, Status.APPROVED, Status.SCHEDULED, Status.IN_TRANSIT, Status.RECEIVED],
        )

    def make_quote_and_hold(self, *, quantity, key):
        quote = SalesQuote.objects.get(pk=self.make_quote(key=f"{key}-quote", quantity=quantity)["quote_id"])
        return create_temporary_quote_hold(actor=self.sales, quote=quote, idempotency_key=f"{key}-hold")

    def test_rejection_needs_a_reason_and_is_visible_to_the_requester(self):
        transfer_request = self.request_obj(self.submit())
        with self.assertRaises(InvalidTransaction):
            decide_transfer_request(
                actor=self.manager, transfer_request=transfer_request, idempotency_key="reject-blank",
                approve=False, reason="  ",
            )
        decide_transfer_request(
            actor=self.manager, transfer_request=transfer_request, idempotency_key="reject",
            approve=False, reason="TEST ONLY display already full",
        )
        transfer_request.refresh_from_db()
        self.assertEqual(transfer_request.status, Status.REJECTED)
        self.assertEqual(transfer_request.rejection_reason, "TEST ONLY display already full")
        self.assertEqual(transfer_request.events.last().note, "TEST ONLY display already full")
        with self.assertRaises(InvalidTransaction):
            schedule_transfer_request(
                actor=self.warehouse, transfer_request=transfer_request, idempotency_key="schedule-rejected",
                transfer_date=timezone.localdate().isoformat(),
            )

    def test_each_step_needs_its_own_permission_and_a_second_person_approves(self):
        transfer_request = self.request_obj(self.submit())
        with self.assertRaises(WorkflowPermissionDenied):
            submit_transfer_request(
                actor=self.warehouse, business_unit=self.unit, idempotency_key="warehouse-submits",
                source_location=self.location, destination_location=self.destination,
                lines=[{"variant": self.variant, "quantity": Decimal("1.00")}],
            )
        with self.assertRaises(WorkflowPermissionDenied):
            decide_transfer_request(
                actor=self.requester, transfer_request=transfer_request, idempotency_key="self-approve",
                approve=True,
            )
        # Even someone holding both permissions cannot approve their own request.
        UserActionGrant.objects.create(user=self.requester, business_unit=self.unit, action=Action.APPROVE_TRANSFER)
        with self.assertRaises(WorkflowPermissionDenied):
            decide_transfer_request(
                actor=self.requester, transfer_request=transfer_request, idempotency_key="self-approve-2",
                approve=True,
            )
        with self.assertRaises(WorkflowPermissionDenied):
            schedule_transfer_request(
                actor=self.manager, transfer_request=transfer_request, idempotency_key="manager-schedules",
                transfer_date=timezone.localdate().isoformat(),
            )
        self.manager.unit_action_grants.update(revoked_at=timezone.now())
        with self.assertRaises(WorkflowPermissionDenied):
            decide_transfer_request(
                actor=self.manager, transfer_request=transfer_request, idempotency_key="revoked-approve",
                approve=True,
            )
        transfer_request.refresh_from_db()
        self.assertEqual(transfer_request.status, Status.SUBMITTED)

    def test_database_refuses_a_self_decided_request(self):
        transfer_request = self.request_obj(self.submit())
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockTransferRequest.objects.filter(pk=transfer_request.pk).update(decided_by=self.requester)

    def test_requests_cannot_exceed_free_stock_or_repeat_items_or_use_past_dates(self):
        with self.assertRaises(InsufficientStock):
            self.submit(key="too-much", quantity="11.00")
        with self.assertRaises(InvalidTransaction):
            self.submit(key="duplicate-items", lines=[
                {"variant": self.variant, "quantity": Decimal("1.00")},
                {"variant": self.variant, "quantity": Decimal("1.00")},
            ])
        with self.assertRaises(InvalidTransaction):
            submit_transfer_request(
                actor=self.requester, business_unit=self.unit, idempotency_key="same-place",
                source_location=self.location, destination_location=self.location,
                lines=[{"variant": self.variant, "quantity": Decimal("1.00")}],
            )
        transfer_request = self.request_obj(self.submit(key="dated"))
        decide_transfer_request(
            actor=self.manager, transfer_request=transfer_request, idempotency_key="dated-ok", approve=True,
        )
        with self.assertRaises(InvalidTransaction):
            schedule_transfer_request(
                actor=self.warehouse, transfer_request=transfer_request, idempotency_key="dated-past",
                transfer_date=(timezone.localdate() - timedelta(days=1)).isoformat(),
            )
        self.assertFalse(IdempotencyRecord.objects.filter(key__in=["too-much", "dated-past"]).exists())

    def test_replay_returns_the_same_result_and_a_changed_payload_conflicts(self):
        first = self.submit(key="replayed")
        self.assertEqual(self.submit(key="replayed"), first)
        self.assertEqual(StockTransferRequest.objects.count(), 1)
        with self.assertRaises(IdempotencyConflict):
            self.submit(key="replayed", quantity="2.00")
        transfer_request = self.through_schedule(key="replay-flow")
        dispatch_transfer_request(actor=self.warehouse, transfer_request=transfer_request, idempotency_key="go-once")
        dispatch_transfer_request(actor=self.warehouse, transfer_request=transfer_request, idempotency_key="go-once")
        self.assertEqual(StockReservation.objects.filter(balance__location=self.location).count(), 1)
        receive_transfer_request(actor=self.warehouse, transfer_request=transfer_request, idempotency_key="in-once")
        receive_transfer_request(actor=self.warehouse, transfer_request=transfer_request, idempotency_key="in-once")
        self.assertEqual(StockTransfer.objects.count(), 1)
        self.assertEqual(self.stock(self.destination), (Decimal("3.00"), Decimal("0.00")))
        with self.assertRaises(InvalidTransaction):
            receive_transfer_request(
                actor=self.warehouse, transfer_request=transfer_request, idempotency_key="in-twice",
            )

    def test_failed_dispatch_rolls_back_every_line(self):
        self.make_receipt(key="second-item-stock", variant=self.alternate_variant, quantity="2.00", charges=[])
        transfer_request = self.request_obj(self.submit(key="two-lines", lines=[
            {"variant": self.variant, "quantity": Decimal("3.00")},
            {"variant": self.alternate_variant, "quantity": Decimal("2.00")},
        ]))
        decide_transfer_request(
            actor=self.manager, transfer_request=transfer_request, idempotency_key="two-ok", approve=True,
        )
        schedule_transfer_request(
            actor=self.warehouse, transfer_request=transfer_request, idempotency_key="two-date",
            transfer_date=timezone.localdate().isoformat(),
        )
        # The second item is sold on a hold after approval, so the dispatch must fail as a whole.
        quote = SalesQuote.objects.get(pk=self.make_quote(
            key="late-sale-quote", quantity="1.00", variant=self.alternate_variant,
        )["quote_id"])
        create_temporary_quote_hold(actor=self.sales, quote=quote, idempotency_key="late-sale-hold")
        before = (StockReservation.objects.count(), IdempotencyRecord.objects.count())
        with self.assertRaises(InsufficientStock):
            dispatch_transfer_request(
                actor=self.warehouse, transfer_request=transfer_request, idempotency_key="two-go",
            )
        transfer_request.refresh_from_db()
        self.assertEqual(transfer_request.status, Status.SCHEDULED)
        self.assertEqual((StockReservation.objects.count(), IdempotencyRecord.objects.count()), before)
        self.assertEqual(self.stock(), (Decimal("10.00"), Decimal("0.00")))
        self.assertFalse(transfer_request.lines.filter(reservation__isnull=False).exists())

    def test_events_are_append_only(self):
        transfer_request = self.request_obj(self.submit())
        event = transfer_request.events.get()
        with self.assertRaises(Exception):
            event.save()
        with self.assertRaises(Exception):
            event.delete()
        self.assertEqual(StockTransferRequestEvent.objects.count(), 1)


@override_settings(APP_ENV="demo")
class QuoteHoldBlockingTests(TransferRequestFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_transfer_fixture()

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.make_receipt(key="hold-opening-stock", charges=[])

    def held_quote(self, quantity="8.00", key="blocking", **kwargs):
        quote = SalesQuote.objects.get(pk=self.make_quote(key=f"{key}-quote", quantity=quantity)["quote_id"])
        return quote, create_temporary_quote_hold(
            actor=self.sales, quote=quote, idempotency_key=f"{key}-hold", **kwargs,
        )

    def test_hold_defaults_to_three_working_days(self):
        thursday = timezone.make_aware(datetime(2026, 10, 22, 10, 30))
        quote = SalesQuote.objects.get(pk=self.make_quote(key="default-days-quote")["quote_id"])
        result = create_temporary_quote_hold(
            actor=self.sales, quote=quote, idempotency_key="default-days", now=thursday,
        )
        self.assertEqual(result["working_days"], 3)
        self.assertEqual(timezone.localtime(datetime.fromisoformat(result["expires_at"])).date(), date(2026, 10, 26))

    def test_hold_counts_from_the_qatar_date_not_the_utc_date(self):
        # 22:30 UTC on Wednesday is 01:30 on Thursday in Doha.
        late = datetime(2026, 10, 21, 22, 30, tzinfo=dt_timezone.utc)
        quote = SalesQuote.objects.get(pk=self.make_quote(key="late-quote")["quote_id"])
        result = create_temporary_quote_hold(
            actor=self.sales, quote=quote, idempotency_key="late-hold", working_days=1, now=late,
        )
        # Thursday plus one working day skips Friday (closed) and lands on Saturday.
        self.assertEqual(timezone.localtime(datetime.fromisoformat(result["expires_at"])).date(), date(2026, 10, 24))

    def test_held_stock_cannot_be_transferred_away(self):
        self.held_quote()
        with self.assertRaises(InsufficientStock):
            transfer_stock(
                idempotency_key="move-held", business_unit=self.unit, variant=self.variant,
                source_location=self.location, destination_location=self.destination, quantity="3.00",
            )
        with self.assertRaises(InsufficientStock):
            self.submit(key="request-held")
        transfer_stock(
            idempotency_key="move-free", business_unit=self.unit, variant=self.variant,
            source_location=self.location, destination_location=self.destination, quantity="2.00",
        )
        self.assertEqual(self.stock(), (Decimal("8.00"), Decimal("0.00")))

    def test_hold_stops_blocking_once_it_expires(self):
        quote, _hold = self.held_quote()
        QuoteHold.objects.filter(quote=quote).update(expires_at=timezone.now() - timedelta(minutes=1))
        transfer_stock(
            idempotency_key="move-after-expiry", business_unit=self.unit, variant=self.variant,
            source_location=self.location, destination_location=self.destination, quantity="3.00",
        )
        self.assertEqual(StockMovement.objects.filter(kind=StockMovement.Kind.TRANSFER_OUT).count(), 1)

    def test_stock_balance_api_shows_held_and_free_to_sell(self):
        self.held_quote()
        UserActionGrant.objects.get_or_create(user=self.sales, business_unit=self.unit, action=Action.READ_STOCK)
        client = APIClient()
        client.force_login(self.sales)
        response = client.get("/api/stock-balances/", {"unit": self.unit.pk, "location": self.location.pk})
        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.data["results"] if item["variant_sku"] == self.variant.sku)
        self.assertEqual(row["quantity"], "10.00")
        self.assertEqual(row["held_quantity"], "8.00")
        self.assertEqual(row["sellable_quantity"], "2.00")


@override_settings(APP_ENV="demo")
class StockTransferRequestApiTests(TransferRequestFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_transfer_fixture()

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.make_receipt(key="api-opening-stock", charges=[])

    def client_for(self, user):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(user)
        csrf = client.get("/api/auth/csrf/").data["csrfToken"]
        return client, csrf

    def post(self, user, path, payload, key):
        client, csrf = self.client_for(user)
        return client.post(
            f"{path}?unit={self.unit.pk}", payload, format="json",
            HTTP_X_CSRFTOKEN=csrf, HTTP_IDEMPOTENCY_KEY=key,
        )

    def test_four_people_walk_a_request_through_the_api(self):
        client, _csrf = self.client_for(self.requester)
        picker = client.get("/api/transfer-requests/stock/", {"unit": self.unit.pk, "location": self.location.pk})
        self.assertEqual(picker.status_code, 200)
        self.assertEqual(picker.data["items"][0]["free_quantity"], "10.00")

        created = self.post(self.requester, "/api/transfer-requests/", {
            "source_location_id": self.location.pk,
            "destination_location_id": self.destination.pk,
            "lines": [{"variant_id": self.variant.pk, "quantity": "4.00"}],
            "note": "TEST ONLY",
        }, "api-submit")
        self.assertEqual(created.status_code, 201, created.data)
        request_id = created.data["request_id"]
        base = f"/api/transfer-requests/{request_id}"

        manager_view = self.client_for(self.manager)[0].get(f"{base}/", {"unit": self.unit.pk})
        self.assertEqual(manager_view.data["next_steps"], ["approve", "reject"])
        requester_view = client.get(f"{base}/", {"unit": self.unit.pk})
        self.assertEqual(requester_view.data["next_steps"], [])

        self.assertEqual(self.post(self.requester, f"{base}/approve/", {}, "api-self").status_code, 403)
        self.assertEqual(self.post(self.manager, f"{base}/approve/", {}, "api-approve").status_code, 200)
        self.assertEqual(
            self.post(self.warehouse, f"{base}/schedule/", {"transfer_date": "not-a-date"}, "api-bad-date").status_code,
            400,
        )
        scheduled = self.post(
            self.warehouse, f"{base}/schedule/", {"transfer_date": timezone.localdate().isoformat()}, "api-date",
        )
        self.assertEqual(scheduled.status_code, 200, scheduled.data)
        self.assertEqual(self.post(self.warehouse, f"{base}/dispatch/", {}, "api-go").status_code, 200)
        self.assertEqual(self.post(self.warehouse, f"{base}/receive/", {}, "api-in").status_code, 200)

        listing = client.get("/api/transfer-requests/", {"unit": self.unit.pk})
        self.assertEqual(listing.status_code, 200)
        self.assertTrue(listing.data["permissions"]["can_request"])
        self.assertFalse(listing.data["permissions"]["can_execute"])
        self.assertEqual(listing.data["results"][0]["status"], Status.RECEIVED)
        self.assertEqual(self.stock(self.destination), (Decimal("4.00"), Decimal("0.00")))

    def test_rejection_reason_reaches_the_requester(self):
        created = self.post(self.requester, "/api/transfer-requests/", {
            "source_location_id": self.location.pk,
            "destination_location_id": self.destination.pk,
            "lines": [{"variant_id": self.variant.pk, "quantity": "1.00"}],
        }, "api-reject-submit")
        base = f"/api/transfer-requests/{created.data['request_id']}"
        self.assertEqual(self.post(self.manager, f"{base}/reject/", {}, "api-reject-blank").status_code, 400)
        self.assertEqual(
            self.post(self.manager, f"{base}/reject/", {"reason": "TEST ONLY not this week"}, "api-reject").status_code,
            200,
        )
        detail = self.client_for(self.requester)[0].get(f"{base}/", {"unit": self.unit.pk}).data
        self.assertEqual(detail["status"], Status.REJECTED)
        self.assertEqual(detail["rejection_reason"], "TEST ONLY not this week")
        self.assertTrue(detail["is_mine"])

    def test_people_without_a_transfer_permission_see_nothing(self):
        outsider = self._person("test_outsider", Action.READ_STOCK)
        created = self.post(self.requester, "/api/transfer-requests/", {
            "source_location_id": self.location.pk,
            "destination_location_id": self.destination.pk,
            "lines": [{"variant_id": self.variant.pk, "quantity": "1.00"}],
        }, "api-private")
        client = self.client_for(outsider)[0]
        self.assertEqual(client.get("/api/transfer-requests/", {"unit": self.unit.pk}).status_code, 403)
        self.assertEqual(
            client.get(f"/api/transfer-requests/{created.data['request_id']}/", {"unit": self.unit.pk}).status_code,
            403,
        )
        self.assertEqual(
            self.post(outsider, f"/api/transfer-requests/{created.data['request_id']}/approve/", {}, "x").status_code,
            403,
        )

    def test_locations_outside_the_unit_are_refused(self):
        foreign = self.roche.locations.get(code="SRBB")
        self.assertFalse(foreign.business_units.filter(pk=self.unit.pk).exists())
        response = self.post(self.requester, "/api/transfer-requests/", {
            "source_location_id": self.location.pk,
            "destination_location_id": foreign.pk,
            "lines": [{"variant_id": self.variant.pk, "quantity": "1.00"}],
        }, "api-foreign")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(StockTransferRequest.objects.exists())


@override_settings(APP_ENV="demo")
class ConcurrentDispatchAndHoldTests(TransferRequestFixtureMixin, TransactionTestCase):
    """A dispatch and a quote hold racing for the same stock on two PostgreSQL connections."""

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.create_transfer_fixture()
        self.make_receipt(key="race-opening-stock", charges=[])
        self.transfer_request = self.through_schedule(key="race", quantity="7.00")
        self.quote = SalesQuote.objects.get(pk=self.make_quote(key="race-quote", quantity="7.00")["quote_id"])

    @staticmethod
    def _on_own_connection(barrier, work):
        close_old_connections()
        try:
            connections["default"].ensure_connection()
            barrier.wait(timeout=10)
            try:
                work()
                return "success"
            except InsufficientStock:
                return "unavailable"
        finally:
            connections.close_all()

    def test_only_one_of_a_dispatch_and_a_hold_gets_the_stock(self):
        barrier = Barrier(2)
        warehouse_id, sales_id = self.warehouse.pk, self.sales.pk
        request_id, quote_id = self.transfer_request.pk, self.quote.pk

        def dispatch():
            dispatch_transfer_request(
                actor=get_user_model().objects.get(pk=warehouse_id),
                transfer_request=StockTransferRequest.objects.get(pk=request_id),
                idempotency_key="race-go",
            )

        def hold():
            create_temporary_quote_hold(
                actor=get_user_model().objects.get(pk=sales_id),
                quote=SalesQuote.objects.get(pk=quote_id),
                idempotency_key="race-hold",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._on_own_connection, barrier, work) for work in (dispatch, hold)]
            outcomes = sorted(future.result(timeout=30) for future in futures)
        self.assertEqual(outcomes, ["success", "unavailable"])
        on_hand, reserved = self.stock()
        held = QuoteHold.objects.filter(status=QuoteHold.Status.ACTIVE).count()
        self.assertEqual(on_hand, Decimal("10.00"))
        self.assertIn((reserved, held), [(Decimal("7.00"), 0), (Decimal("0.00"), 1)])
