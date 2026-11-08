from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import (
    DemoUserMarker,
    JournalEntry,
    Location,
    SalesQuote,
    StockBalance,
    StockCount,
    StockMovement,
    UserActionGrant,
)
from .purchasing_services import WorkflowPermissionDenied
from .stock_counts import (
    approve_count,
    cancel_count,
    count_detail,
    counts_overview,
    decide_line_action,
    record_counts,
    request_line_action,
    return_count,
    start_count,
    submit_count,
)
from .test_purchasing_workflows import PurchasingFixtureMixin
from .transaction_services import InvalidTransaction, deliver_reserved_stock, transfer_stock

COUNT = UserActionGrant.Action.COUNT_STOCK
APPROVE = UserActionGrant.Action.APPROVE_COUNT
WRITE_OFF = UserActionGrant.Action.APPROVE_WRITE_OFF


@override_settings(APP_ENV="demo")
class StockCountTests(PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()
        UserActionGrant.objects.create(user=cls.sales, business_unit=cls.unit, action=COUNT)
        UserActionGrant.objects.create(user=cls.admin, business_unit=cls.unit, action=APPROVE)
        cls.missing = Location.objects.get(code="XX")
        cls.finance = get_user_model().objects.create_user(username="test_count_finance", password="unused-test-password")
        DemoUserMarker.objects.create(user=cls.finance)
        UserActionGrant.objects.create(user=cls.finance, business_unit=cls.unit, action=WRITE_OFF)

    def setUp(self):
        env = patch.dict("os.environ", {"APP_ENV": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.make_receipt(key="count-receipt")  # 10 at STIV-G, QAR 12,000 with shipping

    def balance(self, location=None):
        return StockBalance.objects.filter(business_unit=self.unit, variant=self.variant, location=location or self.location).first()

    def start(self):
        return start_count(actor=self.sales, business_unit=self.unit, location_id=self.location.pk)

    def counted(self, count, quantity):
        line = next(line for line in count["lines"] if line["sku"] == self.variant.sku)
        return record_counts(actor=self.sales, business_unit=self.unit, count_id=count["id"],
                             entries=[{"line_id": line["id"], "counted": quantity}])

    def approve(self, count_id, key="approve-count"):
        return approve_count(actor=self.admin, business_unit=self.unit, count_id=count_id, idempotency_key=key)

    def confirmed(self, quantity, key="approve-count"):
        count = self.start()
        self.counted(count, quantity)
        submit_count(actor=self.sales, business_unit=self.unit, count_id=count["id"])
        self.approve(count["id"], key=key)
        return count_detail(actor=self.admin, business_unit=self.unit, count_id=count["id"])

    def request(self, count, action, note="", key="request", actor=None):
        return request_line_action(actor=actor or self.admin, business_unit=self.unit, count_id=count["id"],
                                   line_id=count["lines"][0]["id"], action=action, note=note, idempotency_key=key)

    def decide(self, count, approve, note="", key="decide", actor=None):
        return decide_line_action(actor=actor or self.finance, business_unit=self.unit, count_id=count["id"],
                                  line_id=count["lines"][0]["id"], approve=approve, note=note, idempotency_key=key)

    def journal(self, entry_id):
        entry = JournalEntry.objects.get(pk=entry_id)
        return {line.account.code: (line.debit, line.credit) for line in entry.lines.select_related("account")}

    def test_counting_is_blind_for_counters_and_reviewers_see_the_difference(self):
        count = self.start()
        self.assertTrue(count["blind"])
        self.assertNotIn("expected_quantity", count["lines"][0])
        self.assertEqual([line["sku"] for line in count["lines"]], [self.variant.sku])
        self.counted(count, "8")
        submit_count(actor=self.sales, business_unit=self.unit, count_id=count["id"])
        review = count_detail(actor=self.admin, business_unit=self.unit, count_id=count["id"])
        line = review["lines"][0]
        self.assertEqual((line["expected_quantity"], line["counted_quantity"], line["difference"]), ("10.00", "8.00", "-2.00"))
        self.assertTrue(review["can_decide"])
        self.assertFalse(count_detail(actor=self.sales, business_unit=self.unit, count_id=count["id"])["can_decide"])
        self.assertFalse(count_detail(actor=self.finance, business_unit=self.unit, count_id=count["id"])["blind"])

    def test_stock_keeps_moving_during_a_count_and_the_review_uses_live_quantities(self):
        quote = SalesQuote.objects.get(pk=self.make_quote(key="live-quote", quantity="1.00")["quote_id"])
        self.pay(quote, key="live-pay", amount=str(quote.total_quote_value))
        reservation = self.balance().reservations.get()
        count = self.start()
        self.make_receipt(key="live-receipt-elsewhere", location=self.other_location)
        transfer_stock(idempotency_key="live-in", business_unit=self.unit, variant=self.variant,
                       source_location=self.other_location, destination_location=self.location, quantity="2.00")
        self.counted(count, "11")  # 10 + 2 transferred in, then 1 delivered by hand before it was entered
        submit_count(actor=self.sales, business_unit=self.unit, count_id=count["id"])
        review = count_detail(actor=self.admin, business_unit=self.unit, count_id=count["id"])
        self.assertEqual(review["lines"][0]["difference"], "-1.00")
        deliver_reserved_stock(idempotency_key="live-manual-delivery", reservation_id=reservation.pk,
                               unit_selling_price="1500.00")
        review = count_detail(actor=self.admin, business_unit=self.unit, count_id=count["id"])
        self.assertEqual(review["lines"][0]["difference"], "0.00", "The manual delivery, once entered, clears the difference")
        result = self.approve(count["id"])
        self.assertEqual((result["status"], result["moved_to_missing"], result["held_surplus"]), ("closed", "0.00", "0.00"))

    def test_a_shortage_moves_to_xx_and_the_count_waits_for_an_action(self):
        count = self.start()
        self.counted(count, "8")
        submit_count(actor=self.sales, business_unit=self.unit, count_id=count["id"])
        journals = JournalEntry.objects.count()
        result = self.approve(count["id"])
        self.assertEqual((result["status"], result["moved_to_missing"]), ("differences", "2.00"))
        here, missing = self.balance(), self.balance(self.missing)
        self.assertEqual((here.on_hand_quantity, here.on_hand_value), (Decimal("8.00"), Decimal("9600.00")))
        self.assertEqual((missing.on_hand_quantity, missing.on_hand_value), (Decimal("2.00"), Decimal("2400.00")))
        self.assertEqual(JournalEntry.objects.count(), journals, "Stock in XX stays on the books")
        self.assertEqual(self.approve(count["id"]), result, "Confirmation replays with the same key")
        with self.assertRaisesMessage(InvalidTransaction, "differences waiting for action"):
            self.approve(count["id"], key="approve-again")
        detail = count_detail(actor=self.admin, business_unit=self.unit, count_id=count["id"])
        self.assertEqual(detail["lines"][0]["allowed_actions"], ["write_off", "found"])
        self.assertEqual(counts_overview(actor=self.admin, business_unit=self.unit)["counts"][0]["open_differences"], 1)

    def test_a_write_off_needs_a_reason_and_another_persons_finance_approval(self):
        count = self.confirmed("8")
        with self.assertRaisesMessage(InvalidTransaction, "A reason is required"):
            self.request(count, "write_off")
        with self.assertRaisesMessage(InvalidTransaction, "choose write off or found, move back"):
            self.request(count, "accept", note="x")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "approve stock write-offs"):
            self.decide(count, True, actor=self.admin)
        self.request(count, "write_off", note="Damaged in storage, thrown away")
        detail = count_detail(actor=self.finance, business_unit=self.unit, count_id=count["id"])
        self.assertTrue(detail["lines"][0]["can_decide_action"])
        self.assertEqual(detail["lines"][0]["action_status"], "requested")
        with self.assertRaisesMessage(InvalidTransaction, "A reason for rejecting is required"):
            self.decide(count, False)
        rejected = self.decide(count, False, note="Find the delivery note first")
        self.assertEqual(rejected["lines"][0]["action_status"], "")
        self.assertEqual(rejected["lines"][0]["action_decision_note"], "Find the delivery note first")
        UserActionGrant.objects.create(user=self.admin, business_unit=self.unit, action=WRITE_OFF)
        self.request(count, "write_off", note="No delivery note: written off")
        with self.assertRaisesMessage(WorkflowPermissionDenied, "someone else in Finance"):
            self.decide(count, True, actor=self.admin)
        result = self.decide(count, True, key="write-off")
        self.assertEqual(result["value"], "2400.00")
        self.assertEqual(self.balance(self.missing).on_hand_quantity, Decimal("0.00"))
        loss = StockMovement.objects.get(kind=StockMovement.Kind.COUNT_LOSS)
        self.assertEqual((loss.quantity_delta, loss.value_delta), (Decimal("-2.00"), Decimal("-2400.00")))
        self.assertEqual(self.journal(result["journal_entry_id"]), {
            "TEST_STOCK_COUNT": (Decimal("2400.00"), Decimal("0.00")),
            "TEST_INVENTORY": (Decimal("0.00"), Decimal("2400.00")),
        })
        self.assertEqual(self.decide(count, True, key="write-off"), result, "The decision replays with the same key")
        self.assertEqual(StockCount.objects.get(pk=count["id"]).status, "closed")

    def test_a_shortage_found_later_moves_back_without_finance(self):
        count = self.confirmed("8")
        journals = JournalEntry.objects.count()
        detail = self.request(count, "found", key="found")
        self.assertEqual(detail["status"], "closed")
        self.assertEqual(detail["lines"][0]["action_status"], "done")
        self.assertEqual(self.balance().on_hand_quantity, Decimal("10.00"))
        self.assertEqual(self.balance(self.missing).on_hand_quantity, Decimal("0.00"))
        self.assertEqual(JournalEntry.objects.count(), journals)

    def test_a_surplus_is_held_until_finance_accepts_it_at_average_cost(self):
        count = self.confirmed("12")
        self.assertEqual(self.balance().on_hand_quantity, Decimal("10.00"), "A surplus is not stock until accepted")
        self.assertEqual(count["lines"][0]["allowed_actions"], ["accept"])
        self.request(count, "accept", note="Two chairs from an unrecorded return")
        result = self.decide(count, True, key="accept")
        self.assertEqual(result["value"], "2400.00")
        self.assertEqual(self.balance().on_hand_quantity, Decimal("12.00"))
        gain = StockMovement.objects.get(kind=StockMovement.Kind.COUNT_GAIN)
        self.assertEqual((gain.quantity_delta, gain.value_delta), (Decimal("2.00"), Decimal("2400.00")))
        self.assertEqual(self.journal(result["journal_entry_id"]), {
            "TEST_INVENTORY": (Decimal("2400.00"), Decimal("0.00")),
            "TEST_STOCK_COUNT": (Decimal("0.00"), Decimal("2400.00")),
        })
        detail = count_detail(actor=self.sales, business_unit=self.unit, count_id=count["id"])
        self.assertEqual(detail["status"], "closed")
        self.assertFalse(detail["blind"], "Once confirmed, everyone sees the result")
        self.assertEqual(detail["lines"][0]["erp_quantity"], "10.00")

    def test_nobody_confirms_their_own_count_and_returns_need_a_reason(self):
        UserActionGrant.objects.create(user=self.sales, business_unit=self.unit, action=APPROVE)
        count = self.start()
        self.counted(count, "10")
        submit_count(actor=self.sales, business_unit=self.unit, count_id=count["id"])
        with self.assertRaisesMessage(WorkflowPermissionDenied, "someone else must confirm"):
            approve_count(actor=self.sales, business_unit=self.unit, count_id=count["id"], idempotency_key="self")
        with self.assertRaisesMessage(InvalidTransaction, "reason for recounting is required"):
            return_count(actor=self.admin, business_unit=self.unit, count_id=count["id"], note=" ")
        returned = return_count(actor=self.admin, business_unit=self.unit, count_id=count["id"], note="Recount the top shelf")
        self.assertEqual(returned["status"], "counting")
        cancelled = cancel_count(actor=self.sales, business_unit=self.unit, count_id=count["id"], note="Wrong location")
        self.assertEqual(cancelled["status"], "cancelled")
        self.start()  # a cancelled count no longer blocks the location

    def test_rules_for_starting_and_submitting(self):
        with self.assertRaisesMessage(InvalidTransaction, "Virtual locations"):
            start_count(actor=self.sales, business_unit=self.unit, location_id=self.missing.pk)
        count = self.start()
        with self.assertRaisesMessage(InvalidTransaction, "already has a count in progress"):
            self.start()
        with self.assertRaisesMessage(InvalidTransaction, "1 line(s) have no count yet"):
            submit_count(actor=self.sales, business_unit=self.unit, count_id=count["id"])
        found = record_counts(actor=self.sales, business_unit=self.unit, count_id=count["id"],
                              entries=[{"sku": self.alternate_variant.sku.lower(), "counted": "1"}])
        added = next(line for line in found["lines"] if line["sku"] == self.alternate_variant.sku)
        self.assertTrue(added["found_during_count"])
        for bad in ("-1", "1.234", "lots"):
            with self.assertRaises(InvalidTransaction):
                record_counts(actor=self.sales, business_unit=self.unit, count_id=count["id"],
                              entries=[{"sku": self.alternate_variant.sku, "counted": bad}])
        with self.assertRaisesMessage(WorkflowPermissionDenied, "'count stock' or 'approve stock counts' or 'approve stock write-offs and count adjustments'"):
            counts_overview(actor=self.roche_user, business_unit=self.unit)
        overview = counts_overview(actor=self.sales, business_unit=self.unit)
        self.assertNotIn("XX", [location["code"] for location in overview["locations"]])
        self.assertTrue(next(location for location in overview["locations"] if location["id"] == self.location.pk)["has_open_count"])

    def test_reserved_stock_cannot_be_counted_away(self):
        quote = SalesQuote.objects.get(pk=self.make_quote(key="count-quote", quantity="2.00")["quote_id"])
        self.pay(quote, key="count-pay", amount=str(quote.total_quote_value))
        self.assertEqual(self.balance().reserved_quantity, Decimal("2.00"))
        count = self.start()
        self.counted(count, "0")
        submit_count(actor=self.sales, business_unit=self.unit, count_id=count["id"])
        with self.assertRaisesMessage(InvalidTransaction, "reserved or held for customers"):
            self.approve(count["id"])
        self.assertEqual(StockCount.objects.get(pk=count["id"]).status, "submitted")

    def test_api_flow_and_permissions(self):
        counter, approver = APIClient(), APIClient()
        counter.force_authenticate(user=self.sales)
        approver.force_authenticate(user=self.admin)
        unit = f"?unit={self.unit.pk}"
        created = counter.post(f"/api/inventory/stock-counts/{unit}", {"location_id": self.location.pk}, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        count_id, line_id = created.data["id"], created.data["lines"][0]["id"]
        saved = counter.post(f"/api/inventory/stock-counts/{count_id}/counts/{unit}",
                             {"entries": [{"line_id": line_id, "counted": "9"}]}, format="json")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(counter.post(f"/api/inventory/stock-counts/{count_id}/submit/{unit}", {}, format="json").status_code, 200)
        self.assertEqual(counter.post(f"/api/inventory/stock-counts/{count_id}/approve/{unit}", {}, format="json",
                                      HTTP_IDEMPOTENCY_KEY="x").status_code, 403)
        self.assertEqual(approver.post(f"/api/inventory/stock-counts/{count_id}/approve/{unit}", {}, format="json").status_code, 400)
        approved = approver.post(f"/api/inventory/stock-counts/{count_id}/approve/{unit}", {}, format="json",
                                 HTTP_IDEMPOTENCY_KEY="api-approve")
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data["moved_to_missing"], "1.00")
        detail = approver.get(f"/api/inventory/stock-counts/{count_id}/{unit}").data
        line_path = f"/api/inventory/stock-counts/{count_id}/lines/{detail['lines'][0]['id']}"
        requested = approver.post(f"{line_path}/action/{unit}", {"action": "write_off", "note": "Broken"}, format="json",
                                  HTTP_IDEMPOTENCY_KEY="api-request")
        self.assertEqual(requested.status_code, 200, requested.data)
        self.assertEqual(approver.post(f"{line_path}/decision/{unit}", {"approve": True}, format="json",
                                       HTTP_IDEMPOTENCY_KEY="api-own").status_code, 403)
        finance = APIClient()
        finance.force_authenticate(user=self.finance)
        decided = finance.post(f"{line_path}/decision/{unit}", {"approve": True}, format="json", HTTP_IDEMPOTENCY_KEY="api-decide")
        self.assertEqual(decided.status_code, 200, decided.data)
        self.assertEqual(finance.get(f"/api/inventory/stock-counts/{count_id}/{unit}").data["status"], "closed")
        outsider = APIClient()
        outsider.force_authenticate(user=self.roche_user)
        self.assertEqual(outsider.get(f"/api/inventory/stock-counts/?unit={self.roche.pk}").status_code, 403)
