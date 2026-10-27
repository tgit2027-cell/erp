"""Disposable synthetic collection worklist contracts; no shared data."""
from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from . import test_purchasing_workflows as purchasing
from .models import (
    BusinessUnitRoleAssignment, Location, QuotePayment, SalesQuote, UserActionGrant,
)
from .purchasing_services import (
    accrue_storage_charges, approve_post_handover_balance, configure_selling_price,
    create_sales_quote, deliver_sales_quote, notify_quote_ready,
)


class CollectionWorklistTests(purchasing.PurchasingFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_fixture()

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.sales)
        self.token = self.client.get("/api/auth/csrf/").data["csrfToken"]

    def get(self, **filters):
        return self.client.get("/api/purchasing/collections/", {
            "business_unit_id": self.unit.pk, **filters,
        })

    def quote(self, key):
        return SalesQuote.objects.get(pk=self.make_quote(key=key)["quote_id"])

    def facts(self):
        return {m._meta.label: list(m.objects.order_by("pk").values())
                for m in apps.get_app_config("foundation").get_models()}

    def api_payment(self, quote, amount, key):
        response = self.client.post(
            f"/api/purchasing/quotes/{quote.pk}/payments/",
            {"amount": amount, "method": "simulated_cash", "purpose": "quote"},
            format="json", HTTP_IDEMPOTENCY_KEY=key, HTTP_X_CSRFTOKEN=self.token,
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_no_partial_full_and_decimal_collections_filter_before_count(self):
        self.make_receipt(charges=[])
        unpaid, partial, full = [self.quote(k) for k in ("unpaid", "partial", "full")]
        self.pay(partial, key="partial-payment", amount="2000.00")
        self.pay(full, key="full-payment", amount="4000.00")
        rows = self.get().data["rows"]
        self.assertEqual({r["quote_id"] for r in rows}, {unpaid.pk, partial.pk})
        by_id = {r["quote_id"]: r for r in rows}
        self.assertEqual(by_id[unpaid.pk]["recorded_collections"], "0.00")
        self.assertEqual(by_id[partial.pk]["remaining_balance"], "2000.00")
        all_rows = self.get(remaining_only="false").data["rows"]
        self.assertEqual(next(r for r in all_rows if r["quote_id"] == full.pk)["remaining_balance"], "0.00")
        configure_selling_price(actor=self.admin, business_unit=self.unit,
                                variant=self.variant, amount=Decimal("41.25"))
        decimal_quote = self.quote("decimal")
        self.pay(decimal_quote, key="decimal-payment", amount="19.99")
        response = self.get(quote_id=decimal_quote.pk)
        row = response.data["rows"][0]
        self.assertEqual((row["total_quote_value"], row["recorded_collections"],
                          row["remaining_balance"]), ("82.50", "19.99", "62.51"))
        self.assertEqual(response.data["pagination"]["total_count"], 1)

    def test_storage_collections_and_exact_payment_retries_do_not_reduce_merchandise(self):
        self.make_receipt(charges=[])
        quote = self.quote("storage-separate")
        first = self.api_payment(quote, "2000.00", "collection-exact-replay")
        self.assertEqual(self.api_payment(quote, "2000.00", "collection-exact-replay"), first)
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key="collection-ready")
        quote.refresh_from_db()
        accrue_storage_charges(actor=self.sales, quote=quote,
                               idempotency_prefix="collection-storage",
                               as_of=quote.ready_at + timedelta(days=35))
        self.pay(quote, key="storage-only", amount="12.34", purpose="storage")
        self.pay(quote, key="storage-only", amount="12.34", purpose="storage")
        self.assertEqual(QuotePayment.objects.filter(quote=quote).count(), 2)
        row = self.get(quote_id=quote.pk).data["rows"][0]
        self.assertEqual((row["recorded_collections"], row["remaining_balance"]),
                         ("2000.00", "2000.00"))
        self.assertIn("Storage collections are separate", self.get().data["test_only_label"])

    def test_approved_post_delivery_balance_and_retry_preserve_delivery_facts(self):
        self.make_receipt(charges=[])
        quote = self.quote("delivered-collection")
        self.pay(quote, key="ninety-percent", amount="3600.00")
        ceo = purchasing.PurchasingWorkflowTests.make_role_user(
            self, "TEST_ONLY_collection_ceo", BusinessUnitRoleAssignment.Role.CEO,
        )
        approve_post_handover_balance(
            actor=ceo, quote=quote, idempotency_key="deferred-approved",
            deferred_balance_percent="10.00", reason="TEST ONLY existing permitted exception",
        )
        notify_quote_ready(actor=self.sales, quote=quote, idempotency_key="deferred-ready")
        deliver_sales_quote(actor=self.sales, quote=quote, idempotency_key="deferred-delivery")
        quote.refresh_from_db()
        preserved = (quote.status, quote.ready_at, quote.delivered_at)
        row = self.get(quote_id=quote.pk).data["rows"][0]
        self.assertEqual(row["remaining_balance"], "400.00")
        self.assertEqual(row["status"], "delivered")
        self.assertEqual(row["delivered_at"], quote.delivered_at.isoformat())
        payment = self.api_payment(quote, "400.00", "after-delivery")
        before_retry = self.facts()
        self.assertEqual(self.api_payment(quote, "400.00", "after-delivery"), payment)
        self.assertEqual(self.facts(), before_retry)
        self.assertEqual(self.get(quote_id=quote.pk).data["rows"], [])
        row = self.get(quote_id=quote.pk, remaining_only="false").data["rows"][0]
        self.assertEqual((row["recorded_collections"], row["remaining_balance"]), ("4000.00", "0.00"))
        quote.refresh_from_db()
        self.assertEqual((quote.status, quote.ready_at, quote.delivered_at), preserved)

    def test_pagination_ties_exact_lookup_and_location_filters_stay_scoped(self):
        quotes = [self.quote(f"page-{i}") for i in range(7)]
        SalesQuote.objects.filter(pk__in=[q.pk for q in quotes]).update(created_at=timezone.now())
        seen = []
        for page, size in ((1, 3), (2, 3), (3, 1)):
            response = self.get(page=page, page_size=3, location_id=self.location.pk)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["pagination"]["total_count"], 7)
            self.assertEqual(len(response.data["rows"]), size)
            seen += [r["quote_id"] for r in response.data["rows"]]
        self.assertEqual(seen, sorted([q.pk for q in quotes], reverse=True))
        self.assertEqual(self.get(quote_id=quotes[0].pk).data["rows"][0]["quote_id"], quotes[0].pk)
        self.assertEqual(self.get(quote_id=99999999).data["rows"], [])
        self.assertEqual(self.get(location_id=self.other_location.pk).data["rows"], [])
        foreign = Location.objects.filter(is_synthetic=True).exclude(business_units=self.unit).first()
        self.assertIsNotNone(foreign)
        self.assertEqual(self.get(location_id=foreign.pk).status_code, 404)

    def test_foreign_unit_quotes_and_reversed_access_do_not_leak_balances(self):
        other = purchasing.PurchasingWorkflowTests.make_role_user(
            self, "TEST_ONLY_collection_roche", BusinessUnitRoleAssignment.Role.SALESPERSON,
            unit=self.roche,
        )
        configure_selling_price(actor=self.admin, business_unit=self.roche,
                                variant=self.variant, amount=Decimal("25.00"))
        foreign = create_sales_quote(
            actor=other, business_unit=self.roche, customer=self.customer,
            idempotency_key="foreign-quote", kind="special_order",
            lines=[{"variant": self.variant, "location": None, "quantity": Decimal("1.00")}],
            discount_percent=Decimal("0.00"), discount_reason="TEST ONLY existing permitted price",
            payment_plan="full", payment_terms_reason="TEST ONLY existing full-payment plan",
        )
        self.assertEqual(self.get(quote_id=foreign["quote_id"]).data["rows"], [])
        self.assertEqual(self.get(business_unit_id=self.roche.pk).status_code, 404)
        self.client.force_login(self.roche_user)
        self.assertEqual(self.get().status_code, 404)
        self.client.force_login(self.admin)  # Staff alone is insufficient.
        self.assertEqual(self.get().status_code, 404)
        self.client.force_login(self.sales)
        self.sales_role.revoked_at = timezone.now()
        self.sales_role.save(update_fields=["revoked_at"])
        self.assertEqual(self.get().status_code, 403)
        self.sales_role.revoked_at = None
        self.sales_role.save(update_fields=["revoked_at"])
        self.sales_grant.revoked_at = timezone.now()
        self.sales_grant.save(update_fields=["revoked_at"])
        self.assertEqual(self.get().status_code, 404)

    def test_read_only_allowlist_constant_query_count_and_no_records_created(self):
        self.quote("allowlist")
        before = self.facts()
        with CaptureQueriesContext(connection) as queries:
            response = self.get(remaining_only="false")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data["rows"][0]), {
            "quote_id", "kind", "status", "currency", "total_quote_value",
            "recorded_collections", "remaining_balance", "ready_at", "delivered_at",
        })
        self.assertTrue(all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries))
        self.assertEqual(self.facts(), before)
        count = len(queries)
        for i in range(5):
            self.quote(f"query-{i}")
        with CaptureQueriesContext(connection) as more:
            self.assertEqual(len(self.get(remaining_only="false").data["rows"]), 6)
        self.assertEqual(len(more), count)  # No per-quote payment lookups.
        self.assertEqual(self.client.post(
            "/api/purchasing/collections/", {}, HTTP_X_CSRFTOKEN=self.token,
        ).status_code, 405)

    def test_invalid_filters_pagination_and_unauthenticated_requests_fail_closed(self):
        for filters in ({"page": 0}, {"page_size": 101}, {"page": 2},
                        {"remaining_only": "yes"}, {"quote_id": "bad"}, {"location_id": 0}):
            with self.subTest(filters=filters):
                self.assertEqual(self.get(**filters).status_code, 400)
        self.assertEqual(self.client.get("/api/purchasing/collections/").status_code, 400)
        self.client.logout()
        self.assertEqual(self.get().status_code, 403)
